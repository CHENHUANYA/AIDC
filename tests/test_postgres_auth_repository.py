from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.base import Base
from db.models import LoginSession, LoginThrottle
from repositories import postgres_auth
from repositories.postgres_auth import (
    ConcurrentUserUpdateError,
    CredentialChangedError,
    PostgresLoginThrottleRepository,
    PostgresSessionRepository,
    PostgresUserRepository,
    SessionCapacityError,
    throttle_key_digest,
    token_digest,
)


@contextmanager
def scoped_session(session: Session):
    yield session


@pytest.fixture
def repository_session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        monkeypatch.setattr(postgres_auth, "session_scope", lambda: scoped_session(session))
        yield session


def user_payload(name: str = "Operator") -> dict:
    return {
        "name": name,
        "role": "operator",
        "team": "A",
        "line_scope": ["L1"],
        "password_hash": "hashed-password",
        "active": True,
    }


def test_user_repository_upserts_loads_and_checks_optimistic_version(repository_session: Session) -> None:
    repository = PostgresUserRepository()
    repository.save_all({"operator01": user_payload()})
    loaded = repository.load_all()

    assert loaded["operator01"]["name"] == "Operator"
    assert loaded["operator01"]["line_scope"] == ["L1"]

    expected = loaded["operator01"]["updated_at"]
    saved = repository.save_one("operator01", user_payload("Updated"), expected_updated_at=expected)
    assert saved["name"] == "Updated"

    with pytest.raises(ConcurrentUserUpdateError, match="updated by another request"):
        repository.save_one(
            "operator01",
            user_payload("Stale"),
            expected_updated_at="2020-01-01T00:00:00+00:00",
        )
    with pytest.raises(ConcurrentUserUpdateError, match="no longer matches"):
        repository.save_one("missing", user_payload(), expected_updated_at=expected)


def test_session_repository_lifecycle_and_prefix_revocation(repository_session: Session) -> None:
    users = PostgresUserRepository()
    users.save_all({"operator01": user_payload()})
    sessions = PostgresSessionRepository()
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(hours=1)).isoformat()

    sessions.create("token-one", "operator01", created_at, expires_at)
    loaded = sessions.get("token-one")
    assert loaded is not None
    assert loaded["user_id"] == "operator01"
    assert sessions.list_active()[0]["token_prefix"] == token_digest("token-one")[:10]
    assert sessions.revoke_prefix(token_digest("token-one")[:10]) == 1
    assert sessions.get("token-one") is None

    sessions.create("token-two", "operator01", created_at, expires_at)
    assert sessions.delete("token-two") is True
    assert sessions.delete("token-two") is False
    assert sessions.revoke_user("missing") == 0


def test_session_epoch_invalidates_rows_and_rejects_stale_login_commit(repository_session: Session) -> None:
    users = PostgresUserRepository()
    users.save_all({"operator01": user_payload()})
    stored = users.load_all()["operator01"]
    sessions = PostgresSessionRepository()
    now = datetime.now(timezone.utc)
    sessions.create("old-token", "operator01", now.isoformat(), (now + timedelta(hours=1)).isoformat())

    stored["credential_epoch"] = 2
    users.save_one("operator01", stored, expected_updated_at=stored["updated_at"])

    assert sessions.get("old-token") is None
    with pytest.raises(CredentialChangedError):
        sessions.create(
            "racing-token",
            "operator01",
            now.isoformat(),
            (now + timedelta(hours=1)).isoformat(),
            expected_credential_epoch=1,
        )


def test_session_repository_rejects_unknown_user_and_removes_expired_session(repository_session: Session) -> None:
    users = PostgresUserRepository()
    users.save_all({"operator01": user_payload()})
    sessions = PostgresSessionRepository()
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="not found"):
        sessions.create("bad-token", "missing", now.isoformat(), (now + timedelta(hours=1)).isoformat())

    sessions.create(
        "expired-token",
        "operator01",
        (now - timedelta(hours=2)).isoformat(),
        (now - timedelta(hours=1)).isoformat(),
    )
    assert sessions.get("expired-token") is None


