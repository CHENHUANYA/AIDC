from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.base import Base
from repositories import postgres_auth
from repositories.postgres_auth import (
    ConcurrentUserUpdateError,
    PostgresSessionRepository,
    PostgresUserRepository,
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
