from typing import Any

from main import app


def response_schema(spec: dict[str, Any], path: str, method: str, status: str) -> dict[str, Any]:
    return spec["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]


def test_stable_endpoints_publish_response_schemas() -> None:
    spec = app.openapi()

    assert response_schema(spec, "/health", "get", "200")["$ref"].endswith("/HealthResponse")
    assert response_schema(spec, "/ready", "get", "200")["$ref"].endswith("/ReadyResponse")
    assert response_schema(spec, "/ready", "get", "503")["$ref"].endswith("/ReadyUnavailableResponse")
    assert response_schema(spec, "/auth/login-config", "get", "200")["$ref"].endswith("/LoginConfigResponse")
    assert response_schema(spec, "/auth/logout", "post", "200")["$ref"].endswith("/StatusOkResponse")


def test_auth_success_and_error_contracts_use_distinct_http_statuses() -> None:
    spec = app.openapi()

    assert response_schema(spec, "/auth/login", "post", "200")["$ref"].endswith("/LoginSuccessResponse")
    assert response_schema(spec, "/auth/me", "get", "200")["$ref"].endswith("/CurrentUserSuccessResponse")
    for path, method in (("/auth/login", "post"), ("/auth/me", "get")):
        assert response_schema(spec, path, method, "401")["$ref"].endswith("/ApiErrorResponse")


def test_public_user_schema_never_exposes_password_material() -> None:
    spec = app.openapi()
    user_schema = spec["components"]["schemas"]["PublicUserResponse"]

    assert "password_hash" not in user_schema["properties"]
    assert {"user_id", "role", "line_scope", "active"}.issubset(user_schema["required"])
