import asyncio
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import auth
from repositories.postgres_auth import ConcurrentUserUpdateError, parse_datetime, same_instant
from routes import stats_routes
from vector_store import QdrantStore


ROOT = Path(__file__).resolve().parents[1]


def test_deactivating_user_revokes_existing_sessions():
    users = {
        "operator01": {
            "user_id": "operator01",
            "name": "Operator",
            "role": "operator",
            "active": True,
        },
        "admin01": {
            "user_id": "admin01",
            "name": "Admin",
            "role": "admin",
            "active": True,
        },
    }
    actor = {"user_id": "admin01", "role": "admin"}
    with (
        patch.object(auth, "load_users", return_value=users),
        patch.object(auth, "save_user", side_effect=lambda _user_id, user, **_kwargs: user) as save_user,
        patch.object(auth, "revoke_user_sessions", return_value=2) as revoke,
    ):
        result = asyncio.run(
            auth._api_update_user("operator01", auth.UpdateUserRequest(active=False), actor)
        )

    assert result["status"] == "ok"
    assert result["sessions_revoked"] == 2
    assert result["user"]["active"] is False
    save_user.assert_called_once()
    revoke.assert_called_once_with("operator01")


def test_stale_user_update_is_rejected_without_saving():
    users = {
        "operator01": {
            "user_id": "operator01",
            "name": "Operator",
            "role": "operator",
            "active": True,
            "updated_at": "2026-07-08T00:00:00+00:00",
        },
        "admin01": {"user_id": "admin01", "role": "admin", "active": True},
    }
    actor = {"user_id": "admin01", "role": "admin"}
    with (
        patch.object(auth, "load_users", return_value=users),
        patch.object(auth, "save_user") as save_user,
    ):
        result = asyncio.run(
            auth._api_update_user(
                "operator01",
                auth.UpdateUserRequest(name="new", expected_updated_at="2026-07-07T00:00:00+00:00"),
                actor,
            )
        )

    assert result["status"] == "error"
    assert "reload and retry" in result["message"]
    save_user.assert_not_called()


def test_postgresql_stale_user_save_is_reported_as_concurrency_error():
    users = {
        "operator01": {
            "user_id": "operator01",
            "name": "Operator",
            "role": "operator",
            "active": True,
            "updated_at": "2026-07-08T00:00:00+00:00",
        },
        "admin01": {"user_id": "admin01", "role": "admin", "active": True},
    }
    actor = {"user_id": "admin01", "role": "admin"}
    with (
        patch.object(auth, "load_users", return_value=users),
        patch.object(auth, "save_user", side_effect=ConcurrentUserUpdateError("stale")),
    ):
        result = asyncio.run(
            auth._api_update_user(
                "operator01",
                auth.UpdateUserRequest(name="new", expected_updated_at="2026-07-08T00:00:00+00:00"),
                actor,
            )
        )

    assert result["status"] == "error"
    assert "reload and retry" in result["message"]


def test_json_session_for_inactive_user_is_rejected_and_removed():
    token = "session-token"
    sessions = {
        token: {
            "user_id": "operator01",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
    }
    with (
        patch.object(auth, "postgres_store_enabled", return_value=False),
        patch.object(auth, "load_sessions", return_value=sessions),
        patch.object(
            auth,
            "resolve_user",
            return_value={"user_id": "operator01", "role": "operator", "active": False},
        ),
        patch.object(auth, "save_sessions") as save_sessions,
    ):
        assert auth.actor_from_token(f"Bearer {token}") is None

    save_sessions.assert_called_once_with({})


def test_postgresql_session_for_inactive_user_is_rejected():
    with (
        patch.object(auth, "postgres_store_enabled", return_value=True),
        patch.object(auth.postgres_sessions, "get", return_value={"user_id": "operator01"}),
        patch.object(
            auth,
            "resolve_user",
            return_value={"user_id": "operator01", "role": "operator", "active": False},
        ),
    ):
        assert auth.actor_from_token("Bearer session-token") is None


def test_login_emits_utc_session_timestamps():
    user = {
        "user_id": "operator01",
        "role": "operator",
        "active": True,
        "password_hash": "hash",
    }
    with (
        patch.object(auth, "load_users", return_value={"operator01": user}),
        patch.object(auth, "verify_password", return_value=True),
        patch.object(auth, "postgres_store_enabled", return_value=False),
        patch.object(auth, "load_sessions", return_value={}),
        patch.object(auth, "save_sessions"),
    ):
        result = asyncio.run(auth.login(auth.LoginRequest(username="operator01", password="secret")))

    created = datetime.fromisoformat(result["expires_at"])
    assert created.utcoffset() == timedelta(0)


def test_datetime_parser_normalizes_explicit_offset_to_utc():
    parsed = parse_datetime("2026-07-06T12:00:00+08:00")
    assert parsed == datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)


