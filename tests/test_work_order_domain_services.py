import json
import logging
from datetime import datetime, timedelta

from services import work_order_issue_sync, work_order_reporting


def apply_issue_update(order, issue, *, validation_error="", transition_error="", note=""):
    history_calls = []
    result = work_order_issue_sync.apply_issue_update(
        order,
        issue,
        work_order_id=order["id"],
        user_id="operator01",
        note=note,
        now="2026-08-21T12:00:00",
        validate_verification=lambda _order_id, _user_id: validation_error,
        status_transition_error=lambda _previous, _next: transition_error,
        append_history=lambda *args: history_calls.append(args),
        calculate_field_changes=lambda _before, _after, fields: list(fields),
    )
    return result, history_calls


def test_issue_sync_verifies_completed_order_and_records_history():
    order = {
        "id": "WO-1",
        "status": "completed",
        "completed_at": "2026-08-21T11:00:00",
    }

    result, history_calls = apply_issue_update(order, {"status": "verified"})

    assert result is order
    assert order["status"] == "verified"
    assert order["verified_by"] == "operator01"
    assert order["updated_at"] == "2026-08-21T12:00:00"
    assert history_calls[0][1:4] == (
        "issue_synced",
        "operator01",
        ["status", "verified_by"],
    )


def test_issue_sync_rejects_invalid_verification_and_transition():
    completed = {"id": "WO-1", "status": "completed"}
    rejected, history_calls = apply_issue_update(
        completed,
        {"status": "verified"},
        validation_error="missing closure evidence",
    )
    assert rejected is None
    assert completed == {"id": "WO-1", "status": "completed"}
    assert history_calls == []

    verified = {"id": "WO-2", "status": "verified"}
    rejected, history_calls = apply_issue_update(
        verified,
        {"status": "cancelled"},
        transition_error="terminal state",
    )
    assert rejected is None
    assert verified["status"] == "verified"
    assert history_calls == []


def test_issue_sync_reopens_order_and_appends_operator_note():
    order = {
        "id": "WO-1",
        "status": "verified",
        "assigned_to": "maintenance01",
        "verified_by": "operator01",
        "completed_at": "2026-08-21T11:00:00",
        "notes": "Existing note",
    }

    result, history_calls = apply_issue_update(
        order,
        {"status": "open"},
        note="Inspect the spindle again",
    )

    assert result is order
    assert order["status"] == "assigned"
    assert order["verified_by"] == ""
    assert order["completed_at"] == ""
    assert order["notes"] == "Existing note\n[Operator follow-up] Inspect the spindle again"
    assert history_calls[0][3] == ["completed_at", "notes", "status", "verified_by"]


def test_issue_sync_returns_unchanged_order_without_history():
    order = {"id": "WO-1", "status": "pending"}

    result, history_calls = apply_issue_update(order, {"status": "open"}, note="   ")

    assert result is order
    assert order == {"id": "WO-1", "status": "pending"}
    assert history_calls == []


def test_work_order_reporting_aggregates_operational_metrics():
    today = datetime.now().strftime("%Y-%m-%d")
    old_created = (datetime.now() - timedelta(hours=25)).isoformat()
    orders = [
        {
            "status": "completed",
            "priority": "high",
            "manual": "808d",
            "source": "manual",
            "machine_id": "CNC-01",
            "assigned_to": "maintenance01",
            "created_at": f"{today}T08:00:00",
            "completed_at": f"{today}T10:00:00",
            "kb_review_status": "pending_review",
        },
        {
            "status": "verified",
            "priority": "high",
            "manual": "808d",
            "source": "manual",
            "machine_id": "CNC-01",
            "assigned_to": "maintenance01",
            "created_at": f"{today}T07:00:00",
            "completed_at": f"{today}T11:00:00",
            "kb_review_status": "ingested",
        },
        {
            "status": "assigned",
            "priority": "medium",
            "manual": "",
            "source": "",
            "machine_id": "",
            "assigned_to": "maintenance01",
            "created_at": old_created,
            "completed_at": "",
        },
        {
            "status": "pending",
            "priority": "medium",
            "created_at": "invalid",
            "completed_at": "invalid",
        },
    ]

    result = work_order_reporting.build_order_stats(
        orders,
        statuses=["pending", "assigned", "completed", "verified", "cancelled"],
        priorities=["medium", "high"],
        knowledge_review_statuses=["not_ready", "pending_review", "ingested"],
        status_labels={"pending": "Pending"},
        priority_labels={"high": "High"},
    )

    assert result["total"] == 4
    assert result["avg_hours"] == 3.0
    assert result["median_hours"] == 4.0
    assert result["today_created"] == 2
    assert result["today_completed"] == 2
    assert result["pending_verification"] == 1
    assert result["closed_orders"] == 1
    assert result["completion_rate"] == 25.0
    assert result["assigned_orders"] == 1
    assert result["unassigned_open"] == 1
    assert result["overdue_open"] == 1
    assert result["top_machines"][0] == {"machine_id": "CNC-01", "count": 2}
    assert result["pending_knowledge_review"] == 1


def test_work_order_reporting_handles_missing_and_non_list_archives(tmp_path):
    missing = tmp_path / "missing"
    assert work_order_reporting.load_archived_orders(str(missing), logging.getLogger(__name__)) == ([], [])

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    non_list = archive_dir / "work_orders_archive_20260821.json"
    non_list.write_text(json.dumps({"id": "not-a-list"}), encoding="utf-8")

    archives, orders = work_order_reporting.load_archived_orders(
        str(archive_dir),
        logging.getLogger(__name__),
    )

    assert archives[0]["file"] == non_list.name
    assert archives[0]["count"] == 0
    assert orders == []
