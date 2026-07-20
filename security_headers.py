"""ASGI middleware for browser-facing defensive response headers."""

from __future__ import annotations

import os

from starlette.types import ASGIApp, Message, Receive, Scope, Send


BASE_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)
HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000")


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

        configured_headers = BASE_SECURITY_HEADERS + ((HSTS_HEADER,) if self.production else ())
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
