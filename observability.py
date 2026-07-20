from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("alarm_rag_request_id", default="")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            record.request_id = current_request_id()
        return True


class JsonLogFormatter(logging.Formatter):
    fields = (
        "request_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "error_type",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.fields:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("alarm_rag")
    if getattr(logger, "_alarm_rag_configured", False):
        return logger

    level_name = os.getenv("ALARM_RAG_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    if os.getenv("ALARM_RAG_LOG_FORMAT", "json").strip().lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    setattr(logger, "_alarm_rag_configured", True)
    return logger


def current_request_id() -> str:
    return request_id_context.get()


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, logger: logging.Logger | None = None) -> None:
        self.app = app
        self.logger = logger or logging.getLogger("alarm_rag.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = dict(scope.get("headers", []))
        supplied_id = request_headers.get(REQUEST_ID_HEADER, b"").decode("ascii", errors="ignore")
        request_id = normalize_request_id(supplied_id)
        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        status_code = 500
        started = time.monotonic()
        token = request_id_context.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            self.logger.exception(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        else:
            self.logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                },
            )
        finally:
            request_id_context.reset(token)
