import asyncio
from unittest.mock import AsyncMock, patch

from app_context import ChatRequest, Message, build_rag_metadata, make_openai_response
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


def test_citation_ids_are_stable_and_openai_response_remains_compatible():
    engine = FakeEngine()
    first = build_rag_metadata("808d", "coolant", engine.retrieve("coolant"))
    second = build_rag_metadata("808d", "another wording", engine.retrieve("another wording"))

    assert first["citations"][0]["id"] == second["citations"][0]["id"]
    assert first["citations"][0]["source"] == "mock-week2-sop"
    response = make_openai_response("answer", rag=first)
    assert response["choices"][0]["message"]["content"] == "answer"
    assert response["rag"]["citation_count"] == 2
    assert "rag" not in make_openai_response("legacy-compatible")


def test_retrieve_endpoint_returns_ranked_structured_sources():
    with patch.object(chat_lookup_routes, "get_engine", return_value=FakeEngine()):
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

    with patch.object(chat_lookup_routes, "get_engine") as get_engine:
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
    with patch.object(chat_lookup_routes, "get_engine", return_value=FakeEngine()):
        with patch.object(chat_lookup_routes, "call_llm", new=AsyncMock(return_value="Check the coolant pump.")):
            response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d"))

    assert response["choices"][0]["message"]["content"].endswith("Check the coolant pump.")
    assert response["rag"]["collection"] == "808d"
    assert response["rag"]["query"] == "coolant pressure"
    assert response["rag"]["citations"][0]["code"] == "340100"
