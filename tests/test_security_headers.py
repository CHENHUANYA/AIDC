import asyncio

import httpx

from security_headers import SecurityHeadersMiddleware


def http_scope(path: str = "/health", query_string: bytes = b"") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }


async def receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def run_middleware(
    production: bool,
    response_headers: list[tuple[bytes, bytes]] | None = None,
    path: str = "/health",
    query_string: bytes = b"",
    response_status: int = 200,
) -> list[dict]:
    messages: list[dict] = []

    async def app(scope, app_receive, send):
        await send({
            "type": "http.response.start",
            "status": response_status,
            "headers": response_headers or [],
        })
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def send(message):
        messages.append(message)

    middleware = SecurityHeadersMiddleware(app, production=production)
    asyncio.run(middleware(http_scope(path, query_string), receive, send))
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
    assert b"default-src 'self'" in headers[b"content-security-policy"]
    assert b"script-src 'self';" in headers[b"content-security-policy"]
    assert b"style-src 'self';" in headers[b"content-security-policy"]
    assert b"font-src 'self';" in headers[b"content-security-policy"]
    assert b"unsafe-inline" not in headers[b"content-security-policy"]
    assert b"sha256-" not in headers[b"content-security-policy"]
    assert b"fonts.googleapis.com" not in headers[b"content-security-policy"]
    assert b"fonts.gstatic.com" not in headers[b"content-security-policy"]
    assert headers[b"cross-origin-opener-policy"] == b"same-origin"
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


def test_sensitive_pages_and_auth_responses_disable_caching() -> None:
    login_headers = response_header_map(run_middleware(production=True, path="/login"))
    auth_headers = response_header_map(run_middleware(production=True, path="/auth/login"))
    metrics_headers = response_header_map(run_middleware(production=True, path="/metrics/runtime"))
    health_headers = response_header_map(run_middleware(production=True, path="/health"))

    assert login_headers[b"cache-control"] == b"no-store"
    assert auth_headers[b"cache-control"] == b"no-store"
    assert metrics_headers[b"cache-control"] == b"no-store"
    assert b"cache-control" not in health_headers


def test_static_assets_receive_safe_version_aware_cache_headers() -> None:
    versioned_headers = response_header_map(
        run_middleware(
            production=True,
            path="/static/css/tokens.css",
            query_string=b"v=quality-2",
            response_headers=[(b"cache-control", b"max-age=0")],
        )
    )
    unversioned_headers = response_header_map(
        run_middleware(production=True, path="/static/js/core/api.js")
    )
    invalid_version_headers = response_header_map(
        run_middleware(
            production=True,
            path="/static/js/core/api.js",
            query_string=b"v=%3Cscript%3E",
        )
    )
    missing_headers = response_header_map(
        run_middleware(
            production=True,
            path="/static/missing.js",
            query_string=b"v=1",
            response_status=404,
        )
    )

    assert versioned_headers[b"cache-control"] == b"public, max-age=31536000, immutable"
    assert unversioned_headers[b"cache-control"] == b"public, max-age=3600, must-revalidate"
    assert invalid_version_headers[b"cache-control"] == b"public, max-age=3600, must-revalidate"
    assert b"cache-control" not in missing_headers


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


def test_main_application_emits_versioned_static_cache_headers() -> None:
    from main import app

    async def get_tokens() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/static/css/tokens.css?v=1")

    response = asyncio.run(get_tokens())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
