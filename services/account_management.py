from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping


def is_active_admin(user: Mapping[str, Any]) -> bool:
    return bool(user.get("active", True)) and user.get("role") == "admin"


def active_admin_count(users: Mapping[str, Mapping[str, Any]]) -> int:
    return sum(1 for user in users.values() if is_active_admin(user))


def is_last_active_admin(user: Mapping[str, Any], users: Mapping[str, Mapping[str, Any]]) -> bool:
    return is_active_admin(user) and active_admin_count(users) <= 1


def valid_password(password: str) -> bool:
    normalized = password.strip()
    if len(normalized) < 8:
        return False
    return normalized.lower() not in {"password", "password1", "12345678", "change-me-now"}


def normalize_line_scope(line_scope: Iterable[Any] | None) -> list[str]:
    return [str(item).strip() for item in (line_scope or []) if str(item).strip()]


def is_valid_user_id(user_id: str) -> bool:
    return bool(user_id) and user_id.replace("-", "").replace("_", "").isalnum()


def build_user(
    request: Any,
    user_id: str,
    password: str,
    *,
    password_hasher: Callable[[str], str],
) -> dict[str, Any]:
    role = request.role or "operator"
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": user_id,
        "name": (request.name or user_id).strip(),
        "role": role,
        "line_scope": normalize_line_scope(request.line_scope),
        "team": (request.team or "").strip(),
        "active": True,
        "credential_epoch": 1,
        "must_change_password": False,
        "password_hash": password_hasher(password),
        "created_at": now,
        "updated_at": now,
    }


def validate_create_user(
    request: Any,
    existing_users: Mapping[str, Mapping[str, Any]],
    *,
    valid_roles: set[str],
    default_password: Callable[[], str],
    missing_password_error: Callable[[], str | None],
) -> str | None:
    user_id = request.user_id.strip()
    role = request.role or "operator"
    if not user_id:
        return "user_id is required"
    if not is_valid_user_id(user_id):
        return "user_id may only contain letters, numbers, dash, or underscore"
    if role not in valid_roles:
        return "Invalid role"
    if user_id in existing_users:
        return f"User {user_id} already exists"
    if not request.password:
        return "Password is required for every new account"
    if not valid_password(request.password):
        return "Password must be at least 8 characters and not use a common placeholder"
    return None


def validate_admin_role_change(
    user_id: str,
    user: Mapping[str, Any],
    request: Any,
    actor_user_id: str,
    users: Mapping[str, Mapping[str, Any]],
    *,
    valid_roles: set[str],
) -> str | None:
    if user_id == actor_user_id and request.active is False:
        return "The current admin account cannot deactivate itself"
    if request.role is not None and request.role not in valid_roles:
        return "Invalid role"
    if user_id == actor_user_id and request.role is not None and request.role != "admin":
        return "The current admin account cannot change its own admin role"
    if not is_last_active_admin(user, users):
        return None
    if request.active is False:
        return "Cannot deactivate the last active admin"
    if request.role is not None and request.role != "admin":
        return "Cannot demote the last active admin"
    return None


def public_user(user: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(user.get("user_id") or ""),
        "name": str(user.get("name") or ""),
        "role": str(user.get("role") or ""),
        "line_scope": user.get("line_scope") if isinstance(user.get("line_scope"), list) else [],
        "team": str(user.get("team") or ""),
        "active": bool(user.get("active", True)),
        "must_change_password": bool(user.get("must_change_password", False)),
        "created_at": str(user.get("created_at") or ""),
        "updated_at": str(user.get("updated_at") or ""),
    }
