import os
import unittest
from unittest.mock import patch

from app_context import AlarmTrigger
from routes import alarm_routes


class AlarmTriggerRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_requires_actor_or_valid_token(self):
        with patch.dict(os.environ, {"ALARM_RAG_TRIGGER_TOKEN": "secret"}, clear=False):
            result = await alarm_routes.trigger_alarm(
                AlarmTrigger(alarm_code="3000", manual="808d", source="unit-test"),
                actor={"user_id": "", "role": ""},
                trigger_token="wrong",
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("Not authenticated", result["message"])

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


if __name__ == "__main__":
    unittest.main()
