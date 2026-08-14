from datetime import datetime
from unittest.mock import patch

import pytest

import work_orders


@pytest.mark.parametrize(
    ("previous", "next_status", "message"),
    [
        ("pending", "pending", ""),
        ("completed", "assigned", "Completed work orders"),
        ("assigned", "verified", "must be completed"),
        ("verified", "assigned", "Verified work orders"),
        ("assigned", "in_progress", ""),
    ],
)
def test_work_order_status_transition_rules(previous, next_status, message):
    assert message in work_orders._status_transition_error(previous, next_status)


def test_work_order_closure_and_direct_verification_rules():
    assert "root_cause" in work_orders._closure_error({"status": "completed"})
    base = {"status": "verified", "root_cause": "cause", "repair_action": "repair"}
    assert "requires verified_by" in work_orders._closure_error(base)

    with patch.object(work_orders, "_is_operator_or_supervisor", return_value=False):
        assert "operator or supervisor verifier" in work_orders._closure_error({**base, "verified_by": "maint01"})
        assert "Only an operator" in work_orders._direct_verification_error({**base, "verified_by": "maint01"}, "maint01")

    with patch.object(work_orders, "_is_operator_or_supervisor", return_value=True):
        assert work_orders._closure_error({**base, "verified_by": "operator01"}) == ""
        assert "must match" in work_orders._direct_verification_error(
            {**base, "verified_by": "operator01"}, "supervisor01"
        )
        assert work_orders._direct_verification_error(
            {**base, "verified_by": "operator01"}, "operator01"
        ) == ""
    assert work_orders._direct_verification_error({"status": "completed"}, "maint01") == ""


def test_knowledge_review_state_tracks_readiness_and_relevant_changes():
    order = {
        "status": "completed",
        "root_cause": "Sensor dirty",
        "repair_action": "Cleaned sensor",
        "resolution": "Alarm cleared",
        "kb_review_status": "not_ready",
        "kb_reviewed_by": "old-reviewer",
        "kb_reviewed_at": "old-time",
    }
    assert work_orders._knowledge_candidate_ready(order) is True
    work_orders._refresh_knowledge_review_state(order, [])
    assert order["kb_candidate"] is True
    assert order["kb_review_status"] == "pending_review"
    assert order["kb_reviewed_by"] == ""

    order["kb_review_status"] = "ingested"
    work_orders._refresh_knowledge_review_state(order, ["priority"])
    assert order["kb_review_status"] == "ingested"
    work_orders._refresh_knowledge_review_state(order, ["root_cause"])
    assert order["kb_review_status"] == "pending_review"

    order["status"] = "assigned"
    order["kb_review_status"] = "rejected"
    work_orders._refresh_knowledge_review_state(order, [])
    assert order["kb_candidate"] is False
    assert order["kb_review_status"] == "not_ready"
    order["kb_review_status"] = "ingested"
    work_orders._refresh_knowledge_review_state(order, [])
    assert order["kb_review_status"] == "ingested"


def test_duplicate_knowledge_detection_requires_matching_ingested_case():
    candidate = {
        "id": "WO-NEW",
        "manual": "808d",
        "alarm_code": "3000",
        "root_cause": "Emergency stop pressed",
        "repair_action": "Release emergency stop",
        "resolution": "Machine ready",
    }
    matching = {**candidate, "id": "WO-OLD", "kb_review_status": "ingested"}
    assert work_orders._find_duplicate_knowledge_order(candidate, [matching]) == "WO-OLD"
    assert work_orders._find_duplicate_knowledge_order(candidate, [{**matching, "manual": "840d"}]) == ""
    assert work_orders._find_duplicate_knowledge_order(candidate, [{**matching, "alarm_code": "7001"}]) == ""
    assert work_orders._find_duplicate_knowledge_order(candidate, [{**matching, "kb_review_status": "rejected"}]) == ""
    assert work_orders._find_duplicate_knowledge_order(candidate, [{**matching, "id": "WO-NEW"}]) == ""
    assert work_orders._find_duplicate_knowledge_order({}, [matching]) == ""


def test_patch_permissions_and_time_helpers():
    operator_request = work_orders.UpdateWorkOrder(status="in_progress", priority="high")
    error = work_orders._work_order_patch_permission_error(
        {"user_id": "operator01", "role": "operator"}, operator_request
    )
    assert "priority" in error
    maintenance_request = work_orders.UpdateWorkOrder(root_cause="Sensor", verified_by="maint01")
    error = work_orders._work_order_patch_permission_error(
        {"user_id": "maint01", "role": "maintenance"}, maintenance_request
    )
    assert "verified_by" in error
    assert work_orders._work_order_patch_permission_error(
        {"user_id": "admin01", "role": "admin"}, operator_request
    ) == ""

    aware = work_orders._parse_iso("2026-08-14T10:00:00+00:00")
    assert aware is not None and aware.tzinfo is not None
    assert work_orders._parse_iso("") is None
    assert work_orders._parse_iso("invalid") is None
    assert work_orders._now_like(aware).tzinfo is not None
    assert work_orders._now_like(datetime(2026, 8, 14)).tzinfo is None
    days = work_orders._recent_day_keys(3)
    assert len(days) == 3 and days == sorted(days)
