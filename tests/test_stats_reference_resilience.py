import asyncio
import builtins
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app_context import FeedbackRequest
from routes import static_reference_routes, stats_routes


ADMIN = {"user_id": "admin01", "role": "admin"}
SUPERVISOR = {"user_id": "supervisor01", "role": "supervisor"}
OPERATOR = {"user_id": "operator01", "role": "operator"}
ANONYMOUS = {"user_id": "", "role": ""}


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_alarm_stats_normalizes_entries_and_aggregates_recent_days() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    alarms = [
        {"date": today, "manual": "808d", "source": "API"},
        {"date": "2026-08-01", "manual": "808d", "source": "n8n"},
        {"date": "2026-08-02", "manual": "828d", "source": "n8n"},
        {"manual": "", "source": ""},
        "invalid",
    ]
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=False),
        patch.object(stats_routes, "alarm_history", alarms),
    ):
        result = asyncio.run(stats_routes.alarm_stats(actor=SUPERVISOR))

    assert result["total"] == 4
    assert result["today"] == 1
    assert result["by_manual"] == {"808d": 2, "828d": 1, "unknown": 1}
    assert result["by_source"] == {"API": 1, "n8n": 2, "unknown": 1}
    assert result["daily"] == [
        {"date": "2026-08-01", "count": 1},
        {"date": "2026-08-02", "count": 1},
        {"date": today, "count": 1},
    ]


def test_alarm_stats_rejects_operator_and_handles_repository_failure() -> None:
    assert asyncio.run(stats_routes.alarm_stats(actor=OPERATOR)) == {
        "status": "error",
        "message": "Permission denied",
    }
    with patch.object(stats_routes, "_load_alarm_entries", side_effect=RuntimeError("db unavailable")):
        response = asyncio.run(stats_routes.alarm_stats(actor=ADMIN))

    assert response.status_code == 503
    assert response_json(response)["message"] == "Alarm statistics are unavailable"


def test_alarm_loader_supports_postgres_and_filters_non_objects() -> None:
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes.postgres_alarms, "load_all", return_value=[{"manual": "808d"}, None]),
    ):
        assert stats_routes._load_alarm_entries() == [{"manual": "808d"}]


def test_clear_alarm_stats_removes_persistence_before_memory() -> None:
    alarms = [{"date": "2026-08-18"}]
    with (
        patch.object(stats_routes, "alarm_history", alarms),
        patch.object(stats_routes, "postgres_store_enabled", return_value=False),
        patch.object(stats_routes.os.path, "exists", return_value=True),
        patch.object(stats_routes.os, "remove") as remove,
    ):
        result = asyncio.run(stats_routes.clear_alarm_stats(actor=ADMIN))

    assert result == {"status": "ok"}
    assert alarms == []
    remove.assert_called_once_with(stats_routes.ALARM_LOG_PATH)


def test_clear_alarm_stats_requires_authentication() -> None:
    assert asyncio.run(stats_routes.clear_alarm_stats(actor=ANONYMOUS)) == {
        "status": "error",
        "message": "Not authenticated",
    }


def test_clear_alarm_stats_failure_preserves_memory_snapshot() -> None:
    alarms = [{"date": "2026-08-18"}]
    with (
        patch.object(stats_routes, "alarm_history", alarms),
        patch.object(stats_routes, "_clear_persisted_alarm_stats", side_effect=OSError("locked")),
    ):
        response = asyncio.run(stats_routes.clear_alarm_stats(actor=ADMIN))

    assert response.status_code == 503
    assert alarms == [{"date": "2026-08-18"}]


def test_clear_alarm_stats_uses_postgres_repository() -> None:
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes.postgres_alarms, "clear") as clear,
        patch.object(stats_routes.os, "remove") as remove,
    ):
        stats_routes._clear_persisted_alarm_stats()

    clear.assert_called_once_with()
    remove.assert_not_called()


def test_save_feedback_uses_jsonl_helper_and_authenticated_identity() -> None:
    request = FeedbackRequest(
        query="alarm 3000",
        collection="808d",
        feedback="good",
        user_id="spoofed",
        role="admin",
    )
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=False),
        patch.object(stats_routes, "append_jsonl") as append,
    ):
        result = asyncio.run(stats_routes.save_feedback(request, actor=OPERATOR))

    assert result == {"status": "ok"}
    path, entry = append.call_args.args
    assert path == stats_routes.FEEDBACK_LOG
    assert entry["user_id"] == "operator01"
    assert entry["role"] == "operator"


def test_save_feedback_returns_service_unavailable_on_write_failure() -> None:
    request = FeedbackRequest(query="alarm", collection="808d", feedback="bad")
    with patch.object(stats_routes, "_persist_feedback", side_effect=OSError("disk full")):
        response = asyncio.run(stats_routes.save_feedback(request, actor=OPERATOR))

    assert response.status_code == 503
    assert response_json(response)["message"] == "Feedback storage is unavailable"


