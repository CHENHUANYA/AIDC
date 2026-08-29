from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StreamDependencies:
    provider_name: str
    ollama_model: str
    school_model: str
    is_troubleshooting_query: Callable[[str], bool]
    stream_ollama: Any
    call_with_retrieval_guard: Any
    answer_source_tags: Callable[[list[dict]], str]
    make_sse_chunk: Any
    unavailable_message: Callable[[Exception, list[dict]], str]
    record_error: Any
    provider_source: Callable[[], str]
    classify_answer_state: Callable[[str], str]
    record_query: Any
    save_answer: Any
    record_metric: Any


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
    dependencies: StreamDependencies,
) -> AsyncIterator[str]:
    model_started = time.monotonic()
    parts: list[str] = []
    first_event = True
    provider = "unavailable"
    answer_state = "complete"
    tags = dependencies.answer_source_tags(docs)
    try:
        if dependencies.provider_name == "ollama" and not dependencies.is_troubleshooting_query(user_query):
            async for content_part in dependencies.stream_ollama(messages, temperature, max_tokens):
                if first_event and tags:
                    content_part = f"{tags}\n{content_part}"
                parts.append(content_part)
                yield dependencies.make_sse_chunk(
                    content_part,
                    rag=rag_metadata if first_event else None,
                    response_id=response_id,
                )
                first_event = False
            if not parts:
                raise RuntimeError("LLM returned empty streaming response")
            provider = dependencies.provider_source()
        else:
            content = await dependencies.call_with_retrieval_guard(
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
            provider = dependencies.provider_source()
            yield dependencies.make_sse_chunk(content, rag=rag_metadata, response_id=response_id)
            first_event = False
    except (asyncio.CancelledError, GeneratorExit):
        dependencies.record_metric(
            retrieval_ms=retrieval_ms,
            model_ms=max((time.monotonic() - model_started) * 1000, 0.0),
            total_ms=max((time.time() - start_ts) * 1000, 0.0),
            provider=dependencies.provider_source(),
            outcome="interrupted",
            streaming=True,
        )
        raise
    except Exception as exc:
        dependencies.record_error(collection_name, user_query, docs, exc)
        if parts and dependencies.provider_source() in {"ollama", "school"}:
            provider = dependencies.provider_source()
            answer_state = "fallback"
        else:
            answer_state = "unavailable"
        fallback = dependencies.unavailable_message(exc, docs)
        if parts:
            fallback = f"\n\n{fallback}"
        parts.append(fallback)
        yield dependencies.make_sse_chunk(
            fallback,
            rag=rag_metadata if first_event else None,
            response_id=response_id,
        )

    elapsed_ms = int((time.time() - start_ts) * 1000)
    answer = "".join(parts)
    if answer_state == "complete":
        answer_state = dependencies.classify_answer_state(provider)
    dependencies.record_query(collection_name, user_query, source="api-stream", elapsed_ms=elapsed_ms)
    model = (
        dependencies.school_model
        if provider == "school"
        else dependencies.ollama_model if provider == "ollama" else ""
    )
    await dependencies.save_answer(
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
    dependencies.record_metric(
        retrieval_ms=retrieval_ms,
        model_ms=max((time.monotonic() - model_started) * 1000, 0.0),
        total_ms=max((time.time() - start_ts) * 1000, 0.0),
        provider=provider,
        outcome=answer_state,
        streaming=True,
    )
    yield dependencies.make_sse_chunk("", finish=True, response_id=response_id)
    yield "data: [DONE]\n\n"
