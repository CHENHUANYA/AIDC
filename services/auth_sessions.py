from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from services.json_file_store import exclusive_file_lock, write_json_atomic


T = TypeVar("T")


def session_cookie_secure(configured: str, request_scheme: str, forwarded_proto: str) -> bool:
    normalized = configured.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    first_forwarded_proto = forwarded_proto.split(",", 1)[0].strip().lower()
    return request_scheme == "https" or first_forwarded_proto == "https"


def session_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def parse_session_expiry(session: Mapping[str, object]) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(session.get("expires_at") or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(seconds=1)


def prune_expired_sessions(sessions: Mapping[str, dict], *, now: datetime | None = None) -> dict[str, dict]:
    current = now or datetime.now(timezone.utc)
    return {token: session for token, session in sessions.items() if parse_session_expiry(session) > current}


def load_sessions(
    path: str | Path,
    *,
    save_migrated: Callable[[dict[str, dict]], None],
) -> dict[str, dict]:
    session_path = Path(path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(str(session_path) + ".lock"):
        normalized, migrated = _load_sessions_unlocked(session_path)
        if migrated:
            write_json_atomic(session_path, normalized)
        return normalized


def _load_sessions_unlocked(session_path: Path) -> tuple[dict[str, dict], bool]:
    if not session_path.exists():
        return {}, False
    try:
        with session_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}, False
    if not isinstance(payload, dict):
        return {}, False
    normalized: dict[str, dict] = {}
    migrated = False
    for stored_token, session in payload.items():
        key = str(stored_token)
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key.lower()):
            key = session_token_digest(key)
            migrated = True
        if isinstance(session, dict):
            normalized[key] = session
    return normalized, migrated


def save_sessions(path: str | Path, sessions: Mapping[str, dict]) -> None:
    session_path = Path(path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(str(session_path) + ".lock"):
        write_json_atomic(session_path, dict(sessions))


def mutate_sessions(
    path: str | Path,
    mutation: Callable[[dict[str, dict]], T],
) -> T:
    """Atomically load, mutate and publish the complete JSON session map."""
    session_path = Path(path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(str(session_path) + ".lock"):
        sessions, _ = _load_sessions_unlocked(session_path)
        result = mutation(sessions)
        write_json_atomic(session_path, sessions)
        return result


def revoke_user_sessions(sessions: Mapping[str, dict], user_id: str) -> dict[str, dict]:
    return {
        token: session
        for token, session in sessions.items()
        if str(session.get("user_id") or "") != user_id
    }


def session_hours(env_value: str, settings_path: str | Path) -> int:
    normalized = env_value.strip()
    if normalized:
        try:
            return min(max(int(normalized), 1), 72)
        except ValueError:
            pass
    try:
        with Path(settings_path).open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return 12
    value = payload.get("session_hours") if isinstance(payload, dict) else 12
    if not isinstance(value, int):
        return 12
    return min(max(value, 1), 72)


def hash_password(password: str, salt: str | None = None) -> str:
    password_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${password_salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    expected = hash_password(password, parts[1])
    return secrets.compare_digest(expected, password_hash)
