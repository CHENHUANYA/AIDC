import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app_context import ChatRequest, Message
from routes import chat_lookup_routes


ACTOR = {"user_id": "operator01", "role": "operator"}
DOC = {
    "text": "Emergency stop circuit",
    "meta": {"code": "3000", "page": 12, "title": "Emergency stop"},
}


class EmptyEngine:
    ready = True
    tokenizer_version = "unicode-domain-v2"

    def retrieve(self, _query, top_k=4):
        del top_k
        return []


def stream_kwargs() -> dict:
    return {
        "messages": [{"role": "user", "content": "question"}],
        "docs": [DOC],
        "rag_metadata": {"collection": "808d", "citation_count": 1, "citations": [{"code": "3000"}]},
        "response_id": "chatcmpl_stream",
        "collection_name": "808d",
        "user_query": "question",
        "temperature": 0.1,
        "max_tokens": 64,
        "created_by": "operator01",
        "tokenizer_version": "unicode-domain-v2",
        "start_ts": time.time(),
        "retrieval_ms": 1.0,
    }


async def collect_stream(iterator) -> str:
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def test_non_stream_timeout_returns_unavailable_snapshot_even_when_error_log_fails(monkeypatch):
    request = ChatRequest(messages=[Message(role="user", content="general question")], stream=False)
    saved_payloads = []
    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: EmptyEngine())
    monkeypatch.setattr(
        chat_lookup_routes,
        "call_llm_with_retrieval_guard",
        AsyncMock(side_effect=httpx.ReadTimeout("provider timed out")),
    )
    monkeypatch.setattr(chat_lookup_routes, "append_jsonl", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(chat_lookup_routes, "log_query", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_lookup_routes.rag_answers, "add", lambda payload: saved_payloads.append(payload) or True)

    response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", ACTOR))

    assert response["choices"][0]["message"]["content"]
    assert response["rag"]["citations"] == []
    assert len(saved_payloads) == 1
    assert saved_payloads[0]["answer_state"] == "unavailable"
    assert saved_payloads[0]["provider"] == "unavailable"
    assert saved_payloads[0]["citations"] == []


def test_non_stream_answer_survives_query_and_snapshot_persistence_failures(monkeypatch):
    request = ChatRequest(messages=[Message(role="user", content="general question")], stream=False)

    async def answer(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        return "usable answer"

    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: EmptyEngine())
    monkeypatch.setattr(chat_lookup_routes, "call_llm_with_retrieval_guard", answer)
    monkeypatch.setattr(chat_lookup_routes, "log_query", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("log failed")))
    monkeypatch.setattr(chat_lookup_routes.rag_answers, "add", lambda _payload: (_ for _ in ()).throw(OSError("snapshot failed")))

    response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", ACTOR))

    assert response["choices"][0]["message"]["content"] == "usable answer"
    assert response["rag"]["citation_count"] == 0


def test_save_rag_answer_reports_duplicate_and_exception_as_failure():
    arguments = {
        "answer_id": "chatcmpl_failure",
        "query": "q",
        "collection": "808d",
        "answer": "a",
        "rag_metadata": {"citations": []},
        "provider": "ollama",
        "model": "model",
        "elapsed_ms": 1,
        "created_by": "operator01",
        "tokenizer_version": "v1",
        "answer_state": "complete",
    }
    with patch.object(chat_lookup_routes.rag_answers, "add", return_value=False):
        assert chat_lookup_routes.save_rag_answer(**arguments) is False
    with patch.object(chat_lookup_routes.rag_answers, "add", side_effect=OSError("disk full")):
        assert chat_lookup_routes.save_rag_answer(**arguments) is False


def test_client_closes_stream_without_persisting_partial_snapshot(monkeypatch):
    async def source(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        yield "partial"
        await asyncio.Event().wait()

    async def scenario():
        iterator = chat_lookup_routes.stream_chat_events(**stream_kwargs())
        first = await anext(iterator)
        await iterator.aclose()
        return first

    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_lookup_routes, "is_troubleshooting_query", lambda _query: False)
    monkeypatch.setattr(chat_lookup_routes, "stream_ollama", source)
    with (
        patch.object(chat_lookup_routes, "save_rag_answer") as save_answer,
        patch.object(chat_lookup_routes, "record_query") as record_query,
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_metric,
    ):
        first = asyncio.run(scenario())

    assert "partial" in first
    save_answer.assert_not_called()
    record_query.assert_not_called()
    assert record_metric.call_args.kwargs["outcome"] == "interrupted"


def test_cancelled_provider_stream_does_not_persist_snapshot(monkeypatch):
    async def cancelled_source(*_args, **_kwargs):
        raise asyncio.CancelledError()
        yield ""  # pragma: no cover - keeps this function an async generator

    async def scenario():
        iterator = chat_lookup_routes.stream_chat_events(**stream_kwargs())
        with pytest.raises(asyncio.CancelledError):
            await anext(iterator)

    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_lookup_routes, "is_troubleshooting_query", lambda _query: False)
    monkeypatch.setattr(chat_lookup_routes, "stream_ollama", cancelled_source)
    with (
        patch.object(chat_lookup_routes, "save_rag_answer") as save_answer,
        patch.object(chat_lookup_routes, "record_query") as record_query,
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_metric,
    ):
        asyncio.run(scenario())

    save_answer.assert_not_called()
    record_query.assert_not_called()
    assert record_metric.call_args.kwargs["outcome"] == "interrupted"


