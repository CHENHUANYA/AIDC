import hashlib
import json
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
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
    PostgresLoginThrottleRepository,
    PostgresSessionRepository,
    PostgresUserRepository,
)
from repositories.runtime import postgres_store_enabled
from secret_values import secret_value


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
_login_failures: dict[str, deque[float]] = {}
_login_lockouts: dict[str, float] = {}
_login_last_seen: dict[str, float] = {}
_login_last_pruned_at = 0.0
_login_rate_lock = threading.Lock()


class LoginRequest(BaseModel):
    username: str
    password: str


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
    return sum(
        1
        for user in users.values()
        if is_active_admin(user)
    )


def is_active_admin(user: dict) -> bool:
    return user.get("active", True) and user.get("role") == "admin"


def is_last_active_admin(user: dict, users: Dict[str, dict]) -> bool:
    return is_active_admin(user) and active_admin_count(users) <= 1


def valid_password(password: str) -> bool:
    normalized = password.strip()
    if len(normalized) < 8:
        return False
    return normalized.lower() not in {"password", "password1", "12345678", "change-me-now"}


def normalize_line_scope(line_scope: Optional[List[str]]) -> List[str]:
    return [str(item).strip() for item in (line_scope or []) if str(item).strip()]


def is_valid_user_id(user_id: str) -> bool:
    return bool(user_id) and user_id.replace("-", "").replace("_", "").isalnum()


def build_user(req: CreateUserRequest, user_id: str, password: str) -> dict:
    role = req.role or "operator"
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": user_id,
        "name": (req.name or user_id).strip(),
        "role": role,
        "line_scope": normalize_line_scope(req.line_scope),
        "team": (req.team or "").strip(),
        "active": True,
        "password_hash": hash_password(password),
        "created_at": now,
        "updated_at": now,
    }


def validate_create_user(req: CreateUserRequest, existing_users: Dict[str, dict]) -> Optional[str]:
    user_id = req.user_id.strip()
    role = req.role or "operator"
    if not user_id:
        return "user_id is required"
    if not is_valid_user_id(user_id):
        return "user_id may only contain letters, numbers, dash, or underscore"
    if role not in VALID_ROLES:
        return "Invalid role"
    if user_id in existing_users:
        return f"User {user_id} already exists"
    if not req.password:
        password_error = implicit_initial_password_error()
        if password_error:
            return password_error
    if not valid_password(req.password or configured_initial_password()):
        return "Password must be at least 8 characters and not use a common placeholder"
    return None


def validate_admin_role_change(
    user_id: str,
    user: dict,
    req: UpdateUserRequest,
    actor: dict,
    users: Dict[str, dict],
) -> Optional[str]:
    if user_id == actor_id(actor) and req.active is False:
        return "The current admin account cannot deactivate itself"
    if req.role is not None and req.role not in VALID_ROLES:
        return "Invalid role"
    if user_id == actor_id(actor) and req.role is not None and req.role != "admin":
        return "The current admin account cannot change its own admin role"
    if not is_last_active_admin(user, users):
        return None
    if req.active is False:
        return "Cannot deactivate the last active admin"
    if req.role is not None and req.role != "admin":
        return "Cannot demote the last active admin"
    return None


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


def _login_rate_key(username: str) -> str:
    return username.strip().casefold() or "<empty>"


def _discard_login_rate_key(key: str) -> None:
    _login_failures.pop(key, None)
    _login_lockouts.pop(key, None)
    _login_last_seen.pop(key, None)


