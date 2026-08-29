import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request

from app_context import AlarmTrigger, ChatRequest, FeedbackRequest, Message
from repositories.rag_answers import RagAnswerRepository
from routes import alarm_routes, chat_lookup_routes, ingest_routes, stats_routes
from security_limits import RequestBodyLimitMiddleware
from services.ai_usage import AIUsageGuard, AIUsageLimitExceeded


OPERATOR_A = {"user_id": "operator-a", "role": "operator", "line_scope": ["LINE-A"]}
OPERATOR_B = {"user_id": "operator-b", "role": "operator", "line_scope": ["LINE-B"]}


def body_limit_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.post("/v1/demo/chat")
    async def consume(request: Request):
        return {"size": len(await request.body())}

    return app


def test_fixed_and_chunked_oversized_requests_are_rejected_before_endpoint():
    async def chunks():
        yield b"a" * 600
        yield b"b" * 600

    async def scenario():
        transport = httpx.ASGITransport(app=body_limit_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            fixed = await client.post("/v1/demo/chat", content=b"x" * 1200)
            chunked = await client.post("/v1/demo/chat", content=chunks())
        return fixed, chunked

    with patch.dict("os.environ", {"ALARM_RAG_CHAT_REQUEST_MAX_BYTES": "1024"}):
        fixed, chunked = asyncio.run(scenario())
    assert fixed.status_code == 413
    assert chunked.status_code == 413


def test_unauthenticated_ingest_is_rejected_without_reading_body():
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b"payload"

    async def scenario():
        transport = httpx.ASGITransport(app=body_limit_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/demo/ingest-text", content=body())

    response = asyncio.run(scenario())
    assert response.status_code == 401
    assert consumed is False


def test_pdf_section_budget_stops_before_indexing(monkeypatch):
    section = {"code": "3000", "title": "Alarm", "text": "123456", "page": 1}
    monkeypatch.setattr(ingest_routes, "PDF_MAX_EXTRACTED_CHARS", 5)
    with (
        patch("ingest.extract_alarm_sections", return_value=[section]),
        patch("ingest.extract_general_chunks", return_value=[]),
        patch.object(ingest_routes, "get_engine") as engine,
    ):
        result = ingest_routes.ingest_pdf_file("808d", "manual.pdf", "manual.pdf", "hash", None)
    assert result == {"status": "error", "message": "PDF processing budget exceeded"}
    engine.assert_not_called()


def test_machine_duplicate_response_omits_linked_objects():
    alarm = {"source": "n8n", "external_event_id": "evt-1", "issue_id": "ISS-1", "work_order_id": "WO-1"}
    with (
        patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
        patch.object(alarm_routes, "alarm_history", [alarm]),
        patch.object(alarm_routes, "read_jsonl", return_value=[]),
        patch.object(alarm_routes, "get_issue_dict", return_value={"issue_id": "ISS-1", "line_id": "LINE-A"}),
        patch.object(alarm_routes, "get_order_dict", return_value={"id": "WO-1"}),
        patch.dict("os.environ", {"ALARM_RAG_TRIGGER_TOKEN": "secret"}),
    ):
        result = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000", source="n8n", external_event_id="evt-1"),
            actor={},
            trigger_token="secret",
        ))
    assert result == {"status": "ok", "duplicate": True, "external_event_id": "evt-1"}


def test_pending_alarm_filter_keeps_unauthorized_event_undelivered():
    pending = [
        {"issue_id": "ISS-A", "_queue_sequence": 1},
        {"issue_id": "ISS-B", "_queue_sequence": 2},
    ]

    def issue(issue_id):
        return {"issue_id": issue_id, "line_id": "LINE-A" if issue_id == "ISS-A" else "LINE-B", "status": "open"}

    with (
        patch.object(alarm_routes, "pending_alarms", pending),
        patch.object(alarm_routes, "_pending_alarm_deliveries", {}),
        patch.object(alarm_routes, "_pending_alarm_cursors", {}),
        patch.object(alarm_routes, "get_issue_dict", side_effect=issue),
        patch.object(alarm_routes, "get_order_dict", return_value=None),
    ):
        result = asyncio.run(alarm_routes.get_pending_alarms(OPERATOR_A))
        deliveries = alarm_routes._pending_alarm_deliveries["operator-a"]
    assert [item["issue_id"] for item in result["alarms"]] == ["ISS-A"]
    assert deliveries == {1}


