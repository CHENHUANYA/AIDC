import asyncio
import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import datetime
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from api_schemas import (
    API_ERROR_RESPONSES,
    LookupResponse,
    ModelsResponse,
    OpenAIChatResponse,
    RagAnswerEnvelope,
    RetrieveResponse,
)
from app_context import (
    CHAT_SYSTEM_PROMPT,
    FREE_CHAT_SYSTEM,
    LLM_TIMEOUT_SECONDS,
    LLM_PROVIDER,
    NOT_READY_TEMPLATE,
    OLLAMA_MODEL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_URL,
    RAG_MAX_OUTPUT_TOKENS,
    RAG_OLLAMA_NUM_CTX,
    SCHOOL_API_BASE_URL,
    SCHOOL_API_FALLBACK_TO_OLLAMA,
    SCHOOL_API_KEY,
    SCHOOL_API_MODEL,
    ChatRequest,
    build_augmented_messages,
    build_grounded_diagnostic_answer,
    build_rag_metadata,
    build_rag_preview,
    classify_alarm,
    error_log,
    get_existing_engine,
    is_troubleshooting_query,
    is_safe_path_segment,
    log_query,
    make_openai_response,
    make_sse_chunk,
    new_answer_id,
    parse_alarm_code_int,
    retrieval_citations,
)
from auth import actor_id, get_actor
from observability import runtime_metrics
from repositories.rag_answers import RagAnswerRepository
from storage import ERROR_LOG_PATH, append_jsonl


logger = logging.getLogger("alarm_rag.chat")
router = APIRouter()
last_llm_source = "none"
request_llm_source: ContextVar[str] = ContextVar("request_llm_source", default="none")
rag_answers = RagAnswerRepository()
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
CHAT_RESPONSES = {
    **API_ERROR_RESPONSES,
    200: {
        "model": OpenAIChatResponse,
        "description": "OpenAI-compatible JSON response or an SSE stream when stream=true",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
}


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
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": temperature,
            "num_predict": min(max_tokens, RAG_MAX_OUTPUT_TOKENS),
            "num_ctx": RAG_OLLAMA_NUM_CTX,
        },
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("message", {}).get("content", "")


async def stream_ollama(messages: list[dict], temperature: float, max_tokens: int) -> AsyncIterator[str]:
    global last_llm_source
    request_llm_source.set("none")
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": temperature,
            "num_predict": min(max_tokens, RAG_MAX_OUTPUT_TOKENS),
            "num_ctx": RAG_OLLAMA_NUM_CTX,
        },
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
            response.raise_for_status()
            last_llm_source = "ollama"
            request_llm_source.set("ollama")
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Ollama returned invalid streaming JSON") from exc
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                content = str(event.get("message", {}).get("content") or "")
                if content:
                    yield content


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
        "max_tokens": min(max_tokens, RAG_MAX_OUTPUT_TOKENS),
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{SCHOOL_API_BASE_URL}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


async def call_llm(messages: list[dict], temperature: float, max_tokens: int) -> str:
    global last_llm_source
    request_llm_source.set("none")
    if LLM_PROVIDER in {"school", "openai", "openai-compatible"}:
        try:
            content = await call_school_api(messages, temperature, max_tokens)
            last_llm_source = "school"
            request_llm_source.set("school")
            return content
        except httpx.HTTPStatusError as exc:
            if not SCHOOL_API_FALLBACK_TO_OLLAMA or exc.response.status_code < 500:
                raise
            logger.warning("School API failed with %s; falling back to Ollama", exc.response.status_code)
        except httpx.HTTPError as exc:
            if not SCHOOL_API_FALLBACK_TO_OLLAMA:
                raise
            logger.warning("School API connection failed: %s; falling back to Ollama", exc)

    content = await call_ollama(messages, temperature, max_tokens)
    last_llm_source = "ollama"
    request_llm_source.set("ollama")
    return content


_NOT_FOUND_ANSWER_MARKERS = (
    "無法在手冊中找到警報代碼",
    "无法在手册中找到报警代码",
    "alarm code was not found",
    "alarm code not found",
    "not found in the manual",
)