def test_session_repository_enforces_per_user_and_global_capacity(
    repository_session: Session,
    monkeypatch,
) -> None:
    users = PostgresUserRepository()
    users.save_all({"operator01": user_payload(), "operator02": user_payload()})
    sessions = PostgresSessionRepository()
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=1)).isoformat()
    monkeypatch.setenv("SESSION_MAX_ACTIVE_PER_USER", "2")
    monkeypatch.setenv("SESSION_MAX_ACTIVE_GLOBAL", "3")

    sessions.create("token-one", "operator01", now.isoformat(), expires)
    sessions.create("token-two", "operator01", now.isoformat(), expires)
    sessions.create("token-three", "operator01", now.isoformat(), expires)

    assert sum(
        sessions.get(token) is not None
        for token in ("token-one", "token-two", "token-three")
    ) == 2
    sessions.create("token-four", "operator02", now.isoformat(), expires)
    with pytest.raises(SessionCapacityError):
        sessions.create("token-five", "operator02", now.isoformat(), expires)
    assert repository_session.query(LoginSession).count() == 3


def test_login_throttle_repository_locks_clears_and_hashes_keys(repository_session: Session) -> None:
    throttles = PostgresLoginThrottleRepository()
    now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)

    assert throttles.record_failure("operator01", 2, 300, 60, now=now) == 0
    assert throttles.record_failure("operator01", 2, 300, 60, now=now + timedelta(seconds=1)) == 60
    assert throttles.retry_after("operator01", 300, now=now + timedelta(seconds=2)) == 59

    stored = repository_session.execute(
        Base.metadata.tables["login_throttles"].select()
    ).mappings().one()
    assert stored["key_hash"] == throttle_key_digest("operator01")
    assert "operator01" not in stored.values()

    throttles.clear("operator01")
    assert throttles.retry_after("operator01", 300, now=now + timedelta(seconds=3)) == 0


def test_login_throttle_repository_resets_windows_and_prunes_stale_rows(repository_session: Session) -> None:
    throttles = PostgresLoginThrottleRepository()
    now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)

    assert throttles.record_failure("old-user", 3, 30, 20, now=now) == 0
    assert throttles.record_failure("old-user", 3, 30, 20, now=now + timedelta(seconds=31)) == 0
    assert throttles.record_failure("new-user", 3, 30, 20, now=now + timedelta(seconds=62)) == 0

    rows = repository_session.execute(
        Base.metadata.tables["login_throttles"].select()
    ).mappings().all()
    assert [row["key_hash"] for row in rows] == [throttle_key_digest("new-user")]


def test_login_throttle_cleanup_is_bounded_and_preserves_active_locks(repository_session: Session) -> None:
    throttles = PostgresLoginThrottleRepository()
    current = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
    for rate_key in ("stale-one", "stale-two", "active-lock", "fresh"):
        throttles.record_failure(rate_key, 5, 300, 300, now=current)

    repository_session.get(LoginThrottle, throttle_key_digest("stale-one")).updated_at = current - timedelta(hours=1)
    repository_session.get(LoginThrottle, throttle_key_digest("stale-two")).updated_at = current - timedelta(hours=1)
    active = repository_session.get(LoginThrottle, throttle_key_digest("active-lock"))
    active.updated_at = current - timedelta(hours=1)
    active.locked_until = current + timedelta(minutes=5)
    repository_session.flush()

    assert throttles.count_expired(300, now=current) == 2
    assert throttles.cleanup_expired(300, batch_size=1, now=current) == 1
    assert throttles.count_expired(300, now=current) == 1
    assert repository_session.get(LoginThrottle, throttle_key_digest("active-lock")) is not None
    assert repository_session.get(LoginThrottle, throttle_key_digest("fresh")) is not None


def test_login_throttle_cleanup_rejects_unbounded_arguments(repository_session: Session) -> None:
    throttles = PostgresLoginThrottleRepository()

    with pytest.raises(ValueError, match="retention_seconds"):
        throttles.count_expired(0)
    with pytest.raises(ValueError, match="batch_size"):
        throttles.cleanup_expired(300, batch_size=0)


def test_login_throttle_batch_fails_closed_at_active_key_ceiling(repository_session: Session) -> None:
    throttles = PostgresLoginThrottleRepository()
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    assert throttles.record_failures(
        ("account:first", "global:login"),
        5,
        300,
        60,
        max_keys=2,
        now=now,
    ) == 0
    assert throttles.record_failures(
        ("account:second", "global:login"),
        5,
        300,
        60,
        max_keys=2,
        now=now + timedelta(seconds=1),
    ) == 60
    assert throttles.retry_after_many(
        ("account:third", "global:login"),
        300,
        now=now + timedelta(seconds=2),
    ) == 59
