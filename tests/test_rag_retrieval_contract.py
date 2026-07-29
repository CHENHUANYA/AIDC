import asyncio
import json
from unittest.mock import AsyncMock, patch

import app_context
import pytest
from app_context import ChatRequest, Message, build_rag_metadata, make_openai_response, make_sse_chunk
from routes import chat_lookup_routes


AUTHENTICATED_ACTOR = {"user_id": "admin01", "role": "admin", "line_scope": ["*"], "team": "admin"}


class FakeEngine:
    ready = True
    tokenizer_version = "unicode-domain-v1"

    def retrieve(self, _query, top_k=5):
        documents = [
            {
                "text": "Coolant pressure is below minimum. Check pump ready signal.",
                "meta": {
                    "code": "340100",
                    "page": 4,
                    "title": "Coolant pressure low",
                    "source": "mock-week2-sop",
                    "doc_id": "doc-coolant",
                    "kind": "alarm",
                },
            },
            {"text": "Hydraulic clamp pressure switch.", "meta": {"code": "5100", "page": 5}},
        ]
        return documents[:top_k]


def test_unknown_collection_does_not_construct_or_cache_engine(tmp_path):
    with (
        patch.object(app_context, "DB_PATH", str(tmp_path)),
        patch.object(app_context, "AlarmRAGEngine") as constructor,
    ):
        app_context.engines.pop("missing", None)
        assert app_context.get_existing_engine("missing") is None
    constructor.assert_not_called()
    assert "missing" not in app_context.engines


def test_citation_ids_are_stable_and_openai_response_remains_compatible():
    engine = FakeEngine()
    first = build_rag_metadata("808d", "coolant", engine.retrieve("coolant"))
    second = build_rag_metadata("808d", "another wording", engine.retrieve("another wording"))

    assert first["citations"][0]["id"] == second["citations"][0]["id"]
    assert first["citations"][0]["source"] == "mock-week2-sop"
    response = make_openai_response("answer", rag=first)
    second_response = make_openai_response("another")
    assert response["choices"][0]["message"]["content"] == "answer"
    assert response["rag"]["citation_count"] == 2
    assert response["id"].startswith("chatcmpl_")
    assert response["rag"]["answer_id"] == response["id"]
    assert second_response["id"] != response["id"]
    assert "rag" not in make_openai_response("legacy-compatible")


def test_sse_chunks_share_answer_id_and_only_first_chunk_needs_rag_metadata():
    rag = build_rag_metadata("808d", "coolant", FakeEngine().retrieve("coolant", top_k=1))
    first = make_sse_chunk("answer", rag=rag, response_id="chatcmpl_test")
    finish = make_sse_chunk("", finish=True, response_id="chatcmpl_test")

    assert '"id": "chatcmpl_test"' in first
    assert '"answer_id": "chatcmpl_test"' in first
    assert '"code": "340100"' in first
    assert '"id": "chatcmpl_test"' in finish
    assert '"rag"' not in finish


def test_retrieve_endpoint_returns_ranked_structured_sources():
    with patch.object(chat_lookup_routes, "get_existing_engine", return_value=FakeEngine()):
        response = asyncio.run(
            chat_lookup_routes.retrieve_collection(
                "808d",
                query="冷卻液壓力過低",
                top_k=1,
                actor=AUTHENTICATED_ACTOR,
            )
        )

    assert response["ready"] is True
    assert response["tokenizer_version"] == "unicode-domain-v1"
    assert response["result_count"] == 1
    assert response["results"][0]["rank"] == 1
    assert response["results"][0]["code"] == "340100"
    assert response["results"][0]["id"].startswith("ragcite_")
    assert response["results"][0]["text"].startswith("Coolant pressure")


