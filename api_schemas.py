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
