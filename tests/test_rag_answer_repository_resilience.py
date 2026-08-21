from contextlib import contextmanager
from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from db.models import RagAnswer
from repositories import rag_answers


def test_answer_repository_rejects_missing_and_oversized_ids():
    repository = rag_answers.RagAnswerRepository()
    assert repository.add({}) is False
    assert repository.add({"answer_id": "x" * 256}) is False


def test_json_lookup_skips_corrupt_lines_and_normalizes_state(tmp_path):
    repository = rag_answers.RagAnswerRepository()
    path = tmp_path / "rag_answers.jsonl"
    path.write_text(
        "not-json\n" + json.dumps({"answer_id": "answer-1", "answer_state": "invalid"}) + "\n",
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"DB_PATH": str(tmp_path), "DATA_STORE": "json"}):
        assert repository.get("missing") is None
        answer = repository.get("answer-1")
    assert answer is not None
    assert answer["answer_state"] == "complete"


def test_postgres_lookup_maps_record_and_handles_missing():
    repository = rag_answers.RagAnswerRepository()
    record = RagAnswer(
        answer_id="answer-1",
        query="query",
        collection="808d",
        answer="answer",
        answer_state="fallback",
        citations=[{"code": "3000"}],
        provider="ollama",
        model="model",
        tokenizer_version="v1",
        retrieval_version="r1",
        elapsed_ms=12,
        created_by_ref="operator01",
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    session = MagicMock()
    session.scalar.side_effect = [record, None]

    @contextmanager
    def scope():
        yield session

    with (
        patch.object(rag_answers, "postgres_store_enabled", return_value=True),
        patch.object(rag_answers, "session_scope", scope),
    ):
        mapped = repository.get("answer-1")
        missing = repository.get("missing")

    assert mapped is not None
    assert mapped["answer_id"] == "answer-1"
    assert mapped["created_at"] == "2026-08-21T00:00:00+00:00"
    assert mapped["citations"] == [{"code": "3000"}]
    assert missing is None


def test_postgres_add_builds_record_and_treats_integrity_error_as_duplicate():
    captured: list[RagAnswer] = []

    @contextmanager
    def successful_scope():
        session = MagicMock()
        session.add.side_effect = captured.append
        yield session

    payload = {
        "answer_id": "answer-1",
        "query": "query",
        "collection": "808d",
        "answer": "answer",
        "answer_state": "fallback",
        "citations": [{"code": "3000"}],
        "provider": "school",
        "model": "model",
        "tokenizer_version": "v1",
        "retrieval_version": "r1",
        "elapsed_ms": "12",
        "created_by": "operator01",
    }
    with patch.object(rag_answers, "session_scope", successful_scope):
        assert rag_answers.RagAnswerRepository._add_postgres(payload) is True
    assert captured[0].answer_id == "answer-1"
    assert captured[0].elapsed_ms == 12
    assert captured[0].answer_state == "fallback"

    @contextmanager
    def duplicate_scope():
        raise IntegrityError("insert", {}, RuntimeError("duplicate"))
        yield  # pragma: no cover

    with patch.object(rag_answers, "session_scope", duplicate_scope):
        assert rag_answers.RagAnswerRepository._add_postgres(payload) is False
