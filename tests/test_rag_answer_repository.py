import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

from app_context import ChatRequest, Message
from repositories.rag_answers import RagAnswerRepository
from routes import chat_lookup_routes


def test_json_answer_repository_is_immutable_and_queryable(tmp_path):
    repository = RagAnswerRepository()
    payload = {
        "answer_id": "chatcmpl_test",
        "query": "Alarm 3000",
        "collection": "808d",
        "answer": "stop safely",
        "citations": [{"id": "c1", "code": "3000"}],
        "provider": "ollama",
    }
    with patch.dict("os.environ", {"DB_PATH": str(tmp_path), "DATA_STORE": "json"}):
        assert repository.add(payload) is True
        assert repository.add({**payload, "answer": "mutated"}) is False
        saved = repository.get("chatcmpl_test")
    assert saved is not None
    assert saved["answer"] == "stop safely"
    assert saved["answer_state"] == "complete"
    assert saved["citations"][0]["code"] == "3000"


def test_concurrent_json_writes_keep_answer_id_unique(tmp_path):
    repository = RagAnswerRepository()
    payload = {"answer_id": "chatcmpl_race", "query": "q", "collection": "808d", "answer": "a"}
    with patch.dict("os.environ", {"DB_PATH": str(tmp_path), "DATA_STORE": "json"}):
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(lambda _index: repository.add(payload), range(8)))
        lines = (tmp_path / "rag_answers.jsonl").read_text(encoding="utf-8").splitlines()
    assert outcomes.count(True) == 1
    assert len(lines) == 1


def test_json_answer_repository_normalizes_unknown_answer_state(tmp_path):
    repository = RagAnswerRepository()
    with patch.dict("os.environ", {"DB_PATH": str(tmp_path), "DATA_STORE": "json"}):
        assert repository.add({"answer_id": "chatcmpl_state", "answer_state": "mystery"}) is True
        saved = repository.get("chatcmpl_state")
    assert saved is not None
    assert saved["answer_state"] == "complete"


def test_answer_lookup_returns_http_404_for_unknown_id():
    with patch.object(chat_lookup_routes.rag_answers, "get", return_value=None):
        response = asyncio.run(chat_lookup_routes.get_rag_answer("missing", actor={"user_id": "operator01"}))
    assert response.status_code == 404


def test_answer_lookup_rejects_other_non_privileged_user():
    answer = {"answer_id": "chatcmpl_1", "created_by": "operator01"}
    with patch.object(chat_lookup_routes.rag_answers, "get", return_value=answer):
        denied = asyncio.run(chat_lookup_routes.get_rag_answer(
            "chatcmpl_1",
            actor={"user_id": "operator02", "role": "operator"},
        ))
        allowed = asyncio.run(chat_lookup_routes.get_rag_answer(
            "chatcmpl_1",
            actor={"user_id": "admin01", "role": "admin"},
        ))
    assert denied.status_code == 403
    assert allowed == {"status": "ok", "answer": answer}


def test_handle_chat_persists_answer_snapshot(tmp_path):
    class Engine:
        ready = True
        tokenizer_version = "unicode-domain-v1"

        def retrieve(self, _query, top_k=4):
            del top_k
            return [{"text": "Emergency stop circuit", "meta": {"code": "3000", "page": 10, "title": "E-stop"}}]

    request = ChatRequest(messages=[Message(role="user", content="Alarm 3000")], stream=False)
    with (
        patch.dict("os.environ", {"DB_PATH": str(tmp_path), "DATA_STORE": "json"}),
        patch.object(chat_lookup_routes, "get_existing_engine", return_value=Engine()),
        patch.object(
            chat_lookup_routes,
            "call_llm",
            new=AsyncMock(return_value="Stop and inspect the emergency stop circuit."),
        ),
    ):
        response = asyncio.run(chat_lookup_routes.handle_chat(request, "808d", {"user_id": "operator01"}))
        answer = RagAnswerRepository().get(response["id"])
    assert answer is not None
    assert answer["answer_id"] == response["rag"]["answer_id"]
    assert answer["created_by"] == "operator01"
    assert answer["citations"][0]["code"] == "3000"
    assert answer["answer_state"] == "complete"


def test_answer_state_classifies_school_to_ollama_as_fallback():
    with patch.object(chat_lookup_routes, "LLM_PROVIDER", "school"):
        assert chat_lookup_routes.classify_answer_state("ollama") == "fallback"
        assert chat_lookup_routes.classify_answer_state("school") == "complete"
    assert chat_lookup_routes.classify_answer_state("unavailable") == "unavailable"
