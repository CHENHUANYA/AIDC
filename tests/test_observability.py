import asyncio
import io
import json
import logging
import re

from observability import (
    JsonLogFormatter,
    RequestContextFilter,
    RequestLoggingMiddleware,
    current_request_id,
    normalize_request_id,
    request_id_context,
)


def _http_scope(request_id: bytes | None = None):
    headers = [] if request_id is None else [(b"x-request-id", request_id)]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def _logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(f"test.observability.{id(stream)}")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def test_request_middleware_preserves_valid_request_id_and_logs_json():
    messages = []
    observed_request_ids = []
    stream = io.StringIO()

    async def app(scope, receive, send):
        observed_request_ids.append(current_request_id())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    middleware = RequestLoggingMiddleware(app, logger=_logger(stream))
    asyncio.run(middleware(_http_scope(b"pilot-request-123"), receive, send))

    response_start = next(message for message in messages if message["type"] == "http.response.start")
    assert (b"x-request-id", b"pilot-request-123") in response_start["headers"]
    assert observed_request_ids == ["pilot-request-123"]
    assert current_request_id() == ""
    payload = json.loads(stream.getvalue())
    assert payload["message"] == "http_request"
    assert payload["request_id"] == "pilot-request-123"
    assert payload["method"] == "GET"
    assert payload["path"] == "/health"
    assert payload["status_code"] == 204
    assert payload["duration_ms"] >= 0


def test_invalid_request_id_is_replaced_with_safe_generated_value():
    generated = normalize_request_id("unsafe\nlog-value")

    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    assert normalize_request_id("safe:value-1.2") == "safe:value-1.2"
    assert normalize_request_id("x" * 129) != "x" * 129


def test_request_context_filter_correlates_internal_logs():
    record = logging.LogRecord("alarm_rag.test", logging.INFO, __file__, 1, "internal", (), None)
    token = request_id_context.set("correlated-123")
    try:
        assert RequestContextFilter().filter(record) is True
    finally:
        request_id_context.reset(token)

    assert record.request_id == "correlated-123"


def test_request_middleware_logs_exception_and_resets_context():
    stream = io.StringIO()

    async def app(scope, receive, send):
        raise RuntimeError("test failure")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        raise AssertionError("No response should be sent")

    middleware = RequestLoggingMiddleware(app, logger=_logger(stream))
    try:
        asyncio.run(middleware(_http_scope(b"failure-123"), receive, send))
    except RuntimeError as exc:
        assert str(exc) == "test failure"
    else:
        raise AssertionError("RuntimeError was not propagated")

    payload = json.loads(stream.getvalue())
    assert payload["message"] == "http_request_failed"
    assert payload["request_id"] == "failure-123"
    assert payload["status_code"] == 500
    assert payload["error_type"] == "RuntimeError"
    assert "RuntimeError: test failure" in payload["exception"]
    assert current_request_id() == ""