def test_invalid_user_version_is_treated_as_stale_not_exception():
    assert same_instant("not-a-date", datetime.now(timezone.utc)) is False


def test_postgresql_single_user_save_does_not_load_or_rewrite_other_accounts():
    payload = {"user_id": "operator01", "name": "Updated", "role": "operator"}
    with (
        patch.object(auth, "postgres_store_enabled", return_value=True),
        patch.object(auth.postgres_users, "save_one") as save_one,
        patch.object(auth, "load_users", side_effect=AssertionError("must not load all users")),
    ):
        auth.save_user("operator01", payload)

    save_one.assert_called_once_with("operator01", payload, expected_updated_at=None)


def test_readiness_returns_503_when_postgresql_is_unavailable():
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes, "get_engine", side_effect=RuntimeError("db down")),
    ):
        response = asyncio.run(stats_routes.ready())

    assert response.status_code == 503
    assert json.loads(response.body)["checks"]["database"] == "unavailable"


def test_readiness_checks_postgresql_connection():
    connection = MagicMock()
    connection.scalar.return_value = 1
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    with (
        patch.object(stats_routes, "postgres_store_enabled", return_value=True),
        patch.object(stats_routes, "get_engine", return_value=engine),
    ):
        response = asyncio.run(stats_routes.ready())

    assert response == {"status": "ok", "checks": {"database": "ok"}}
    connection.scalar.assert_called_once()


def test_qdrant_client_receives_required_api_key():
    client_module = types.ModuleType("qdrant_client")
    http_module = types.ModuleType("qdrant_client.http")
    models_module = types.ModuleType("qdrant_client.http.models")
    client_class = MagicMock()
    client_module.QdrantClient = client_class
    http_module.models = models_module
    with (
        patch.dict(
            sys.modules,
            {
                "qdrant_client": client_module,
                "qdrant_client.http": http_module,
                "qdrant_client.http.models": models_module,
            },
        ),
        patch.dict(
            os.environ,
            {"QDRANT_HOST": "qdrant", "QDRANT_PORT": "6333", "QDRANT_API_KEY": "secret-key"},
            clear=True,
        ),
    ):
        QdrantStore()

    client_class.assert_called_once_with(host="qdrant", port=6333, api_key="secret-key", https=False)


def test_qdrant_https_is_explicitly_opt_in():
    client_module = types.ModuleType("qdrant_client")
    http_module = types.ModuleType("qdrant_client.http")
    models_module = types.ModuleType("qdrant_client.http.models")
    client_class = MagicMock()
    client_module.QdrantClient = client_class
    http_module.models = models_module
    with (
        patch.dict(
            sys.modules,
            {
                "qdrant_client": client_module,
                "qdrant_client.http": http_module,
                "qdrant_client.http.models": models_module,
            },
        ),
        patch.dict(
            os.environ,
            {
                "QDRANT_HOST": "qdrant.example.com",
                "QDRANT_PORT": "443",
                "QDRANT_API_KEY": "secret-key",
                "QDRANT_HTTPS": "true",
            },
            clear=True,
        ),
    ):
        QdrantStore()

    client_class.assert_called_once_with(host="qdrant.example.com", port=443, api_key="secret-key", https=True)


def test_qdrant_and_postgresql_container_boundaries_are_declared():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    base_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    postgres_dockerfile = (ROOT / "Dockerfile.postgresql").read_text(encoding="utf-8")

    assert "${ALARM_RAG_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "${QDRANT_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "${N8N_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "QDRANT__SERVICE__API_KEY" in compose
    assert "QDRANT_API_KEY: ${QDRANT_API_KEY:?" in compose
    assert "USER alarm-rag" in base_dockerfile
    assert "USER alarm-rag" in postgres_dockerfile
