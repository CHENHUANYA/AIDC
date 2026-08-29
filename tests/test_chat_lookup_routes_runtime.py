import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app_context import ChatRequest, Message
from routes import chat_lookup_routes


ACTOR = {"user_id": "operator01", "role": "operator"}
REQUEST = ChatRequest(messages=[Message(role="user", content="general question")], stream=False)
DOC = {
    "text": "3000 Emergency stop circuit. Remove the cause and acknowledge the alarm.",
    "meta": {
        "code": "3000",
        "page": 12,
        "title": "Emergency stop",
        "source": "manual.pdf",
        "doc_id": "doc-1",
        "type": "alarm",
        "imported_at": "today",
    },
}


async def response_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.parametrize("streaming", [False, True])
def test_not_ready_chat_returns_and_persists_unavailable_answer(monkeypatch, streaming):
    request = ChatRequest(messages=REQUEST.messages, stream=streaming)
    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: None)
    with (
        patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer,
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_metric,
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "missing", ACTOR))
        if streaming:
            body = asyncio.run(response_body(response))
            assert "data: [DONE]" in body
        else:
            assert response["rag"]["citation_count"] == 0

    assert save_answer.call_args.kwargs["answer_state"] == "unavailable"
    assert save_answer.call_args.kwargs["tokenizer_version"] == "none"
    assert record_metric.call_args.kwargs["streaming"] is streaming


def test_grounded_diagnostic_stream_bypasses_provider(monkeypatch):
    class Engine:
        ready = True
        tokenizer_version = "v2"

        def retrieve(self, _query, top_k=4):
            del top_k
            return [DOC]

    request = ChatRequest(messages=[Message(role="user", content="Alarm 3000 cannot start")], stream=True)
    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: Engine())
    with (
        patch.object(chat_lookup_routes, "call_llm_with_retrieval_guard", new=AsyncMock(side_effect=AssertionError("provider called"))),
        patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer,
        patch.object(chat_lookup_routes, "record_query"),
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", ACTOR))
        body = asyncio.run(response_body(response))

    assert "Alarm 3000" in body
    assert "data: [DONE]" in body
    assert save_answer.call_args.kwargs["provider"] == "retrieval"
    assert save_answer.call_args.kwargs["answer_state"] == "complete"


def test_non_ollama_stream_uses_buffered_guard_and_school_model(monkeypatch):
    async def answer(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("school")
        return "school answer"

    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "school")
    monkeypatch.setattr(chat_lookup_routes, "call_llm_with_retrieval_guard", answer)
    with (
        patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer,
        patch.object(chat_lookup_routes, "record_query"),
    ):
        body = asyncio.run(
            response_body_iterator(
                chat_lookup_routes.stream_chat_events(
                    messages=[],
                    docs=[DOC],
                    rag_metadata={"citations": [{"code": "3000"}]},
                    response_id="chatcmpl_school",
                    collection_name="808d",
                    user_query="question",
                    temperature=0.1,
                    max_tokens=64,
                    created_by="operator01",
                    tokenizer_version="v2",
                    start_ts=0.0,
                    retrieval_ms=1.0,
                )
            )
        )

    assert "school answer" in body
    assert save_answer.call_args.kwargs["provider"] == "school"
    assert save_answer.call_args.kwargs["model"] == chat_lookup_routes.SCHOOL_API_MODEL


async def response_body_iterator(iterator) -> str:
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_empty_buffered_stream_becomes_unavailable_fallback(monkeypatch):
    monkeypatch.setattr(chat_lookup_routes, "LLM_PROVIDER", "school")
    monkeypatch.setattr(chat_lookup_routes, "call_llm_with_retrieval_guard", AsyncMock(return_value=""))
    with (
        patch.object(chat_lookup_routes, "save_rag_answer", return_value=True) as save_answer,
        patch.object(chat_lookup_routes, "record_chat_error") as record_error,
        patch.object(chat_lookup_routes, "record_query"),
    ):
        body = asyncio.run(
            response_body_iterator(
                chat_lookup_routes.stream_chat_events(
                    messages=[],
                    docs=[],
                    rag_metadata={"citations": []},
                    response_id="chatcmpl_empty",
                    collection_name="808d",
                    user_query="question",
                    temperature=0.1,
                    max_tokens=64,
                    created_by="operator01",
                    tokenizer_version="v2",
                    start_ts=0.0,
                    retrieval_ms=1.0,
                )
            )
        )

    assert "data: [DONE]" in body
    assert save_answer.call_args.kwargs["answer_state"] == "unavailable"
    record_error.assert_called_once()


def test_chat_route_wrappers_validate_and_forward(monkeypatch):
    handle = AsyncMock(return_value={"forwarded": True})
    monkeypatch.setattr(chat_lookup_routes, "handle_chat", handle)

    assert asyncio.run(chat_lookup_routes.chat_default(REQUEST, collection="808d", actor={})) == {
        "status": "error",
        "message": "Not authenticated",
    }
    assert asyncio.run(chat_lookup_routes.chat_default(REQUEST, collection="808d", actor=ACTOR)) == {
        "forwarded": True
    }
    assert asyncio.run(chat_lookup_routes.chat_collection(REQUEST, "../bad", actor=ACTOR))["status"] == "error"
    assert asyncio.run(chat_lookup_routes.chat_collection(REQUEST, "808d", actor=ACTOR)) == {"forwarded": True}
    assert asyncio.run(chat_lookup_routes.chat_multiturn(REQUEST, "808d", actor=ACTOR)) == {"forwarded": True}
    assert handle.await_count == 3


def test_free_chat_guards_and_builds_system_prompt(monkeypatch):
    call = AsyncMock(return_value="")
    monkeypatch.setattr(chat_lookup_routes, "call_llm", call)

    assert asyncio.run(chat_lookup_routes.free_chat(REQUEST, actor={}))["message"] == "Not authenticated"
    response = asyncio.run(chat_lookup_routes.free_chat(REQUEST, actor=ACTOR))

    assert response["choices"][0]["message"]["content"] == "LLM service unavailable."
    assert call.await_args.kwargs["messages"][0]["content"] == chat_lookup_routes.FREE_CHAT_SYSTEM


def test_retrieve_route_covers_blank_not_ready_and_ready(monkeypatch):
    class Engine:
        ready = True
        tokenizer_version = "v2"

        def retrieve(self, query, top_k=5):
            assert query == "alarm"
            assert top_k == 2
            return [DOC]

    assert asyncio.run(chat_lookup_routes.retrieve_collection("808d", query=" ", actor=ACTOR))["message"] == "Query is required"
    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: None)
    assert asyncio.run(chat_lookup_routes.retrieve_collection("808d", query="alarm", actor=ACTOR))["ready"] is False
    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: Engine())
    with patch.object(chat_lookup_routes, "record_query") as record_query:
        result = asyncio.run(chat_lookup_routes.retrieve_collection("808d", query=" alarm ", top_k=2, actor=ACTOR))

    assert result["ready"] is True
    assert result["result_count"] == 1
    assert result["results"][0]["text"].startswith("3000 Emergency")
    record_query.assert_called_once()


