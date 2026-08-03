import asyncio
from pathlib import Path

import httpx
from starlette.middleware.gzip import GZipMiddleware


ROOT = Path(__file__).resolve().parents[1]


def test_main_application_compresses_large_static_assets() -> None:
    from main import app

    async def get_tokens() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(
                "/static/css/tokens.css?v=1",
                headers={"accept-encoding": "gzip"},
            )

    response = asyncio.run(get_tokens())

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.text == (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")


def test_gzip_middleware_does_not_compress_event_streams() -> None:
    messages: list[dict] = []
    event = b"data: " + (b"x" * 2048) + b"\n\n"

    async def app(scope, receive, send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": event, "more_body": False})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/events",
        "headers": [(b"accept-encoding", b"gzip")],
    }
    middleware = GZipMiddleware(app, minimum_size=1, compresslevel=6)
    asyncio.run(middleware(scope, receive, send))

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = dict(start["headers"])

    assert b"content-encoding" not in headers
    assert body == event
