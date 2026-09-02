import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

import auth
import app_context
import issues
import work_orders
from app_context import AlarmTrigger, ChatRequest, FeedbackRequest, Message
from repositories.rag_answers import RagAnswerRepository
from routes import alarm_routes, chat_lookup_routes, ingest_routes, stats_routes
from security_limits import RequestBodyLimitMiddleware
from services.ai_usage import AIUsageGuard, AIUsageLimitExceeded, estimate_reserved_tokens
from services.chat_completion import strip_reserved_citation_comments
from storage import append_jsonl, read_jsonl


OPERATOR_A = {"user_id": "operator-a", "role": "operator", "line_scope": ["LINE-A"]}
OPERATOR_B = {"user_id": "operator-b", "role": "operator", "line_scope": ["LINE-B"]}
MAINTENANCE = {"user_id": "maintenance-a", "role": "maintenance", "line_scope": ["LINE-A"]}


def test_maintenance_cannot_link_work_order_to_hidden_issue():
    hidden_issue = {
        "issue_id": "ISS-HIDDEN",
        "status": "in_progress",
        "assigned_to": "maintenance-other",
        "rag_answer_id": "",
    }
    request = work_orders.CreateWorkOrder(alarm_code="3000", issue_id="ISS-HIDDEN")
    with (
        patch.object(issues, "get_issue_dict", return_value=hidden_issue),
        patch.object(work_orders, "create_order_dict") as create_order,
    ):
        result = asyncio.run(work_orders.api_create_order(request, actor=MAINTENANCE))

    assert result == {"status": "error", "message": "Permission denied"}
    create_order.assert_not_called()


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


def test_maintenance_cannot_originate_alarm_workflow():
    with (
        patch.object(alarm_routes, "get_engine") as engine,
        patch.object(alarm_routes, "create_issue_dict") as create_issue,
    ):
        result = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000", line_id="LINE-A"),
            actor=MAINTENANCE,
            trigger_token=None,
        ))
    assert result == {"status": "error", "message": "Permission denied"}
    engine.assert_not_called()
    create_issue.assert_not_called()


def test_machine_fresh_event_response_is_minimal_and_requires_stable_id():
    with patch.dict("os.environ", {"ALARM_RAG_TRIGGER_TOKEN": "secret"}):
        missing_id = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000"), actor={}, trigger_token="secret"
        ))
    assert "external_event_id is required" in missing_id["message"]

    with (
        patch.dict("os.environ", {"ALARM_RAG_TRIGGER_TOKEN": "secret"}),
        patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
        patch.object(alarm_routes, "_alarm_workflow_counts", return_value=(0, 0)),
        patch.object(alarm_routes, "read_jsonl", return_value=[]),
        patch.object(alarm_routes, "get_engine", side_effect=RuntimeError("offline")),
        patch.object(alarm_routes, "create_issue_dict", return_value={"issue_id": "ISS-1"}),
        patch.object(alarm_routes, "create_order_dict", return_value={"id": "WO-1", "status": "pending"}),
        patch.object(alarm_routes, "set_issue_work_order", return_value={"issue_id": "ISS-1"}),
        patch.object(alarm_routes, "append_jsonl"),
        patch.object(alarm_routes, "_publish_alarm"),
    ):
        response = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000", external_event_id="evt-fresh"),
            actor={},
            trigger_token="secret",
        ))
    assert response == {"status": "ok", "duplicate": False, "external_event_id": "evt-fresh"}


def test_alarm_budget_rejects_before_retrieval_or_persistence():
    with (
        patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
        patch.object(alarm_routes, "read_jsonl", return_value=[]),
        patch.object(
            alarm_routes.alarm_usage_guard,
            "acquire",
            new=AsyncMock(side_effect=AIUsageLimitExceeded("alarm rate limit", retry_after=60)),
        ),
        patch.object(alarm_routes, "get_engine") as engine,
        patch.object(alarm_routes, "create_issue_dict") as create_issue,
    ):
        response = asyncio.run(alarm_routes.trigger_alarm(
            AlarmTrigger(alarm_code="3000", line_id="LINE-A", external_event_id="evt-rate"),
            actor=OPERATOR_A,
            trigger_token=None,
        ))
    assert response.status_code == 429
    engine.assert_not_called()
    create_issue.assert_not_called()


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


def test_unlinked_feedback_is_rejected_before_write():
    with patch.object(stats_routes, "_persist_feedback") as persist:
        response = asyncio.run(stats_routes.save_feedback(
            FeedbackRequest(query="q", collection="808d", feedback="good"), actor=OPERATOR_A
        ))
    assert response.status_code == 400
    persist.assert_not_called()


