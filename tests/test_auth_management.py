import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import auth


ADMIN = {"user_id": "admin02", "role": "admin", "line_scope": ["*"]}
SUPERVISOR = {"user_id": "supervisor01", "role": "supervisor", "line_scope": ["*"]}
OPERATOR = {"user_id": "operator01", "role": "operator", "line_scope": ["LINE-A"]}


@pytest.fixture
def local_auth_store(tmp_path, monkeypatch):
    db_dir = tmp_path / "nested" / "alarm_db"
    monkeypatch.setattr(auth, "DB_DIR", str(db_dir))
    monkeypatch.setattr(auth, "USER_FILE", str(db_dir / "users.json"))
    monkeypatch.setattr(auth, "SESSION_FILE", str(db_dir / "sessions.json"))
    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: False)
    return db_dir


@pytest.mark.parametrize(
    ("user_request", "existing", "message"),
    [
        (auth.CreateUserRequest(user_id="  ", password="StrongPass!"), {}, "user_id is required"),
        (
            auth.CreateUserRequest(user_id="bad/id", password="StrongPass!"),
            {},
            "user_id may only contain",
        ),
        (auth.CreateUserRequest(user_id="new", role="unknown", password="StrongPass!"), {}, "Invalid role"),
        (
            auth.CreateUserRequest(user_id="existing", password="StrongPass!"),
            {"existing": {}},
            "already exists",
        ),
        (
            auth.CreateUserRequest(user_id="new", password="password1"),
            {},
            "Password must be at least",
        ),
    ],
)
def test_create_user_validation_rejects_invalid_inputs(user_request, existing, message):
    assert message in str(auth.validate_create_user(user_request, existing))


def test_create_user_validation_requires_explicit_per_account_password(monkeypatch):
    request = auth.CreateUserRequest(user_id="new", password=None)
    monkeypatch.setattr(auth, "implicit_initial_password_error", lambda: "production password required")

    assert auth.validate_create_user(request, {}) == "Password is required for every new account"


