from __future__ import annotations

import json
from collections.abc import Iterable
from http.cookies import SimpleCookie

from config_values import env_float, env_int
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    pass


def _header_value(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes:
    lowered = name.lower()
    return next((value for key, value in headers if key.lower() == lowered), b"")


def request_body_limit(path: str) -> int:
    """Return a pre-parser byte limit for the requested endpoint."""
    if path.startswith("/v1/") and path.endswith("/ingest"):
        pdf_mb = env_float("ALARM_RAG_PDF_UPLOAD_MAX_MB", 50, minimum=0.000001)
        multipart_overhead = env_int("ALARM_RAG_MULTIPART_OVERHEAD_BYTES", 64 * 1024, minimum=4096)
        return int(pdf_mb * 1024 * 1024) + multipart_overhead
    if path.startswith("/v1/") and path.endswith("/ingest-text"):
        return env_int("ALARM_RAG_INGEST_TEXT_REQUEST_MAX_BYTES", 256 * 1024, minimum=1024)
    if path.endswith("/chat/completions") or path.endswith("/chat"):
        return env_int("ALARM_RAG_CHAT_REQUEST_MAX_BYTES", 512 * 1024, minimum=1024)
    return env_int("ALARM_RAG_REQUEST_MAX_BYTES", 2 * 1024 * 1024, minimum=1024)


class RequestBodyLimitMiddleware:
    """Reject oversized fixed-length and chunked bodies before framework parsing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if scope.get("method") == "POST" and path.startswith("/v1/") and path.endswith(("/ingest", "/ingest-text")):
            admission_status = self._ingest_admission_status(scope)
            if admission_status is not None:
                await self._reject_auth(send, admission_status)
                return

        limit = request_body_limit(path)
        content_length = _header_value(scope.get("headers", []), b"content-length")
        try:
            declared = int(content_length) if content_length else None
        except ValueError:
            declared = None
        if declared is not None and declared > limit:
            await self._reject(send, limit)
            return

        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            {"status": "error", "message": "Request body too large", "limit_bytes": limit},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    @staticmethod
    def _ingest_admission_status(scope: Scope) -> int | None:
        from auth import SESSION_COOKIE_NAME, actor_from_credentials, actor_id, is_admin

        headers = scope.get("headers", [])
        authorization = _header_value(headers, b"authorization").decode("latin-1") or None
        cookie_header = _header_value(headers, b"cookie").decode("latin-1")
        session_cookie = None
        if cookie_header:
            cookies = SimpleCookie()
            try:
                cookies.load(cookie_header)
                morsel = cookies.get(SESSION_COOKIE_NAME)
                session_cookie = morsel.value if morsel else None
            except Exception:
                session_cookie = None
        if not authorization and not session_cookie:
            return 401
        actor = actor_from_credentials(authorization, session_cookie) or {}
        if not actor_id(actor):
            return 401
        if not is_admin(actor):
            return 403
        return None

    @staticmethod
    async def _reject_auth(send: Send, status: int) -> None:
        message = "Not authenticated" if status == 401 else "Permission denied"
        body = json.dumps({"status": "error", "message": message}, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})
