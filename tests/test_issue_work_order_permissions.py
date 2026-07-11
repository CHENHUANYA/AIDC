import shutil
from datetime import datetime, timedelta, timezone
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import issues
import work_orders


OPERATOR = {
    "user_id": "operator01",
    "role": "operator",
    "line_scope": ["LINE-A"],
    "team": "LINE-A-DAY",
}
SUPERVISOR = {
    "user_id": "supervisor01",
    "role": "supervisor",
    "line_scope": ["*"],
    "team": "supervisor",
}
MAINTENANCE = {
    "user_id": "maintenance01",
    "role": "maintenance",
    "line_scope": ["LINE-A"],
    "team": "maintenance",
}


class IssueWorkOrderPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_root = Path("tests_tmp") / f"issue_wo_{uuid.uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=False)
        self.patches = [
            patch.object(issues, "DB_DIR", str(self.tmp_root)),
            patch.object(issues, "ISSUE_FILE", str(self.tmp_root / "issues.json")),
            patch.object(work_orders, "DB_DIR", str(self.tmp_root)),
            patch.object(work_orders, "WO_FILE", str(self.tmp_root / "work_orders.json")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def create_linked_issue_and_order(self):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="Spindle alarm",
            line_id="LINE-A",
            created_by="operator01",
        )
        order = work_orders.create_order_dict(
            alarm_code="3000",
            machine_id="CNC-01",
            description="Spindle alarm",
            issue_id=issue["issue_id"],
            created_by="operator01",
        )
        issue = issues.set_issue_work_order(issue["issue_id"], order["id"], order["status"], "operator01")
        return issue, order

    async def test_operator_cannot_patch_issue_management_fields(self):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="Original description",
            line_id="LINE-A",
            created_by="operator01",
        )

        result = await issues.api_update_issue(
            issue["issue_id"],
            issues.UpdateIssue(description="Tampered description"),
            actor=OPERATOR,
        )

        self.assertEqual("error", result["status"])
        self.assertIn("description", result["message"])
        self.assertEqual("Original description", issues.get_issue_dict(issue["issue_id"])["description"])

    async def test_operator_cannot_patch_work_order_management_fields(self):
        _, order = self.create_linked_issue_and_order()

        result = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(priority="critical"),
            actor=OPERATOR,
        )

        self.assertEqual("error", result["status"])
        self.assertIn("priority", result["message"])
        self.assertEqual("medium", work_orders.get_order_dict(order["id"])["priority"])

    async def test_maintenance_cannot_patch_work_order_verification_fields(self):
        _, order = self.create_linked_issue_and_order()

        result = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(verified_by="maintenance01"),
            actor=MAINTENANCE,
        )

        self.assertEqual("error", result["status"])
        self.assertIn("verified_by", result["message"])
        self.assertEqual("", work_orders.get_order_dict(order["id"])["verified_by"])

    async def test_work_order_update_without_issue_changes_does_not_append_issue_history(self):
        issue, order = self.create_linked_issue_and_order()
        initial_history_count = len(issue["issue_history"])

        result = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(priority="high", version=order["version"]),
            actor=SUPERVISOR,
        )

        self.assertEqual("ok", result["status"])
        synced_issue = issues.get_issue_dict(issue["issue_id"])
        self.assertEqual(initial_history_count, len(synced_issue["issue_history"]))
        self.assertEqual("open", synced_issue["status"])



    async def test_issue_update_without_version_is_rejected(self):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="Original description",
            line_id="LINE-A",
            created_by="operator01",
        )

        result = await issues.api_update_issue(
            issue["issue_id"],
            issues.UpdateIssue(description="No version overwrite"),
            actor=SUPERVISOR,
        )

        self.assertEqual("error", result["status"])
        self.assertIn("Reload and retry", result["message"])
        self.assertEqual("Original description", issues.get_issue_dict(issue["issue_id"])["description"])

    async def test_work_order_update_without_version_is_rejected(self):
        _, order = self.create_linked_issue_and_order()

        result = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(priority="high"),
            actor=SUPERVISOR,
        )

        self.assertEqual("error", result["status"])
        self.assertIn("Reload and retry", result["message"])
        self.assertEqual("medium", work_orders.get_order_dict(order["id"])["priority"])

    async def test_stale_issue_update_is_rejected_with_reload_message(self):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="Original description",
            line_id="LINE-A",
            created_by="operator01",
        )
        fresh = await issues.api_update_issue(
            issue["issue_id"],
            issues.UpdateIssue(status="assigned", version=issue["version"]),
            actor=SUPERVISOR,
        )
        self.assertEqual("ok", fresh["status"])
        self.assertEqual(2, fresh["issue"]["version"])

        stale = await issues.api_update_issue(
            issue["issue_id"],
            issues.UpdateIssue(description="Stale overwrite", version=issue["version"]),
            actor=SUPERVISOR,
        )

        self.assertEqual("error", stale["status"])
        self.assertIn("Reload and retry", stale["message"])
        self.assertNotEqual("Stale overwrite", issues.get_issue_dict(issue["issue_id"])["description"])

    async def test_stale_work_order_update_is_rejected_with_reload_message(self):
        _, order = self.create_linked_issue_and_order()
        fresh = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(priority="high", version=order["version"]),
            actor=SUPERVISOR,
        )
        self.assertEqual("ok", fresh["status"])
        self.assertEqual(2, fresh["order"]["version"])

        stale = await work_orders.api_update_order(
            order["id"],
            work_orders.UpdateWorkOrder(priority="critical", version=order["version"]),
            actor=SUPERVISOR,
        )

        self.assertEqual("error", stale["status"])
        self.assertIn("Reload and retry", stale["message"])
        self.assertEqual("high", work_orders.get_order_dict(order["id"])["priority"])


    async def test_work_order_stats_accepts_timezone_aware_created_at(self):
        order = work_orders.create_order_dict(
            alarm_code="3000",
            machine_id="CNC-01",
            description="Aware timestamp order",
            created_by="operator01",
        )
        orders = work_orders._load_orders()
        orders[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        work_orders._save_orders(orders)

        result = await work_orders.api_order_stats(actor=SUPERVISOR)

        self.assertEqual(1, result["total"])
        self.assertEqual(1, result["overdue_open"])
        self.assertEqual(1, result["by_source"][order["source"]])
if __name__ == "__main__":
    unittest.main()
