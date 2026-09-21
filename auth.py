import hashlib
import json
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api_schemas import (
    API_ERROR_RESPONSES,
    ApiErrorResponse,
    CurrentUserSuccessResponse,
    LoginConfigResponse,
    LoginSuccessResponse,
    PasswordResetResponse,
    SessionsResponse,
    SessionsRevokedResponse,
    StatusOkResponse,
    UserCreatedResponse,
    UserUpdatedResponse,
    UsersResponse,
)
from config_values import env_int
from observability import runtime_metrics
from repositories.postgres_auth import (
    ConcurrentUserUpdateError,
    CredentialChangedError,
    PostgresLoginThrottleRepository,
    PostgresSessionRepository,
    PostgresUserRepository,
    SessionCapacityError,
)
from repositories.postgres_content import PostgresSettingsRepository
from repositories.runtime import postgres_store_enabled
from secret_values import secret_value
from services import account_management, auth_sessions
from services.json_file_store import exclusive_file_lock, write_json_atomic
from services.system_settings import load_effective_settings, session_hours_override
from services.login_throttle import (
    LoginThrottleLimits,
    LoginThrottleState,
    discard_key,
    normalize_login_key,
    prune_state,
    record_failure as record_local_login_failure,
    retry_after as local_login_retry_after,
)


BOOTSTRAP_USERS: Dict[str, dict] = {
    "operator01": {
        "user_id": "operator01",
        "name": "Operator LINE-A",
        "role": "operator",
        "line_scope": ["LINE-A"],
        "team": "LINE-A-DAY",
    },
    "operator02": {
        "user_id": "operator02",
        "name": "Operator LINE-B",
        "role": "operator",
        "line_scope": ["LINE-B"],
        "team": "LINE-B-DAY",
    },
    "maintenance01": {
        "user_id": "maintenance01",
        "name": "Maintenance Tech",
        "role": "maintenance",
        "line_scope": ["LINE-A", "LINE-B"],
        "team": "maintenance",
    },
    "supervisor01": {
        "user_id": "supervisor01",
        "name": "Production Supervisor",
        "role": "supervisor",
        "line_scope": ["*"],
        "team": "supervisor",
    },
    "admin01": {
        "user_id": "admin01",
        "name": "System Admin",
        "role": "admin",
        "line_scope": ["*"],
        "team": "admin",
    },
}

DB_DIR = os.getenv("DB_PATH", "./alarm_db")
USER_FILE = os.path.join(DB_DIR, "users.json")
SESSION_FILE = os.path.join(DB_DIR, "sessions.json")
DEFAULT_ADMIN_INITIAL_PASSWORD = "change-me-now"
PLACEHOLDER_INITIAL_PASSWORDS = {DEFAULT_ADMIN_INITIAL_PASSWORD, ""}
VALID_ROLES = {"operator", "maintenance", "supervisor", "admin"}
ADMIN_ROLES = {"admin"}
FULL_ACCESS_ROLES = {"supervisor", "admin"}
VERIFY_ROLES = {"operator", "supervisor"}
SUPERVISOR_VISIBLE_ROLES = {"maintenance", "supervisor"}
SESSION_TOKEN_PREFIX_LENGTH = 10
SESSION_COOKIE_NAME = "alarm_rag_session"
LOGIN_FAILURE_LIMIT = env_int("LOGIN_FAILURE_LIMIT", 5, minimum=1, maximum=100)
LOGIN_FAILURE_WINDOW_SECONDS = env_int("LOGIN_FAILURE_WINDOW_SECONDS", 300, minimum=1, maximum=86_400)
LOGIN_LOCKOUT_SECONDS = env_int("LOGIN_LOCKOUT_SECONDS", 300, minimum=1, maximum=86_400)
LOGIN_RATE_MAX_KEYS = env_int("LOGIN_RATE_MAX_KEYS", 10_000, minimum=100, maximum=1_000_000)
LOGIN_RATE_PRUNE_INTERVAL_SECONDS = 60
router = APIRouter()
postgres_users = PostgresUserRepository()
postgres_sessions = PostgresSessionRepository()
postgres_login_throttles = PostgresLoginThrottleRepository()
postgres_settings = PostgresSettingsRepository()
_login_failures: dict[str, deque[float]] = {}
_login_lockouts: dict[str, float] = {}
_login_last_seen: dict[str, float] = {}
_login_last_pruned_at = 0.0
_login_rate_lock = threading.Lock()


class LoginRequest(BaseModel):
    username: str
    password: str


class InitialPasswordChangeRequest(BaseModel):
    username: str
    current_password: str
    new_password: str


