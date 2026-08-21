from collections import deque
from datetime import datetime, timedelta, timezone
import json

from services import auth_sessions, login_throttle, work_order_lifecycle


def test_auth_session_cookie_and_expiry_policies(tmp_path):
    assert auth_sessions.session_cookie_secure("true", "http", "") is True
    assert auth_sessions.session_cookie_secure("false", "https", "https") is False
    assert auth_sessions.session_cookie_secure("auto", "http", "https, http") is True
    assert auth_sessions.session_cookie_secure("auto", "http", "") is False

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    naive_future = datetime.now() + timedelta(hours=1)
    sessions = {
        "aware": {"expires_at": future.isoformat()},
        "naive": {"expires_at": naive_future.isoformat()},
        "invalid": {"expires_at": "bad"},
    }
    pruned = auth_sessions.prune_expired_sessions(sessions)
    assert set(pruned) == {"aware", "naive"}

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"session_hours": 48}), encoding="utf-8")
    assert auth_sessions.session_hours("", settings) == 48
    settings.write_text(json.dumps({"session_hours": "bad"}), encoding="utf-8")
    assert auth_sessions.session_hours("", settings) == 12
    settings.write_text("not-json", encoding="utf-8")
    assert auth_sessions.session_hours("", settings) == 12


def test_auth_session_password_and_token_helpers():
    password_hash = auth_sessions.hash_password("strong-password", "fixed-salt")
    assert auth_sessions.verify_password("strong-password", password_hash) is True
    assert auth_sessions.verify_password("wrong-password", password_hash) is False
    assert auth_sessions.verify_password("password", "legacy-hash") is False
    assert auth_sessions.bearer_token(" Basic value ") == ""
    assert auth_sessions.bearer_token(" Bearer token ") == "token"
    assert len(auth_sessions.session_token_digest("token")) == 64


def test_login_throttle_prunes_expired_failures_and_locks_at_limit():
    state = login_throttle.LoginThrottleState(
        failures={"expired": deque([1.0]), "active": deque([95.0])},
        lockouts={"old-lock": 90.0},
        last_seen={"expired": 1.0, "active": 95.0, "old-lock": 90.0},
    )
    limits = login_throttle.LoginThrottleLimits(
        failure_limit=2,
        failure_window_seconds=10,
        lockout_seconds=30,
        max_keys=10,
        prune_interval_seconds=1,
    )
    login_throttle.prune_state(state, limits, 100.0)
    assert set(state.failures) == {"active"}
    assert state.lockouts == {}
    assert login_throttle.record_failure(state, limits, "new", 100.0) == 0
    assert login_throttle.record_failure(state, limits, "new", 101.0) == 30
    assert login_throttle.retry_after(state, limits, "new", 102.0) == 29
    login_throttle.discard_key(state, "new")
    assert login_throttle.retry_after(state, limits, "new", 103.0) == 0


def test_work_order_lifecycle_supports_legacy_request_field_tracking():
    class LegacyRequest:
        __fields_set__ = {"status", "description"}

    request = LegacyRequest()
    assert work_order_lifecycle.request_fields(request) == {"status", "description"}
    error = work_order_lifecycle.patch_permission_error(
        "operator",
        request,
        operator_fields={"status"},
        maintenance_fields={"status", "description"},
    )
    assert "description" in error
    assert work_order_lifecycle.patch_permission_error(
        "admin",
        request,
        operator_fields=set(),
        maintenance_fields=set(),
    ) == ""
