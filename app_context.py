import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional


logger = logging.getLogger("alarm_rag.app_context")


def load_local_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_local_env()

from pydantic import BaseModel, Field

from config_values import env_float, env_int
from bm25_text import expand_query_with_domain_aliases
from rag_engine import AlarmRAGEngine
from secret_values import secret_value
from storage import (
    ALARM_LOG_PATH,
    DB_PATH,
    ERROR_LOG_PATH,
    INGEST_LOG_PATH,
    QUERY_LOG_PATH,
    append_jsonl,
    build_legacy_document_entry,
    ensure_db_dir,
    get_documents,
    is_safe_path_segment,
    read_jsonl,
)

ensure_db_dir()


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-nemo:latest")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
SCHOOL_API_BASE_URL = os.getenv("SCHOOL_API_BASE_URL", "").rstrip("/")
SCHOOL_API_KEY = secret_value("SCHOOL_API_KEY")
SCHOOL_API_MODEL = os.getenv("SCHOOL_API_MODEL", "gpt-oss-120b")
SCHOOL_API_FALLBACK_TO_OLLAMA = os.getenv("SCHOOL_API_FALLBACK_TO_OLLAMA", "true").strip().lower() == "true"
LLM_TIMEOUT_SECONDS = env_float("RAG_LLM_TIMEOUT_SECONDS", 20, minimum=0.1)
RAG_CHAT_TOP_K = env_int("RAG_CHAT_TOP_K", 3, minimum=1, maximum=20)
RAG_CONTEXT_CHARS_PER_DOC = env_int("RAG_CONTEXT_CHARS_PER_DOC", 1800, minimum=200)
RAG_MAX_OUTPUT_TOKENS = env_int("RAG_MAX_OUTPUT_TOKENS", 512, minimum=64)
RAG_OLLAMA_NUM_CTX = env_int("RAG_OLLAMA_NUM_CTX", 4096, minimum=512)
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip() or "30m"
REFERENCE_DIR = os.path.join(DB_PATH, "reference")
FEEDBACK_LOG = os.path.join(DB_PATH, "feedback.jsonl")

SYSTEM_PROMPT = """You are a SINUMERIK factory maintenance assistant.
Your ONLY job is to copy the alarm information from the provided manual sections WORD FOR WORD.
Do NOT paraphrase, summarize, or change any text. Do NOT add information from your own knowledge.
Always respond in Traditional Chinese. Translate the output fields into Chinese.

Output format — include ONLY the fields that exist in the manual section:

**Alarm [code]:** [copy exact title including any %1 %2 placeholders]
**Parameters:** [copy exactly — ONLY if the manual section has a Parameters field]
**Explanation:** [copy every word exactly as written in the manual]
**Reaction:** [copy every line exactly as written in the manual]
**Remedy:** [copy every word exactly as written in the manual]
**Program continuation:** [copy exactly as written in the manual]
**Manual Page:** [page number]

STRICT RULES:
1. Copy text EXACTLY — do not change a single word, do not shorten any field
2. If a field like Parameters exists in the manual section, you MUST include it
3. Keep %1 %2 %3 placeholders exactly as they appear
4. If the alarm code is NOT found in the provided sections, respond ONLY with:
   無法在手冊中找到警報代碼 [code]，請確認代碼或建立索引後再試。
5. Do NOT add any extra explanation or commentary outside the format above"""

CHAT_SYSTEM_PROMPT = """You are a SINUMERIK CNC machine maintenance assistant with access to official manuals.
Answer questions in Traditional Chinese unless the user writes in another language.

When manual sections are provided, base your answer on them. You may:
- Summarize and explain in plain language
- Combine information from multiple sections
- Answer follow-up questions using the conversation history
- Say "根據手冊..." when citing manual content

If the manual sections are not relevant to the question, answer from general CNC/SINUMERIK knowledge and clearly say so.
Keep answers concise and practical for factory floor use."""