def test_retrieve_endpoint_requires_auth_and_valid_collection():
    unauthenticated = asyncio.run(chat_lookup_routes.retrieve_collection("808d", query="q", top_k=1, actor={}))
    assert unauthenticated == {"status": "error", "message": "Not authenticated"}

    with patch.object(chat_lookup_routes, "get_existing_engine") as get_engine:
        invalid = asyncio.run(
            chat_lookup_routes.retrieve_collection(
                "../bad",
                query="q",
                top_k=1,
                actor=AUTHENTICATED_ACTOR,
            )
        )
    assert invalid == {"status": "error", "message": "Invalid collection name"}
    get_engine.assert_not_called()


def test_non_streaming_chat_includes_structured_rag_metadata():
    request = ChatRequest(messages=[Message(role="user", content="coolant pressure")])
    with (
        patch.object(chat_lookup_routes, "get_existing_engine", return_value=FakeEngine()),
        patch.object(chat_lookup_routes, "call_llm", new=AsyncMock(return_value="Check the coolant pump.")),
        patch.object(chat_lookup_routes, "save_rag_answer"),
        patch.object(chat_lookup_routes, "log_query"),
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_rag,
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d"))

    assert response["choices"][0]["message"]["content"].endswith("Check the coolant pump.")
    assert response["rag"]["collection"] == "808d"
    assert response["rag"]["query"] == "coolant pressure"
    assert response["rag"]["citations"][0]["code"] == "340100"
    assert record_rag.call_args.kwargs["streaming"] is False
    assert record_rag.call_args.kwargs["retrieval_ms"] >= 0
    assert record_rag.call_args.kwargs["model_ms"] >= 0


def test_streaming_chat_emits_incremental_chunks_and_persists_combined_answer():
    request = ChatRequest(messages=[Message(role="user", content="coolant pressure")], stream=True)

    async def stream_parts(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        yield "Check the "
        yield "coolant pump."

    with (
        patch.object(chat_lookup_routes, "get_existing_engine", return_value=FakeEngine()),
        patch.object(chat_lookup_routes, "stream_ollama", new=stream_parts),
        patch.object(chat_lookup_routes, "save_rag_answer") as save,
        patch.object(chat_lookup_routes, "log_query"),
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_rag,
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", AUTHENTICATED_ACTOR))

        async def collect():
            return [chunk async for chunk in response.body_iterator]

        body = "".join(asyncio.run(collect()))

    events = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]
    assert len(events) == 3
    assert events[0]["choices"][0]["delta"]["content"].endswith("Check the ")
    assert events[1]["choices"][0]["delta"]["content"] == "coolant pump."
    assert "rag" in events[0]
    assert "rag" not in events[1]
    assert events[2]["choices"][0]["finish_reason"] == "stop"
    assert len({event["id"] for event in events}) == 1
    saved = save.call_args.kwargs
    assert saved["answer"].endswith("Check the coolant pump.")
    assert saved["provider"] == "ollama"
    assert saved["answer_id"] == events[0]["id"]
    assert record_rag.call_args.kwargs["provider"] == "ollama"
    assert record_rag.call_args.kwargs["outcome"] == "complete"
    assert record_rag.call_args.kwargs["streaming"] is True


def test_interrupted_stream_does_not_persist_partial_answer():
    request = ChatRequest(messages=[Message(role="user", content="coolant pressure")], stream=True)

    async def interrupted_stream(*_args, **_kwargs):
        chat_lookup_routes.request_llm_source.set("ollama")
        yield "partial"
        raise asyncio.CancelledError

    with (
        patch.object(chat_lookup_routes, "get_existing_engine", return_value=FakeEngine()),
        patch.object(chat_lookup_routes, "stream_ollama", new=interrupted_stream),
        patch.object(chat_lookup_routes, "save_rag_answer") as save,
        patch.object(chat_lookup_routes, "log_query"),
        patch.object(chat_lookup_routes.runtime_metrics, "record_rag") as record_rag,
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", AUTHENTICATED_ACTOR))

        async def consume():
            return [chunk async for chunk in response.body_iterator]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(consume())

    save.assert_not_called()
    assert record_rag.call_args.kwargs["provider"] == "ollama"
    assert record_rag.call_args.kwargs["outcome"] == "interrupted"
    assert record_rag.call_args.kwargs["streaming"] is True
