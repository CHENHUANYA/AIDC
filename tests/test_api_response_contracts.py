from typing import Any
from unittest.mock import patch

import issues
import work_orders
from api_schemas import (
    IssueMutationResponse,
    IssueResponse,
    ModelsResponse,
    OpenAIChatResponse,
    WorkOrderResponse,
    WorkOrderSuccessResponse,
)
from app_context import make_openai_response
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


def test_issue_endpoints_publish_complete_success_and_error_models() -> None:
    spec = app.openapi()
    expected_success_models = {
        ("/issues", "post"): "IssueMutationResponse",
        ("/issues", "get"): "IssuesResponse",
        ("/issues/page", "get"): "IssuesPageResponse",
        ("/issues/stats", "get"): "IssueStatsResponse",
        ("/issues/{issue_id}", "get"): "IssueSuccessResponse",
        ("/issues/{issue_id}/history", "get"): "IssueHistoryResponse",
        ("/issues/{issue_id}", "patch"): "IssueMutationResponse",
        ("/issues/{issue_id}/escalate", "post"): "IssueEscalatedResponse",
    }

    for (path, method), model in expected_success_models.items():
        assert response_schema(spec, path, method, "200")["$ref"].endswith(f"/{model}")
        for status in ("400", "401", "403", "404", "409", "410", "503"):
            assert response_schema(spec, path, method, status)["$ref"].endswith("/ApiErrorResponse")

    issue_schema = spec["components"]["schemas"]["IssueResponse"]
    assert {
        "issue_id",
        "severity",
        "status",
        "operator_notes",
        "issue_history",
        "version",
    }.issubset(issue_schema["required"])
    history_change = spec["components"]["schemas"]["HistoryChangeResponse"]
    assert {"field", "from", "to"} == set(history_change["required"])


def test_issue_models_validate_current_json_record_shape() -> None:
    with (
        patch.object(issues, "_load_issues", return_value=[]),
        patch.object(issues, "_save_issues"),
    ):
        issue = issues.create_issue_dict(
            machine_id="M-1",
            description="Drive alarm",
            line_id="LINE-A",
            alarm_code="3000",
            created_by="operator01",
        )

    validated = IssueResponse.model_validate(issue)
    envelope = IssueMutationResponse.model_validate({"status": "ok", "issue": issue, "work_order": None})
    assert validated.issue_id.startswith("ISS-")
    assert envelope.issue.version == 1


def test_work_order_endpoints_publish_complete_success_and_error_models() -> None:
    spec = app.openapi()
    expected_success_models = {
        ("/work-orders", "post"): "WorkOrderSuccessResponse",
        ("/work-orders", "get"): "WorkOrdersResponse",
        ("/work-orders/page", "get"): "WorkOrdersPageResponse",
        ("/work-orders/stats", "get"): "WorkOrderStatsResponse",
        ("/work-orders/archive", "get"): "WorkOrderArchiveResponse",
        ("/work-orders/{order_id}", "get"): "WorkOrderSuccessResponse",
        ("/work-orders/{order_id}/history", "get"): "WorkOrderHistoryResponse",
        ("/work-orders/{order_id}", "patch"): "WorkOrderMutationResponse",
        ("/work-orders/{order_id}", "delete"): "WorkOrderDeleteResponse",
        ("/work-orders/{order_id}/knowledge-review", "post"): "KnowledgeReviewResponse",
        ("/work-orders/import-excel", "post"): "WorkOrderImportResponse",
    }

    for (path, method), model in expected_success_models.items():
        assert response_schema(spec, path, method, "200")["$ref"].endswith(f"/{model}")
        for status in ("400", "401", "403", "404", "409", "410", "503"):
            expected_error = (
                "KnowledgeReviewErrorResponse"
                if path == "/work-orders/{order_id}/knowledge-review" and status == "400"
                else "ApiErrorResponse"
            )
            assert response_schema(spec, path, method, status)["$ref"].endswith(f"/{expected_error}")

    order_schema = spec["components"]["schemas"]["WorkOrderResponse"]
    assert {
        "id",
        "status",
        "priority",
        "root_cause",
        "repair_action",
        "kb_review_status",
        "work_order_history",
        "version",
    }.issubset(order_schema["required"])
    assert "archive_file" not in order_schema["required"]
    assert "deleted_at" not in order_schema["required"]


