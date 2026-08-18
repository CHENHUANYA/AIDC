import shutil
import sys
import types
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
            started = await work_orders.api_update_order(
                order["id"],
                work_orders.UpdateWorkOrder(
                    status="in_progress",
                    accepted_by="maintenance01",
                    version=order["version"],
                ),
                actor=MAINTENANCE,
            )
            self.assertEqual("ok", started["status"])
            result = await work_orders.api_update_order(
                order["id"],
                work_orders.UpdateWorkOrder(
                    status="completed",
                    root_cause="Door interlock sensor was dirty",
                    repair_action="Cleaned sensor and reset emergency stop",
                    resolution="Alarm cleared and spindle started normally",
                    completed_by="maintenance01",
                    version=started["order"]["version"],
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

    async def test_auto_feedback_text_uses_readable_chinese_labels(self):
        captured = {}

        class FakeRequest:
            def __init__(self, **payload):
                captured["payload"] = payload

        class FakeEngine:
            sections = [{"doc_id": "doc-1"}]

            def retrieve(self, query, top_k=5):
                return [{"meta": {"doc_id": "doc-1"}}]

        async def fake_ingest_text_entry(manual, request):
            return {"status": "ok", "doc_id": "doc-1"}

        app_context = types.ModuleType("app_context")
        app_context.IngestTextRequest = FakeRequest
        app_context.get_engine = lambda manual: FakeEngine()
        ingest_routes = types.ModuleType("routes.ingest_routes")
        ingest_routes.ingest_text_entry = fake_ingest_text_entry

        order = {
            "id": "WO-KB-TEXT",
            "alarm_code": "3000",
            "machine_id": "CNC-01",
            "description": "Spindle cannot start",
            "root_cause": "Door interlock sensor was dirty",
            "repair_action": "Cleaned sensor",
            "resolution": "Alarm cleared",
            "assigned_to": "maintenance01",
            "completed_at": "2026-07-09T10:00:00",
            "notes": "Verified on line",
            "manual": "808d",
        }

        with patch.dict(sys.modules, {
            "app_context": app_context,
            "routes.ingest_routes": ingest_routes,
        }):
            result = await work_orders._auto_feedback_to_kb(order)

        text = captured["payload"]["text"]
        self.assertTrue(result["auto_ingested"])
        self.assertIn("[維修工單]", text)
        self.assertIn("機台: CNC-01", text)
        self.assertIn("備註: Verified on line", text)
        self.assertNotIn("???", text)

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

    async def test_revision_and_rejection_are_persisted_with_new_versions(self):
        order = await self.complete_candidate()
        revision = await work_orders.review_work_order_knowledge(
            order["id"],
            work_orders.KnowledgeReviewRequest(
                action="needs_revision", note="Add torque reading", version=order["version"]
            ),
            actor=ADMIN,
        )
        self.assertEqual("ok", revision["status"])
        self.assertEqual("needs_revision", revision["order"]["kb_review_status"])
        self.assertEqual(order["version"] + 1, revision["order"]["version"])

        rejected = await work_orders.review_work_order_knowledge(
            order["id"],
            work_orders.KnowledgeReviewRequest(
                action="reject", note="Too site-specific", version=revision["order"]["version"]
            ),
            actor=ADMIN,
        )
        self.assertEqual("ok", rejected["status"])
        self.assertEqual("rejected", rejected["order"]["kb_review_status"])
        self.assertEqual(revision["order"]["version"] + 1, rejected["order"]["version"])

    async def test_stale_review_version_does_not_change_candidate(self):
        order = await self.complete_candidate()

        result = await work_orders.review_work_order_knowledge(
            order["id"],
            work_orders.KnowledgeReviewRequest(action="reject", version=order["version"] - 1),
            actor=ADMIN,
        )

        self.assertEqual("error", result["status"])
        self.assertEqual(work_orders.WORK_ORDER_STALE_UPDATE_MESSAGE, result["message"])
        self.assertEqual("pending_review", work_orders.get_order_dict(order["id"])["kb_review_status"])

    async def test_invalid_missing_and_not_ready_reviews_are_rejected(self):
        order = work_orders.create_order_dict(alarm_code="3000", created_by="admin01")

        invalid = await work_orders.review_work_order_knowledge(
            order["id"], work_orders.KnowledgeReviewRequest(action="publish"), actor=ADMIN
        )
        missing = await work_orders.review_work_order_knowledge(
            "WO-MISSING", work_orders.KnowledgeReviewRequest(action="approve"), actor=ADMIN
        )
        not_ready = await work_orders.review_work_order_knowledge(
            order["id"], work_orders.KnowledgeReviewRequest(action="approve"), actor=ADMIN
        )

        self.assertIn("Invalid review action", invalid["message"])
        self.assertIn("not found", missing["message"])
        self.assertIn("requires completed status", not_ready["message"])

    async def test_duplicate_candidate_is_recorded_without_ingestion(self):
        existing = await self.complete_candidate()
        orders = work_orders._load_orders()
        orders[0]["kb_review_status"] = "ingested"
        work_orders._save_orders(orders)
        duplicate = work_orders.create_order_dict(
            alarm_code=existing["alarm_code"],
            manual=existing["manual"],
            machine_id=existing["machine_id"],
            description=existing["description"],
            assigned_to="maintenance01",
            created_by="maintenance01",
        )
        duplicate.update({
            "status": "completed",
            "root_cause": existing["root_cause"],
            "repair_action": existing["repair_action"],
            "resolution": existing["resolution"],
            "kb_candidate": True,
            "kb_review_status": "pending_review",
        })
        orders = work_orders._load_orders()
        orders[0] = duplicate
        work_orders._save_orders(orders)

        with patch.object(work_orders, "_auto_feedback_to_kb", new=AsyncMock()) as ingest:
            result = await work_orders.review_work_order_knowledge(
                duplicate["id"], work_orders.KnowledgeReviewRequest(action="approve"), actor=ADMIN
            )

        self.assertEqual("error", result["status"])
        self.assertEqual(existing["id"], result["duplicate_of"])
        saved = work_orders.get_order_dict(duplicate["id"])
        self.assertEqual(existing["id"], saved["kb_duplicate_of"])
        self.assertEqual(duplicate["version"] + 1, saved["version"])
        self.assertEqual("knowledge_duplicate_detected", saved["work_order_history"][-1]["action"])
        ingest.assert_not_awaited()

    async def test_failed_ingestion_is_persisted_for_retry(self):
        order = await self.complete_candidate()
        ingest_result = {"auto_ingested": False, "error": "index unavailable"}

        with patch.object(
            work_orders, "_auto_feedback_to_kb", new=AsyncMock(return_value=ingest_result)
        ):
            result = await work_orders.review_work_order_knowledge(
                order["id"],
                work_orders.KnowledgeReviewRequest(action="approve", version=order["version"]),
                actor=ADMIN,
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("index unavailable", result["message"])
        saved = work_orders.get_order_dict(order["id"])
        self.assertEqual("validation_failed", saved["kb_review_status"])
        self.assertEqual(ingest_result, saved["kb_ingest_result"])

    async def test_auto_feedback_reports_ingest_exception(self):
        order = await self.complete_candidate()
        app_context = types.ModuleType("app_context")
        app_context.IngestTextRequest = lambda **payload: payload
        app_context.get_engine = lambda _manual: None
        ingest_routes = types.ModuleType("routes.ingest_routes")

        async def fail_ingest(_manual, _request):
            raise TimeoutError("provider timeout")

        ingest_routes.ingest_text_entry = fail_ingest
        with patch.dict(sys.modules, {
            "app_context": app_context,
            "routes.ingest_routes": ingest_routes,
        }):
            result = await work_orders._auto_feedback_to_kb(order)

        self.assertFalse(result["auto_ingested"])
        self.assertEqual("provider timeout", result["error"])

    async def test_postgres_review_saves_only_the_reviewed_order(self):
        order = {
            "id": "WO-PG-REVIEW",
            "status": "completed",
            "root_cause": "Loose connector",
            "repair_action": "Reseated connector",
            "resolution": "Alarm cleared",
            "kb_candidate": True,
            "kb_review_status": "pending_review",
            "kb_duplicate_of": "",
            "version": 4,
            "work_order_history": [],
        }
        saved = {**order, "kb_review_status": "rejected", "version": 5}
        with (
            patch.object(work_orders, "postgres_store_enabled", return_value=True),
            patch.object(work_orders, "_load_orders", return_value=[order]),
            patch.object(work_orders, "_save_orders", side_effect=AssertionError("must not save all orders")),
            patch.object(work_orders.postgres_work_orders, "save_one", return_value=saved) as save_one,
        ):
            result = await work_orders.review_work_order_knowledge.__wrapped__(
                order["id"],
                work_orders.KnowledgeReviewRequest(action="reject", version=4),
                actor=ADMIN,
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(5, result["order"]["version"])
        save_one.assert_called_once()


if __name__ == "__main__":
    unittest.main()
