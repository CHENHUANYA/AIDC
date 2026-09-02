import asyncio
import os
from unittest.mock import patch

from app_context import AlarmTrigger, FeedbackRequest
from routes import alarm_routes, settings_routes, stats_routes
import storage


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"], "team": "admin"}


def test_postgresql_alarm_trigger_writes_alarm_and_links_issue():
    issue = {"issue_id": "ISS-PG"}
    order = {"id": "WO-PG"}
    with (
        patch.object(alarm_routes, "postgres_store_enabled", return_value=True),
        patch.object(alarm_routes.postgres_alarms, "workflow_counts_for_principal", return_value=(0, 0)),
        patch.object(alarm_routes.postgres_alarms, "add_once", return_value=("alarm-pk", True)) as add_alarm,
        patch.object(alarm_routes, "append_jsonl") as append_jsonl,
        patch.object(alarm_routes, "get_engine", side_effect=RuntimeError("skip rag")),
        patch.object(alarm_routes, "postgres_create_issue", return_value=(issue, order)) as create_issue,
    ):
        result = asyncio.run(
            alarm_routes.trigger_alarm.__wrapped__(
                AlarmTrigger(alarm_code="3000", manual="808d", source="phase4-test"),
                actor=ADMIN,
            )
        )

    assert result["status"] == "ok"
    add_alarm.assert_called_once()
    append_jsonl.assert_not_called()
    assert create_issue.call_args.kwargs["alarm_event_id"] == "alarm-pk"


def test_postgresql_duplicate_alarm_returns_existing_workflow():
    alarm = {"alarm_code": "3000", "source": "n8n", "external_event_id": "evt-1"}
    issue = {"issue_id": "ISS-PG"}
    order = {"id": "WO-PG"}
    with (
        patch.object(alarm_routes, "postgres_store_enabled", return_value=True),
        patch.object(alarm_routes.postgres_alarms, "get_by_event_key", return_value=None),
        patch.object(alarm_routes.postgres_alarms, "workflow_counts_for_principal", return_value=(0, 0)),
        patch.object(alarm_routes.postgres_alarms, "add_once", return_value=("alarm-pk", False)),
        patch.object(alarm_routes.postgres_alarms, "get", return_value=alarm),
        patch.object(alarm_routes, "postgres_get_issue_for_alarm_event", return_value=(issue, order)),
        patch.object(alarm_routes, "postgres_create_issue") as create_issue,
        patch.object(alarm_routes, "get_engine") as get_engine,
    ):
        result = asyncio.run(
            alarm_routes.trigger_alarm.__wrapped__(
                AlarmTrigger(
                    alarm_code="3000",
                    manual="808d",
                    source="n8n",
                    external_event_id="evt-1",
                ),
                actor=ADMIN,
            )
        )

    assert result["status"] == "ok"
    assert result["duplicate"] is True
    assert result["issue"] == issue
    assert result["work_order"] == order
    create_issue.assert_not_called()
    get_engine.assert_not_called()


def test_postgresql_feedback_uses_repository_not_jsonl():
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(
            stats_routes.rag_answers,
            "get",
            return_value={"query": "q", "collection": "808d", "created_by": "admin01"},
        ),
        patch.object(stats_routes.postgres_feedback, "add") as add_feedback,
        patch.object(stats_routes.os, "makedirs") as make_dirs,
    ):
        result = asyncio.run(
            stats_routes.save_feedback(
                FeedbackRequest(query="q", collection="808d", feedback="good", answer_id="answer-1"),
                actor=ADMIN,
            )
        )

    assert result == {"status": "ok"}
    add_feedback.assert_called_once()
    make_dirs.assert_not_called()


def test_postgresql_settings_use_repository():
    current = {**settings_routes.DEFAULT_SETTINGS, "session_hours": 12}
    with (
        patch.object(settings_routes, "postgres_store_enabled", return_value=True),
        patch.dict(os.environ, {"SESSION_TTL_HOURS": ""}, clear=False),
        patch.object(settings_routes.postgres_settings, "load_all", return_value=current),
        patch.object(settings_routes.postgres_settings, "save_all") as save_all,
    ):
        result = asyncio.run(
            settings_routes.update_system_settings(
                settings_routes.UpdateSystemSettings(session_hours=24),
                actor=ADMIN,
            )
        )

    assert result["settings"]["session_hours"] == 24
    save_all.assert_called_once()
    assert save_all.call_args.args[1] == "admin01"


def test_postgresql_document_metadata_uses_repository():
    document = {"doc_id": "doc-1", "filename": "manual.pdf", "source_hash": "abc"}
    with (
        patch.object(storage, "postgres_store_enabled", return_value=True),
        patch.object(storage.postgres_documents, "upsert") as upsert,
        patch.object(storage.postgres_documents, "load_collection", return_value=[document]) as load,
        patch.object(storage.postgres_documents, "find_by_hash", return_value=document) as find,
    ):
        storage.upsert_document_entry("808d", document)
        assert storage.get_documents("808d") == [document]
        assert storage.find_document_by_hash("808d", "abc") == document

    upsert.assert_called_once_with("808d", document)
    load.assert_called_once_with("808d")
    find.assert_called_once_with("808d", "abc")
