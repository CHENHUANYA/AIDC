from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import delete, select

from db.models import LoginSession, User
from db.session import session_scope


TOKEN_PREFIX_LENGTH = 10


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def user_dict(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "role": user.role,
        "line_scope": list(user.line_scope or []),
        "team": user.team,
        "active": user.active,
        "password_hash": user.password_hash,
    }


class PostgresUserRepository:
    def load_all(self) -> Dict[str, dict]:
        with session_scope() as session:
            users = session.scalars(select(User).order_by(User.user_id)).all()
            return {user.user_id: user_dict(user) for user in users}

    def save_all(self, users: Dict[str, dict]) -> None:
        with session_scope() as session:
            existing = {
                user.user_id: user
                for user in session.scalars(select(User).where(User.user_id.in_(list(users) or [""]))).all()
            }
            for user_id, payload in users.items():
                user = existing.get(user_id)
                if user is None:
                    user = User(user_id=user_id)
                    session.add(user)
                user.name = str(payload.get("name") or user_id)
                user.role = str(payload.get("role") or "operator")
                user.team = str(payload.get("team") or "")
                user.line_scope = [str(item) for item in payload.get("line_scope", [])]
                user.password_hash = str(payload.get("password_hash") or "")
                user.active = bool(payload.get("active", True))


class PostgresSessionRepository:
    def create(self, token: str, user_id: str, created_at: str, expires_at: str) -> None:
        with session_scope() as session:
            user_pk = session.scalar(select(User.id).where(User.user_id == user_id))
            if user_pk is None:
                raise ValueError(f"User {user_id} not found")
            session.add(LoginSession(
                token_hash=token_digest(token),
                user_id=user_pk,
                created_at=parse_datetime(created_at),
                expires_at=parse_datetime(expires_at),
            ))

    def get(self, token: str) -> dict | None:
        digest = token_digest(token)
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            record = session.scalar(select(LoginSession).where(LoginSession.token_hash == digest))
            if record is None or record.revoked_at is not None or record.expires_at <= now:
                if record is not None:
                    session.delete(record)
                return None
            user_id = session.scalar(select(User.user_id).where(User.id == record.user_id))
            record.last_seen_at = now
            return {
                "user_id": str(user_id or ""),
                "created_at": iso(record.created_at),
                "expires_at": iso(record.expires_at),
            }

    def delete(self, token: str) -> bool:
        with session_scope() as session:
            record = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(token)))
            if record is None:
                return False
            session.delete(record)
            return True

    def revoke_user(self, user_id: str) -> int:
        with session_scope() as session:
            user_pk = session.scalar(select(User.id).where(User.user_id == user_id))
            if user_pk is None:
                return 0
            result = session.execute(delete(LoginSession).where(LoginSession.user_id == user_pk))
            return int(result.rowcount or 0)

    def revoke_prefix(self, prefix: str) -> int:
        # PostgreSQL exposes a hash prefix, never the bearer token prefix.
        with session_scope() as session:
            records = session.scalars(
                select(LoginSession).where(LoginSession.token_hash.like(f"{prefix}%"))
            ).all()
            if len(records) > 1:
                raise ValueError("Ambiguous token prefix")
            for record in records:
                session.delete(record)
            return len(records)

    def list_active(self) -> List[dict]:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
            rows = session.execute(
                select(LoginSession, User.user_id, User.role)
                .join(User, User.id == LoginSession.user_id)
                .where(LoginSession.revoked_at.is_(None), LoginSession.expires_at > now)
                .order_by(LoginSession.expires_at)
            ).all()
            return [
                {
                    "token_prefix": record.token_hash[:TOKEN_PREFIX_LENGTH],
                    "user_id": user_id,
                    "role": role,
                    "created_at": iso(record.created_at),
                    "expires_at": iso(record.expires_at),
                }
                for record, user_id, role in rows
            ]
