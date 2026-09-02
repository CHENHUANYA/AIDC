import json
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from services import (
    work_order_issue_sync,
    work_order_operations,
    work_order_queries,
    work_order_reporting,
)


def apply_issue_update(order, issue, *, validation_error="", transition_error="", note="", allow_reopen=False):
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
        allow_reopen=allow_reopen,
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
        allow_reopen=True,
    )

    assert result is order
    assert order["status"] == "assigned"
    assert order["verified_by"] == ""
    assert order["completed_at"] == ""
    assert order["notes"] == "Existing note\n[Operator follow-up] Inspect the spindle again"
    assert history_calls[0][3] == ["completed_at", "notes", "status", "verified_by"]


def test_issue_sync_advances_json_revision():
    order = {"id": "WO-1", "status": "completed", "version": 4}
    history_calls = []
    result = work_order_issue_sync.apply_issue_update(
        order,
        {"status": "verified"},
        work_order_id="WO-1",
        user_id="operator01",
        note="",
        now="2026-08-21T12:00:00",
        validate_verification=lambda *_args: "",
        status_transition_error=lambda *_args: "",
        append_history=lambda *args: history_calls.append(args),
        calculate_field_changes=lambda _before, _after, fields: list(fields),
        increment_version=True,
    )
    assert result["version"] == 5


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


def query_dependencies(order, *, linked_issue=None, issue_error=None):
    get_issue = MagicMock(return_value=linked_issue, side_effect=issue_error)
    logger = MagicMock()
    dependencies = work_order_queries.QueryDependencies(
        find_order=lambda _order_id: (-1, order),
        get_issue=get_issue,
        can_view=lambda actor, _order, issue: actor.get("role") == "admin" or issue is not None,
        can_view_issue=lambda actor, _issue: actor.get("role") == "admin",
        history_list=lambda payload, field: list((payload or {}).get(field, [])),
        logger=logger,
    )
    return dependencies, get_issue, logger


def test_work_order_queries_return_detail_and_combined_history():
    order = {
        "id": "WO-1",
        "issue_id": "ISS-1",
        "work_order_history": [{"action": "created"}],
    }
    issue = {"issue_id": "ISS-1", "issue_history": [{"action": "linked"}]}
    dependencies, get_issue, _logger = query_dependencies(order, linked_issue=issue)

    detail = work_order_queries.get_order_response("WO-1", {"role": "operator"}, dependencies)
    history = work_order_queries.get_history_response("WO-1", {"role": "admin"}, dependencies)

    assert detail == {"status": "ok", "order": order}
    assert history["work_order_history"] == [{"action": "created"}]
    assert history["issue_history"] == [{"action": "linked"}]
    assert get_issue.call_count == 2


def test_work_order_history_hides_issue_history_without_independent_issue_access():
    order = {"id": "WO-1", "issue_id": "ISS-HIDDEN", "work_order_history": []}
    issue = {"issue_id": "ISS-HIDDEN", "issue_history": [{"action": "secret"}]}
    dependencies, _get_issue, _logger = query_dependencies(order, linked_issue=issue)

    history = work_order_queries.get_history_response(
        "WO-1",
        {"role": "maintenance"},
        dependencies,
    )

    assert history["status"] == "ok"
    assert history["issue_id"] == ""
    assert history["issue_history"] == []


def test_work_order_queries_treat_soft_deleted_order_as_missing():
    order = {"id": "WO-1", "deleted_at": "2026-09-02T00:00:00+00:00"}
    dependencies, _get_issue, _logger = query_dependencies(order)

    detail = work_order_queries.get_order_response("WO-1", {"role": "admin"}, dependencies)
    history = work_order_queries.get_history_response("WO-1", {"role": "admin"}, dependencies)

    assert detail == {"status": "error", "message": "Work order WO-1 not found"}
    assert history == {"status": "error", "message": "Work order WO-1 not found"}


def test_work_order_queries_fail_closed_when_linked_issue_lookup_fails():
    order = {"id": "WO-1", "issue_id": "ISS-1", "work_order_history": []}
    dependencies, _get_issue, logger = query_dependencies(
        order,
        issue_error=OSError("issue store unavailable"),
    )

    denied = work_order_queries.get_order_response(
        "WO-1",
        {"role": "operator"},
        dependencies,
    )
    admin_history = work_order_queries.get_history_response(
        "WO-1",
        {"role": "admin"},
        dependencies,
    )

    assert denied == {"status": "error", "message": "Permission denied"}
    assert admin_history["status"] == "ok"
    assert admin_history["issue_history"] == []
    assert logger.warning.call_count == 2


def test_work_order_queries_report_missing_order():
    dependencies, get_issue, _logger = query_dependencies(None)

    detail = work_order_queries.get_order_response("WO-X", {"role": "admin"}, dependencies)
    history = work_order_queries.get_history_response("WO-X", {"role": "admin"}, dependencies)

    assert detail["message"] == "Work order WO-X not found"
    assert history["message"] == "Work order WO-X not found"
    get_issue.assert_not_called()


def test_work_order_history_query_enforces_visibility():
    order = {"id": "WO-1", "work_order_history": []}
    dependencies, _get_issue, _logger = query_dependencies(order)

    result = work_order_queries.get_history_response(
        "WO-1",
        {"role": "operator"},
        dependencies,
    )

    assert result == {"status": "error", "message": "Permission denied"}


def operation_dependencies(order, *, sync_issue=None, unlink_issue=None, get_issue=None):
    return work_order_operations.OperationDependencies(
        get_one=lambda _order_id: order,
        save_one=lambda payload: payload,
        load_all=lambda: [order],
        save_all=lambda _orders: None,
        get_issue=get_issue or (lambda _issue_id: None),
        sync_issue=sync_issue or (lambda _order: {"issue_id": "ISS-1"}),
        unlink_issue=unlink_issue or (lambda _order: {"issue_id": "ISS-1"}),
        restore_issue=lambda _issue: None,
        append_history=lambda *_args: None,
        calculate_field_changes=lambda _before, _after, fields: list(fields),
        apply_soft_delete=lambda payload, **_kwargs: dict(payload),
        logger=MagicMock(),
    )


def test_postgres_update_propagates_missing_linked_issue_sync():
    order = {"id": "WO-1", "issue_id": "ISS-1"}
    dependencies = operation_dependencies(order, sync_issue=lambda _order: None)

    with pytest.raises(RuntimeError, match="was not synchronized"):
        work_order_operations.persist_update_and_sync_issue(
            order,
            dict(order),
            orders=[order],
            index=-1,
            use_postgres=True,
            linked_issue_snapshot=None,
            dependencies=dependencies,
        )


def test_postgres_delete_propagates_missing_linked_issue_unlink():
    order = {"id": "WO-1", "issue_id": "ISS-1"}
    dependencies = operation_dependencies(
        order,
        get_issue=lambda _issue_id: {"issue_id": "ISS-1"},
        unlink_issue=lambda _order: None,
    )

    with pytest.raises(RuntimeError, match="was not unlinked"):
        work_order_operations.soft_delete_order(
            "WO-1",
            deleted_by="admin01",
            use_postgres=True,
            dependencies=dependencies,
        )