def test_partial_provider_failure_finishes_with_fallback_snapshot(monkeypatch):
    async def interrupted_source(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        yield "partial"
        raise httpx.ReadTimeout("stream timed out")

    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_lookup_routes, "is_troubleshooting_query", lambda _query: False)
    monkeypatch.setattr(chat_lookup_routes, "stream_ollama", interrupted_source)
    monkeypatch.setattr(chat_lookup_routes, "append_jsonl", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with (
        patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer,
        patch.object(chat_lookup_routes, "record_query"),
    ):
        body = asyncio.run(collect_stream(chat_lookup_routes.stream_chat_events(**stream_kwargs())))

    assert "partial" in body
    assert "data: [DONE]" in body
    events = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]
    assert len({event["id"] for event in events}) == 1
    saved = save_answer.call_args.kwargs
    assert saved["answer_state"] == "fallback"
    assert saved["provider"] == "ollama"
    assert "partial" in saved["answer"]


def test_stream_completion_survives_query_telemetry_failure(monkeypatch):
    async def source(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        yield "complete answer"

    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(chat_lookup_routes, "is_troubleshooting_query", lambda _query: False)
    monkeypatch.setattr(chat_lookup_routes, "stream_ollama", source)
    monkeypatch.setattr(chat_lookup_routes, "log_query", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("log failed")))
    with patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer:
        body = asyncio.run(collect_stream(chat_lookup_routes.stream_chat_events(**stream_kwargs())))

    assert "complete answer" in body
    assert "data: [DONE]" in body
    assert save_answer.call_args.kwargs["answer_state"] == "complete"


def test_ollama_adapter_sends_bounded_payload(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "ollama answer"}}

    class Client:
        def __init__(self, *, timeout):
            assert timeout == chat_lookup_routes.LLM_TIMEOUT_SECONDS

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json):
            calls.append((url, json))
            return Response()

    monkeypatch.setattr(chat_lookup_routes.httpx, "AsyncClient", Client)
    monkeypatch.setattr(chat_lookup_routes, "RAG_MAX_OUTPUT_TOKENS", 128)

    answer = asyncio.run(chat_lookup_routes.call_ollama([{"role": "user", "content": "q"}], 0.2, 999))

    assert answer == "ollama answer"
    assert calls[0][0].endswith("/api/chat")
    assert calls[0][1]["options"]["num_predict"] == 128
    assert calls[0][1]["stream"] is False


@pytest.mark.parametrize(
    ("lines", "error"),
    [
        (["not-json"], "invalid streaming JSON"),
        ([json.dumps({"error": "model failed"})], "model failed"),
    ],
)
def test_ollama_stream_adapter_rejects_invalid_events(monkeypatch, lines, error):
    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        def __init__(self, *, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, *, json):
            assert method == "POST"
            assert url.endswith("/api/chat")
            assert json["stream"] is True
            return StreamResponse()

    async def scenario():
        with pytest.raises(RuntimeError, match=error):
            async for _part in chat_lookup_routes.stream_ollama([], 0.1, 64):
                pass

    monkeypatch.setattr(chat_lookup_routes.httpx, "AsyncClient", Client)
    asyncio.run(scenario())


def test_ollama_stream_adapter_skips_blank_lines_and_yields_content(monkeypatch):
    lines = ["", json.dumps({"message": {"content": ""}}), json.dumps({"message": {"content": "part"}})]

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Client:
        def __init__(self, *, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return StreamResponse()

    monkeypatch.setattr(chat_lookup_routes.httpx, "AsyncClient", Client)

    async def scenario():
        body = await collect_stream(chat_lookup_routes.stream_ollama([], 0.1, 64))
        return body, chat_lookup_routes.request_llm_source.get()

    assert asyncio.run(scenario()) == ("part", "ollama")


def test_school_adapter_requires_url_and_sends_auth_header(monkeypatch):
    monkeypatch.setattr(chat_lookup_routes, "SCHOOL_API_BASE_URL", "")
    with pytest.raises(RuntimeError, match="SCHOOL_API_BASE_URL"):
        asyncio.run(chat_lookup_routes.call_school_api([], 0.1, 64))

    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "school answer"}}]}

    class Client:
        def __init__(self, *, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            calls.append((url, headers, json))
            return Response()

    monkeypatch.setattr(chat_lookup_routes, "SCHOOL_API_BASE_URL", "https://school.example")
    monkeypatch.setattr(chat_lookup_routes, "SCHOOL_API_KEY", "secret")
    monkeypatch.setattr(chat_lookup_routes.httpx, "AsyncClient", Client)

    answer = asyncio.run(chat_lookup_routes.call_school_api([{"role": "user", "content": "q"}], 0.3, 64))

    assert answer == "school answer"
    assert calls[0][0] == "https://school.example/chat/completions"
    assert calls[0][1]["Authorization"] == "Bearer secret"
