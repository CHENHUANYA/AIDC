from __future__ import annotations

import time
import re
from dataclasses import dataclass
from typing import Any, Callable


RESERVED_CITATION_COMMENT = re.compile(r"<!--\s*(?:PAGE|CODE|TITLE)\s*:[\s\S]*?-->", re.IGNORECASE)


def strip_reserved_citation_comments(content: str) -> str:
    return RESERVED_CITATION_COMMENT.sub("", str(content or "")).lstrip("\r\n")


@dataclass(frozen=True)
class CompletionDependencies:
    call_with_retrieval_guard: Any
    answer_source_tags: Callable[[list[dict]], str]
    provider_source: Callable[[], str]
    unavailable_message: Callable[[Exception, list[dict]], str]
    record_error: Any
    classify_answer_state: Callable[[str], str]
    record_query: Any
    save_answer: Any
    record_metric: Any
    make_response: Any
    ollama_model: str
    school_model: str


async def complete_non_streaming_chat(
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
    request_started: float,
    start_ts: float,
    retrieval_ms: float,
    dependencies: CompletionDependencies,
) -> Any:
    model_started = time.monotonic()
    try:
        content = await dependencies.call_with_retrieval_guard(
            messages=messages,
            docs=docs,
            user_query=user_query,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not content:
            raise RuntimeError("LLM returned empty response")

        content = strip_reserved_citation_comments(content)
        provider = dependencies.provider_source()
    except Exception as exc:
        content = dependencies.unavailable_message(exc, docs)
        provider = "unavailable"
        dependencies.record_error(collection_name, user_query, docs, exc)

    elapsed_ms = int((time.time() - start_ts) * 1000)
    answer_state = dependencies.classify_answer_state(provider)
    dependencies.record_query(collection_name, user_query, source="api", elapsed_ms=elapsed_ms)
    model = (
        dependencies.school_model
        if provider == "school"
        else dependencies.ollama_model if provider == "ollama" else ""
    )
    await dependencies.save_answer(
        answer_id=response_id,
        query=user_query,
        collection=collection_name,
        answer=content,
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
        model_ms=(time.monotonic() - model_started) * 1000,
        total_ms=(time.monotonic() - request_started) * 1000,
        provider=provider,
        outcome=answer_state,
        streaming=False,
    )
    return dependencies.make_response(content, rag=rag_metadata, response_id=response_id)