def _prune_login_rate_state(current: float, *, incoming_key: str | None = None) -> None:
    global _login_last_pruned_at

    known_keys = set(_login_failures) | set(_login_lockouts)
    should_prune = (
        current - _login_last_pruned_at >= LOGIN_RATE_PRUNE_INTERVAL_SECONDS
        or (incoming_key not in known_keys and len(known_keys) >= LOGIN_RATE_MAX_KEYS)
    )
    if not should_prune:
        return

    cutoff = current - LOGIN_FAILURE_WINDOW_SECONDS
    for key, failures in list(_login_failures.items()):
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            _login_failures.pop(key, None)
    for key, locked_until in list(_login_lockouts.items()):
        if locked_until <= current:
            _login_lockouts.pop(key, None)

    active_keys = set(_login_failures) | set(_login_lockouts)
    for key in list(_login_last_seen):
        if key not in active_keys:
            _login_last_seen.pop(key, None)

    if incoming_key not in active_keys and len(active_keys) >= LOGIN_RATE_MAX_KEYS:
        oldest_key = min(active_keys, key=lambda key: _login_last_seen.get(key, 0.0))
        _discard_login_rate_key(oldest_key)
    _login_last_pruned_at = current


def login_retry_after(username: str, *, now: float | None = None) -> int:
    key = _login_rate_key(username)
    if postgres_store_enabled() and now is None:
        return postgres_login_throttles.retry_after(key, LOGIN_FAILURE_WINDOW_SECONDS)
    current = time.monotonic() if now is None else now
    with _login_rate_lock:
        _prune_login_rate_state(current)
        locked_until = _login_lockouts.get(key, 0.0)
        if locked_until > current:
            _login_last_seen[key] = current
            return max(ceil(locked_until - current), 1)
        _login_lockouts.pop(key, None)
        failures = _login_failures.get(key)
        if failures is None:
            _login_last_seen.pop(key, None)
            return 0
        cutoff = current - LOGIN_FAILURE_WINDOW_SECONDS
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            _discard_login_rate_key(key)
        else:
            _login_last_seen[key] = current
        return 0


def record_login_failure(username: str, *, now: float | None = None) -> int:
    key = _login_rate_key(username)
    if postgres_store_enabled() and now is None:
        return postgres_login_throttles.record_failure(
            key,
            LOGIN_FAILURE_LIMIT,
            LOGIN_FAILURE_WINDOW_SECONDS,
            LOGIN_LOCKOUT_SECONDS,
        )
    current = time.monotonic() if now is None else now
    with _login_rate_lock:
        _prune_login_rate_state(current, incoming_key=key)
        failures = _login_failures.setdefault(key, deque())
        _login_last_seen[key] = current
        cutoff = current - LOGIN_FAILURE_WINDOW_SECONDS
        while failures and failures[0] <= cutoff:
            failures.popleft()
        failures.append(current)
        if len(failures) < LOGIN_FAILURE_LIMIT:
            return 0
        locked_until = current + LOGIN_LOCKOUT_SECONDS
        _login_lockouts[key] = locked_until
        failures.clear()
        return LOGIN_LOCKOUT_SECONDS


def clear_login_failures(username: str) -> None:
    key = _login_rate_key(username)
    if postgres_store_enabled():
        postgres_login_throttles.clear(key)
        return
    with _login_rate_lock:
        _discard_login_rate_key(key)


def session_cookie_secure(request: Request) -> bool:
    configured = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


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

    user = build_user(req, user_id, req.password or configured_initial_password())
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
    if not user:
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
    if not req.password:
        password_error = implicit_initial_password_error()
        if password_error:
            return api_error(password_error)
    password = req.password or configured_initial_password()
    if not valid_password(password):
        return api_error("Password must be at least 8 characters and not use a common placeholder")
    user["password_hash"] = hash_password(password)
    users[key] = user
    saved_user = save_user(key, user)
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
    if key not in users:
        return api_error(f"User {user_id} not found")
    return api_ok(revoked=revoke_user_sessions(key))


@router.delete(
    "/users/{user_id}/sessions",
    responses={200: {"model": SessionsRevokedResponse}, **API_ERROR_RESPONSES},
)
async def api_revoke_user_sessions(user_id: str, actor: dict = Depends(get_actor)):
    return await _api_revoke_user_sessions(user_id, actor)


