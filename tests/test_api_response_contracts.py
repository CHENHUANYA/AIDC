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


def test_ingest_endpoints_publish_success_and_error_models() -> None:
    spec = app.openapi()
    expected_success_models = {
        ("/v1/{collection_name}/ingest", "post"): "IngestPdfResponse",
        ("/v1/{collection_name}/ingest-text", "post"): "IngestTextResponse",
        ("/v1/{collection_name}/ingest-log", "get"): "IngestLogResponse",
        ("/ingest-log", "get"): "IngestLogResponse",
        ("/collections", "get"): "CollectionsResponse",
        ("/v1/{collection_name}/documents", "get"): "DocumentsResponse",
        ("/v1/{collection_name}/documents/{doc_id}", "delete"): "DocumentDeleteResponse",
        ("/v1/{collection_name}/rebuild/{job_id}", "get"): "RebuildJobResponse",
        ("/v1/{collection_name}/rebuild/{job_id}", "delete"): "RebuildJobResponse",
    }

    for (path, method), model in expected_success_models.items():
        assert response_schema(spec, path, method, "200")["$ref"].endswith(f"/{model}")
        error_statuses = ("400", "401", "403", "404", "410", "503")
        if path != "/v1/{collection_name}/ingest":
            error_statuses += ("409",)
        for status in error_statuses:
            assert response_schema(spec, path, method, status)["$ref"].endswith("/ApiErrorResponse")

    assert response_schema(spec, "/v1/{collection_name}/ingest", "post", "409")["$ref"].endswith(
        "/DuplicateResponse"
    )
    assert response_schema(spec, "/v1/{collection_name}/rebuild", "post", "202")["$ref"].endswith(
        "/RebuildJobResponse"
    )


def test_all_historical_operations_document_standard_error_envelopes() -> None:
    spec = app.openapi()
    operation = spec["paths"]["/work-orders/{order_id}"]["get"]

    for status in ("400", "401", "403", "404", "409", "410", "503"):
        schema = operation["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ApiErrorResponse")


def test_user_and_session_management_endpoints_publish_complete_models() -> None:
    spec = app.openapi()
    expected_success_models = {
        ("/users", "get"): "UsersResponse",
        ("/users", "post"): "UserCreatedResponse",
        ("/users/{user_id}", "patch"): "UserUpdatedResponse",
        ("/users/{user_id}/password", "patch"): "PasswordResetResponse",
        ("/users/{user_id}/sessions", "delete"): "SessionsRevokedResponse",
        ("/sessions", "get"): "SessionsResponse",
        ("/sessions/{token_prefix}", "delete"): "SessionsRevokedResponse",
    }

    for (path, method), model in expected_success_models.items():
        assert response_schema(spec, path, method, "200")["$ref"].endswith(f"/{model}")
        for status in ("400", "401", "403", "404", "409", "410", "503"):
            assert response_schema(spec, path, method, status)["$ref"].endswith("/ApiErrorResponse")

    session_schema = spec["components"]["schemas"]["SessionResponse"]
    assert set(session_schema["required"]) == {"token_prefix", "user_id", "role", "created_at", "expires_at"}
    assert "token" not in session_schema["properties"]
