import unittest
from unittest.mock import patch

from app_context import AlarmTrigger, ChatRequest
from routes import alarm_routes, chat_lookup_routes


AUTHENTICATED_ACTOR = {"user_id": "admin01", "role": "admin", "line_scope": ["*"], "team": "admin"}


class CollectionValidationTests(unittest.IsolatedAsyncioTestCase):
    def assert_invalid_name(self, payload, message):
        self.assertEqual("error", payload["status"])
        self.assertEqual(message, payload["message"])

    async def test_chat_collection_rejects_unsafe_collection_before_engine_lookup(self):
        request = ChatRequest(messages=[{"role": "user", "content": "hello"}])

        with patch.object(chat_lookup_routes, "get_existing_engine") as get_engine:
            result = await chat_lookup_routes.chat_collection(request, "../bad", actor=AUTHENTICATED_ACTOR)

        self.assert_invalid_name(result, "Invalid collection name")
        get_engine.assert_not_called()

    async def test_lookup_rejects_unsafe_collection_before_engine_lookup(self):
        with patch.object(chat_lookup_routes, "get_existing_engine") as get_engine:
            result = await chat_lookup_routes.lookup_alarm("../bad", code="1234", actor=AUTHENTICATED_ACTOR)

        self.assert_invalid_name(result, "Invalid collection name")
        get_engine.assert_not_called()

    async def test_trigger_alarm_rejects_unsafe_manual_before_engine_lookup(self):
        request = AlarmTrigger(alarm_code="1234", manual="../bad")

        with patch.object(alarm_routes, "get_engine") as get_engine:
            result = await alarm_routes.trigger_alarm(request, actor=AUTHENTICATED_ACTOR)

        self.assert_invalid_name(result, "Invalid manual name")
        get_engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
