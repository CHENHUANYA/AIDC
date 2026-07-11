import re
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app_context import (
    CHAT_SYSTEM_PROMPT,
    FREE_CHAT_SYSTEM,
    LLM_TIMEOUT_SECONDS,
    LLM_PROVIDER,
    NOT_READY_TEMPLATE,
    OLLAMA_MODEL,
    OLLAMA_URL,
    SCHOOL_API_BASE_URL,
    SCHOOL_API_FALLBACK_TO_OLLAMA,
    SCHOOL_API_KEY,
    SCHOOL_API_MODEL,
    ChatRequest,
    build_augmented_messages,
    build_rag_metadata,
    build_rag_preview,
    classify_alarm,
    error_log,
    get_engine,
    is_safe_path_segment,
    log_query,
    make_openai_response,
    make_sse_chunk,
    parse_alarm_code_int,
    retrieval_citations,
)
from auth import actor_id, get_actor
from storage import ERROR_LOG_PATH, append_jsonl


router = APIRouter()
last_llm_source = "none"


def require_authenticated(actor: dict) -> dict | None:
    if not actor_id(actor):
        return {"status": "error", "message": "Not authenticated"}
    return None


def validate_collection_name(collection_name: str) -> dict | None:
    if not is_safe_path_segment(collection_name):
        return {"status": "error", "message": "Invalid collection name"}
    return None


