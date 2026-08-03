"""ASGI middleware for browser-facing defensive response headers."""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send


BASE_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (
        b"content-security-policy",
        (
            b"default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            b"form-action 'self'; script-src 'self'; "
            b"style-src 'self'; font-src 'self'; img-src 'self' data:; "
            b"connect-src 'self'; media-src 'self'; worker-src 'none'"
        ),
    ),
)
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000")
NO_STORE_HEADER = (b"cache-control", b"no-store")
STATIC_CACHE_HEADER = (b"cache-control", b"public, max-age=3600, must-revalidate")
VERSIONED_STATIC_CACHE_HEADER = (b"cache-control", b"public, max-age=31536000, immutable")
STATIC_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SENSITIVE_PATHS = {
    "/",
    "/login",
    "/dashboard",
    "/supervisor",
    "/admin",
    "/operator",
    "/maintenance",
    "/assistant",
    "/operations",
    "/metrics/runtime",
}


def production_mode() -> bool:
    return os.getenv("ALARM_RAG_ENV", "development").strip().lower() in {"prod", "production"}


def static_cache_header(scope: Scope, status: int) -> tuple[bytes, bytes] | None:
    path = str(scope.get("path") or "")
    method = str(scope.get("method") or "GET").upper()
    if not path.startswith("/static/") or method not in {"GET", "HEAD"} or status not in {200, 304}:
        return None

    raw_query = bytes(scope.get("query_string") or b"").decode("ascii", errors="ignore")
    version = (parse_qs(raw_query, keep_blank_values=False).get("v") or [""])[0]
    if STATIC_VERSION_RE.fullmatch(version):
        return VERSIONED_STATIC_CACHE_HEADER
    return STATIC_CACHE_HEADER


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, production: bool | None = None) -> None:
        self.app = app
        self.production = production_mode() if production is None else production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        no_store = path in SENSITIVE_PATHS or path.startswith("/auth/")

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                cache_header = (
                    NO_STORE_HEADER
                    if no_store
                    else static_cache_header(scope, int(message.get("status") or 0))
                )
                configured_headers = (
                    BASE_SECURITY_HEADERS
                    + ((HSTS_HEADER,) if self.production else ())
                    + ((cache_header,) if cache_header else ())
                )
                managed_names = {name for name, _ in configured_headers}
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in managed_names
                ]
                headers.extend(configured_headers)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
