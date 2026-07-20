import asyncio

import httpx

from security_headers import SecurityHeadersMiddleware


def http_scope() -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }


async def receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def run_middleware(production: bool, response_headers: list[tuple[bytes, bytes]] | None = None) -> list[dict]:
    messages: list[dict] = []

    async def app(scope, app_receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": response_headers or []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def send(message):
        messages.append(message)

    middleware = SecurityHeadersMiddleware(app, production=production)
    asyncio.run(middleware(http_scope(), receive, send))
    return messages


def response_header_map(messages: list[dict]) -> dict[bytes, bytes]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    return dict(start["headers"])


def test_browser_defense_headers_are_added_without_hsts_in_development() -> None:
    headers = response_header_map(run_middleware(production=False))

    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"permissions-policy"] == b"camera=(), microphone=(), geolocation=()"
    assert b"strict-transport-security" not in headers


def test_production_adds_hsts_and_replaces_downstream_managed_values() -> None:
    headers = response_header_map(
        run_middleware(
            production=True,
            response_headers=[
                (b"content-type", b"application/json"),
                (b"x-frame-options", b"SAMEORIGIN"),
                (b"strict-transport-security", b"max-age=0"),
            ],
        )
    )

    assert headers[b"content-type"] == b"application/json"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"


def test_non_http_scopes_pass_through_unchanged() -> None:
    observed_scopes: list[dict] = []

    async def app(scope, app_receive, send):
        observed_scopes.append(scope)

    async def send(message):
        raise AssertionError("No response expected")

    scope = {"type": "websocket", "path": "/events"}
    middleware = SecurityHeadersMiddleware(app, production=True)
    asyncio.run(middleware(scope, receive, send))

    assert observed_scopes == [scope]


def test_main_application_emits_browser_defense_headers() -> None:
    from main import app

    async def get_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    response = asyncio.run(get_health())

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
