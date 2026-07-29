from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Dict, List

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.models import LoginSession, LoginThrottle, User
from db.session import session_scope


TOKEN_PREFIX_LENGTH = 10


class ConcurrentUserUpdateError(RuntimeError):
    """Raised when a caller tries to save a stale user record."""


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def throttle_key_digest(rate_key: str) -> str:
    return hashlib.sha256(rate_key.encode("utf-8")).hexdigest()


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # Older callers emitted local wall-clock timestamps without an offset.
        # Interpret those as local time before normalizing instead of relabeling
        # them as UTC, which shifts expiry in every non-UTC deployment.
        return parsed.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def utc_datetime(value: datetime) -> datetime:
    """Normalize database timestamps, including timezone-naive test/legacy rows."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def same_instant(left: str, right: datetime | None) -> bool:
    if not left or right is None:
        return False
    try:
        return parse_datetime(left) == right.astimezone(timezone.utc)
    except ValueError:
        return False


def user_dict(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "role": user.role,
        "line_scope": list(user.line_scope or []),
        "team": user.team,
        "active": user.active,
        "password_hash": user.password_hash,
        "created_at": iso(user.created_at),
        "updated_at": iso(user.updated_at),
    }


def apply_user_payload(user: User, user_id: str, payload: dict) -> None:
    user.name = str(payload.get("name") or user_id)
    user.role = str(payload.get("role") or "operator")
    user.team = str(payload.get("team") or "")
    user.line_scope = [str(item) for item in payload.get("line_scope", [])]
    user.password_hash = str(payload.get("password_hash") or "")
    user.active = bool(payload.get("active", True))


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
                apply_user_payload(user, user_id, payload)

    def save_one(self, user_id: str, payload: dict, expected_updated_at: str | None = None) -> dict:
        """Upsert one account without rewriting unrelated, possibly stale rows."""
        with session_scope() as session:
            user = session.scalar(select(User).where(User.user_id == user_id).with_for_update())
            if user is None:
                if expected_updated_at:
                    raise ConcurrentUserUpdateError(f"User {user_id} no longer matches the expected version")
                user = User(user_id=user_id)
                session.add(user)
            elif expected_updated_at and not same_instant(expected_updated_at, user.updated_at):
                raise ConcurrentUserUpdateError(f"User {user_id} was updated by another request")
            apply_user_payload(user, user_id, payload)
            session.flush()
            session.refresh(user)
            return user_dict(user)


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
            row = session.execute(
                select(LoginSession, User.user_id, User.active)
                .join(User, User.id == LoginSession.user_id)
                .where(LoginSession.token_hash == digest)
            ).one_or_none()
            if row is None:
                return None
            record, user_id, active = row
            if record.revoked_at is not None or utc_datetime(record.expires_at) <= now or not active:
                session.delete(record)
                return None
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
                .where(
                    LoginSession.revoked_at.is_(None),
                    LoginSession.expires_at > now,
                    User.active.is_(True),
                )
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


class PostgresLoginThrottleRepository:
    @staticmethod
    def _expired_condition(current: datetime, retention_seconds: int):
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        cutoff = current - timedelta(seconds=retention_seconds)
        return (
            LoginThrottle.updated_at < cutoff,
            or_(LoginThrottle.locked_until.is_(None), LoginThrottle.locked_until <= current),
        )

    def _get_or_create_locked(self, session, key_hash: str, now: datetime) -> LoginThrottle:
        values = {
            "key_hash": key_hash,
            "failure_count": 0,
            "window_started_at": now,
            "updated_at": now,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(LoginThrottle).values(**values).on_conflict_do_nothing(
                index_elements=[LoginThrottle.key_hash]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(LoginThrottle).values(**values).on_conflict_do_nothing(
                index_elements=[LoginThrottle.key_hash]
            )
        else:
            raise RuntimeError(f"Unsupported login throttle database dialect: {dialect}")
        session.execute(statement)
        throttle = session.scalar(
            select(LoginThrottle)
            .where(LoginThrottle.key_hash == key_hash)
            .with_for_update()
        )
        if throttle is None:
            raise RuntimeError("Unable to create login throttle state")
        return throttle

    def retry_after(self, rate_key: str, window_seconds: int, now: datetime | None = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        key_hash = throttle_key_digest(rate_key)
        with session_scope() as session:
            throttle = session.scalar(
                select(LoginThrottle)
                .where(LoginThrottle.key_hash == key_hash)
                .with_for_update()
            )
            if throttle is None:
                return 0
            locked_until = utc_datetime(throttle.locked_until) if throttle.locked_until else None
            if locked_until and locked_until > current:
                throttle.updated_at = current
                return max(ceil((locked_until - current).total_seconds()), 1)
            window_started_at = utc_datetime(throttle.window_started_at)
            if window_started_at + timedelta(seconds=window_seconds) <= current:
                session.delete(throttle)
            else:
                throttle.locked_until = None
                throttle.updated_at = current
            return 0

    def record_failure(
        self,
        rate_key: str,
        limit: int,
        window_seconds: int,
        lockout_seconds: int,
        now: datetime | None = None,
    ) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        retention = timedelta(seconds=max(window_seconds, lockout_seconds))
        key_hash = throttle_key_digest(rate_key)
        with session_scope() as session:
            session.execute(
                delete(LoginThrottle).where(LoginThrottle.updated_at < current - retention)
            )
            throttle = self._get_or_create_locked(session, key_hash, current)
            window_started_at = utc_datetime(throttle.window_started_at)
            if window_started_at + timedelta(seconds=window_seconds) <= current:
                throttle.failure_count = 0
                throttle.window_started_at = current
                throttle.locked_until = None

            throttle.failure_count += 1
            throttle.updated_at = current
            if throttle.failure_count < limit:
                return 0

            throttle.failure_count = 0
            throttle.window_started_at = current
            throttle.locked_until = current + timedelta(seconds=lockout_seconds)
            return lockout_seconds

    def clear(self, rate_key: str) -> None:
        with session_scope() as session:
            session.execute(
                delete(LoginThrottle).where(
                    LoginThrottle.key_hash == throttle_key_digest(rate_key)
                )
            )

    def count_expired(self, retention_seconds: int, now: datetime | None = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with session_scope() as session:
            count = session.scalar(
                select(func.count())
                .select_from(LoginThrottle)
                .where(*self._expired_condition(current, retention_seconds))
            )
            return int(count or 0)

    def cleanup_expired(
        self,
        retention_seconds: int,
        *,
        batch_size: int = 1000,
        now: datetime | None = None,
    ) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with session_scope() as session:
            expired_keys = list(
                session.scalars(
                    select(LoginThrottle.key_hash)
                    .where(*self._expired_condition(current, retention_seconds))
                    .order_by(LoginThrottle.updated_at, LoginThrottle.key_hash)
                    .limit(batch_size)
                )
            )
            if not expired_keys:
                return 0
            result = session.execute(
                delete(LoginThrottle).where(
                    LoginThrottle.key_hash.in_(expired_keys),
                    *self._expired_condition(current, retention_seconds),
                )
            )
            return max(int(result.rowcount or 0), 0)
