import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel


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
router = APIRouter()


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


def get_actor(authorization: Optional[str] = Header(default=None, alias="Authorization")) -> dict:
    actor = actor_from_token(authorization)
    if actor:
        return actor
    return {"user_id": "", "name": "Unauthenticated", "role": "", "line_scope": [], "team": ""}


def resolve_user(user_id: Optional[str]) -> dict:
    users = load_users()
    key = (user_id or "").strip()
    return public_user(users.get(key, {}))


def configured_initial_password() -> str:
    password = os.getenv("ADMIN_INITIAL_PASSWORD", "").strip()
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
    return {
        "user_id": user_id,
        "name": (req.name or user_id).strip(),
        "role": role,
        "line_scope": normalize_line_scope(req.line_scope),
        "team": (req.team or "").strip(),
        "active": True,
        "password_hash": hash_password(password),
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


def permission_denied() -> dict:
    return api_error("Permission denied")


async def _api_list_users(actor: dict) -> dict:
    if not actor_id(actor):
        return api_error("Not authenticated")
    if actor_role(actor) not in ("admin", "supervisor"):
        return permission_denied()
    return {"users": list_users(actor)}


@router.get("/users")
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
    save_users(users)
    return api_ok(user=public_user(user))


@router.post("/users")
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
    save_users(users)
    return api_ok(user=public_user(user))


@router.patch("/users/{user_id}")
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
    save_users(users)
    revoke_user_sessions(key)
    return api_ok(user=public_user(user), sessions_revoked=True)


@router.patch("/users/{user_id}/password")
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


@router.delete("/users/{user_id}/sessions")
async def api_revoke_user_sessions(user_id: str, actor: dict = Depends(get_actor)):
    return await _api_revoke_user_sessions(user_id, actor)


@router.get("/sessions")
async def api_list_sessions(actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()
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


@router.delete("/sessions/{token_prefix}")
async def api_revoke_session(token_prefix: str, actor: dict = Depends(get_actor)):
    if not actor_id(actor):
        return api_error("Not authenticated")
    if not is_admin(actor):
        return permission_denied()
    prefix = token_prefix.strip()
    if len(prefix) != SESSION_TOKEN_PREFIX_LENGTH:
        return api_error(f"token_prefix must be exactly {SESSION_TOKEN_PREFIX_LENGTH} characters")
    sessions = load_sessions()
    matched = [token for token in sessions if token.startswith(prefix)]
    if len(matched) > 1:
        return api_error("Ambiguous token prefix")
    for token in matched:
        sessions.pop(token, None)
    save_sessions(sessions)
    return api_ok(revoked=len(matched))


@router.post("/auth/login")
async def login(req: LoginRequest):
    users = load_users()
    user = users.get(req.username.strip())
    if not user or not user.get("active", True):
        return {"status": "error", "message": "Invalid username or password"}
    if not verify_password(req.password, str(user.get("password_hash") or "")):
        return {"status": "error", "message": "Invalid username or password"}

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(hours=session_hours())).isoformat()
    sessions = prune_expired_sessions(load_sessions())
    sessions[token] = {"user_id": user["user_id"], "created_at": datetime.now().isoformat(), "expires_at": expires_at}
    save_sessions(sessions)
    return {"status": "ok", "token": token, "expires_at": expires_at, "user": public_user(user)}


@router.get("/auth/login-config")
async def login_config():
    return {
        "status": "ok",
        "production": production_mode(),
        "initial_password_configured": not initial_password_is_placeholder(),
        "bootstrap_users": bootstrap_user_summaries(),
    }


@router.get("/auth/me")
async def me(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    current = actor_from_token(authorization)
    if not current:
        return {"status": "error", "message": "Not authenticated"}
    return {"status": "ok", "user": current}


@router.post("/auth/logout")
async def logout(req: LogoutRequest, authorization: Optional[str] = Header(default=None, alias="Authorization")):
    token = req.token or bearer_token(authorization)
    if not token:
        return {"status": "ok"}
    sessions = load_sessions()
    sessions.pop(token, None)
    save_sessions(sessions)
    return {"status": "ok"}


def ensure_user_store() -> None:
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
    try:
        with open(USER_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def save_users(users: Dict[str, dict]) -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    with open(USER_FILE, "w", encoding="utf-8") as file:
        json.dump(users, file, ensure_ascii=False, indent=2)


def load_sessions() -> Dict[str, dict]:
    os.makedirs(DB_DIR, exist_ok=True)
    if not os.path.exists(SESSION_FILE):
        return {}
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_sessions(sessions: Dict[str, dict]) -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as file:
        json.dump(sessions, file, ensure_ascii=False, indent=2)


def revoke_user_sessions(user_id: str) -> int:
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
    now = datetime.now()
    return {
        token: session
        for token, session in sessions.items()
        if _parse_session_expiry(session) > now
    }


def _parse_session_expiry(session: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(session.get("expires_at") or ""))
    except ValueError:
        return datetime.now() - timedelta(seconds=1)


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
    }


def bearer_token(authorization: Optional[str]) -> str:
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def actor_from_token(authorization: Optional[str]) -> Optional[dict]:
    token = bearer_token(authorization)
    if not token:
        return None
    sessions = load_sessions()
    session = sessions.get(token)
    if not session:
        return None
    expires_at = _parse_session_expiry(session)
    if expires_at < datetime.now():
        sessions.pop(token, None)
        save_sessions(sessions)
        return None
    actor = resolve_user(str(session.get("user_id") or ""))
    return actor if actor.get("user_id") else None


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
        return bool(linked_issue) and can_view_issue(actor, linked_issue)
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