def answer_conflicts_with_retrieval(answer: str, user_query: str, docs: list[dict]) -> bool:
    normalized_answer = re.sub(r"\s+", " ", str(answer or "")).casefold()
    if not any(marker.casefold() in normalized_answer for marker in _NOT_FOUND_ANSWER_MARKERS):
        return False
    requested_codes = set(re.findall(r"\b\d{2,6}\b", str(user_query or "")))
    retrieved_codes = {
        str(doc.get("meta", {}).get("code") or "").strip()
        for doc in docs
        if str(doc.get("meta", {}).get("code") or "").strip()
    }
    return bool(requested_codes & retrieved_codes)


def _grounding_repair_messages(messages: list[dict], user_query: str, docs: list[dict]) -> list[dict]:
    confirmed_codes = sorted(
        {
            str(doc.get("meta", {}).get("code") or "").strip()
            for doc in docs
            if str(doc.get("meta", {}).get("code") or "").strip()
        }
    )
    correction = (
        "\n\nCORRECTION: Retrieval metadata confirms these alarm codes are present: "
        f"{', '.join(confirmed_codes)}. Your previous response incorrectly claimed a requested code was missing. "
        "Answer the user's troubleshooting question from the retrieved sections. State any uncovered part of the scenario "
        "as a limitation, but do not use the not-found template for a confirmed code."
    )
    repaired = [dict(message) for message in messages]
    if repaired and repaired[0].get("role") == "system":
        repaired[0]["content"] = str(repaired[0].get("content") or "") + correction
    else:
        repaired.insert(0, {"role": "system", "content": correction.strip()})
    repaired.append(
        {
            "role": "user",
            "content": f"Re-answer the original question without contradicting the retrieved alarm metadata: {user_query}",
        }
    )
    return repaired


def _retrieval_conflict_fallback(user_query: str, docs: list[dict]) -> str:
    requested_codes = set(re.findall(r"\b\d{2,6}\b", str(user_query or "")))
    matches = []
    for doc in docs:
        meta = doc.get("meta", {})
        code = str(meta.get("code") or "").strip()
        if code not in requested_codes:
            continue
        title = str(meta.get("title") or "").strip()
        page = str(meta.get("page") or "").strip()
        matches.append(f"Alarm {code} {title} (P.{page})".strip())
    confirmed = "、".join(matches) or "已命中的手冊段落"
    return (
        f"已在檢索資料中找到 {confirmed}，但模型回覆與檢索結果衝突，因此已阻止錯誤的「找不到」結論。"
        "請先依下方引用檢查；目前資料不足以安全生成其餘故障步驟。"
    )


async def call_llm_with_retrieval_guard(
    messages: list[dict],
    docs: list[dict],
    user_query: str,
    temperature: float,
    max_tokens: int,
) -> str:
    generation_max_tokens = min(max_tokens, 256) if is_troubleshooting_query(user_query) else max_tokens
    content = await call_llm(messages, temperature, generation_max_tokens)
    if not answer_conflicts_with_retrieval(content, user_query, docs):
        return content

    logger.warning("LLM contradicted exact retrieval metadata; retrying grounded answer")
    repaired_messages = _grounding_repair_messages(messages, user_query, docs)
    repaired_content = await call_llm(repaired_messages, 0.0, generation_max_tokens)
    if answer_conflicts_with_retrieval(repaired_content, user_query, docs):
        logger.error("LLM repeated a retrieval contradiction; returning deterministic guard response")
        return _retrieval_conflict_fallback(user_query, docs)
    return repaired_content


def save_rag_answer(
    *,
    answer_id: str,
    query: str,
    collection: str,
    answer: str,
    rag_metadata: dict,
    provider: str,
    model: str,
    elapsed_ms: int,
    created_by: str,
    tokenizer_version: str,
    answer_state: str,
) -> None:
    try:
        rag_answers.add({
            "answer_id": answer_id,
            "query": query,
            "collection": collection,
            "answer": answer,
            "answer_state": answer_state,
            "citations": list(rag_metadata.get("citations") or []),
            "provider": provider,
            "model": model,
            "tokenizer_version": tokenizer_version,
            "retrieval_version": "bm25-vector-rrf-reranker-v1",
            "elapsed_ms": elapsed_ms,
            "created_by": created_by,
        })
    except Exception as exc:
        # Persistence must be observable but must not turn an otherwise valid answer into a 500.
        logger.warning("Failed to persist RAG answer %s: %s", answer_id, exc)