def test_alarm_audit_principal_comes_from_session_not_source():
    captured = {}

    def create_issue(**kwargs):
        captured.update(kwargs)
        return {"issue_id": "ISS-1", **kwargs}

    with (
        patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
        patch.object(alarm_routes, "create_issue_dict", side_effect=create_issue),
        patch.object(alarm_routes, "create_order_dict", return_value={"id": "WO-1", "status": "pending"}),
        patch.object(alarm_routes, "set_issue_work_order", return_value=None),
        patch.object(alarm_routes, "append_jsonl"),
        patch.object(alarm_routes, "_publish_alarm"),
        patch.object(alarm_routes, "get_engine", side_effect=RuntimeError("offline")),
    ):
        result = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000", source="admin01", line_id="LINE-A"),
            actor=OPERATOR_A,
            trigger_token=None,
        ))
    assert result["status"] == "ok"
    assert captured["created_by"] == "operator-a"
    assert captured["source"] == "admin01"


def test_foreign_answer_cannot_receive_feedback():
    answer = {"query": "q", "collection": "808d", "created_by": "operator-b"}
    with patch.object(stats_routes.rag_answers, "get", return_value=answer):
        response = asyncio.run(stats_routes.save_feedback(
            FeedbackRequest(query="q", collection="808d", feedback="good", answer_id="answer-b"),
            actor=OPERATOR_A,
        ))
    assert response.status_code == 403


def test_json_answer_repository_enforces_retention_and_record_size(tmp_path):
    repository = RagAnswerRepository()
    env = {
        "DB_PATH": str(tmp_path),
        "DATA_STORE": "json",
        "ALARM_RAG_JSON_ANSWER_MAX_RECORDS": "2",
        "ALARM_RAG_JSON_ANSWER_MAX_PER_USER": "2",
        "ALARM_RAG_ANSWER_MAX_RECORD_BYTES": "1024",
    }
    with patch.dict("os.environ", env):
        for index in range(3):
            assert repository.add({"answer_id": f"a-{index}", "answer": "ok", "created_by": "operator-a"})
        assert repository.get("a-0") is None
        assert repository.get("a-2")["answer"] == "ok"
        assert repository.add({"answer_id": "huge", "answer": "x" * 2000}) is False
    assert len((tmp_path / "rag_answers.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_ai_guard_rejects_budget_and_concurrency_excess():
    async def scenario():
        guard = AIUsageGuard()
        first = await guard.acquire("operator-a", 4)
        try:
            try:
                await guard.acquire("operator-b", 4)
            except AIUsageLimitExceeded as exc:
                concurrency_error = str(exc)
            else:  # pragma: no cover
                concurrency_error = ""
        finally:
            first.release()
        await guard.acquire("operator-a", 4)
        try:
            await guard.acquire("operator-a", 4)
        except AIUsageLimitExceeded as exc:
            budget_error = str(exc)
        return concurrency_error, budget_error

    env = {
        "ALARM_RAG_LLM_GLOBAL_CONCURRENCY": "1",
        "ALARM_RAG_LLM_REQUESTS_PER_WINDOW": "2",
        "ALARM_RAG_LLM_TOKENS_PER_WINDOW": "8",
    }
    with patch.dict("os.environ", env):
        concurrency_error, budget_error = asyncio.run(scenario())
    assert "concurrency" in concurrency_error
    assert "limit" in budget_error or "budget" in budget_error


def test_public_llm_fallback_does_not_disclose_exception_text():
    message = chat_lookup_routes.build_llm_unavailable_message(
        RuntimeError("secret-provider-body https://internal.example"), []
    )
    assert "secret-provider-body" not in message
    assert "internal.example" not in message
    assert "LLM_UNAVAILABLE" in message
