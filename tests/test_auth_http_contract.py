import asyncio
from unittest.mock import Mock, call, patch

import httpx
from fastapi import FastAPI

import auth


app = FastAPI()
app.include_router(auth.router)


async def request(method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def test_invalid_login_returns_401_with_bearer_challenge() -> None:
    with patch.object(auth, "load_users", return_value={}):
        response = asyncio.run(
            request("POST", "/auth/login", json={"username": "unknown", "password": "invalid-password"})
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"status": "error", "message": "Invalid username or password"}


def test_current_user_without_session_returns_401_with_bearer_challenge() -> None:
    response = asyncio.run(request("GET", "/auth/me"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"status": "error", "message": "Not authenticated"}


def test_public_login_config_does_not_disclose_bootstrap_account_ids() -> None:
    response = asyncio.run(request("GET", "/auth/login-config"))

    assert response.status_code == 200
    assert response.json()["bootstrap_users"] == []


def test_browser_login_uses_http_only_cookie_and_hashes_json_session_at_rest() -> None:
    user = {
        "user_id": "operator01",
        "name": "Operator",
        "role": "operator",
        "line_scope": ["LINE-A"],
        "team": "LINE-A-DAY",
        "active": True,
        "password_hash": auth.hash_password("correct-password"),
    }
    session_store = {}

    def load_sessions():
        return dict(session_store)

    def save_sessions(sessions):
        session_store.clear()
        session_store.update(sessions)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            login_response = await client.post(
                "/auth/login",
                json={"username": "operator01", "password": "correct-password"},
            )
            stored_keys_after_login = set(session_store)
            me_response = await client.get("/auth/me")
            logout_response = await client.post("/auth/logout")
            expired_response = await client.get("/auth/me")
            return login_response, stored_keys_after_login, me_response, logout_response, expired_response

    auth.clear_login_failures("operator01")
    with (
        patch.object(auth, "load_users", return_value={"operator01": user}),
        patch.object(auth, "postgres_store_enabled", return_value=False),
        patch.object(auth, "load_sessions", side_effect=load_sessions),
        patch.object(auth, "save_sessions", side_effect=save_sessions),
        patch.object(auth.runtime_metrics, "record_auth") as record_auth,
    ):
        login_response, stored_keys_after_login, me_response, logout_response, expired_response = asyncio.run(scenario())

    assert login_response.status_code == 200
    cookie = login_response.headers["set-cookie"]
    assert "alarm_rag_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    token = login_response.json()["token"]
    assert token not in stored_keys_after_login
    assert auth.session_token_digest(token) in stored_keys_after_login
    assert me_response.status_code == 200
    assert me_response.json()["user"]["user_id"] == "operator01"
    assert logout_response.status_code == 200
    assert "alarm_rag_session=" in logout_response.headers["set-cookie"]
    assert expired_response.status_code == 401
    record_auth.assert_called_once_with("success")


def test_repeated_invalid_logins_are_rate_limited() -> None:
    username = "rate-limited-user"
    auth.clear_login_failures(username, client_identity="127.0.0.1")
    with (
        patch.object(auth, "LOGIN_FAILURE_LIMIT", 2),
        patch.object(auth, "LOGIN_LOCKOUT_SECONDS", 60),
        patch.object(auth, "load_users", return_value={}),
        patch.object(auth.runtime_metrics, "record_auth") as record_auth,
    ):
        first = asyncio.run(
            request("POST", "/auth/login", json={"username": username, "password": "invalid-password"})
        )
        second = asyncio.run(
            request("POST", "/auth/login", json={"username": username, "password": "invalid-password"})
        )
        third = asyncio.run(
            request("POST", "/auth/login", json={"username": username, "password": "invalid-password"})
        )
    auth.clear_login_failures(username, client_identity="127.0.0.1")

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert third.status_code == 429
    assert record_auth.call_args_list == [
        call("failure"),
        call("throttled"),
        call("throttled"),
    ]


def test_login_throttle_isolated_by_client_identity() -> None:
    username = "shared-account"
    first_source = "198.51.100.10"
    second_source = "198.51.100.11"
    with (
        patch.object(auth, "LOGIN_FAILURE_LIMIT", 2),
        patch.object(auth, "LOGIN_LOCKOUT_SECONDS", 60),
        patch.object(auth, "postgres_store_enabled", return_value=False),
    ):
        assert auth.record_login_failure(username, client_identity=first_source, now=100.0) == 0
        assert auth.record_login_failure(username, client_identity=first_source, now=101.0) == 60
        assert auth.login_retry_after(username, client_identity=first_source, now=102.0) == 59
        assert auth.login_retry_after(username, client_identity=second_source, now=102.0) == 0

    auth.clear_login_failures(username, client_identity=first_source)
    auth.clear_login_failures(username, client_identity=second_source)


def test_login_rate_state_evicts_oldest_key_at_capacity() -> None:
    with auth._login_rate_lock:
        auth._login_failures.clear()
        auth._login_lockouts.clear()
        auth._login_last_seen.clear()
        auth._login_last_pruned_at = 0.0

    with (
        patch.object(auth, "LOGIN_FAILURE_LIMIT", 50),
        patch.object(auth, "LOGIN_RATE_MAX_KEYS", 2),
    ):
        auth.record_login_failure("first-user", now=100.0)
        auth.record_login_failure("second-user", now=101.0)
        auth.record_login_failure("third-user", now=102.0)

    with auth._login_rate_lock:
        active_keys = set(auth._login_failures) | set(auth._login_lockouts)
        assert len(active_keys) == 2
        assert "first-user" not in active_keys
        assert {"second-user", "third-user"} == active_keys
        auth._login_failures.clear()
        auth._login_lockouts.clear()
        auth._login_last_seen.clear()
        auth._login_last_pruned_at = 0.0


def test_postgresql_login_rate_functions_use_shared_repository() -> None:
    repository = Mock()
    repository.retry_after.return_value = 17
    repository.record_failure.return_value = 23

    with (
        patch.object(auth, "postgres_store_enabled", return_value=True),
        patch.object(auth, "postgres_login_throttles", repository),
    ):
        assert auth.login_retry_after(" Operator01 ") == 17
        assert auth.record_login_failure(" Operator01 ") == 23
        auth.clear_login_failures(" Operator01 ")

    repository.retry_after.assert_called_once_with("operator01", auth.LOGIN_FAILURE_WINDOW_SECONDS)
    repository.record_failure.assert_called_once_with(
        "operator01",
        auth.LOGIN_FAILURE_LIMIT,
        auth.LOGIN_FAILURE_WINDOW_SECONDS,
        auth.LOGIN_LOCKOUT_SECONDS,
    )
    repository.clear.assert_called_once_with("operator01")
