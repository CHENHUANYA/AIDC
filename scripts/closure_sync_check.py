import asyncio
import faulthandler
import shutil
import sys
from pathlib import Path

faulthandler.enable()
faulthandler.dump_traceback_later(5, exit=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import issues
import work_orders


async def fake_auto_feedback_to_kb(order):
    return {"auto_ingested": False, "mocked": True, "order_id": order.get("id")}


def run(coro):
    return asyncio.run(coro)


def assert_status(payload: dict, expected: str) -> None:
    assert payload["status"] == expected, payload


def patch_order(order_id: str, **kwargs):
    return run(work_orders.api_update_order(order_id, work_orders.UpdateWorkOrder(**kwargs)))


def patch_issue(issue_id: str, **kwargs):
    return run(issues.api_update_issue(issue_id, issues.UpdateIssue(**kwargs)))


def main():
    work_orders._auto_feedback_to_kb = fake_auto_feedback_to_kb

    tmp_path = ROOT / "alarm_db" / "__closure_sync_check__"
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        issues.ISSUE_FILE = str(tmp_path / "issues.json")
        work_orders.WO_FILE = str(tmp_path / "work_orders.json")
        issues.DB_DIR = str(tmp_path)
        work_orders.DB_DIR = str(tmp_path)

        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="spindle alarm",
            line_id="LINE-A",
            created_by="operator01",
        )
        order = work_orders.create_order_dict(
            alarm_code="3000",
            machine_id="CNC-01",
            description="spindle alarm",
            issue_id=issue["issue_id"],
            created_by="operator01",
        )
        issues.set_issue_work_order(issue["issue_id"], order["id"], order["status"])

        rejected_early_issue_verify = patch_issue(
            issue["issue_id"],
            status="verified",
            operator_note="Trying to verify too early.",
            updated_by="operator01",
        )
        assert_status(rejected_early_issue_verify, "error")

        rejected_early_work_order_verify = patch_order(
            order["id"],
            status="verified",
            root_cause="simulated spindle alarm",
            repair_action="reset drive",
            verified_by="operator01",
            updated_by="operator01",
        )
        assert_status(rejected_early_work_order_verify, "error")

        rejected_complete = patch_order(
            order["id"],
            status="completed",
            resolution="reset drive",
            updated_by="maintenance01",
        )
        assert_status(rejected_complete, "error")

        completed = patch_order(
            order["id"],
            status="completed",
            resolution="reset drive",
            root_cause="simulated spindle alarm",
            repair_action="reset drive",
            updated_by="maintenance01",
        )
        assert_status(completed, "ok")
        assert completed["issue"]["status"] == "completed", completed
        assert completed["order"]["work_order_history"][-1]["action"] == "status_changed", completed
        assert completed["order"]["work_order_history"][-1]["changes"], completed

        rejected_completed_reopen_from_work_order = patch_order(order["id"], status="in_progress", updated_by="maintenance01")
        assert_status(rejected_completed_reopen_from_work_order, "error")

        order_history = run(work_orders.api_get_order_history(order["id"]))
        assert_status(order_history, "ok")
        assert order_history["work_order_history"], order_history
        assert order_history["issue_history"], order_history

        issue_history = run(issues.api_get_issue_history(issue["issue_id"]))
        assert_status(issue_history, "ok")
        assert issue_history["issue_history"], issue_history
        assert issue_history["work_order_history"], issue_history

        rejected_direct_issue_complete = patch_issue(issue["issue_id"], status="completed", updated_by="operator01")
        assert_status(rejected_direct_issue_complete, "error")

        rejected_verified = patch_order(
            order["id"],
            status="verified",
            root_cause="simulated spindle alarm",
            repair_action="reset drive",
            updated_by="maintenance01",
        )
        assert_status(rejected_verified, "error")

        rejected_maintenance_verified = patch_order(
            order["id"],
            status="verified",
            root_cause="simulated spindle alarm",
            repair_action="reset drive",
            verified_by="maintenance01",
            updated_by="maintenance01",
        )
        assert_status(rejected_maintenance_verified, "error")

        verified = patch_issue(
            issue["issue_id"],
            status="verified",
            operator_note="Operator confirmed resolved.",
            updated_by="operator01",
        )
        assert_status(verified, "ok")
        assert verified["work_order"]["status"] == "verified", verified

        rejected = patch_issue(issue["issue_id"], status="open", updated_by="operator01")
        assert_status(rejected, "error")

        reopened = patch_issue(
            issue["issue_id"],
            status="open",
            operator_note="Alarm returned after restart.",
            updated_by="operator01",
        )
        assert_status(reopened, "ok")
        assert reopened["work_order"]["status"] == "pending", reopened
        assert reopened["work_order"]["verified_by"] == "", reopened
        assert "Alarm returned after restart." in reopened["work_order"]["notes"], reopened
        assert reopened["work_order"]["work_order_history"][-1]["action"] == "issue_synced", reopened
        assert reopened["work_order"]["work_order_history"][-1]["changes"], reopened

        soft_deleted = run(work_orders.api_delete_order(order["id"]))
        assert_status(soft_deleted, "ok")
        fetched_deleted = run(work_orders.api_get_order(order["id"]))
        assert fetched_deleted["order"]["deleted_at"], fetched_deleted
        assert fetched_deleted["order"]["work_order_history"][-1]["action"] == "deleted", fetched_deleted
        assert fetched_deleted["order"]["work_order_history"][-1]["changes"], fetched_deleted
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    print("closure sync ok")


if __name__ == "__main__":
    main()