def classify_answer_state(provider: str) -> str:
    if provider == "unavailable":
        return "unavailable"
    if LLM_PROVIDER == "school" and provider == "ollama":
        return "fallback"
    return "complete"


def answer_source_tags(docs: list[dict]) -> str:
    if not docs:
        return ""
    meta = docs[0].get("meta", {})
    values = [
        ("PAGE", meta.get("page")),
        ("TITLE", meta.get("title")),
        ("CODE", meta.get("code")),
    ]
    return "".join(f"<!-- {name}:{value} -->" for name, value in values if value not in {None, ""})


def record_chat_error(collection_name: str, user_query: str, docs: list[dict], exc: Exception) -> None:
    entry = {
        "time": datetime.now().isoformat(),
        "collection": collection_name,
        "query": user_query,
        "error": error_detail(exc),
        "rag_preview": build_rag_preview(docs),
    }
    error_log.append(entry)
    append_jsonl(ERROR_LOG_PATH, entry)


async def stream_chat_events(
    *,
    messages: list[dict],
    docs: list[dict],
    rag_metadata: dict,
    response_id: str,
    collection_name: str,
    user_query: str,
    temperature: float,
    max_tokens: int,
    created_by: str,
    tokenizer_version: str,
    start_ts: float,
    retrieval_ms: float,
) -> AsyncIterator[str]:
    model_started = time.monotonic()
    parts: list[str] = []
    first_event = True
    provider = "unavailable"
    answer_state = "complete"
    tags = answer_source_tags(docs)
    try:
        if LLM_PROVIDER == "ollama" and not is_troubleshooting_query(user_query):
            async for content_part in stream_ollama(messages, temperature, max_tokens):
                if first_event and tags:
                    content_part = f"{tags}\n{content_part}"
                parts.append(content_part)
                yield make_sse_chunk(
                    content_part,
                    rag=rag_metadata if first_event else None,
                    response_id=response_id,
                )
                first_event = False
            if not parts:
                raise RuntimeError("LLM returned empty streaming response")
            provider = request_llm_source.get()
        else:
            content = await call_llm_with_retrieval_guard(
                messages,
                docs,
                user_query,
                temperature,
                max_tokens,
            )
            if not content:
                raise RuntimeError("LLM returned empty response")
            if tags:
                content = f"{tags}\n{content}"
            parts.append(content)
            provider = request_llm_source.get()
            yield make_sse_chunk(content, rag=rag_metadata, response_id=response_id)
            first_event = False
    except asyncio.CancelledError:
        runtime_metrics.record_rag(
            retrieval_ms=retrieval_ms,
            model_ms=max((time.monotonic() - model_started) * 1000, 0.0),
            total_ms=max((time.time() - start_ts) * 1000, 0.0),
            provider=request_llm_source.get(),
            outcome="interrupted",
            streaming=True,
        )
        raise
    except Exception as exc:
        record_chat_error(collection_name, user_query, docs, exc)
        if parts and request_llm_source.get() in {"ollama", "school"}:
            provider = request_llm_source.get()
            answer_state = "fallback"
        else:
            answer_state = "unavailable"
        fallback = build_llm_unavailable_message(exc, docs)
        if parts:
            fallback = f"\n\n{fallback}"
        parts.append(fallback)
        yield make_sse_chunk(
            fallback,
            rag=rag_metadata if first_event else None,
            response_id=response_id,
        )

    elapsed_ms = int((time.time() - start_ts) * 1000)
    answer = "".join(parts)
    if answer_state == "complete":
        answer_state = classify_answer_state(provider)
    log_query(collection_name, user_query, source="api-stream", elapsed_ms=elapsed_ms)
    model = SCHOOL_API_MODEL if provider == "school" else OLLAMA_MODEL if provider == "ollama" else ""
    save_rag_answer(
        answer_id=response_id,
        query=user_query,
        collection=collection_name,
        answer=answer,
        rag_metadata=rag_metadata,
        provider=provider,
        model=model,
        elapsed_ms=elapsed_ms,
        created_by=created_by,
        tokenizer_version=tokenizer_version,
        answer_state=answer_state,
    )
    total_ms = max((time.time() - start_ts) * 1000, 0.0)
    runtime_metrics.record_rag(
        retrieval_ms=retrieval_ms,
        model_ms=max((time.monotonic() - model_started) * 1000, 0.0),
        total_ms=total_ms,
        provider=provider,
        outcome=answer_state,
        streaming=True,
    )
    yield make_sse_chunk("", finish=True, response_id=response_id)
    yield "data: [DONE]\n\n"