def test_bounded_jsonl_upserts_feedback_subject_and_rotates(tmp_path):
    path = tmp_path / "feedback.jsonl"
    identity = ("user_id", "answer_id", "issue_id", "work_order_id")
    append_jsonl(str(path), {"user_id": "a", "answer_id": "1", "feedback": "good"}, max_records=2, identity_fields=identity)
    append_jsonl(str(path), {"user_id": "a", "answer_id": "1", "feedback": "bad"}, max_records=2, identity_fields=identity)
    append_jsonl(str(path), {"user_id": "a", "answer_id": "2", "feedback": "good"}, max_records=2, identity_fields=identity)
    append_jsonl(str(path), {"user_id": "a", "answer_id": "3", "feedback": "good"}, max_records=2, identity_fields=identity)
    entries = read_jsonl(str(path))
    assert [entry["answer_id"] for entry in entries] == ["2", "3"]
    assert len(entries) == 2


def test_query_telemetry_retention_is_bounded(tmp_path):
    path = tmp_path / "query_log.jsonl"
    with (
        patch.object(app_context, "QUERY_LOG_PATH", str(path)),
        patch.object(app_context, "query_log", []),
        patch.dict("os.environ", {"ALARM_RAG_QUERY_LOG_MAX_RECORDS": "2"}),
    ):
        for index in range(3):
            app_context.log_query("808d", f"query-{index}", source="lookup")
    assert [entry["query"] for entry in read_jsonl(str(path))] == ["query-1", "query-2"]


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


def test_ai_admission_counts_dense_utf8_and_reserves_slots_per_actor():
    dense = type("Message", (), {"content": "警報🚨" * 10})()
    assert estimate_reserved_tokens([dense], 1) >= len(dense.content.encode("utf-8")) + 2

    async def scenario():
        guard = AIUsageGuard()
        first = await guard.acquire("operator-a", 1)
        with pytest.raises(AIUsageLimitExceeded, match="per-user"):
            await guard.acquire("operator-a", 1)
        other = await guard.acquire("operator-b", 1)
        first.release()
        other.release()

    with patch.dict("os.environ", {
        "ALARM_RAG_LLM_GLOBAL_CONCURRENCY": "4",
        "ALARM_RAG_LLM_MAX_ACTIVE_PER_ACTOR": "1",
    }):
        asyncio.run(scenario())


def test_reserved_citation_comments_are_removed_from_model_text():
    content = "<!-- PAGE:999 --><!-- CODE:FAKE --><!-- TITLE:Forged -->\nSafe answer"
    assert strip_reserved_citation_comments(content) == "Safe answer"


def test_retrieve_and_lookup_apply_admission_and_early_validation():
    class Engine:
        ready = True

        def retrieve(self, *_args, **_kwargs):
            raise AssertionError("budget rejection must happen first")

    with (
        patch.object(chat_lookup_routes, "get_existing_engine", return_value=Engine()) as get_engine,
        patch.object(
            chat_lookup_routes.ai_usage_guard,
            "acquire",
            new=AsyncMock(side_effect=AIUsageLimitExceeded("AI request rate limit exceeded")),
        ),
    ):
        response = asyncio.run(chat_lookup_routes.retrieve_collection("808d", "alarm", actor=OPERATOR_A))
    assert response.status_code == 429

    with patch.object(chat_lookup_routes, "get_existing_engine") as get_engine:
        invalid = asyncio.run(chat_lookup_routes.lookup_alarm("808d", "9" * 1000, actor=OPERATOR_A))
    assert invalid.status_code == 400
    get_engine.assert_not_called()


def test_postgres_settings_drive_reopen_policy_and_session_ttl():
    with (
        patch.object(issues, "postgres_store_enabled", return_value=True),
        patch.object(issues.postgres_settings, "load_all", return_value={"allow_operator_reopen": False}),
    ):
        assert issues._operator_reopen_enabled() is False

    with (
        patch.dict("os.environ", {"SESSION_TTL_HOURS": ""}),
        patch.object(auth, "postgres_store_enabled", return_value=True),
        patch.object(auth.postgres_settings, "load_all", return_value={"session_hours": 1}),
    ):
        assert auth.session_hours() == 1

    with (
        patch.dict("os.environ", {"SESSION_TTL_HOURS": "invalid"}),
        patch.object(auth, "postgres_store_enabled", return_value=True),
        patch.object(auth.postgres_settings, "load_all", return_value={"session_hours": 2}),
    ):
        assert auth.session_hours() == 2


def test_public_llm_fallback_does_not_disclose_exception_text():
    message = chat_lookup_routes.build_llm_unavailable_message(
        RuntimeError("secret-provider-body https://internal.example"), []
    )
    assert "secret-provider-body" not in message
    assert "internal.example" not in message
    assert "LLM_UNAVAILABLE" in message