@router.get("/sessions", responses={200: {"model": SessionsResponse}, **API_ERROR_RESPONSES})
async def api_list_sessions(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()
    if postgres_store_enabled():
        entries = postgres_sessions.list_active()
        return api_ok(total=len(entries), sessions=entries)
    users = load_users()
    sessions = prune_expired_sessions(load_sessions())
    save_sessions(sessions)
    entries = [
        {
            "token_prefix": token[:SESSION_TOKEN_PREFIX_LENGTH],
            "user_id": str(session.get("user_id") or ""),
            "role": public_user(users.get(str(session.get("user_id") or ""), {})).get("role", ""),
            "created_at": session.get("created_at", ""),
            "expires_at": session.get("expires_at", ""),
        }
        for token, session in sorted(sessions.items(), key=lambda item: str(item[1].get("expires_at") or ""))
    ]
    return api_ok(total=len(entries), sessions=entries)


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
    sessions = load_sessions()
    matched = [token for token in sessions if token.startswith(prefix)]
    if len(matched) > 1:
        return api_error("Ambiguous token prefix")
    for token in matched:
        sessions.pop(token, None)
    save_sessions(sessions)
    return api_ok(revoked=len(matched))


@router.post(
    "/auth/login",
    response_model=LoginSuccessResponse,
    responses={
        401: {"model": ApiErrorResponse, "description": "Invalid credentials"},
        429: {"model": ApiErrorResponse, "description": "Too many login attempts"},
    },
)
async def login(req: LoginRequest, request: Request, response: Response):
    retry_after = login_retry_after(req.username)
    if retry_after:
        runtime_metrics.record_auth("throttled")
        return login_rate_limit_error(retry_after)

    users = load_users()
    user = users.get(req.username.strip())
    if not user or not user.get("active", True):
        retry_after = record_login_failure(req.username)
        if retry_after:
            runtime_metrics.record_auth("throttled")
            return login_rate_limit_error(retry_after)
        runtime_metrics.record_auth("failure")
        return authentication_error("Invalid username or password")
    if not verify_password(req.password, str(user.get("password_hash") or "")):
        retry_after = record_login_failure(req.username)
        if retry_after:
            runtime_metrics.record_auth("throttled")
            return login_rate_limit_error(retry_after)
        runtime_metrics.record_auth("failure")
        return authentication_error("Invalid username or password")

    clear_login_failures(req.username)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at_value = now + timedelta(hours=session_hours())
    expires_at = expires_at_value.isoformat()
    sessions = prune_expired_sessions(load_sessions())
    sessions[session_token_digest(token)] = {
        "user_id": user["user_id"],
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if postgres_store_enabled():
        postgres_sessions.create(token, user["user_id"], created_at, expires_at)
    else:
        save_sessions(sessions)
    set_session_cookie(response, request, token, expires_at_value)
    runtime_metrics.record_auth("success")
    return {"status": "ok", "token": token, "expires_at": expires_at, "user": public_user(user)}


@router.get("/auth/login-config", response_model=LoginConfigResponse)
async def login_config():
    return {
        "status": "ok",
        "production": production_mode(),
        "initial_password_configured": not initial_password_is_placeholder(),
        "bootstrap_users": bootstrap_user_summaries(),
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
    sessions = load_sessions()
    sessions.pop(token, None)
    sessions.pop(session_token_digest(token), None)
    save_sessions(sessions)
    return {"status": "ok"}


def ensure_user_store() -> None:
    if postgres_store_enabled():
        if postgres_users.load_all():
            return
        initial_password = configured_initial_password()
        password_error = implicit_initial_password_error()
        if password_error:
            raise RuntimeError(password_error)
        postgres_users.save_all({
            user_id: {**user, "password_hash": hash_password(initial_password), "active": True}
            for user_id, user in BOOTSTRAP_USERS.items()
        })
        return
    os.makedirs(DB_DIR, exist_ok=True)
    if os.path.exists(USER_FILE):
        return
    initial_password = configured_initial_password()
    password_error = implicit_initial_password_error()
    if password_error:
        raise RuntimeError(password_error)
    users = {
        user_id: {**user, "password_hash": hash_password(initial_password), "active": True}
        for user_id, user in BOOTSTRAP_USERS.items()
    }
    with open(USER_FILE, "w", encoding="utf-8") as file:
        json.dump(users, file, ensure_ascii=False, indent=2)


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
    with open(USER_FILE, "w", encoding="utf-8") as file:
        json.dump(users, file, ensure_ascii=False, indent=2)


def save_user(user_id: str, user: dict, expected_updated_at: Optional[str] = None) -> dict:
    if postgres_store_enabled():
        return postgres_users.save_one(user_id, user, expected_updated_at=expected_updated_at)
    users = load_users()
    current = users.get(user_id, {})
    if not same_user_version(expected_updated_at, current):
        raise ConcurrentUserUpdateError(f"User {user_id} was updated by another request")
    saved = dict(user)
    if not saved.get("created_at"):
        saved["created_at"] = current.get("created_at") or datetime.now(timezone.utc).isoformat()
    saved["updated_at"] = datetime.now(timezone.utc).isoformat()
    users[user_id] = saved
    save_users(users)
    return saved


def load_sessions() -> Dict[str, dict]:
    os.makedirs(DB_DIR, exist_ok=True)
    if not os.path.exists(SESSION_FILE):
        return {}
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: Dict[str, dict] = {}
    migrated = False
    for stored_token, session in payload.items():
        key = str(stored_token)
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key.lower()):
            key = session_token_digest(key)
            migrated = True
        if isinstance(session, dict):
            normalized[key] = session
    if migrated:
        save_sessions(normalized)
    return normalized


def save_sessions(sessions: Dict[str, dict]) -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as file:
        json.dump(sessions, file, ensure_ascii=False, indent=2)


def revoke_user_sessions(user_id: str) -> int:
    if postgres_store_enabled():
        return postgres_sessions.revoke_user(user_id)
    sessions = load_sessions()
    before = len(sessions)
    sessions = {
        token: session
        for token, session in sessions.items()
        if str(session.get("user_id") or "") != user_id
    }
    save_sessions(sessions)
    return before - len(sessions)


def prune_expired_sessions(sessions: Dict[str, dict]) -> Dict[str, dict]:
    now = datetime.now(timezone.utc)
    return {
        token: session
        for token, session in sessions.items()
        if _parse_session_expiry(session) > now
    }


def _parse_session_expiry(session: dict) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(session.get("expires_at") or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(seconds=1)


def session_hours() -> int:
    env_value = os.getenv("SESSION_TTL_HOURS", "").strip()
    if env_value:
        try:
            return min(max(int(env_value), 1), 72)
        except ValueError:
            pass
    try:
        with open(os.path.join(DB_DIR, "system_settings.json"), "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return 12
    value = payload.get("session_hours") if isinstance(payload, dict) else 12
    if not isinstance(value, int):
        return 12
    return min(max(value, 1), 72)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    password_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${password_salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    expected = hash_password(password, parts[1])
    return secrets.compare_digest(expected, password_hash)


def public_user(user: dict) -> dict:
    return {
        "user_id": str(user.get("user_id") or ""),
        "name": str(user.get("name") or ""),
        "role": str(user.get("role") or ""),
        "line_scope": user.get("line_scope") if isinstance(user.get("line_scope"), list) else [],
        "team": str(user.get("team") or ""),
        "active": bool(user.get("active", True)),
        "created_at": str(user.get("created_at") or ""),
        "updated_at": str(user.get("updated_at") or ""),
    }


def bearer_token(authorization: Optional[str]) -> str:
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def session_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    sessions = load_sessions()
    session_key = session_token_digest(token)
    stored_key = session_key if session_key in sessions else token
    session = sessions.get(stored_key)
    if not session:
        return None
    expires_at = _parse_session_expiry(session)
    if expires_at < datetime.now(timezone.utc):
        sessions.pop(stored_key, None)
        save_sessions(sessions)
        return None
    actor = resolve_user(str(session.get("user_id") or ""))
    if not actor.get("user_id") or not actor.get("active", True):
        sessions.pop(stored_key, None)
        save_sessions(sessions)
        return None
    return actor


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
        if order.get("status") in ("completed", "verified"):
            return False
        assigned_to = str(order.get("assigned_to") or "")
        return not assigned_to or assigned_to == actor_id(actor)
    return False


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