def test_lookup_route_covers_validation_success_missing_and_failure(monkeypatch):
    class Engine:
        ready = True

        def __init__(self, outcome):
            self.outcome = outcome

        def lookup_code(self, code):
            assert code == "3000"
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: Engine(None))
    assert asyncio.run(chat_lookup_routes.lookup_alarm("808d", "abc", actor=ACTOR))["error"] == "Invalid alarm code"
    assert "not found" in asyncio.run(chat_lookup_routes.lookup_alarm("808d", "3000", actor=ACTOR))["error"]

    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: Engine({"text": DOC["text"], "meta": DOC["meta"]}))
    found = asyncio.run(chat_lookup_routes.lookup_alarm("808d", "Alarm 3000", actor=ACTOR))
    assert found["found"] is True
    assert found["metadata"]["doc_id"] == "doc-1"
    assert found["alarm_type"]

    monkeypatch.setattr(chat_lookup_routes, "get_existing_engine", lambda _collection: Engine(RuntimeError("lookup failed")))
    with (
        patch.object(chat_lookup_routes, "record_chat_error"),
        patch.object(chat_lookup_routes, "record_query"),
    ):
        failed = asyncio.run(chat_lookup_routes.lookup_alarm("808d", "3000", actor=ACTOR))
    assert failed == {
        "found": False,
        "error": "Lookup service unavailable",
        "error_code": "LOOKUP_UNAVAILABLE",
    }


def test_answer_and_models_routes_cover_auth_and_validation(monkeypatch):
    assert asyncio.run(chat_lookup_routes.get_rag_answer("id", actor={}))["message"] == "Not authenticated"
    assert asyncio.run(chat_lookup_routes.models_collection("../bad"))["status"] == "error"
    assert asyncio.run(chat_lookup_routes.models_collection("808d"))["data"][0]["id"] == "alarm-rag-808d"
    assert asyncio.run(chat_lookup_routes.models_default())["data"][0]["id"] == "alarm-rag"
