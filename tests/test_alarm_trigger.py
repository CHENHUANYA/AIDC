import os
import unittest
from unittest.mock import patch

from app_context import AlarmTrigger
from routes import alarm_routes


class AlarmTriggerRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_external_event_key_is_source_scoped_and_stable(self):
        first = alarm_routes._external_event_key("n8n", "event-1")

        self.assertEqual(first, alarm_routes._external_event_key("N8N", "event-1"))
        self.assertNotEqual(first, alarm_routes._external_event_key("opcua", "event-1"))

    async def test_trigger_requires_actor_or_valid_token(self):
        with patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret"}, clear=False):
            result = await alarm_routes.trigger_alarm(
                AlarmTrigger(alarm_code="3000", manual="808d", source="unit-test"),
                actor={"user_id": "", "role": ""},
                trigger_token="wrong",
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("Not authenticated", result["message"])

    async def test_trigger_rejects_unknown_rag_answer_id(self):
        with patch.object(alarm_routes.rag_answers, "get", return_value=None):
            result = await alarm_routes.trigger_alarm(
                AlarmTrigger(alarm_code="3000", rag_answer_id="missing"),
                actor={"user_id": "admin01", "role": "admin"},
                trigger_token=None,
            )

        self.assertEqual({"status": "error", "message": "Unknown RAG answer ID"}, result)

    async def test_trigger_default_work_order_description_is_clean(self):
        created_orders = []

        def fake_create_order_dict(**kwargs):
            created_orders.append(kwargs)
            return {"id": "WO-1", "status": "open", **kwargs}

        patches = [
            patch.object(alarm_routes, "append_jsonl", lambda *_args, **_kwargs: None),
            patch.object(alarm_routes, "create_issue_dict", lambda **kwargs: {"issue_id": "ISS-1", **kwargs}),
            patch.object(alarm_routes, "create_order_dict", fake_create_order_dict),
            patch.object(alarm_routes, "set_issue_work_order", lambda *_args, **_kwargs: None),
            patch.object(alarm_routes, "get_engine", side_effect=RuntimeError("skip rag")),
        ]

        with patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret"}, clear=False):
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                result = await alarm_routes.trigger_alarm(
                    AlarmTrigger(alarm_code="3000", manual="808d", source="n8n-mock"),
                    actor={"user_id": "", "role": ""},
                    trigger_token="secret",
                )

        self.assertEqual("ok", result["status"])
        self.assertEqual("Alarm 3000 reported from n8n-mock", created_orders[0]["description"])
        self.assertNotIn("demo1234", created_orders[0]["description"])

    async def test_json_duplicate_external_event_returns_existing_workflow(self):
        alarm = {
            "alarm_code": "3000",
            "source": "n8n-mock",
            "external_event_id": "evt-duplicate",
            "issue_id": "ISS-1",
            "work_order_id": "WO-1",
        }
        issue = {"issue_id": "ISS-1"}
        order = {"id": "WO-1"}
        with (
            patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
            patch.object(alarm_routes, "alarm_history", [alarm]),
            patch.object(alarm_routes, "read_jsonl", return_value=[]),
            patch.object(alarm_routes, "get_issue_dict", return_value=issue),
            patch.object(alarm_routes, "get_order_dict", return_value=order),
            patch.object(alarm_routes, "create_issue_dict") as create_issue,
            patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret"}, clear=False),
        ):
            result = await alarm_routes.trigger_alarm(
                AlarmTrigger(
                    alarm_code="3000",
                    manual="808d",
                    source="n8n-mock",
                    external_event_id="evt-duplicate",
                ),
                actor={"user_id": "", "role": ""},
                trigger_token="secret",
            )

        self.assertTrue(result["duplicate"])
        self.assertEqual(issue, result["issue"])
        self.assertEqual(order, result["work_order"])
        create_issue.assert_not_called()

    async def test_json_new_external_event_persists_workflow_links(self):
        logs = []
        history = []
        pending = []
        issue = {"issue_id": "ISS-NEW"}
        order = {"id": "WO-NEW", "status": "pending"}
        with (
            patch.object(alarm_routes, "postgres_store_enabled", return_value=False),
            patch.object(alarm_routes, "alarm_history", history),
            patch.object(alarm_routes, "pending_alarms", pending),
            patch.object(alarm_routes, "read_jsonl", return_value=[]),
            patch.object(alarm_routes, "append_jsonl", side_effect=lambda _path, entry: logs.append(dict(entry))),
            patch.object(alarm_routes, "create_issue_dict", return_value=issue),
            patch.object(alarm_routes, "create_order_dict", return_value=order),
            patch.object(alarm_routes, "set_issue_work_order", return_value=issue),
            patch.object(alarm_routes, "get_engine", side_effect=RuntimeError("skip rag")),
            patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret"}, clear=False),
        ):
            result = await alarm_routes.trigger_alarm(
                AlarmTrigger(
                    alarm_code="3000",
                    manual="808d",
                    source="n8n-mock",
                    external_event_id="evt-new",
                ),
                actor={"user_id": "", "role": ""},
                trigger_token="secret",
            )

        self.assertFalse(result["duplicate"])
        self.assertEqual("ISS-NEW", logs[0]["issue_id"])
        self.assertEqual("WO-NEW", logs[0]["work_order_id"])
        self.assertEqual("evt-new", pending[0]["external_event_id"])


if __name__ == "__main__":
    unittest.main()
