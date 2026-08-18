import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import issues
import work_orders


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"]}


class WorkOrderDeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_root = Path("tests_tmp") / f"work_order_delete_{uuid.uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=False)
        self.patches = [
            patch.object(work_orders, "DB_DIR", str(self.tmp_root)),
            patch.object(work_orders, "WO_FILE", str(self.tmp_root / "work_orders.json")),
            patch.object(issues, "DB_DIR", str(self.tmp_root)),
            patch.object(issues, "ISSUE_FILE", str(self.tmp_root / "issues.json")),
            patch.object(work_orders, "postgres_store_enabled", return_value=False),
            patch.object(issues, "postgres_store_enabled", return_value=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def linked_records(self):
        issue = issues.create_issue_dict(
            machine_id="CNC-01",
            description="Spindle alarm",
            assigned_to="maintenance01",
            created_by="operator01",
        )
        order = work_orders.create_order_dict(
            alarm_code="3000",
            machine_id="CNC-01",
            description="Spindle alarm",
            assigned_to="maintenance01",
            issue_id=issue["issue_id"],
            created_by="operator01",
        )
        linked = issues.set_issue_work_order(
            issue["issue_id"], order["id"], status="assigned", updated_by="operator01"
        )
        return linked, order

    async def test_soft_delete_unlinks_and_reopens_linked_issue(self):
        issue, order = self.linked_records()

        result = await work_orders.api_delete_order(order["id"], actor=ADMIN)

        self.assertEqual("ok", result["status"])
        deleted = work_orders.get_order_dict(order["id"])
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(order["version"] + 1, deleted["version"])
        unlinked = issues.get_issue_dict(issue["issue_id"])
        self.assertEqual("", unlinked["work_order_id"])
        self.assertEqual("open", unlinked["status"])
        self.assertEqual(issue["version"] + 1, unlinked["version"])
        self.assertEqual("work_order_unlinked", unlinked["issue_history"][-1]["action"])

    async def test_unlink_failure_restores_both_json_records(self):
        issue, order = self.linked_records()

        with patch.object(issues, "unlink_issue_from_work_order", side_effect=OSError("disk full")):
            result = await work_orders.api_delete_order(order["id"], actor=ADMIN)

        self.assertEqual("error", result["status"])
        self.assertIn("rolled back", result["message"])
        restored_order = work_orders.get_order_dict(order["id"])
        restored_issue = issues.get_issue_dict(issue["issue_id"])
        self.assertFalse(restored_order.get("deleted_at"))
        self.assertEqual(order["version"], restored_order["version"])
        self.assertEqual(order["id"], restored_issue["work_order_id"])
        self.assertEqual(issue["version"], restored_issue["version"])

    async def test_delete_is_idempotent_and_missing_order_is_reported(self):
        _, order = self.linked_records()
        first = await work_orders.api_delete_order(order["id"], actor=ADMIN)
        saved = work_orders.get_order_dict(order["id"])
        second = await work_orders.api_delete_order(order["id"], actor=ADMIN)
        missing = await work_orders.api_delete_order("WO-MISSING", actor=ADMIN)

        self.assertEqual("ok", first["status"])
        self.assertEqual("ok", second["status"])
        self.assertEqual(saved, work_orders.get_order_dict(order["id"]))
        self.assertEqual("error", missing["status"])
        self.assertIn("not found", missing["message"])


if __name__ == "__main__":
    unittest.main()
