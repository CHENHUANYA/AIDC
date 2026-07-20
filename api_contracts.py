"""HTTP boundary contracts for legacy JSON endpoints.

Older handlers return ``{"status": "error"}`` dictionaries because they are
also called directly by a few internal workflows.  This middleware preserves
that Python-level compatibility while ensuring API clients receive meaningful
HTTP status codes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def response_status_code(payload: Mapping[str, Any]) -> int | None:
    """Map a legacy response envelope to its HTTP status code."""
    status = str(payload.get("status") or "").strip().lower()
    message = str(payload.get("message") or "").strip().lower()
    if status == "accepted":
        return 202
    if status == "duplicate":
        return 409
    if status == "not_found":
        return 404
    if status != "error":
        return None
    if message == "not authenticated" or "invalid username or password" in message:
        return 401
    if "permission denied" in message:
        return 403
    if "not found" in message or message == "unknown rag answer id":
        return 404
    if " is deleted" in message:
        return 410
    if any(marker in message for marker in ("stale", "changed since", "updated by another", "ambiguous")):
        return 409
    if "not ready" in message or message.startswith("failed to clear") or message.startswith("unable to load"):
        return 503
    return 400


class ApiContractMiddleware:
    """Translate legacy JSON error envelopes after endpoint serialization."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        body_messages: list[Message] = []
        buffering_json = False

        async def send_with_contract(message: Message) -> None:
            nonlocal start_message, buffering_json
            if message["type"] == "http.response.start":
                content_type = next(
                    (
                        value.lower()
                        for name, value in message.get("headers", [])
                        if name.lower() == b"content-type"
                    ),
                    b"",
                )
                buffering_json = int(message["status"]) < 300 and b"application/json" in content_type
                if buffering_json:
                    start_message = message
                    return
                await send(message)
                return

            if message["type"] != "http.response.body" or not buffering_json:
                await send(message)
                return

            body_messages.append(message)
            if message.get("more_body", False):
                return

            body = b"".join(part.get("body", b"") for part in body_messages)
            status_code: int | None = None
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    status_code = response_status_code(payload)
                    if str(payload.get("status") or "").lower() == "not_found":
                        payload["status"] = "error"
                        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

            assert start_message is not None
            if status_code is not None:
                start_message["status"] = status_code
                headers = [
                    (name, value)
                    for name, value in start_message.get("headers", [])
                    if name.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode("ascii")))
                if status_code == 401 and not any(name.lower() == b"www-authenticate" for name, _ in headers):
                    headers.append((b"www-authenticate", b"Bearer"))
                start_message["headers"] = headers

            await send(start_message)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, send_with_contract)