def test_feedback_stats_aggregates_quality_fields_and_skips_invalid_entries() -> None:
    entries = [
        {
            "feedback": "good",
            "correctness": "correct",
            "coverage": "complete",
            "role": "maintenance",
        },
        {
            "feedback": "bad",
            "correctness": "partially_correct",
            "coverage": "missing_steps",
            "role": "operator",
        },
        {"feedback": "good", "correctness": "ignored", "coverage": "ignored"},
    ]
    with patch.object(stats_routes, "_load_feedback_entries", return_value=entries):
        result = asyncio.run(stats_routes.feedback_stats(actor=SUPERVISOR))

    assert result["total"] == 3
    assert result["good"] == 2
    assert result["bad"] == 1
    assert result["rate"] == "67%"
    assert result["correctness_total"] == 2
    assert result["correctness_rate"] == "50%"
    assert result["coverage_total"] == 2
    assert result["coverage_rate"] == "50%"
    assert result["technician_feedback"] == 1


def test_feedback_stats_empty_permission_and_load_failure_branches() -> None:
    assert asyncio.run(stats_routes.feedback_stats(actor=OPERATOR)) == {
        "status": "error",
        "message": "Permission denied",
    }
    with patch.object(stats_routes, "_load_feedback_entries", return_value=[]):
        empty = asyncio.run(stats_routes.feedback_stats(actor=ADMIN))
    assert empty["rate"] == "0%"
    assert empty["correctness_rate"] == "0%"
    assert empty["coverage_rate"] == "0%"

    with patch.object(stats_routes, "_load_feedback_entries", side_effect=OSError("unreadable")):
        response = asyncio.run(stats_routes.feedback_stats(actor=ADMIN))
    assert response.status_code == 503


def test_feedback_loader_supports_postgres_and_filters_non_objects() -> None:
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes.postgres_feedback, "load_all", return_value=[{"feedback": "good"}, None]),
    ):
        assert stats_routes._load_feedback_entries() == [{"feedback": "good"}]

    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=False),
        patch.object(stats_routes, "read_jsonl", return_value=[{"feedback": "bad"}, []]),
    ):
        assert stats_routes._load_feedback_entries() == [{"feedback": "bad"}]


def test_query_stats_ignores_invalid_elapsed_values_and_non_objects() -> None:
    queries = [
        {"query": "alarm 3000", "collection": "808d", "elapsed_ms": "100"},
        {"query": "alarm 3000 and 5000", "collection": "808d", "elapsed_ms": 200},
        {"query": "bad", "collection": None, "elapsed_ms": True},
        {"query": "bad", "elapsed_ms": "NaN"},
        {"query": "bad", "elapsed_ms": float("inf")},
        {"query": "bad", "elapsed_ms": "invalid"},
        "invalid",
    ]
    with patch.object(stats_routes, "read_jsonl", return_value=queries):
        result = asyncio.run(stats_routes.query_stats(actor=ADMIN))

    assert result["total"] == 6
    assert result["avg_ms"] == 150
    assert result["p95_ms"] == 195
    assert result["p99_ms"] == 199
    assert result["top_codes"] == [("3000", 2), ("5000", 1)]
    assert result["by_collection"] == {"808d": 2, "unknown": 4}


def test_query_and_error_stats_permission_and_failure_branches() -> None:
    assert asyncio.run(stats_routes.query_stats(actor=OPERATOR))["message"] == "Permission denied"
    with patch.object(stats_routes, "_query_stats_payload", side_effect=OSError("unreadable")):
        response = asyncio.run(stats_routes.query_stats(actor=ADMIN))
    assert response.status_code == 503

    with patch.object(stats_routes, "error_log", [{"error": "one"}, "invalid", {"error": "two"}]):
        result = asyncio.run(stats_routes.error_stats(actor=SUPERVISOR))
    assert result == {"recent": [{"error": "one"}, {"error": "two"}], "total": 2}
    assert asyncio.run(stats_routes.error_stats(actor=OPERATOR))["message"] == "Permission denied"


def test_runtime_metrics_requires_authentication() -> None:
    assert asyncio.run(stats_routes.runtime_metrics_snapshot(actor=ANONYMOUS)) == {
        "status": "error",
        "message": "Not authenticated",
    }


