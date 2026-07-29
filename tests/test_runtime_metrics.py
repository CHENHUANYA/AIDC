import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from observability import RequestLoggingMiddleware, RuntimeMetrics
from routes import stats_routes


def test_runtime_metrics_aggregate_without_sensitive_labels() -> None:
    metrics = RuntimeMetrics(slow_request_ms=100)
    metrics.record_http("GET", "/issues/{issue_id}", 200, 25)
    metrics.record_http("GET", "/issues/{issue_id}", 503, 250, timed_out=True)
    metrics.record_auth("success")
    metrics.record_auth("failure")
    metrics.record_auth("throttled")
    metrics.record_rag(
        retrieval_ms=12,
        model_ms=80,
        total_ms=95,
        provider="ollama",
        outcome="complete",
        streaming=False,
    )

    snapshot = metrics.snapshot()

    assert snapshot["http"]["requests"] == 2
    assert snapshot["http"]["errors"] == 1
    assert snapshot["http"]["server_errors"] == 1
    assert snapshot["http"]["timeouts"] == 1
    assert snapshot["http"]["slow_requests"] == 1
    assert snapshot["http"]["routes"] == [{
        "method": "GET",
        "route": "/issues/{issue_id}",
        "count": 2,
        "errors": 1,
        "avg_ms": 137.5,
        "max_ms": 250.0,
    }]
    assert snapshot["auth"] == {
        "login_attempts": 3,
        "login_successes": 1,
        "login_failures": 1,
        "throttle_triggers": 1,
    }
    assert snapshot["rag"]["requests"] == 1
    assert snapshot["rag"]["providers"]["ollama"] == 1
    assert snapshot["rag"]["outcomes"]["complete"] == 1
    serialized = json.dumps(snapshot)
    assert "username" not in serialized
    assert "password" not in serialized
    assert "query" not in serialized


def test_runtime_metrics_invalid_slow_request_setting_uses_safe_default() -> None:
    with patch.dict(os.environ, {"ALARM_RAG_SLOW_REQUEST_MS": "not-a-number"}):
        metrics = RuntimeMetrics()

    assert metrics.slow_request_ms == 1000


def test_request_middleware_records_route_template_instead_of_raw_identifier() -> None:
    metrics = RuntimeMetrics(slow_request_ms=10_000)
    messages = []

    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/issues/{issue_id}")
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/issues/ISS-sensitive-value",
        "headers": [],
    }
    asyncio.run(RequestLoggingMiddleware(app, metrics=metrics)(scope, receive, send))

    routes = metrics.snapshot()["http"]["routes"]
    assert routes[0]["route"] == "/issues/{issue_id}"
    assert "ISS-sensitive-value" not in json.dumps(routes)


def test_runtime_metrics_endpoint_requires_admin_and_adds_pool_snapshot() -> None:
    supervisor = {"user_id": "supervisor01", "role": "supervisor"}
    admin = {"user_id": "admin01", "role": "admin"}

    denied = asyncio.run(stats_routes.runtime_metrics_snapshot(actor=supervisor))
    assert denied == {"status": "error", "message": "Permission denied"}

    with (
        patch.object(stats_routes.runtime_metrics, "snapshot", return_value={"http": {}, "auth": {}, "rag": {}}),
        patch.object(stats_routes, "_postgres_pool_metrics", return_value={"enabled": False, "status": "not-required"}),
    ):
        result = asyncio.run(stats_routes.runtime_metrics_snapshot(actor=admin))

    assert result["status"] == "ok"
    assert result["postgres"] == {"enabled": False, "status": "not-required"}
    assert set(result) == {"status", "generated_at", "http", "auth", "rag", "postgres"}


def test_postgres_pool_snapshot_exposes_counts_without_connection_details() -> None:
    pool = Mock()
    pool.size.return_value = 5
    pool.checkedin.return_value = 3
    pool.checkedout.return_value = 2
    pool.overflow.return_value = 1
    engine = SimpleNamespace(pool=pool)

    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes, "get_engine", return_value=engine),
    ):
        result = stats_routes._postgres_pool_metrics()

    assert result == {
        "enabled": True,
        "status": "ok",
        "pool_size": 5,
        "checked_in": 3,
        "checked_out": 2,
        "overflow": 1,
    }
    assert "url" not in result
    assert "password" not in result
