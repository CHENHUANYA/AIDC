"""ASGI middleware for browser-facing defensive response headers."""

from __future__ import annotations

import os

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
            b"style-src 'self' https://fonts.googleapis.com; "
            b"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            b"connect-src 'self'; media-src 'self'; worker-src 'none'"
        ),
    ),
)
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000")
NO_STORE_HEADER = (b"cache-control", b"no-store")
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
        configured_headers = (
            BASE_SECURITY_HEADERS
            + ((HSTS_HEADER,) if self.production else ())
            + ((NO_STORE_HEADER,) if no_store else ())
        )
        managed_names = {name for name, _ in configured_headers}

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in managed_names
                ]
                headers.extend(configured_headers)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