def test_postgres_pool_snapshot_handles_disabled_missing_methods_and_failure() -> None:
    with patch.object(stats_routes, "postgres_store_enabled", return_value=False):
        assert stats_routes._postgres_pool_metrics() == {"enabled": False, "status": "not-required"}

    engine = SimpleNamespace(pool=SimpleNamespace(size=5))
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes, "get_engine", return_value=engine),
    ):
        assert stats_routes._postgres_pool_metrics()["pool_size"] == 0

    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes, "get_engine", side_effect=RuntimeError("offline")),
    ):
        assert stats_routes._postgres_pool_metrics() == {"enabled": True, "status": "unavailable"}


def test_model_cache_public_shape_discards_private_and_invalid_fields() -> None:
    raw = {
        "ready": 1,
        "local_only": 0,
        "private_path": "C:/secret",
        "models": [
            {"role": "embed", "name": "model-a", "available": 1, "path": "C:/secret/model"},
            "invalid",
        ],
    }
    with patch.object(stats_routes, "model_cache_status", return_value=raw):
        result = stats_routes._public_model_cache_status()

    assert result == {
        "ready": True,
        "local_only": False,
        "models": [{"role": "embed", "name": "model-a", "available": True}],
    }


def test_health_reports_each_engine_without_exposing_internal_state() -> None:
    engine = SimpleNamespace(
        ready=True,
        sections=[{"private": "content"}],
        retrieval_runtime_status=Mock(return_value={"backend": "bm25"}),
        traceability_coverage=Mock(
            return_value={"traceable_sections": 1, "traceability_ready": True}
        ),
    )
    with (
        patch.object(stats_routes, "engines", {"808d": engine}),
        patch.object(stats_routes, "_last_llm_source", return_value="ollama"),
        patch.object(stats_routes, "_public_model_cache_status", return_value={"ready": True}),
    ):
        result = asyncio.run(stats_routes.health_details({"user_id": "admin01", "role": "admin"}))

    assert result["collections"] == {
        "808d": {
            "ready": True,
            "alarms_indexed": 1,
            "retrieval_runtime": {"backend": "bm25"},
            "traceability": {"traceable_sections": 1, "traceability_ready": True},
        }
    }


def test_last_llm_source_returns_value_and_safe_fallback() -> None:
    assert isinstance(stats_routes._last_llm_source(), str)
    real_import = builtins.__import__

    def fail_chat_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "routes.chat_lookup_routes":
            raise ImportError("chat route unavailable")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fail_chat_import):
        assert stats_routes._last_llm_source() == "unknown"


@pytest.mark.parametrize(
    ("handler", "filename"),
    [
        (static_reference_routes.serve_app, "dashboard.html"),
        (static_reference_routes.serve_login, "login.html"),
        (static_reference_routes.serve_dashboard, "dashboard.html"),
        (static_reference_routes.serve_supervisor, "supervisor.html"),
        (static_reference_routes.serve_admin, "admin.html"),
        (static_reference_routes.serve_operator, "operator.html"),
        (static_reference_routes.serve_maintenance, "maintenance.html"),
        (static_reference_routes.serve_assistant, "assistant.html"),
        (static_reference_routes.serve_operations, "operations.html"),
    ],
)
def test_static_pages_resolve_from_project_root_when_cwd_changes(tmp_path, monkeypatch, handler, filename) -> None:
    monkeypatch.chdir(tmp_path)
    result = asyncio.run(handler())
    assert result == (static_reference_routes.PROJECT_ROOT / filename).read_text(encoding="utf-8")


def test_action_number_reference_filters_authenticated_request() -> None:
    entries = [{"action_number": "100", "reaction": "Stop"}]
    with (
        patch.object(static_reference_routes, "load_json_entries", return_value=entries) as load,
        patch.object(static_reference_routes, "filter_entries", return_value=entries) as filter_items,
    ):
        result = asyncio.run(static_reference_routes.action_numbers("808d", q="stop", actor=OPERATOR))

    assert result == {"collection": "808d", "total": 1, "entries": entries}
    assert load.call_args.args[0].endswith("action_numbers.json")
    filter_items.assert_called_once_with(
        entries,
        "stop",
        ["action_number", "reaction", "effect", "recovery", "note"],
    )


def test_error_code_reference_and_authentication_branches() -> None:
    denied = asyncio.run(static_reference_routes.error_codes_300500("808d", actor=ANONYMOUS))
    assert denied == {"status": "error", "message": "Not authenticated"}

    entries = [{"hex": "0x1", "code": "300500", "meaning": "Drive"}]
    with (
        patch.object(static_reference_routes, "load_json_entries", return_value=entries),
        patch.object(static_reference_routes, "filter_entries", return_value=entries) as filter_items,
    ):
        result = asyncio.run(static_reference_routes.error_codes_300500("840d", q="drive", actor=ADMIN))

    assert result == {"collection": "840d", "total": 1, "entries": entries}
    filter_items.assert_called_once_with(
        entries,
        "drive",
        ["hex", "code", "meaning", "cause", "remedy", "severity"],
    )
