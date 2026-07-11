import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional


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


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-nemo:latest")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
SCHOOL_API_BASE_URL = os.getenv("SCHOOL_API_BASE_URL", "").rstrip("/")
SCHOOL_API_KEY = secret_value("SCHOOL_API_KEY")
SCHOOL_API_MODEL = os.getenv("SCHOOL_API_MODEL", "gpt-oss-120b")
SCHOOL_API_FALLBACK_TO_OLLAMA = os.getenv("SCHOOL_API_FALLBACK_TO_OLLAMA", "true").strip().lower() == "true"
LLM_TIMEOUT_SECONDS = env_float("RAG_LLM_TIMEOUT_SECONDS", 20)
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
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = OLLAMA_MODEL
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.1
    max_tokens: Optional[int] = 1024


class AlarmTrigger(BaseModel):
    alarm_code: str
    manual: Optional[str] = "808d"
    machine_id: Optional[str] = None
    source: Optional[str] = "API"
    external_event_id: Optional[str] = Field(default=None, max_length=255)
    severity: Optional[str] = None
    description: Optional[str] = None


class FeedbackRequest(BaseModel):
    query: str
    collection: str
    feedback: str
    alarm_code: Optional[str] = None
    answer_id: Optional[str] = None
    issue_id: Optional[str] = None
    work_order_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    correctness: Optional[str] = None
    coverage: Optional[str] = None
    missing_info: Optional[str] = None
    expected_fix: Optional[str] = None
    kb_candidate: Optional[bool] = None


class IngestTextRequest(BaseModel):
    text: str
    code: Optional[str] = ""
    title: Optional[str] = ""
    page: Optional[int] = 0
    source: Optional[str] = "api"


engines: Dict[str, AlarmRAGEngine] = {}
pending_alarms: list[dict] = []
alarm_history: list[dict] = read_jsonl(ALARM_LOG_PATH, limit=1000)
ingest_log: list[dict] = read_jsonl(INGEST_LOG_PATH, limit=500)
query_log: list[dict] = read_jsonl(QUERY_LOG_PATH, limit=500)
error_log: list[dict] = read_jsonl(ERROR_LOG_PATH, limit=500)


def get_engine(collection_name: str) -> AlarmRAGEngine:
    if collection_name not in engines:
        print(f"Creating engine for collection: {collection_name}")
        engines[collection_name] = AlarmRAGEngine(collection_name)
    return engines[collection_name]


def load_all_engines() -> None:
    db_path = DB_PATH
    if not os.path.exists(db_path):
        print(f"{db_path}/ not found - run ingest.py first")
        return
    for fname in os.listdir(db_path):
        if fname.startswith("bm25_") and fname.endswith(".pkl"):
            get_engine(fname[5:-4])
    if not engines:
        print(f"No indexes found in {db_path}/")
        print("  Run: docker exec -it alarm_rag python ingest.py --pdf data/manual.pdf --name mymanual")


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


def build_augmented_messages(messages: List[Message], engine: AlarmRAGEngine) -> tuple[list[dict], list[dict]]:
    user_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if not user_query:
        return [{"role": m.role, "content": m.content} for m in messages], []

    docs = engine.retrieve(user_query, top_k=4)
    context = "\n\n".join([
        f"[Page {d['meta']['page']} | Alarm: {d['meta']['code']}]\n{d['text']}"
        for d in docs
    ]) if docs else "No relevant alarm sections found."

    if len([m for m in messages if m.role == "user"]) == 1:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
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