def test_create_user_builds_normalized_account_and_returns_public_fields(monkeypatch):
    request = auth.CreateUserRequest(
        user_id=" new_user ",
        name=" New User ",
        role="maintenance",
        team=" repair ",
        line_scope=[" LINE-A ", "", "LINE-B"],
        password="StrongPass!",
    )
    monkeypatch.setattr(auth, "load_users", lambda: {})
    monkeypatch.setattr(auth, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(auth, "save_user", lambda _user_id, user, expected_updated_at=None: user)

    result = asyncio.run(auth._api_create_user(request, ADMIN))

    assert result["status"] == "ok"
    assert result["user"]["user_id"] == "new_user"
    assert result["user"]["name"] == "New User"
    assert result["user"]["line_scope"] == ["LINE-A", "LINE-B"]
    assert "password_hash" not in result["user"]


@pytest.mark.parametrize("actor,message", [({}, "Not authenticated"), (OPERATOR, "Permission denied")])
def test_create_user_requires_admin(actor, message):
    result = asyncio.run(
        auth._api_create_user(
            auth.CreateUserRequest(user_id="new", password="StrongPass!"),
            actor,
        )
    )
    assert result == {"status": "error", "message": message}


def test_last_active_admin_cannot_be_deactivated_or_demoted():
    user = {"user_id": "admin01", "role": "admin", "active": True}
    users = {"admin01": user}

    assert auth.validate_admin_role_change(
        "admin01", user, auth.UpdateUserRequest(active=False), ADMIN, users
    ) == "Cannot deactivate the last active admin"
    assert auth.validate_admin_role_change(
        "admin01", user, auth.UpdateUserRequest(role="operator"), ADMIN, users
    ) == "Cannot demote the last active admin"


def test_current_admin_cannot_deactivate_or_demote_self():
    user = {"user_id": "admin02", "role": "admin", "active": True}
    users = {"admin02": user, "admin01": {"role": "admin", "active": True}}

    assert "cannot deactivate itself" in str(
        auth.validate_admin_role_change("admin02", user, auth.UpdateUserRequest(active=False), ADMIN, users)
    )
    assert "cannot change its own admin role" in str(
        auth.validate_admin_role_change("admin02", user, auth.UpdateUserRequest(role="operator"), ADMIN, users)
    )
    assert auth.validate_admin_role_change(
        "admin02", user, auth.UpdateUserRequest(role="invalid"), {**ADMIN, "user_id": "admin01"}, users
    ) == "Invalid role"


def test_update_user_applies_all_fields_and_revokes_sessions(monkeypatch):
    user = {
        "user_id": "operator01",
        "name": "Old",
        "role": "operator",
        "team": "old",
        "line_scope": ["LINE-A"],
        "active": True,
        "updated_at": "v1",
        "must_change_password": False,
    }
    monkeypatch.setattr(auth, "load_users", lambda: {"operator01": user})
    monkeypatch.setattr(auth, "save_user", lambda _key, saved, expected_updated_at=None: saved)
    revoke = patch.object(auth, "revoke_user_sessions", return_value=2)
    request = auth.UpdateUserRequest(
        name=" New ",
        role="maintenance",
        team=" repair ",
        line_scope=[" LINE-B ", ""],
        active=False,
        expected_updated_at="v1",
    )

    with revoke as revoke_sessions:
        result = asyncio.run(auth._api_update_user(" operator01 ", request, ADMIN))

    assert result["status"] == "ok"
    assert result["user"] == {
        "user_id": "operator01",
        "name": "New",
        "role": "maintenance",
        "line_scope": ["LINE-B"],
        "team": "repair",
        "active": False,
        "created_at": "",
        "updated_at": "v1",
        "must_change_password": False,
    }
    assert result["sessions_revoked"] == 2
    revoke_sessions.assert_called_once_with("operator01")


@pytest.mark.parametrize(
    ("actor", "user_id", "users", "message"),
    [
        ({}, "operator01", {}, "Not authenticated"),
        (OPERATOR, "operator01", {}, "Permission denied"),
        (ADMIN, "missing", {}, "User missing not found"),
    ],
)
def test_update_user_rejects_access_and_missing_account(monkeypatch, actor, user_id, users, message):
    monkeypatch.setattr(auth, "load_users", lambda: users)
    result = asyncio.run(auth._api_update_user(user_id, auth.UpdateUserRequest(), actor))
    assert result == {"status": "error", "message": message}


@pytest.mark.parametrize(
    ("actor", "user_id", "users", "message"),
    [
        ({}, "operator01", {}, "Not authenticated"),
        (OPERATOR, "operator01", {}, "Permission denied"),
        (ADMIN, "admin02", {"admin02": {}}, "cannot revoke its own sessions"),
        (ADMIN, "missing", {}, "User missing not found"),
    ],
)
def test_revoke_user_sessions_endpoint_guards(monkeypatch, actor, user_id, users, message):
    monkeypatch.setattr(auth, "load_users", lambda: users)
    result = asyncio.run(auth._api_revoke_user_sessions(user_id, actor))
    assert message in result["message"]


def test_revoke_user_sessions_endpoint_reports_count(monkeypatch):
    monkeypatch.setattr(auth, "load_users", lambda: {"operator01": {}})
    monkeypatch.setattr(auth, "save_user", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(auth, "revoke_user_sessions", lambda _user_id: 3)

    assert asyncio.run(auth._api_revoke_user_sessions("operator01", ADMIN)) == {
        "status": "ok",
        "revoked": 3,
    }


def test_session_list_prunes_and_sorts_local_sessions(monkeypatch):
    sessions = {
        "b" * 64: {
            "user_id": "operator01",
            "created_at": "later",
            "expires_at": "2099-02-01T00:00:00+00:00",
        },
        "a" * 64: {
            "user_id": "admin02",
            "created_at": "earlier",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
        "c" * 64: {"user_id": "operator01", "expires_at": "invalid"},
    }
    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: False)
    monkeypatch.setattr(
        auth,
        "load_users",
        lambda: {"operator01": {"role": "operator"}, "admin02": {"role": "admin"}},
    )
    monkeypatch.setattr(auth.auth_sessions, "mutate_sessions", lambda _path, mutation: mutation(sessions))

    result = asyncio.run(auth.api_list_sessions(actor=ADMIN))

    assert result["total"] == 2
    assert result["limit"] == 200
    assert result["offset"] == 0
    assert result["has_more"] is False
    assert [entry["token_prefix"] for entry in result["sessions"]] == ["a" * 10, "b" * 10]
    assert [entry["role"] for entry in result["sessions"]] == ["admin", "operator"]
    assert list(sessions) == ["b" * 64, "a" * 64]


@pytest.mark.parametrize("actor,message", [({}, "Not authenticated"), (OPERATOR, "Permission denied")])
def test_session_list_requires_admin(actor, message):
    assert asyncio.run(auth.api_list_sessions(actor=actor)) == {"status": "error", "message": message}


def test_postgres_session_list_delegates(monkeypatch):
    entries = [{"token_prefix": "1234567890", "user_id": "operator01"}]
    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: True)
    monkeypatch.setattr(auth.postgres_sessions, "list_active_page", lambda **_kwargs: (entries, 1))

    assert asyncio.run(auth.api_list_sessions(actor=ADMIN)) == {
        "status": "ok",
        "total": 1,
        "limit": 200,
        "offset": 0,
        "has_more": False,
        "sessions": entries,
    }


def test_local_session_revoke_rejects_ambiguous_prefix_and_handles_exact_match(monkeypatch):
    prefix = "1234567890"
    sessions = {prefix + "a" * 54: {}, prefix + "b" * 54: {}}
    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: False)
    store = dict(sessions)
    monkeypatch.setattr(auth.auth_sessions, "mutate_sessions", lambda _path, mutation: mutation(store))
    ambiguous = asyncio.run(auth.api_revoke_session(prefix, actor=ADMIN))
    assert ambiguous == {"status": "error", "message": "Ambiguous token prefix"}
    assert store == sessions

    store = {prefix + "a" * 54: {}}
    revoked = asyncio.run(auth.api_revoke_session(prefix, actor=ADMIN))
    assert revoked == {"status": "ok", "revoked": 1}
    assert store == {}