class LogoutRequest(BaseModel):
    token: Optional[str] = None


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    line_scope: Optional[List[str]] = None
    active: Optional[bool] = None
    expected_updated_at: Optional[str] = None


class CreateUserRequest(BaseModel):
    user_id: str
    name: Optional[str] = ""
    role: Optional[str] = "operator"
    team: Optional[str] = ""
    line_scope: Optional[List[str]] = None
    password: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: Optional[str] = None
    expected_updated_at: Optional[str] = None


def bootstrap_user_summaries() -> List[dict]:
    return [
        {
            "user_id": user["user_id"],
            "name": user["name"],
            "role": user["role"],
            "team": user["team"],
            "line_scope": user["line_scope"],
        }
        for user in BOOTSTRAP_USERS.values()
    ]


def get_actor(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    actor = actor_from_credentials(authorization, session_cookie)
    if actor:
        return actor
    return {"user_id": "", "name": "Unauthenticated", "role": "", "line_scope": [], "team": ""}


def resolve_user(user_id: Optional[str]) -> dict:
    users = load_users()
    key = (user_id or "").strip()
    return public_user(users.get(key, {}))


def configured_initial_password() -> str:
    password = secret_value("ADMIN_INITIAL_PASSWORD").strip()
    if password:
        return password
    return DEFAULT_ADMIN_INITIAL_PASSWORD


def initial_password_is_placeholder(password: Optional[str] = None) -> bool:
    return (password if password is not None else configured_initial_password()).strip() in PLACEHOLDER_INITIAL_PASSWORDS


def implicit_initial_password_error() -> Optional[str]:
    if production_mode() and initial_password_is_placeholder():
        return "ADMIN_INITIAL_PASSWORD must be set to a non-placeholder value in production"
    return None


def production_mode() -> bool:
    return os.getenv("ALARM_RAG_ENV", "development").strip().lower() in {"prod", "production"}


def list_users(actor: Optional[dict] = None) -> List[dict]:
    users = [public_user(user) for user in load_users().values()]
    if actor is None or is_admin(actor):
        return users
    if actor_role(actor) == "supervisor":
        return [
            user
            for user in users
            if user.get("role") in SUPERVISOR_VISIBLE_ROLES and user.get("active", True)
        ]
    return []


def active_admin_count(users: Dict[str, dict]) -> int:
    return account_management.active_admin_count(users)


def is_active_admin(user: dict) -> bool:
    return account_management.is_active_admin(user)


def is_last_active_admin(user: dict, users: Dict[str, dict]) -> bool:
    return account_management.is_last_active_admin(user, users)


def valid_password(password: str) -> bool:
    return account_management.valid_password(password)


def normalize_line_scope(line_scope: Optional[List[str]]) -> List[str]:
    return account_management.normalize_line_scope(line_scope)


def is_valid_user_id(user_id: str) -> bool:
    return account_management.is_valid_user_id(user_id)


def build_user(req: CreateUserRequest, user_id: str, password: str) -> dict:
    return account_management.build_user(req, user_id, password, password_hasher=hash_password)


def validate_create_user(req: CreateUserRequest, existing_users: Dict[str, dict]) -> Optional[str]:
    return account_management.validate_create_user(
        req,
        existing_users,
        valid_roles=VALID_ROLES,
        default_password=configured_initial_password,
        missing_password_error=implicit_initial_password_error,
    )


def validate_admin_role_change(
    user_id: str,
    user: dict,
    req: UpdateUserRequest,
    actor: dict,
    users: Dict[str, dict],
) -> Optional[str]:
    return account_management.validate_admin_role_change(
        user_id,
        user,
        req,
        actor_id(actor),
        users,
        valid_roles=VALID_ROLES,
    )


def api_ok(**payload: object) -> dict:
    return {"status": "ok", **payload}


def api_error(message: str) -> dict:
    return {"status": "error", "message": message}


def authentication_error(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=api_error(message),
        headers={"WWW-Authenticate": "Bearer"},
    )


def login_rate_limit_error(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=api_error("Too many login attempts; try again later"),
        headers={"Retry-After": str(max(retry_after, 1))},
    )


def login_client_identity(request: Request) -> str:
    """Use the ASGI peer identity so one source cannot lock an account for everyone."""
    host = str(request.client.host if request.client else "").strip().casefold()
    return host or "unknown"


def _login_rate_key(username: str, client_identity: str | None = None) -> str:
    normalized_username = normalize_login_key(username)
    if client_identity is None:
        return normalized_username
    identity_digest = hashlib.sha256(client_identity.encode("utf-8")).hexdigest()[:16]
    return f"{normalized_username}:{identity_digest}"


def _login_rate_keys(username: str, client_identity: str | None = None) -> tuple[str, ...]:
    normalized_username = normalize_login_key(username)
    if client_identity is None:
        return (f"account:{normalized_username}",)
    identity_digest = hashlib.sha256(client_identity.encode("utf-8")).hexdigest()[:16]
    return (
        f"account:{normalized_username}",
        f"pair:{normalized_username}:{identity_digest}",
        f"source:{identity_digest}",
    )


def _postgres_login_rate_keys(username: str, client_identity: str | None = None) -> tuple[str, ...]:
    return (*_login_rate_keys(username, client_identity), "global:login")


def _login_rate_state() -> LoginThrottleState:
    return LoginThrottleState(
        failures=_login_failures,
        lockouts=_login_lockouts,
        last_seen=_login_last_seen,
        last_pruned_at=_login_last_pruned_at,
    )


def _login_rate_limits() -> LoginThrottleLimits:
    return LoginThrottleLimits(
        failure_limit=LOGIN_FAILURE_LIMIT,
        failure_window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
        lockout_seconds=LOGIN_LOCKOUT_SECONDS,
        max_keys=LOGIN_RATE_MAX_KEYS,
        prune_interval_seconds=LOGIN_RATE_PRUNE_INTERVAL_SECONDS,
    )


def _discard_login_rate_key(key: str) -> None:
    discard_key(_login_rate_state(), key)


def _prune_login_rate_state(current: float, *, incoming_key: str | None = None) -> None:
    global _login_last_pruned_at
    state = _login_rate_state()
    prune_state(state, _login_rate_limits(), current, incoming_key=incoming_key)
    _login_last_pruned_at = state.last_pruned_at


def login_retry_after(
    username: str,
    *,
    client_identity: str | None = None,
    now: float | None = None,
) -> int:
    keys = _login_rate_keys(username, client_identity)
    if postgres_store_enabled() and now is None:
        return postgres_login_throttles.retry_after_many(
            _postgres_login_rate_keys(username, client_identity),
            LOGIN_FAILURE_WINDOW_SECONDS,
        )
    current = time.monotonic() if now is None else now
    with _login_rate_lock:
        global _login_last_pruned_at
        state = _login_rate_state()
        result = max(local_login_retry_after(state, _login_rate_limits(), key, current) for key in keys)
        _login_last_pruned_at = state.last_pruned_at
        return result


def record_login_failure(
    username: str,
    *,
    client_identity: str | None = None,
    now: float | None = None,
) -> int:
    keys = _login_rate_keys(username, client_identity)
    if postgres_store_enabled() and now is None:
        return postgres_login_throttles.record_failures(
            _postgres_login_rate_keys(username, client_identity),
            LOGIN_FAILURE_LIMIT,
            LOGIN_FAILURE_WINDOW_SECONDS,
            LOGIN_LOCKOUT_SECONDS,
            max_keys=LOGIN_RATE_MAX_KEYS,
        )
    current = time.monotonic() if now is None else now
    with _login_rate_lock:
        global _login_last_pruned_at
        state = _login_rate_state()
        result = max(
            record_local_login_failure(state, _login_rate_limits(), key, current)
            for key in keys
        )
        _login_last_pruned_at = state.last_pruned_at
        return result


def clear_login_failures(username: str, *, client_identity: str | None = None) -> None:
    keys = _login_rate_keys(username, client_identity)
    if postgres_store_enabled():
        postgres_login_throttles.clear_many(keys)
        return
    with _login_rate_lock:
        for key in keys:
            _discard_login_rate_key(key)


def session_cookie_secure(request: Request) -> bool:
    return auth_sessions.session_cookie_secure(
        os.getenv("SESSION_COOKIE_SECURE", "auto"),
        request.url.scheme,
        request.headers.get("x-forwarded-proto", ""),
    )


def set_session_cookie(response: Response, request: Request, token: str, expires_at: datetime) -> None:
    max_age = max(int((expires_at - datetime.now(timezone.utc)).total_seconds()), 1)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        expires=expires_at,
        path="/",
        secure=session_cookie_secure(request),
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def concurrency_error() -> dict:
    return api_error("User was updated by another administrator; reload and retry")


def same_user_version(expected: Optional[str], user: dict) -> bool:
    if expected is None:
        return True
    return str(user.get("updated_at") or "") == expected


def credential_epoch(user: dict) -> int:
    try:
        return max(int(user.get("credential_epoch") or 1), 1)
    except (TypeError, ValueError):
        return 1


def permission_denied() -> dict:
    return api_error("Permission denied")


async def _api_list_users(actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if actor_role(actor) not in ("admin", "supervisor"):
        return permission_denied()
    return {"users": list_users(actor)}


@router.get("/users", responses={200: {"model": UsersResponse}, **API_ERROR_RESPONSES})
async def api_list_users(actor: dict = Depends(get_actor)):
    return await _api_list_users(actor)


async def _api_create_user(req: CreateUserRequest, actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()

    user_id = req.user_id.strip()
    users = load_users()
    validation_error = validate_create_user(req, users)
    if validation_error:
        return api_error(validation_error)

    user = build_user(req, user_id, str(req.password))
    users[user_id] = user
    saved_user = save_user(user_id, user)
    return api_ok(user=public_user(saved_user))


@router.post("/users", responses={200: {"model": UserCreatedResponse}, **API_ERROR_RESPONSES})
async def api_create_user(req: CreateUserRequest, actor: dict = Depends(get_actor)):
    return await _api_create_user(req, actor)


async def _api_update_user(user_id: str, req: UpdateUserRequest, actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()

    users = load_users()
    key = user_id.strip()
    user = users.get(key)
    if user is None:
        return api_error(f"User {user_id} not found")
    validation_error = validate_admin_role_change(key, user, req, actor, users)
    if validation_error:
        return api_error(validation_error)
    if not same_user_version(req.expected_updated_at, user):
        return concurrency_error()

    if req.name is not None:
        user["name"] = req.name.strip()
    if req.role is not None:
        user["role"] = req.role
    if req.team is not None:
        user["team"] = req.team.strip()
    if req.line_scope is not None:
        user["line_scope"] = normalize_line_scope(req.line_scope)
    if req.active is not None:
        user["active"] = req.active
        if req.active is False:
            user["credential_epoch"] = credential_epoch(user) + 1

    users[key] = user
    try:
        saved_user = save_user(key, user, expected_updated_at=req.expected_updated_at)
    except ConcurrentUserUpdateError:
        return concurrency_error()
    revoked = revoke_user_sessions(key) if req.active is False else 0
    return api_ok(user=public_user(saved_user), sessions_revoked=revoked)


@router.patch(
    "/users/{user_id}",
    responses={200: {"model": UserUpdatedResponse}, **API_ERROR_RESPONSES},
)
async def api_update_user(user_id: str, req: UpdateUserRequest, actor: dict = Depends(get_actor)):
    return await _api_update_user(user_id, req, actor)


async def _api_reset_user_password(user_id: str, req: ResetPasswordRequest, actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()

    users = load_users()
    key = user_id.strip()
    user = users.get(key)
    if not user:
        return api_error(f"User {user_id} not found")
    if not same_user_version(req.expected_updated_at, user):
        return concurrency_error()
    if not req.password:
        return api_error("Password is required")
    password = req.password
    if not valid_password(password):
        return api_error("Password must be at least 8 characters and not use a common placeholder")
    read_version = str(user.get("updated_at") or "") or None
    user["password_hash"] = hash_password(password)
    user["credential_epoch"] = credential_epoch(user) + 1
    user["must_change_password"] = False
    users[key] = user
    try:
        saved_user = save_user(key, user, expected_updated_at=read_version)
    except ConcurrentUserUpdateError:
        return concurrency_error()
    revoke_user_sessions(key)
    return api_ok(user=public_user(saved_user), sessions_revoked=True)


@router.patch(
    "/users/{user_id}/password",
    responses={200: {"model": PasswordResetResponse}, **API_ERROR_RESPONSES},
)
async def api_reset_user_password(
    user_id: str,
    req: ResetPasswordRequest,
    actor: dict = Depends(get_actor),
):
    return await _api_reset_user_password(user_id, req, actor)


async def _api_revoke_user_sessions(user_id: str, actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()

    key = user_id.strip()
    if key == actor_id(actor):
        return api_error("The current admin account cannot revoke its own sessions")
    users = load_users()
    user = users.get(key)
    if user is None:
        return api_error(f"User {user_id} not found")
    read_version = str(user.get("updated_at") or "") or None
    user["credential_epoch"] = credential_epoch(user) + 1
    try:
        save_user(key, user, expected_updated_at=read_version)
    except ConcurrentUserUpdateError:
        return concurrency_error()
    return api_ok(revoked=revoke_user_sessions(key))


@router.delete(
    "/users/{user_id}/sessions",
    responses={200: {"model": SessionsRevokedResponse}, **API_ERROR_RESPONSES},
)
async def api_revoke_user_sessions(user_id: str, actor: dict = Depends(get_actor)):
    return await _api_revoke_user_sessions(user_id, actor)


@router.get("/sessions", responses={200: {"model": SessionsResponse}, **API_ERROR_RESPONSES})
async def api_list_sessions(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(get_actor),
):
    page_limit = limit if isinstance(limit, int) else 200
    page_offset = offset if isinstance(offset, int) else 0
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()
    if postgres_store_enabled():
        entries, total = postgres_sessions.list_active_page(limit=page_limit, offset=page_offset)
        return api_ok(
            total=total,
            limit=page_limit,
            offset=page_offset,
            has_more=page_offset + len(entries) < total,
            sessions=entries,
        )
    users = load_users()
    sessions = auth_sessions.mutate_sessions(
        SESSION_FILE,
        lambda current: _replace_sessions(current, prune_expired_sessions(current)),
    )
    all_entries = [
        {
            "token_prefix": token[:SESSION_TOKEN_PREFIX_LENGTH],
            "user_id": str(session.get("user_id") or ""),
            "role": public_user(users.get(str(session.get("user_id") or ""), {})).get("role", ""),
            "created_at": session.get("created_at", ""),
            "expires_at": session.get("expires_at", ""),
        }
        for token, session in sorted(sessions.items(), key=lambda item: str(item[1].get("expires_at") or ""))
    ]
    entries = all_entries[page_offset:page_offset + page_limit]
    return api_ok(
        total=len(all_entries),
        limit=page_limit,
        offset=page_offset,
        has_more=page_offset + len(entries) < len(all_entries),
        sessions=entries,
    )


@router.delete(
    "/sessions/{token_prefix}",
    responses={200: {"model": SessionsRevokedResponse}, **API_ERROR_RESPONSES},
)
async def api_revoke_session(token_prefix: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()
    prefix = token_prefix.strip()
    if len(prefix) != SESSION_TOKEN_PREFIX_LENGTH:
        return api_error(f"token_prefix must be exactly {SESSION_TOKEN_PREFIX_LENGTH} characters")
    if postgres_store_enabled():
        try:
            return api_ok(revoked=postgres_sessions.revoke_prefix(prefix))
        except ValueError as exc:
            return api_error(str(exc))
    def revoke_prefix(sessions: Dict[str, dict]) -> int:
        matched = [token for token in sessions if token.startswith(prefix)]
        if len(matched) > 1:
            raise ValueError("Ambiguous token prefix")
        for token in matched:
            sessions.pop(token, None)
        return len(matched)

    try:
        return api_ok(revoked=auth_sessions.mutate_sessions(SESSION_FILE, revoke_prefix))
    except ValueError as exc:
        return api_error(str(exc))


@router.post(
    "/auth/login",
    response_model=LoginSuccessResponse,
    responses={
        401: {"model": ApiErrorResponse, "description": "Invalid credentials"},
        429: {"model": ApiErrorResponse, "description": "Too many login attempts"},
    },
)
async def login(req: LoginRequest, request: Request, response: Response):
    client_identity = login_client_identity(request)
    retry_after = login_retry_after(req.username, client_identity=client_identity)
    if retry_after:
        runtime_metrics.record_auth("throttled")
        return login_rate_limit_error(retry_after)

    users = load_users()
    user = users.get(req.username.strip())
    if not user or not user.get("active", True):
        retry_after = record_login_failure(req.username, client_identity=client_identity)
        if retry_after:
            runtime_metrics.record_auth("throttled")
            return login_rate_limit_error(retry_after)
        runtime_metrics.record_auth("failure")
        return authentication_error("Invalid username or password")
    if not verify_password(req.password, str(user.get("password_hash") or "")):
        retry_after = record_login_failure(req.username, client_identity=client_identity)
        if retry_after:
            runtime_metrics.record_auth("throttled")
            return login_rate_limit_error(retry_after)
        runtime_metrics.record_auth("failure")
        return authentication_error("Invalid username or password")
    if user.get("must_change_password"):
        return JSONResponse(
            status_code=403,
            content={
                "status": "error",
                "message": "Initial password must be changed before login",
                "error_code": "PASSWORD_CHANGE_REQUIRED",
                "change_endpoint": "/auth/initial-password",
            },
        )

    clear_login_failures(req.username, client_identity=client_identity)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at_value = now + timedelta(hours=session_hours())
    expires_at = expires_at_value.isoformat()
    if postgres_store_enabled():
        try:
            postgres_sessions.create(
                token,
                user["user_id"],
                created_at,
                expires_at,
                expected_credential_epoch=credential_epoch(user),
            )
        except (CredentialChangedError, ValueError):
            runtime_metrics.record_auth("failure")
            return authentication_error("Invalid username or password")
        except SessionCapacityError:
            runtime_metrics.record_auth("throttled")
            return JSONResponse(
                status_code=503,
                content=api_error("Session capacity reached; retry later"),
                headers={"Retry-After": "60"},
            )
    else:
        def create_session(sessions: Dict[str, dict]) -> bool:
            current_user = load_users().get(str(user["user_id"]))
            if (
                not current_user
                or not current_user.get("active", True)
                or credential_epoch(current_user) != credential_epoch(user)
                or str(current_user.get("password_hash") or "") != str(user.get("password_hash") or "")
            ):
                return False
            _replace_sessions(sessions, prune_expired_sessions(sessions))
            sessions[session_token_digest(token)] = {
                "user_id": user["user_id"],
                "created_at": created_at,
                "expires_at": expires_at,
                "credential_epoch": credential_epoch(current_user),
            }
            return True

        if not auth_sessions.mutate_sessions(SESSION_FILE, create_session):
            runtime_metrics.record_auth("failure")
            return authentication_error("Invalid username or password")
    set_session_cookie(response, request, token, expires_at_value)
    runtime_metrics.record_auth("success")
    return {"status": "ok", "token": token, "expires_at": expires_at, "user": public_user(user)}


@router.post(
    "/auth/initial-password",
    response_model=StatusOkResponse,
    responses={400: {"model": ApiErrorResponse}, 401: {"model": ApiErrorResponse}, 409: {"model": ApiErrorResponse}, 429: {"model": ApiErrorResponse}},
)
async def change_initial_password(req: InitialPasswordChangeRequest, request: Request):
    client_identity = login_client_identity(request)
    retry_after = login_retry_after(req.username, client_identity=client_identity)
    if retry_after:
        return login_rate_limit_error(retry_after)
    users = load_users()
    key = req.username.strip()
    user = users.get(key)
    if (
        not user
        or not user.get("active", True)
        or not user.get("must_change_password")
        or not verify_password(req.current_password, str(user.get("password_hash") or ""))
    ):
        retry_after = record_login_failure(req.username, client_identity=client_identity)
        if retry_after:
            return login_rate_limit_error(retry_after)
        return authentication_error("Invalid username or password")
    if req.new_password == req.current_password or not valid_password(req.new_password):
        return JSONResponse(
            status_code=400,
            content=api_error("New password must be distinct, at least 8 characters, and not a common placeholder"),
        )
    read_version = str(user.get("updated_at") or "") or None
    user["password_hash"] = hash_password(req.new_password)
    user["credential_epoch"] = credential_epoch(user) + 1
    user["must_change_password"] = False
    try:
        save_user(key, user, expected_updated_at=read_version)
    except ConcurrentUserUpdateError:
        return JSONResponse(status_code=409, content=concurrency_error())
    revoke_user_sessions(key)
    clear_login_failures(req.username, client_identity=client_identity)
    return api_ok(message="Initial password changed; sign in with the new password")


@router.get("/auth/login-config", response_model=LoginConfigResponse)
async def login_config():
    return {
        "status": "ok",
        "production": production_mode(),
        "initial_password_configured": not initial_password_is_placeholder(),
        # Keep the response shape stable without publishing valid account IDs.
        "bootstrap_users": [],
    }


@router.get(
    "/auth/me",
    response_model=CurrentUserSuccessResponse,
    responses={401: {"model": ApiErrorResponse, "description": "Missing, expired, or invalid session"}},
)
async def me(actor: dict = Depends(get_actor)):
    current = actor
    if not current:
        return authentication_error("Not authenticated")
    if not actor_id(current):
        return authentication_error("Not authenticated")
    return {"status": "ok", "user": current}


@router.post("/auth/logout", response_model=StatusOkResponse)
async def logout(
    response: Response,
    req: Optional[LogoutRequest] = None,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    session_cookie: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    token = (req.token if req else None) or bearer_token(authorization) or (session_cookie or "")
    clear_session_cookie(response)
    if not token:
        return {"status": "ok"}
    if postgres_store_enabled():
        postgres_sessions.delete(token)
        return {"status": "ok"}
    def delete_session(sessions: Dict[str, dict]) -> None:
        sessions.pop(token, None)
        sessions.pop(session_token_digest(token), None)

    auth_sessions.mutate_sessions(SESSION_FILE, delete_session)
    return {"status": "ok"}


def ensure_user_store() -> None:
    if postgres_store_enabled():
        if postgres_users.load_all():
            return
        initial_password = configured_initial_password()
        password_error = implicit_initial_password_error()
        if password_error:
            raise RuntimeError(password_error)
        admin = BOOTSTRAP_USERS["admin01"]
        postgres_users.save_all({
            "admin01": {
                **admin,
                "password_hash": hash_password(initial_password),
                "active": True,
                "credential_epoch": 1,
                "must_change_password": True,
            }
        })
        return
    os.makedirs(DB_DIR, exist_ok=True)
    if os.path.exists(USER_FILE):
        return
    initial_password = configured_initial_password()
    password_error = implicit_initial_password_error()
    if password_error:
        raise RuntimeError(password_error)
    admin = BOOTSTRAP_USERS["admin01"]
    users = {
        "admin01": {
            **admin,
            "password_hash": hash_password(initial_password),
            "active": True,
            "credential_epoch": 1,
            "must_change_password": True,
        }
    }
    with exclusive_file_lock(USER_FILE + ".lock"):
        write_json_atomic(USER_FILE, users)


def load_users() -> Dict[str, dict]:
    ensure_user_store()
    if postgres_store_enabled():
        return postgres_users.load_all()
    try:
        with open(USER_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def save_users(users: Dict[str, dict]) -> None:
    if postgres_store_enabled():
        postgres_users.save_all(users)
        return
    os.makedirs(DB_DIR, exist_ok=True)
    with exclusive_file_lock(USER_FILE + ".lock"):
        write_json_atomic(USER_FILE, users)


def save_user(user_id: str, user: dict, expected_updated_at: Optional[str] = None) -> dict:
    if postgres_store_enabled():
        return postgres_users.save_one(user_id, user, expected_updated_at=expected_updated_at)
    with exclusive_file_lock(USER_FILE + ".lock"):
        try:
            with open(USER_FILE, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (json.JSONDecodeError, OSError):
            payload = {}
        users = payload if isinstance(payload, dict) else {}
        current = users.get(user_id, {})
        if not same_user_version(expected_updated_at, current):
            raise ConcurrentUserUpdateError(f"User {user_id} was updated by another request")
        saved = dict(user)
        if not saved.get("created_at"):
            saved["created_at"] = current.get("created_at") or datetime.now(timezone.utc).isoformat()
        saved["updated_at"] = datetime.now(timezone.utc).isoformat()
        users[user_id] = saved
        write_json_atomic(USER_FILE, users)
        return saved


def load_sessions() -> Dict[str, dict]:
    return auth_sessions.load_sessions(SESSION_FILE, save_migrated=save_sessions)


def save_sessions(sessions: Dict[str, dict]) -> None:
    auth_sessions.save_sessions(SESSION_FILE, sessions)


def revoke_user_sessions(user_id: str) -> int:
    if postgres_store_enabled():
        return postgres_sessions.revoke_user(user_id)
    def revoke(sessions: Dict[str, dict]) -> int:
        before = len(sessions)
        _replace_sessions(sessions, auth_sessions.revoke_user_sessions(sessions, user_id))
        return before - len(sessions)

    return auth_sessions.mutate_sessions(SESSION_FILE, revoke)


def _replace_sessions(target: Dict[str, dict], replacement: Dict[str, dict]) -> Dict[str, dict]:
    target.clear()
    target.update(replacement)
    return dict(target)


def prune_expired_sessions(sessions: Dict[str, dict]) -> Dict[str, dict]:
    return auth_sessions.prune_expired_sessions(sessions)


def _parse_session_expiry(session: dict) -> datetime:
    return auth_sessions.parse_session_expiry(session)


def session_hours() -> int:
    override = session_hours_override(os.getenv("SESSION_TTL_HOURS", ""))
    if override is not None:
        return override
    settings = load_effective_settings(
        os.path.join(DB_DIR, "system_settings.json"),
        postgres_reader=postgres_settings,
        use_postgres=postgres_store_enabled(),
    )
    value = settings.get("session_hours", 12)
    if not isinstance(value, int) or isinstance(value, bool):
        return 12
    return min(max(value, 1), 72)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    return auth_sessions.hash_password(password, salt)


def verify_password(password: str, password_hash: str) -> bool:
    return auth_sessions.verify_password(password, password_hash)


def public_user(user: dict) -> dict:
    return account_management.public_user(user)


def bearer_token(authorization: Optional[str]) -> str:
    return auth_sessions.bearer_token(authorization)


def session_token_digest(token: str) -> str:
    return auth_sessions.session_token_digest(token)


def actor_from_credentials(authorization: Optional[str], session_cookie: Optional[str]) -> Optional[dict]:
    header_token = bearer_token(authorization)
    token = header_token or (session_cookie or "").strip()
    return actor_from_session_token(token)


def actor_from_token(authorization: Optional[str]) -> Optional[dict]:
    token = bearer_token(authorization)
    return actor_from_session_token(token)


def actor_from_session_token(token: str) -> Optional[dict]:
    if not token:
        return None
    if postgres_store_enabled():
        session = postgres_sessions.get(token)
        if not session:
            return None
        actor = resolve_user(str(session.get("user_id") or ""))
        return actor if actor.get("user_id") and actor.get("active", True) else None
    def resolve_session(sessions: Dict[str, dict]) -> Optional[dict]:
        session_key = session_token_digest(token)
        stored_key = session_key if session_key in sessions else token
        session = sessions.get(stored_key)
        if not session:
            return None
        user = load_users().get(str(session.get("user_id") or ""), {})
        invalid = (
            _parse_session_expiry(session) < datetime.now(timezone.utc)
            or not user.get("user_id")
            or not user.get("active", True)
            or credential_epoch(session) != credential_epoch(user)
        )
        if invalid:
            sessions.pop(stored_key, None)
            return None
        return public_user(user)

    return auth_sessions.mutate_sessions(SESSION_FILE, resolve_session)


def has_full_access(actor: dict) -> bool:
    return actor.get("role") in FULL_ACCESS_ROLES


def is_admin(actor: dict) -> bool:
    return actor.get("role") in ADMIN_ROLES


def can_verify(actor: dict) -> bool:
    return actor.get("role") in VERIFY_ROLES


def actor_id(actor: dict) -> str:
    return str(actor.get("user_id") or "")


def actor_role(actor: dict) -> str:
    return str(actor.get("role") or "")


def line_allowed(actor: dict, line_id: str) -> bool:
    scope = actor.get("line_scope")
    if not isinstance(scope, list):
        return False
    if "*" in scope:
        return True
    return (line_id or "") in scope


def can_view_issue(actor: dict, issue: dict) -> bool:
    role = actor_role(actor)
    if role in FULL_ACCESS_ROLES:
        return True
    if role == "operator":
        return line_allowed(actor, str(issue.get("line_id") or ""))
    if role == "maintenance":
        if issue.get("status") in ("completed", "verified", "cancelled"):
            return False
        assigned_to = str(issue.get("assigned_to") or "")
        return not assigned_to or assigned_to == actor_id(actor)
    return False


def can_view_work_order(actor: dict, order: dict, linked_issue: Optional[dict] = None) -> bool:
    role = actor_role(actor)
    if role in FULL_ACCESS_ROLES:
        return True
    if role == "operator":
        return linked_issue is not None and can_view_issue(actor, linked_issue)
    if role == "maintenance":
        if order.get("status") in ("completed", "verified", "cancelled"):
            return False
        assigned_to = str(order.get("assigned_to") or "")
        return not assigned_to or assigned_to == actor_id(actor)
    return False


def can_reference_rag_answer(actor: dict, answer: dict | None) -> bool:
    """Allow an answer reference only to its creator or a privileged reviewer."""
    if not answer or not actor_id(actor):
        return False
    if actor_role(actor) in FULL_ACCESS_ROLES:
        return True
    return bool(answer.get("created_by")) and str(answer.get("created_by")) == actor_id(actor)


def can_trigger_alarm(actor: dict) -> bool:
    """Authorize user-session callers that may originate operational workflows."""
    return actor_role(actor) in {"operator", "supervisor", "admin"}


def can_update_issue(actor: dict, issue: dict, next_status: Optional[str]) -> bool:
    role = actor_role(actor)
    if role in FULL_ACCESS_ROLES:
        return True
    if role == "operator":
        if not can_view_issue(actor, issue):
            return False
        return next_status in (None, "open", "assigned", "verified")
    if role == "maintenance":
        return can_view_issue(actor, issue) and next_status != "verified"
    return False


def can_update_work_order(actor: dict, order: dict, next_status: Optional[str]) -> bool:
    role = actor_role(actor)
    if role in FULL_ACCESS_ROLES:
        return True
    if role == "maintenance":
        return can_view_work_order(actor, order) and next_status != "verified"
    if role == "operator":
        return next_status in (None, "verified") and can_verify(actor)
    return False