DIAGNOSTIC_SYSTEM_PROMPT = """You are a SINUMERIK CNC maintenance assistant answering a troubleshooting question.
Use ONLY facts supported by the retrieved sections. Answer the user's actual question in Traditional Chinese; do not merely
copy or translate one alarm entry.

GROUNDING RULES:
1. A header such as [Alarm: 3000] proves that Alarm 3000 was found. Never claim that a requested code was not found when
   the same code appears in a retrieved header.
2. Start with the checks directly supported by the exact alarm section, then add relevant checks from the other retrieved
   sections. Every check MUST end with its supporting source, for example [Alarm 3000, P.58] or [mock-week2-sop].
3. Do not invent PLC bits, signal states, thresholds, causal relationships, or machine-specific procedures.
4. Clearly distinguish official manual content from internal or MOCK DATA sources. Never present mock knowledge as an
   official manual instruction.
5. If the retrieved sections do not cover part of the user's scenario, say exactly what is not covered and ask for the
   missing alarm code, PLC signal, or machine state needed to continue.
6. Do not say a cause is likely unless a retrieved section explicitly states that relationship.
7. Use at most six short bullets and 300 Traditional Chinese characters. Organize the answer as: direct conclusion, checks,
   and missing evidence (only when needed)."""

NOT_READY_TEMPLATE = """⚠️ The index for manual **{name}** has not been built yet.

Please run:
```
docker exec -it alarm_rag python ingest.py --pdf data/YOUR_MANUAL.pdf --name {name}
```
Then restart: `docker restart alarm_rag`"""

FREE_CHAT_SYSTEM = """You are a helpful factory maintenance assistant for SINUMERIK CNC machines.
Answer questions in Traditional Chinese (繁體中文) about machine operation, maintenance, and general technical topics.
Be concise and practical."""


