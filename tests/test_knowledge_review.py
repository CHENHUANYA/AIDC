import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import work_orders


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"]}
MAINTENANCE = {"user_id": "maintenance01", "role": "maintenance", "line_scope": ["LINE-A"]}
SUPERVISOR = {"user_id": "supervisor01", "role": "supervisor", "line_scope": ["*"]}


class KnowledgeReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_root = Path("tests_tmp") / f"knowledge_review_{uuid.uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=False)
        self.patches = [
            patch.object(work_orders, "DB_DIR", str(self.tmp_root)),
            patch.object(work_orders, "WO_FILE", str(self.tmp_root / "work_orders.json")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    async def complete_candidate(self):
        order = work_orders.create_order_dict(
            alarm_code="3000",
            manual="808d",
            machine_id="CNC-01",
            description="Spindle cannot start",
            assigned_to="maintenance01",
            created_by="maintenance01",
        )
        with patch.object(work_orders, "_auto_feedback_to_kb", new=AsyncMock()) as ingest:
            result = await work_orders.api_update_order(
                order["id"],
                work_orders.UpdateWorkOrder(
                    status="completed",
                    root_cause="Door interlock sensor was dirty",
                    repair_action="Cleaned sensor and reset emergency stop",
                    resolution="Alarm cleared and spindle started normally",
                    completed_by="maintenance01",
                ),
                actor=MAINTENANCE,
            )
            ingest.assert_not_awaited()
        self.assertEqual("ok", result["status"])
        return result["order"]

    async def test_completed_order_becomes_pending_candidate_without_ingest(self):
        order = await self.complete_candidate()

        self.assertTrue(order["kb_candidate"])
        self.assertEqual("pending_review", order["kb_review_status"])
        self.assertEqual("", order["kb_ingested_at"])

    async def test_admin_approval_ingests_and_records_review(self):
        order = await self.complete_candidate()
        ingest_result = {
            "auto_ingested": True,
            "collection": "808d",
            "response": {"status": "ok"},
        }

        with patch.object(
            work_orders,
            "_auto_feedback_to_kb",
            new=AsyncMock(return_value=ingest_result),
        ) as ingest:
            result = await work_orders.review_work_order_knowledge(
                order["id"],
                work_orders.KnowledgeReviewRequest(action="approve", note="Approved case"),
                actor=ADMIN,
            )

        ingest.assert_awaited_once()
        self.assertEqual("ok", result["status"])
        saved = work_orders.get_order_dict(order["id"])
        self.assertEqual("ingested", saved["kb_review_status"])
        self.assertEqual("admin01", saved["kb_reviewed_by"])
        self.assertTrue(saved["kb_ingested_at"])
        self.assertEqual(ingest_result, saved["kb_ingest_result"])

    async def test_non_admin_cannot_review_candidate(self):
        order = await self.complete_candidate()

        result = await work_orders.review_work_order_knowledge(
            order["id"],
            work_orders.KnowledgeReviewRequest(action="approve"),
            actor=SUPERVISOR,
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("Permission denied", result["message"])
        self.assertEqual("pending_review", work_orders.get_order_dict(order["id"])["kb_review_status"])

    async def test_revision_request_requires_note(self):
        order = await self.complete_candidate()

        result = await work_orders.review_work_order_knowledge(
            order["id"],
            work_orders.KnowledgeReviewRequest(action="needs_revision"),
            actor=ADMIN,
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("Revision note is required", result["message"])


if __name__ == "__main__":
    unittest.main()