@pytest.mark.parametrize(
    ("actor", "prefix", "message"),
    [
        ({}, "1234567890", "Not authenticated"),
        (OPERATOR, "1234567890", "Permission denied"),
        (ADMIN, "short", "must be exactly 10"),
    ],
)
def test_single_session_revoke_guards(actor, prefix, message):
    result = asyncio.run(auth.api_revoke_session(prefix, actor=actor))
    assert message in result["message"]


def test_postgres_session_revoke_maps_repository_ambiguity(monkeypatch):
    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: True)

    with patch.object(auth.postgres_sessions, "revoke_prefix", return_value=1):
        assert asyncio.run(auth.api_revoke_session("1234567890", actor=ADMIN))["revoked"] == 1
    with patch.object(auth.postgres_sessions, "revoke_prefix", side_effect=ValueError("Ambiguous token prefix")):
        assert asyncio.run(auth.api_revoke_session("1234567890", actor=ADMIN)) == {
            "status": "error",
            "message": "Ambiguous token prefix",
        }


def test_local_bootstrap_store_and_corrupt_json_recovery_behavior(local_auth_store, monkeypatch):
    db_dir = local_auth_store
    monkeypatch.setattr(auth, "configured_initial_password", lambda: "StrongPass!")
    monkeypatch.setattr(auth, "implicit_initial_password_error", lambda: None)
    monkeypatch.setattr(auth, "hash_password", lambda password: f"hashed:{password}")

    auth.ensure_user_store()
    users = auth.load_users()

    assert set(users) == {"admin01"}
    assert users["admin01"]["password_hash"] == "hashed:StrongPass!"
    assert users["admin01"]["must_change_password"] is True
    user_path = db_dir / "users.json"
    user_path.write_text("{broken", encoding="utf-8")
    assert auth.load_users() == {}
    user_path.write_text("[]", encoding="utf-8")
    assert auth.load_users() == {}


def test_local_user_save_and_optimistic_update(local_auth_store, monkeypatch):
    db_dir = local_auth_store
    db_dir.mkdir(parents=True)
    initial = {
        "operator01": {
            "user_id": "operator01",
            "name": "Old",
            "updated_at": "v1",
            "created_at": "created",
        }
    }
    (db_dir / "users.json").write_text(json.dumps(initial), encoding="utf-8")

    saved = auth.save_user(
        "operator01",
        {"user_id": "operator01", "name": "New"},
        expected_updated_at="v1",
    )

    assert saved["created_at"] == "created"
    assert saved["updated_at"] != "v1"
    assert auth.load_users()["operator01"]["name"] == "New"


def test_session_store_migrates_plain_tokens_and_skips_invalid_records(local_auth_store):
    db_dir = local_auth_store
    db_dir.mkdir(parents=True)
    session_path = db_dir / "sessions.json"
    raw_token = "plain-session-token"
    session = {"user_id": "operator01", "expires_at": "2099-01-01T00:00:00Z"}
    session_path.write_text(json.dumps({raw_token: session, "discarded": "invalid"}), encoding="utf-8")

    loaded = auth.load_sessions()

    assert loaded == {auth.session_token_digest(raw_token): session}
    assert json.loads(session_path.read_text(encoding="utf-8")) == loaded
    session_path.write_text("{broken", encoding="utf-8")
    assert auth.load_sessions() == {}
    session_path.write_text("[]", encoding="utf-8")
    assert auth.load_sessions() == {}


def test_revoke_user_sessions_updates_local_and_postgres_stores(local_auth_store, monkeypatch):
    db_dir = local_auth_store
    db_dir.mkdir(parents=True)
    sessions = {
        "a" * 64: {"user_id": "operator01"},
        "b" * 64: {"user_id": "maintenance01"},
    }
    (db_dir / "sessions.json").write_text(json.dumps(sessions), encoding="utf-8")

    assert auth.revoke_user_sessions("operator01") == 1
    assert list(auth.load_sessions()) == ["b" * 64]

    monkeypatch.setattr(auth, "postgres_store_enabled", lambda: True)
    with patch.object(auth.postgres_sessions, "revoke_user", return_value=4) as revoke:
        assert auth.revoke_user_sessions("operator01") == 4
    revoke.assert_called_once_with("operator01")