async def handle_chat(req: ChatRequest, collection_name: str, actor: dict | None = None):
    request_started = time.monotonic()
    actor = actor or {}
    engine = get_existing_engine(collection_name)
    user_query = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    if engine is None or not engine.ready:
        msg = NOT_READY_TEMPLATE.format(name=collection_name)
        rag_metadata = build_rag_metadata(collection_name, user_query, [])
        response_id = new_answer_id()
        save_rag_answer(
            answer_id=response_id,
            query=user_query,
            collection=collection_name,
            answer=msg,
            rag_metadata=rag_metadata,
            provider="unavailable",
            model="",
            elapsed_ms=0,
            created_by=actor_id(actor),
            tokenizer_version=getattr(engine, "tokenizer_version", "none"),
            answer_state="unavailable",
        )
        runtime_metrics.record_rag(
            retrieval_ms=0,
            model_ms=0,
            total_ms=(time.monotonic() - request_started) * 1000,
            provider="unavailable",
            outcome="unavailable",
            streaming=req.stream,
        )
        if req.stream:
            async def s():
                yield make_sse_chunk(msg, rag=rag_metadata, response_id=response_id)
                yield make_sse_chunk("", finish=True, response_id=response_id)
                yield "data: [DONE]\n\n"
            return StreamingResponse(s(), media_type="text/event-stream", headers=SSE_HEADERS)
        return make_openai_response(msg, rag=rag_metadata, response_id=response_id)

    start_ts = time.time()
    retrieval_started = time.monotonic()
    augmented, docs = build_augmented_messages(req.messages, engine)
    retrieval_ms = (time.monotonic() - retrieval_started) * 1000
    rag_metadata = build_rag_metadata(collection_name, user_query, docs)
    response_id = new_answer_id()
    temperature = req.temperature if req.temperature is not None else 0.1
    max_tokens = req.max_tokens or 1024
    grounded_answer = build_grounded_diagnostic_answer(user_query, docs)
    if grounded_answer:
        tags = answer_source_tags(docs)
        content = f"{tags}\n{grounded_answer}" if tags else grounded_answer
        elapsed_ms = int((time.time() - start_ts) * 1000)
        log_query(collection_name, user_query, source="api-grounded", elapsed_ms=elapsed_ms)
        save_rag_answer(
            answer_id=response_id,
            query=user_query,
            collection=collection_name,
            answer=content,
            rag_metadata=rag_metadata,
            provider="retrieval",
            model="",
            elapsed_ms=elapsed_ms,
            created_by=actor_id(actor),
            tokenizer_version=getattr(engine, "tokenizer_version", "legacy-whitespace-v0"),
            answer_state="complete",
        )
        runtime_metrics.record_rag(
            retrieval_ms=retrieval_ms,
            model_ms=0,
            total_ms=(time.monotonic() - request_started) * 1000,
            provider="retrieval",
            outcome="complete",
            streaming=req.stream,
        )
        if req.stream:
            async def grounded_stream():
                yield make_sse_chunk(content, rag=rag_metadata, response_id=response_id)
                yield make_sse_chunk("", finish=True, response_id=response_id)
                yield "data: [DONE]\n\n"

            return StreamingResponse(grounded_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
        return make_openai_response(content, rag=rag_metadata, response_id=response_id)

    if req.stream:
        return StreamingResponse(
            stream_chat_events(
                messages=augmented,
                docs=docs,
                rag_metadata=rag_metadata,
                response_id=response_id,
                collection_name=collection_name,
                user_query=user_query,
                temperature=temperature,
                max_tokens=max_tokens,
                created_by=actor_id(actor),
                tokenizer_version=getattr(engine, "tokenizer_version", "legacy-whitespace-v0"),
                start_ts=start_ts,
                retrieval_ms=retrieval_ms,
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    model_started = time.monotonic()
    try:
        content = await call_llm_with_retrieval_guard(
            messages=augmented,
            docs=docs,
            user_query=user_query,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not content:
            raise RuntimeError("LLM returned empty response")

        tags = answer_source_tags(docs)
        if tags:
            content = tags + "\n" + content
        provider = request_llm_source.get()
    except Exception as exc:
        content = build_llm_unavailable_message(exc, docs)
        provider = "unavailable"
        record_chat_error(collection_name, user_query, docs, exc)

    elapsed_ms = int((time.time() - start_ts) * 1000)
    answer_state = classify_answer_state(provider)
    log_query(collection_name, user_query, source="api", elapsed_ms=elapsed_ms)

    model = SCHOOL_API_MODEL if provider == "school" else OLLAMA_MODEL if provider == "ollama" else ""
    save_rag_answer(
        answer_id=response_id,
        query=user_query,
        collection=collection_name,
        answer=content,
        rag_metadata=rag_metadata,
        provider=provider,
        model=model,
        elapsed_ms=elapsed_ms,
        created_by=actor_id(actor),
        tokenizer_version=getattr(engine, "tokenizer_version", "legacy-whitespace-v0"),
        answer_state=answer_state,
    )
    runtime_metrics.record_rag(
        retrieval_ms=retrieval_ms,
        model_ms=(time.monotonic() - model_started) * 1000,
        total_ms=(time.monotonic() - request_started) * 1000,
        provider=provider,
        outcome=answer_state,
        streaming=False,
    )

    return make_openai_response(content, rag=rag_metadata, response_id=response_id)


@router.post("/v1/chat/completions", responses=CHAT_RESPONSES)
async def chat_default(req: ChatRequest, collection: str = "alarms", actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    return await handle_chat(req, collection, actor)


@router.post(
    "/v1/free/chat/completions",
    responses={200: {"model": OpenAIChatResponse}, **API_ERROR_RESPONSES},
)
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


@router.post("/v1/{collection_name}/chat/completions", responses=CHAT_RESPONSES)
async def chat_collection(req: ChatRequest, collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    return await handle_chat(req, collection_name, actor)


@router.get(
    "/v1/{collection_name}/retrieve",
    responses={200: {"model": RetrieveResponse}, **API_ERROR_RESPONSES},
)
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
    engine = get_existing_engine(collection_name)
    if engine is None or not engine.ready:
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


@router.post("/v1/{collection_name}/chat", responses=CHAT_RESPONSES)
async def chat_multiturn(req: ChatRequest, collection_name: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    return await handle_chat(req, collection_name, actor)


@router.get(
    "/rag/answers/{answer_id}",
    responses={200: {"model": RagAnswerEnvelope}, **API_ERROR_RESPONSES},
)
async def get_rag_answer(answer_id: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    answer = rag_answers.get(answer_id)
    if answer is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "RAG answer not found"})
    if answer.get("created_by") and answer.get("created_by") != actor_id(actor) and actor.get("role") not in {"supervisor", "admin"}:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Permission denied"})
    return {"status": "ok", "answer": answer}


@router.get(
    "/v1/{collection_name}/lookup",
    responses={200: {"model": LookupResponse}, **API_ERROR_RESPONSES},
)
async def lookup_alarm(collection_name: str, code: str, actor: dict = Depends(get_actor)):
    denied = require_authenticated(actor)
    if denied:
        return denied
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    engine = get_existing_engine(collection_name)
    if engine is None or not engine.ready:
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


@router.get(
    "/v1/{collection_name}/models",
    responses={200: {"model": ModelsResponse}, **API_ERROR_RESPONSES},
)
async def models_collection(collection_name: str):
    invalid = validate_collection_name(collection_name)
    if invalid:
        return invalid
    return {
        "object": "list",
        "data": [{"id": f"alarm-rag-{collection_name}", "object": "model", "owned_by": "local"}],
    }


@router.get("/v1/models", responses={200: {"model": ModelsResponse}, **API_ERROR_RESPONSES})
async def models_default():
    return {"object": "list", "data": [{"id": "alarm-rag", "object": "model", "owned_by": "local"}]}
