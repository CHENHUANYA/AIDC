import unittest
from unittest.mock import patch

from routes import stats_routes


class StatsRouteRobustnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_stats_tolerates_malformed_log_entries(self):
        actor = {"user_id": "supervisor01", "role": "supervisor", "line_scope": ["*"], "team": "supervisor"}
        logs = [
            {"query": "alarm 3000", "collection": "808d", "elapsed_ms": 120, "date": "2026-06-08"},
            {"elapsed_ms": 80},
            {"query": None, "collection": ""},
        ]

        with patch.object(stats_routes, "query_log", logs):
            result = await stats_routes.query_stats(actor=actor)

        self.assertEqual(3, result["total"])
        self.assertEqual([("3000", 1)], result["top_codes"])
        self.assertEqual(1, result["by_collection"]["808d"])
        self.assertEqual(2, result["by_collection"]["unknown"])

    async def test_supervisor_can_clear_alarm_stats(self):
        actor = {"user_id": "supervisor01", "role": "supervisor", "line_scope": ["*"], "team": "supervisor"}
        logs = [{"alarm_code": "3000", "date": "2026-06-24", "manual": "808d"}]

        with patch.object(stats_routes, "alarm_history", logs), patch.object(stats_routes.os.path, "exists", return_value=False):
            result = await stats_routes.clear_alarm_stats(actor=actor)

        self.assertEqual({"status": "ok"}, result)
        self.assertEqual([], logs)

    async def test_operator_cannot_clear_alarm_stats(self):
        actor = {"user_id": "operator01", "role": "operator", "line_scope": ["LINE-A"], "team": "LINE-A-DAY"}
        logs = [{"alarm_code": "3000", "date": "2026-06-24", "manual": "808d"}]

        with patch.object(stats_routes, "alarm_history", logs):
            result = await stats_routes.clear_alarm_stats(actor=actor)

        self.assertEqual({"status": "error", "message": "Permission denied"}, result)
        self.assertEqual(1, len(logs))

if __name__ == "__main__":
    unittest.main()