def error_detail(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def build_llm_unavailable_message(exc: Exception, docs: list[dict]) -> str:
    detail = error_detail(exc)
    if not docs:
        return (
            "系統目前無法連線至 LLM 服務，暫時不能產生對話回答。\n\n"
            f"狀態：{detail}\n\n"
            "請確認 alarm_rag 後端設定的 LLM 服務已啟動，例如 Ollama 或 SCHOOL_API_BASE_URL。"
        )

    first = docs[0]
    meta = first.get("meta", {})
    page = str(meta.get("page") or "-")
    code = str(meta.get("code") or "-")
    text = re.sub(r"\s+", " ", str(first.get("text") or "")).strip()
    excerpt = text[:1200]
    return (
        "系統目前無法連線至 LLM 服務，因此先顯示 RAG 找到的手冊內容。\n\n"
        f"來源：Alarm {code} / P.{page}\n\n"
        f"{excerpt}\n\n"
        f"狀態：{detail}"
    )


async def call_ollama(messages: list[dict], temperature: float, max_tokens: int) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        data = response.json()
    return data.get("message", {}).get("content", "")


async def call_school_api(messages: list[dict], temperature: float, max_tokens: int) -> str:
    if not SCHOOL_API_BASE_URL:
        raise RuntimeError("SCHOOL_API_BASE_URL is required when LLM_PROVIDER=school")
    headers = {"Content-Type": "application/json"}
    if SCHOOL_API_KEY:
        headers["Authorization"] = f"Bearer {SCHOOL_API_KEY}"
    payload = {
        "model": SCHOOL_API_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{SCHOOL_API_BASE_URL}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


async def call_llm(messages: list[dict], temperature: float, max_tokens: int) -> str:
    global last_llm_source
    if LLM_PROVIDER in {"school", "openai", "openai-compatible"}:
        try:
            content = await call_school_api(messages, temperature, max_tokens)
            last_llm_source = "school"
            return content
        except httpx.HTTPStatusError as exc:
            if not SCHOOL_API_FALLBACK_TO_OLLAMA or exc.response.status_code < 500:
                raise
            print(f"[LLM] school API failed with {exc.response.status_code}; falling back to Ollama")
        except httpx.HTTPError as exc:
            if not SCHOOL_API_FALLBACK_TO_OLLAMA:
                raise
            print(f"[LLM] school API connection failed: {exc}; falling back to Ollama")

    content = await call_ollama(messages, temperature, max_tokens)
    last_llm_source = "ollama"
    return content


async def handle_chat(req: ChatRequest, collection_name: str):
    engine = get_engine(collection_name)

    if not engine.ready:
        msg = NOT_READY_TEMPLATE.format(name=collection_name)
        if req.stream:
            async def s():
                yield make_sse_chunk(msg)
                yield make_sse_chunk("", finish=True)
                yield "data: [DONE]\n\n"
            return StreamingResponse(s(), media_type="text/event-stream")
        return make_openai_response(msg)

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    start_ts = time.time()
    augmented, docs = build_augmented_messages(req.messages, engine)
    try:
        content = await call_llm(
            messages=augmented,
            temperature=req.temperature or 0.1,
            max_tokens=req.max_tokens or 1024,
        )
        if not content:
            raise RuntimeError("LLM returned empty response")

        tags = ""
        if docs:
            meta = docs[0]["meta"]
            page = str(meta.get("page", ""))
            title = str(meta.get("title", ""))
            code = str(meta.get("code", ""))
            if page:
                tags += f"<!-- PAGE:{page} -->"
            if title:
                tags += f"<!-- TITLE:{title} -->"
            if code:
                tags += f"<!-- CODE:{code} -->"
        if tags:
            content = tags + "\n" + content
    except Exception as exc:
        content = build_llm_unavailable_message(exc, docs)
        error_log.append({
            "time": datetime.now().isoformat(),
            "collection": collection_name,
            "query": user_query,
            "error": error_detail(exc),
            "rag_preview": build_rag_preview(docs),
        })
        append_jsonl(ERROR_LOG_PATH, error_log[-1])

    elapsed_ms = int((time.time() - start_ts) * 1000)
    log_query(collection_name, user_query, source="api", elapsed_ms=elapsed_ms)

    if req.stream:
        async def full_as_stream():
            yield make_sse_chunk(content)
            yield make_sse_chunk("", finish=True)
            yield "data: [DONE]\n\n"
        return StreamingResponse(full_as_stream(), media_type="text/event-stream")

    return make_openai_response(content, rag=build_rag_metadata(collection_name, user_query, docs))


@router.post("/v1/chat/completions")
async def chat_default(req: ChatRequest, collection: str = "alarms", actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    return await handle_chat(req, collection)


@router.post("/v1/free/chat/completions")
async def free_chat(req: ChatRequest, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    messages = [{"role": "system", "content": FREE_CHAT_SYSTEM}]
    messages.extend({"role": m.role, "content": m.content} for m in req.messages)
    content = await call_llm(
        messages=messages,
        temperature=req.temperature or 0.7,
        max_tokens=req.max_tokens or 1024,
    )
    return make_openai_response(content or "Error: empty response from LLM.")


@router.post("/v1/{collection_name}/chat/completions")
async def chat_collection(req: ChatRequest, collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    return await handle_chat(req, collection_name)


@router.get("/v1/{collection_name}/retrieve")
async def retrieve_collection(
    collection_name: str,
    query: str = Query(min_length=1, max_length=1000),
    top_k: int = Query(default=5, ge=1, le=20),
    actor: dict = Depends(get_actor),
):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    clean_query = query.strip()
    if not clean_query:
        return {"status": "error", "message": "Query is required"}
    engine = get_engine(collection_name)
    if not engine.ready:
        return {
            "collection": collection_name,
            "query": clean_query,
            "ready": False,
            "tokenizer_version": getattr(engine, "tokenizer_version", "none"),
            "results": [],
            "error": NOT_READY_TEMPLATE.format(name=collection_name),
        }

    start_ts = time.time()
    docs = engine.retrieve(clean_query, top_k=top_k)
    citations = retrieval_citations(collection_name, docs)
    results = [
        {**citation, "text": str(doc.get("text") or "")}
        for citation, doc in zip(citations, docs, strict=True)
    ]
    log_query(collection_name, clean_query, source="retrieve", elapsed_ms=int((time.time() - start_ts) * 1000))
    return {
        "collection": collection_name,
        "query": clean_query,
        "ready": True,
        "tokenizer_version": getattr(engine, "tokenizer_version", "legacy-whitespace-v0"),
        "result_count": len(results),
        "results": results,
    }


@router.post("/v1/{collection_name}/chat")
async def chat_multiturn(req: ChatRequest, collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    engine = get_engine(collection_name)
    if not engine.ready:
        return make_openai_response(NOT_READY_TEMPLATE.format(name=collection_name))

    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not user_query:
        return make_openai_response("請輸入問題")

    docs = engine.retrieve(user_query, top_k=4)
    context = "\n\n".join([
        f"[頁 {d['meta']['page']} | 警報 {d['meta']['code']}\n{d['text']}"
        for d in docs
    ]) if docs else "No relevant sections found."

    history = [m for m in req.messages[:-1]]
    if len(history) > 6:
        history = history[-6:]

    messages_to_send = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages_to_send.extend({"role": m.role, "content": m.content} for m in history)
    messages_to_send.append({
        "role": "user",
        "content": f"相關手冊段落如下：\n---\n{context}\n---\n\n{user_query}",
    })

    try:
        content = await call_llm(
            messages=messages_to_send,
            temperature=req.temperature if req.temperature is not None else 0.7,
            max_tokens=req.max_tokens or 1024,
        )
        if not content:
            raise RuntimeError("LLM returned empty response")
    except Exception as exc:
        content = build_llm_unavailable_message(exc, docs)
        error_log.append({
            "time": datetime.now().isoformat(),
            "collection": collection_name,
            "query": user_query,
            "error": error_detail(exc),
            "rag_preview": build_rag_preview(docs),
        })
        append_jsonl(ERROR_LOG_PATH, error_log[-1])

    return make_openai_response(content, rag=build_rag_metadata(collection_name, user_query, docs))


@router.get("/v1/{collection_name}/lookup")
async def lookup_alarm(collection_name: str, code: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    engine = get_engine(collection_name)
    if not engine.ready:
        return {"found": False, "error": NOT_READY_TEMPLATE.format(name=collection_name)}

    start_ts = time.time()
    code_clean = re.sub(r"\D", "", code)
    if not code_clean:
        log_query(collection_name, code, source="lookup", elapsed_ms=int((time.time() - start_ts) * 1000))
        return {"found": False, "error": "Invalid alarm code"}

    try:
        result = engine.lookup_code(code_clean)
        if result:
            doc = result["text"]
            meta = result["meta"]
            alarm_code = str(meta.get("code", code_clean))
            alarm_info = classify_alarm(parse_alarm_code_int(alarm_code), collection_name)
            log_query(collection_name, code_clean, source="lookup", elapsed_ms=int((time.time() - start_ts) * 1000))
            return {
                "found": True,
                "code": alarm_code,
                "page": meta.get("page", ""),
                "title": meta.get("title", ""),
                "text": doc,
                "metadata": {
                    "collection": collection_name,
                    "code": alarm_code,
                    "page": meta.get("page", ""),
                    "title": meta.get("title", ""),
                    "source": meta.get("source", ""),
                    "source_file": meta.get("source_file", ""),
                    "doc_id": meta.get("doc_id", ""),
                    "kind": meta.get("kind") or meta.get("type", ""),
                    "imported_at": meta.get("imported_at", ""),
                },
                "alarm_type": alarm_info["type"],
                "category": alarm_info["category"],
                "severity": alarm_info["severity"],
            }
        log_query(collection_name, code_clean, source="lookup", elapsed_ms=int((time.time() - start_ts) * 1000))
        return {"found": False, "error": f"Alarm {code_clean} not found in {collection_name}"}
    except Exception as exc:
        error_log.append({
            "time": datetime.now().isoformat(),
            "collection": collection_name,
            "query": code_clean,
            "error": error_detail(exc),
        })
        append_jsonl(ERROR_LOG_PATH, error_log[-1])
        log_query(collection_name, code_clean, source="lookup", elapsed_ms=int((time.time() - start_ts) * 1000))
        return {"found": False, "error": str(exc)}


@router.get("/v1/{collection_name}/models")
async def models_collection(collection_name: str):
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    return {
        "object": "list",
        "data": [{"id": f"alarm-rag-{collection_name}", "object": "model", "owned_by": "local"}],
    }


@router.get("/v1/models")
async def models_default():
    return {"object": "list", "data": [{"id": "alarm-rag", "object": "model", "owned_by": "local"}]}
