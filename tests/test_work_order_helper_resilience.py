from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import issues
import work_orders


def test_work_order_identity_and_date_helpers(monkeypatch):
    monkeypatch.setattr(work_orders, "resolve_user", lambda user_id: {"user_id": user_id, "role": "maintenance"})
    monkeypatch.setattr(work_orders, "can_verify", lambda _actor: False)
    assert work_orders._is_operator_or_supervisor(" Operator01 ") is True
    assert work_orders._is_operator_or_supervisor("maintenance01") is False
    assert work_orders._parse_iso("") is None
    assert work_orders._parse_iso("invalid") is None
    assert work_orders._parse_iso("2026-08-21T00:00:00+00:00") is not None
    assert work_orders._now_like(datetime.now(timezone.utc)).tzinfo is not None
    assert work_orders._now_like(datetime.now()).tzinfo is None
    assert len(work_orders._recent_day_keys(3)) == 3


def test_postgres_storage_wrappers_use_single_record_repository(monkeypatch):
    order = {"id": "WO-1", "status": "pending"}
    monkeypatch.setattr(work_orders, "postgres_store_enabled", lambda: True)
    monkeypatch.setattr(work_orders.postgres_work_orders, "load_all", lambda: [order])
    save_all = MagicMock()
    get_one = MagicMock(return_value=order)
    save_one = MagicMock(return_value=order)
    monkeypatch.setattr(work_orders.postgres_work_orders, "save_all", save_all)
    monkeypatch.setattr(work_orders.postgres_work_orders, "get_one", get_one)
    monkeypatch.setattr(work_orders.postgres_work_orders, "save_one", save_one)

    assert work_orders._load_orders()[0]["id"] == "WO-1"
    work_orders._save_orders([order])
    assert work_orders._find_order("WO-1") == (-1, order)
    assert work_orders._persist_new_order(order) == order
    save_all.assert_called_once()
    get_one.assert_called_once_with("WO-1")
    save_one.assert_called_once_with(order)


def test_issue_map_and_restore_helpers_handle_success_missing_and_failure(monkeypatch):
    monkeypatch.setattr(issues, "_load_issues", lambda: [{"issue_id": "ISS-1"}, {"description": "missing id"}])
    assert work_orders._issue_map_by_id() == {"ISS-1": {"issue_id": "ISS-1"}}
    monkeypatch.setattr(issues, "_load_issues", MagicMock(side_effect=RuntimeError("offline")))
    assert work_orders._issue_map_by_id() == {}

    saved = []
    monkeypatch.setattr(issues, "_load_issues", lambda: [{"issue_id": "ISS-1", "status": "open"}])
    monkeypatch.setattr(issues, "_save_issues", lambda payload: saved.append(payload))
    work_orders._restore_json_issue({"issue_id": "ISS-1", "status": "assigned"})
    assert saved[0][0]["status"] == "assigned"
    saved.clear()
    work_orders._restore_json_issue({"issue_id": "ISS-MISSING"})
    assert saved == []


def test_issue_verification_reports_each_guard(monkeypatch):
    monkeypatch.setattr(work_orders, "_find_order", lambda _order_id: (-1, None))
    assert "not found" in work_orders.validate_issue_verification("WO-X", "operator01")
    monkeypatch.setattr(work_orders, "_find_order", lambda _order_id: (0, {"status": "pending"}))
    assert "must be completed" in work_orders.validate_issue_verification("WO-1", "operator01")
    monkeypatch.setattr(
        work_orders,
        "_find_order",
        lambda _order_id: (0, {"status": "completed", "root_cause": "", "repair_action": ""}),
    )
    assert "requires root_cause" in work_orders.validate_issue_verification("WO-1", "operator01")
    monkeypatch.setattr(
        work_orders,
        "_find_order",
        lambda _order_id: (0, {"status": "completed", "root_cause": "cause", "repair_action": "repair"}),
    )
    monkeypatch.setattr(work_orders, "_is_operator_or_supervisor", lambda _user_id: False)
    assert "operator or supervisor" in work_orders.validate_issue_verification("WO-1", "maintenance01")
    monkeypatch.setattr(work_orders, "_is_operator_or_supervisor", lambda _user_id: True)
    assert work_orders.validate_issue_verification("WO-1", "operator01") == ""
