"""Stable response contracts for externally consumed API endpoints."""

from typing import Any, Literal

from pydantic import BaseModel


class ApiErrorResponse(BaseModel):
    status: Literal["error"]
    message: str


class StatusOkResponse(BaseModel):
    status: Literal["ok"]


class PublicUserResponse(BaseModel):
    user_id: str
    name: str
    role: str
    line_scope: list[str]
    team: str
    active: bool
    created_at: str
    updated_at: str


class BootstrapUserResponse(BaseModel):
    user_id: str
    name: str
    role: str
    line_scope: list[str]
    team: str


class LoginSuccessResponse(StatusOkResponse):
    token: str
    expires_at: str
    user: PublicUserResponse


class LoginConfigResponse(StatusOkResponse):
    production: bool
    initial_password_configured: bool
    bootstrap_users: list[BootstrapUserResponse]


class CurrentUserSuccessResponse(StatusOkResponse):
    user: PublicUserResponse


class UsersResponse(BaseModel):
    users: list[PublicUserResponse]


class UserCreatedResponse(StatusOkResponse):
    user: PublicUserResponse


class UserUpdatedResponse(UserCreatedResponse):
    sessions_revoked: int


class PasswordResetResponse(UserCreatedResponse):
    sessions_revoked: bool


class SessionResponse(BaseModel):
    token_prefix: str
    user_id: str
    role: str
    created_at: str
    expires_at: str


class SessionsResponse(StatusOkResponse):
    total: int
    sessions: list[SessionResponse]


class SessionsRevokedResponse(StatusOkResponse):
    revoked: int


class CollectionHealthResponse(BaseModel):
    ready: bool
    alarms_indexed: int
    retrieval_runtime: dict[str, Any]


class HealthResponse(StatusOkResponse):
    llm_provider: str
    ollama_url: str
    ollama_model: str
    school_api_base_url: str
    school_api_model: str
    school_api_fallback_to_ollama: bool
    last_llm_source: str
    model_cache: dict[str, Any]
    collections: dict[str, CollectionHealthResponse]


class ReadyChecksResponse(BaseModel):
    database: Literal["ok", "not-required"]


class ReadyResponse(StatusOkResponse):
    checks: ReadyChecksResponse


class ReadyUnavailableChecksResponse(BaseModel):
    database: Literal["unavailable"]


class ReadyUnavailableResponse(BaseModel):
    status: Literal["unavailable"]
    checks: ReadyUnavailableChecksResponse


class DuplicateResponse(BaseModel):
    status: Literal["duplicate"]
    message: str
    doc_id: str | None = None
    source_hash: str = ""


class IngestPdfResponse(StatusOkResponse):
    collection: str
    filename: str
    doc_id: str
    source_hash: str
    alarms_added: int
    general_added: int
    total_added: int
    total_in_collection: int


class IngestTextResponse(StatusOkResponse):
    collection: str
    doc_id: str
    sections_added: int
    total_in_collection: int


class IngestLogResponse(BaseModel):
    collection: str | None = None
    entries: list[dict[str, Any]]


class CollectionsResponse(BaseModel):
    collections: list[dict[str, Any]]


class DocumentsResponse(BaseModel):
    collection: str
    summary: dict[str, Any]
    documents: list[dict[str, Any]]


class DocumentDeleteResponse(StatusOkResponse):
    removed_sections: int
    remaining: int


class RebuildSyncResponse(StatusOkResponse):
    sections: int


class RebuildJobResponse(BaseModel):
    status: Literal["ok", "accepted"]
    job_id: str
    collection: str
    state: str
    phase: str
    processed_sections: int
    total_sections: int
    sections: int
    percent: float
    error: str
    created_at: str
    updated_at: str
    finished_at: str


API_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "Invalid request"},
    401: {"model": ApiErrorResponse, "description": "Authentication required"},
    403: {"model": ApiErrorResponse, "description": "Permission denied"},
    404: {"model": ApiErrorResponse, "description": "Resource not found"},
    409: {"model": ApiErrorResponse, "description": "Duplicate or concurrent update"},
    410: {"model": ApiErrorResponse, "description": "Resource was deleted"},
    503: {"model": ApiErrorResponse, "description": "Dependency not ready"},
}