class Message(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    model: Optional[str] = Field(default=OLLAMA_MODEL, max_length=255)
    messages: List[Message] = Field(min_length=1, max_length=24)
    stream: Optional[bool] = False
    temperature: Optional[float] = Field(default=0.1, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=1024, ge=1, le=8192)


class AlarmTrigger(BaseModel):
    alarm_code: str = Field(min_length=1, max_length=128)
    manual: Optional[str] = Field(default="808d", max_length=64)
    machine_id: Optional[str] = Field(default=None, max_length=255)
    source: Optional[str] = Field(default="API", max_length=128)
    external_event_id: Optional[str] = Field(default=None, max_length=255)
    severity: Optional[str] = Field(default=None, max_length=32)
    description: Optional[str] = Field(default=None, max_length=10_000)
    rag_answer_id: Optional[str] = Field(default="", max_length=255)


class FeedbackRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    collection: str = Field(min_length=1, max_length=128)
    feedback: str = Field(min_length=1, max_length=32)
    alarm_code: Optional[str] = Field(default=None, max_length=128)
    answer_id: Optional[str] = Field(default=None, max_length=255)
    issue_id: Optional[str] = Field(default=None, max_length=128)
    work_order_id: Optional[str] = Field(default=None, max_length=128)
    user_id: Optional[str] = Field(default=None, max_length=128)
    role: Optional[str] = Field(default=None, max_length=32)
    correctness: Optional[str] = Field(default=None, max_length=64)
    coverage: Optional[str] = Field(default=None, max_length=64)
    missing_info: Optional[str] = Field(default=None, max_length=20_000)
    expected_fix: Optional[str] = Field(default=None, max_length=20_000)
    kb_candidate: Optional[bool] = None


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    code: Optional[str] = Field(default="", max_length=128)
    title: Optional[str] = Field(default="", max_length=500)
    page: Optional[int] = Field(default=0, ge=0, le=1_000_000)
    source: Optional[str] = Field(default="api", max_length=128)


engines: Dict[str, AlarmRAGEngine] = {}
pending_alarms: list[dict] = []
alarm_history: list[dict] = read_jsonl(ALARM_LOG_PATH, limit=1000)
ingest_log: list[dict] = read_jsonl(INGEST_LOG_PATH, limit=500)
query_log: list[dict] = read_jsonl(QUERY_LOG_PATH, limit=500)
error_log: list[dict] = read_jsonl(ERROR_LOG_PATH, limit=500)


def get_engine(collection_name: str) -> AlarmRAGEngine:
    if collection_name not in engines:
        logger.info("Creating engine for collection: %s", collection_name)
        engines[collection_name] = AlarmRAGEngine(collection_name)
    return engines[collection_name]


def get_existing_engine(collection_name: str) -> AlarmRAGEngine | None:
    existing = engines.get(collection_name)
    if existing is not None:
        return existing
    index_path = os.path.join(DB_PATH, f"bm25_{collection_name}.pkl")
    if not os.path.isfile(index_path):
        return None
    return get_engine(collection_name)


def load_all_engines() -> None:
    db_path = DB_PATH
    if not os.path.exists(db_path):
        logger.warning("%s/ not found - run ingest.py first", db_path)
        return
    for fname in os.listdir(db_path):
        if fname.startswith("bm25_") and fname.endswith(".pkl"):
            get_engine(fname[5:-4])
    if not engines:
        logger.warning("No indexes found in %s/", db_path)
        logger.warning("Run: docker exec -it alarm_rag python ingest.py --pdf data/manual.pdf --name mymanual")


def sort_documents(documents: list[dict]) -> list[dict]:
    return sorted(
        documents,
        key=lambda doc: (doc.get("imported_at") or "", doc.get("filename") or ""),
        reverse=True,
    )


def get_collection_documents(collection_name: str) -> list[dict]:
    documents = get_documents(collection_name)
    if documents:
        return sort_documents(documents)

    engine = engines.get(collection_name)
    legacy = build_legacy_document_entry(collection_name, engine.sections if engine else [])
    return [legacy] if legacy else []


def get_collection_summary(collection_name: str) -> dict:
    engine = engines.get(collection_name)
    documents = get_collection_documents(collection_name)
    summary = {
        "name": collection_name,
        "ready": engine.ready if engine else False,
        "documents": len(documents),
        "sections": len(engine.sections) if engine else sum(doc.get("sections", 0) for doc in documents),
        "updated_at": next((doc.get("imported_at") for doc in documents if doc.get("imported_at")), None),
        "has_legacy_index": any(doc.get("legacy") for doc in documents),
    }
    if engine:
        summary["bm25_tokenizer_version"] = getattr(engine, "tokenizer_version", "legacy-whitespace-v0")
        try:
            summary.update(engine.vector_coverage())
        except Exception as exc:
            summary.update({
                "vector_points": 0,
                "bm25_sections": len(engine.sections),
                "vector_coverage_percent": 0,
                "vector_ready": False,
                "vector_error": str(exc),
            })
    else:
        summary["bm25_tokenizer_version"] = "none"
        summary.update({
            "vector_points": 0,
            "bm25_sections": summary["sections"],
            "vector_coverage_percent": 0 if summary["sections"] else 100,
            "vector_ready": False,
            "vector_error": "",
        })
    return summary


_DIAGNOSTIC_QUERY_MARKERS = (
    "檢查",
    "检查",
    "原因",
    "為什麼",
    "为什么",
    "無法",
    "无法",
    "不能",
    "怎麼",
    "怎么",
    "如何",
    "還需要",
    "还需要",
    "check",
    "why",
    "cannot",
    "can't",
    "won't",
    "troubleshoot",
    "root cause",
    "remedy",
)


def is_troubleshooting_query(query: str) -> bool:
    normalized = str(query or "").casefold()
    return any(marker in normalized for marker in _DIAGNOSTIC_QUERY_MARKERS)


def _doc_identity(doc: dict) -> tuple[str, str, str, str]:
    meta = doc.get("meta", {})
    return (
        str(meta.get("doc_id") or ""),
        str(meta.get("code") or ""),
        str(meta.get("page") or ""),
        str(doc.get("text") or ""),
    )


def _retrieve_for_question(user_query: str, engine: AlarmRAGEngine) -> list[dict]:
    docs = engine.retrieve(user_query, top_k=RAG_CHAT_TOP_K)
    if not is_troubleshooting_query(user_query) or len(docs) != 1 or RAG_CHAT_TOP_K <= 1:
        return docs

    exact_code = str(docs[0].get("meta", {}).get("code") or "").strip()
    if not exact_code or not re.search(rf"\b{re.escape(exact_code)}\b", user_query):
        return docs

    related_query = re.sub(
        rf"\b(?:alarm\s*)?{re.escape(exact_code)}\b",
        " ",
        user_query,
        flags=re.IGNORECASE,
    ).strip()
    if not related_query:
        return docs

    expanded_related_query = expand_query_with_domain_aliases(related_query)
    related_docs = engine.retrieve(expanded_related_query, top_k=max(RAG_CHAT_TOP_K * 4, 12))
    priority_weights = {
        "tool change": 6,
        "tool magazine": 5,
        "tool clamp": 4,
        "clamp confirmation": 3,
        "start disable": 2,
    }

    def related_priority(item: tuple[int, dict]) -> tuple[int, int]:
        rank, doc = item
        searchable = f"{doc.get('meta', {}).get('title') or ''} {doc.get('text') or ''}".casefold()
        score = sum(
            weight
            for phrase, weight in priority_weights.items()
            if phrase in expanded_related_query.casefold() and phrase in searchable
        )
        if "tool" in expanded_related_query.casefold() and "tool" in searchable:
            score += 2
        if doc.get("meta", {}).get("source") or doc.get("meta", {}).get("source_file"):
            score += 1
        return (-score, rank)

    related_docs = [doc for _, doc in sorted(enumerate(related_docs), key=related_priority)]
    merged: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for doc in [*docs, *related_docs]:
        identity = _doc_identity(doc)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(doc)
        if len(merged) >= RAG_CHAT_TOP_K:
            break
    return merged


def _retrieval_context(docs: list[dict]) -> str:
    if not docs:
        return "No relevant alarm sections found."
    sections = []
    for doc in docs:
        meta = doc.get("meta", {})
        source = meta.get("source") or meta.get("source_file") or meta.get("kind") or meta.get("type") or "manual"
        sections.append(
            f"[Page {meta.get('page', '')} | Alarm: {meta.get('code', '')} | Source: {source}]\n"
            f"{str(doc.get('text') or '')[:RAG_CONTEXT_CHARS_PER_DOC]}"
        )
    return "\n\n".join(sections)


def build_augmented_messages(messages: List[Message], engine: AlarmRAGEngine) -> tuple[list[dict], list[dict]]:
    user_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if not user_query:
        return [{"role": m.role, "content": m.content} for m in messages], []

    docs = _retrieve_for_question(user_query, engine)
    context = _retrieval_context(docs)

    if len([m for m in messages if m.role == "user"]) == 1:
        return [
            {
                "role": "system",
                "content": DIAGNOSTIC_SYSTEM_PROMPT if is_troubleshooting_query(user_query) else SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"Retrieved manual sections:\n---\n{context}\n---\n\nQuestion: {user_query}",
            },
        ], docs

    history = messages[:-1]
    if len(history) > 6:
        history = history[-6:]

    result = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    result.extend({"role": m.role, "content": m.content} for m in history)
    result.append({
        "role": "user",
        "content": f"Relevant manual sections for this question:\n---\n{context}\n---\n\n{user_query}",
    })
    return result, docs


def _matching_evidence(text: str, keywords: tuple[str, ...], limit: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    matches = []
    for sentence in sentences:
        clean = sentence.strip()
        if not clean or not any(keyword in clean.casefold() for keyword in keywords):
            continue
        matches.append(clean)
        if len(matches) >= limit:
            break
    return matches


def build_grounded_diagnostic_answer(user_query: str, docs: list[dict]) -> str:
    """Build a source-locked answer for exact-code troubleshooting questions."""
    if not is_troubleshooting_query(user_query) or not docs:
        return ""

    requested_codes = set(re.findall(r"\b\d{2,6}\b", str(user_query or "")))
    exact_doc = next(
        (
            doc
            for doc in docs
            if str(doc.get("meta", {}).get("code") or "").strip() in requested_codes
        ),
        None,
    )
    if exact_doc is None:
        return ""

    meta = exact_doc.get("meta", {})
    code = str(meta.get("code") or "").strip()
    title = str(meta.get("title") or "").strip()
    page = str(meta.get("page") or "").strip()
    exact_text = str(exact_doc.get("text") or "")
    status_evidence = _matching_evidence(
        exact_text,
        ("not ready", "start disable", "interface signals", "alarm display"),
        3,
    )
    recovery_evidence = _matching_evidence(
        exact_text,
        ("remove", "acknowledge", "clear alarm", "reset key", "restart"),
        3,
    )

    lines = [
        "**直接結論**",
        f"已找到 Alarm {code} {title}（P.{page}）。以下僅列出檢索資料實際包含的證據，不推測未提供的信號或原因。",
        "",
        "**手冊可確認的檢查點**",
    ]
    if status_evidence:
        lines.append(f"- 狀態：`{' '.join(status_evidence)}` [Alarm {code}, P.{page}]")
    if recovery_evidence:
        lines.append(f"- 排除與復歸：`{' '.join(recovery_evidence)}` [Alarm {code}, P.{page}]")

    related_lines = []
    for doc in docs:
        if doc is exact_doc:
            continue
        related_meta = doc.get("meta", {})
        source = str(related_meta.get("source") or related_meta.get("source_file") or "").strip()
        related_text = str(doc.get("text") or "")
        evidence = _matching_evidence(
            related_text,
            ("checks:", "root cause:", "repair action:", "recovery:", "check ", "tool change"),
            2,
        )
        if not evidence:
            continue
        related_code = str(related_meta.get("code") or "").strip()
        related_page = str(related_meta.get("page") or "").strip()
        citation = source or f"Alarm {related_code}, P.{related_page}"
        qualifier = "內部模擬資料，非官方手冊" if "mock" in f"{source} {related_text}".casefold() else "相關檢索資料"
        related_lines.append(f"- {qualifier}：`{' '.join(evidence)}` [{citation}]")
        if len(related_lines) >= 2:
            break

    if related_lines:
        lines.extend(["", "**換刀情境的補充證據**", *related_lines])

    scenario = "換刀與 Alarm " + code + " 之間的直接因果關係" if "換刀" in user_query or "换刀" in user_query else "額外故障情境"
    lines.extend(
        [
            "",
            "**尚缺證據**",
            f"- 目前引用資料沒有證明{scenario}。若上述檢查後仍無法啟動，請提供同時出現的其他警報碼與實際 PLC／互鎖狀態。",
        ]
    )
    return "\n".join(lines)


def retrieval_citations(collection: str, docs: list[dict]) -> list[dict]:
    citations = []
    for rank, doc in enumerate(docs, start=1):
        meta = doc.get("meta", {})
        text = str(doc.get("text") or "")
        identity = "\x1f".join([
            collection,
            str(meta.get("doc_id") or ""),
            str(meta.get("source") or meta.get("source_file") or ""),
            str(meta.get("code") or ""),
            str(meta.get("page") or ""),
            text,
        ])
        citations.append({
            "id": f"ragcite_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
            "rank": rank,
            "code": str(meta.get("code") or ""),
            "title": str(meta.get("title") or ""),
            "page": meta.get("page", ""),
            "source": str(meta.get("source") or ""),
            "source_file": str(meta.get("source_file") or ""),
            "doc_id": str(meta.get("doc_id") or ""),
            "kind": str(meta.get("kind") or meta.get("type") or ""),
            "excerpt": re.sub(r"\s+", " ", text).strip()[:300],
        })
    return citations


def build_rag_metadata(collection: str, query: str, docs: list[dict]) -> dict:
    citations = retrieval_citations(collection, docs)
    return {
        "collection": collection,
        "query": query,
        "citation_count": len(citations),
        "citations": citations,
    }


def new_answer_id() -> str:
    return f"chatcmpl_{uuid.uuid4().hex}"


def _rag_with_answer_id(rag: dict, answer_id: str) -> dict:
    return {**rag, "answer_id": answer_id}


def make_openai_response(
    content: str,
    *,
    rag: dict | None = None,
    response_id: str | None = None,
) -> dict:
    answer_id = response_id or new_answer_id()
    response = {
        "id": answer_id,
        "object": "chat.completion",
        "created": 0,
        "model": OLLAMA_MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    if rag is not None:
        response["rag"] = _rag_with_answer_id(rag, answer_id)
    return response


def make_sse_chunk(
    content: str,
    finish: bool = False,
    *,
    rag: dict | None = None,
    response_id: str | None = None,
) -> str:
    answer_id = response_id or new_answer_id()
    chunk = {
        "id": answer_id,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": OLLAMA_MODEL,
        "choices": [{
            "index": 0,
            "delta": {"content": content} if not finish else {},
            "finish_reason": "stop" if finish else None,
        }],
    }
    if rag is not None:
        chunk["rag"] = _rag_with_answer_id(rag, answer_id)
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def parse_alarm_code_int(code: str) -> Optional[int]:
    digits = re.sub(r"\D", "", str(code or ""))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def classify_alarm(code_int: Optional[int], collection: str) -> dict:
    manual = (collection or "").lower()
    if code_int is None:
        return {"type": "Other", "category": "Other", "severity": "info", "manual": manual}
    if code_int < 10000:
        return {"type": "NCK", "category": "System Core", "severity": "high", "manual": manual}
    if code_int < 20000:
        return {"type": "Channel", "category": "Channel", "severity": "medium", "manual": manual}
    if code_int < 30000:
        return {"type": "Axis/Spindle", "category": "Axis/Spindle", "severity": "high", "manual": manual}
    if code_int < 70000:
        return {"type": "Cycle", "category": "Cycle", "severity": "medium", "manual": manual}
    if 300000 <= code_int < 400000:
        return {"type": "Drive", "category": "SIMODRIVE/SINAMICS", "severity": "high", "manual": manual}
    if 400000 <= code_int < 500000:
        return {"type": "PLC", "category": "PLC", "severity": "medium", "manual": manual}
    return {"type": "Other", "category": "Other", "severity": "info", "manual": manual}


def load_json_entries(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return []
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [entry for entry in payload["entries"] if isinstance(entry, dict)]
    return []


def filter_entries(entries: list[dict], q: str, fields: list[str]) -> list[dict]:
    query = (q or "").strip().lower()
    if not query:
        return entries
    return [
        entry for entry in entries
        if query in " ".join(str(entry.get(field, "")) for field in fields).lower()
    ]


def build_rag_preview(docs: list[dict]) -> str:
    if not docs:
        return ""
    first = docs[0]
    meta = first.get("meta", {})
    page = str(meta.get("page") or "")
    code = str(meta.get("code") or "")
    text = re.sub(r"\s+", " ", str(first.get("text") or "")).strip()
    snippet = text[:180]
    if code and page:
        return f"Alarm {code} / P.{page}: {snippet}"
    if code:
        return f"Alarm {code}: {snippet}"
    return snippet


def log_query(collection: str, query: str, source: str = "web", elapsed_ms: int = 0) -> None:
    entry = {
        "time": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "collection": collection,
        "query": query[:200],
        "source": source,
        "elapsed_ms": elapsed_ms,
    }
    query_log.append(entry)
    if len(query_log) > 500:
        query_log.pop(0)
    append_jsonl(QUERY_LOG_PATH, entry)
