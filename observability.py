from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config_values import env_int


REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("alarm_rag_request_id", default="")
HTTP_DURATION_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000)
RAG_PROVIDERS = {"ollama", "school", "unavailable", "unknown"}
RAG_OUTCOMES = {"complete", "fallback", "interrupted", "unavailable"}


class RuntimeMetrics:
    """Bounded, process-local operational counters without sensitive labels."""

    def __init__(self, *, slow_request_ms: int | None = None) -> None:
        self._lock = threading.Lock()
        configured_slow_request_ms = (
            env_int("ALARM_RAG_SLOW_REQUEST_MS", 1000, minimum=1, maximum=3_600_000)
            if slow_request_ms is None
            else slow_request_ms
        )
        self.slow_request_ms = max(int(configured_slow_request_ms), 1)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._started = time.monotonic()
            self._http_total = 0
            self._http_errors = 0
            self._http_server_errors = 0
            self._http_timeouts = 0
            self._http_slow = 0
            self._http_duration_ms = 0.0
            self._http_max_ms = 0.0
            self._http_buckets = {limit: 0 for limit in HTTP_DURATION_BUCKETS_MS}
            self._http_routes: dict[tuple[str, str], dict[str, float | int]] = {}
            self._auth = {"success": 0, "failure": 0, "throttled": 0}
            self._rag_requests = 0
            self._rag_errors = 0
            self._rag_streaming = 0
            self._rag_retrieval_ms = 0.0
            self._rag_model_ms = 0.0
            self._rag_total_ms = 0.0
            self._rag_max_ms = 0.0
            self._rag_providers = {provider: 0 for provider in sorted(RAG_PROVIDERS)}
            self._rag_outcomes = {outcome: 0 for outcome in sorted(RAG_OUTCOMES)}

    def record_http(
        self,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        *,
        timed_out: bool = False,
    ) -> None:
        safe_method = method.upper() if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        safe_route = route if route.startswith("/") and len(route) <= 160 else "<unmatched>"
        duration = max(float(duration_ms), 0.0)
        with self._lock:
            self._http_total += 1
            self._http_duration_ms += duration
            self._http_max_ms = max(self._http_max_ms, duration)
            if status_code >= 400:
                self._http_errors += 1
            if status_code >= 500:
                self._http_server_errors += 1
            if timed_out or status_code in {408, 504}:
                self._http_timeouts += 1
            if duration >= self.slow_request_ms:
                self._http_slow += 1
            for limit in HTTP_DURATION_BUCKETS_MS:
                if duration <= limit:
                    self._http_buckets[limit] += 1
            key = (safe_method, safe_route)
            route_metrics = self._http_routes.setdefault(
                key,
                {"count": 0, "errors": 0, "duration_ms": 0.0, "max_ms": 0.0},
            )
            route_metrics["count"] += 1
            route_metrics["duration_ms"] += duration
            route_metrics["max_ms"] = max(float(route_metrics["max_ms"]), duration)
            if status_code >= 400:
                route_metrics["errors"] += 1

    def record_auth(self, outcome: str) -> None:
        safe_outcome = outcome if outcome in self._auth else "failure"
        with self._lock:
            self._auth[safe_outcome] += 1

    def record_rag(
        self,
        *,
        retrieval_ms: float,
        model_ms: float,
        total_ms: float,
        provider: str,
        outcome: str,
        streaming: bool,
    ) -> None:
        safe_provider = provider if provider in RAG_PROVIDERS else "unknown"
        safe_outcome = outcome if outcome in RAG_OUTCOMES else "unavailable"
        retrieval = max(float(retrieval_ms), 0.0)
        model = max(float(model_ms), 0.0)
        total = max(float(total_ms), 0.0)
        with self._lock:
            self._rag_requests += 1
            self._rag_retrieval_ms += retrieval
            self._rag_model_ms += model
            self._rag_total_ms += total
            self._rag_max_ms = max(self._rag_max_ms, total)
            self._rag_streaming += int(streaming)
            self._rag_errors += int(safe_outcome != "complete")
            self._rag_providers[safe_provider] += 1
            self._rag_outcomes[safe_outcome] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            http_total = self._http_total
            rag_total = self._rag_requests
            routes = []
            for (method, route), values in sorted(self._http_routes.items()):
                count = int(values["count"])
                routes.append({
                    "method": method,
                    "route": route,
                    "count": count,
                    "errors": int(values["errors"]),
                    "avg_ms": round(float(values["duration_ms"]) / count, 3) if count else 0.0,
                    "max_ms": round(float(values["max_ms"]), 3),
                })
            return {
                "uptime_seconds": round(time.monotonic() - self._started, 3),
                "http": {
                    "requests": http_total,
                    "errors": self._http_errors,
                    "server_errors": self._http_server_errors,
                    "timeouts": self._http_timeouts,
                    "slow_requests": self._http_slow,
                    "slow_request_ms": self.slow_request_ms,
                    "avg_ms": round(self._http_duration_ms / http_total, 3) if http_total else 0.0,
                    "max_ms": round(self._http_max_ms, 3),
                    "duration_buckets": {
                        f"le_{limit}_ms": count
                        for limit, count in self._http_buckets.items()
                    },
                    "routes": routes,
                },
                "auth": {
                    "login_attempts": sum(self._auth.values()),
                    "login_successes": self._auth["success"],
                    "login_failures": self._auth["failure"],
                    "throttle_triggers": self._auth["throttled"],
                },
                "rag": {
                    "requests": rag_total,
                    "errors": self._rag_errors,
                    "streaming_requests": self._rag_streaming,
                    "avg_retrieval_ms": round(self._rag_retrieval_ms / rag_total, 3) if rag_total else 0.0,
                    "avg_model_ms": round(self._rag_model_ms / rag_total, 3) if rag_total else 0.0,
                    "avg_total_ms": round(self._rag_total_ms / rag_total, 3) if rag_total else 0.0,
                    "max_total_ms": round(self._rag_max_ms, 3),
                    "providers": dict(self._rag_providers),
                    "outcomes": dict(self._rag_outcomes),
                },
            }


runtime_metrics = RuntimeMetrics()


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


def metric_route(scope: Scope) -> str:
    route = getattr(scope.get("route"), "path", "")
    if isinstance(route, str) and route.startswith("/"):
        return route
    path = str(scope.get("path") or "")
    if path.startswith("/static/"):
        return "/static/{asset}"
    return "<unmatched>"


class RequestLoggingMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        logger: logging.Logger | None = None,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        self.app = app
        self.logger = logger or logging.getLogger("alarm_rag.request")
        self.metrics = metrics or runtime_metrics

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
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            self.metrics.record_http(
                method,
                metric_route(scope),
                500,
                duration_ms,
                timed_out=isinstance(exc, TimeoutError),
            )
            self.logger.exception(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        else:
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            self.metrics.record_http(
                method,
                metric_route(scope),
                status_code,
                duration_ms,
            )
            self.logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            request_id_context.reset(token)