def test_work_order_models_validate_current_json_record_shape() -> None:
    with (
        patch.object(work_orders, "postgres_store_enabled", return_value=False),
        patch.object(work_orders, "_load_orders", return_value=[]),
        patch.object(work_orders, "_save_orders"),
    ):
        order = work_orders.create_order_dict(
            alarm_code="3000",
            machine_id="M-1",
            created_by="maintenance01",
        )

    validated = WorkOrderResponse.model_validate(order)
    envelope = WorkOrderSuccessResponse.model_validate({"status": "ok", "order": order})
    assert validated.status == "pending"
    assert validated.kb_review_status == "not_ready"
    assert envelope.order.version == 1


def test_remaining_json_endpoints_publish_named_success_models() -> None:
    spec = app.openapi()
    expected_success_models = {
        ("/trigger-alarm", "post"): "AlarmTriggerResponse",
        ("/pending-alarms", "get"): "PendingAlarmsResponse",
        ("/stats/alarms", "get"): "AlarmStatsResponse",
        ("/stats/alarms", "delete"): "StatusOkResponse",
        ("/feedback", "post"): "StatusOkResponse",
        ("/feedback/stats", "get"): "FeedbackStatsResponse",
        ("/stats/queries", "get"): "QueryStatsResponse",
        ("/stats/errors", "get"): "ErrorStatsResponse",
        ("/system-settings", "get"): "SystemSettingsEnvelope",
        ("/system-settings", "patch"): "SystemSettingsEnvelope",
        ("/v1/{collection_name}/reference/action-numbers", "get"): "ActionNumbersResponse",
        ("/v1/{collection_name}/reference/error-codes", "get"): "ErrorCodesResponse",
        ("/v1/chat/completions", "post"): "OpenAIChatResponse",
        ("/v1/free/chat/completions", "post"): "OpenAIChatResponse",
        ("/v1/{collection_name}/chat/completions", "post"): "OpenAIChatResponse",
        ("/v1/{collection_name}/retrieve", "get"): "RetrieveResponse",
        ("/v1/{collection_name}/chat", "post"): "OpenAIChatResponse",
        ("/rag/answers/{answer_id}", "get"): "RagAnswerEnvelope",
        ("/v1/{collection_name}/lookup", "get"): "LookupResponse",
        ("/v1/{collection_name}/models", "get"): "ModelsResponse",
        ("/v1/models", "get"): "ModelsResponse",
    }

    for (path, method), model in expected_success_models.items():
        assert response_schema(spec, path, method, "200")["$ref"].endswith(f"/{model}")

    for path in (
        "/v1/chat/completions",
        "/v1/{collection_name}/chat/completions",
        "/v1/{collection_name}/chat",
    ):
        content = spec["paths"][path]["post"]["responses"]["200"]["content"]
        assert content["text/event-stream"]["schema"] == {"type": "string"}


def test_every_json_success_response_has_an_openapi_schema() -> None:
    spec = app.openapi()
    missing = []
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            json_response = operation.get("responses", {}).get("200", {}).get("content", {}).get("application/json")
            if json_response is not None and not json_response.get("schema"):
                missing.append(f"{method.upper()} {path}")

    assert missing == []


def test_openai_and_model_list_contracts_validate_current_payloads() -> None:
    chat = OpenAIChatResponse.model_validate(make_openai_response("safe response"))
    models = ModelsResponse.model_validate(
        {"object": "list", "data": [{"id": "alarm-rag", "object": "model", "owned_by": "local"}]}
    )

    assert chat.choices[0].message.content == "safe response"
    assert models.data[0].id == "alarm-rag"
