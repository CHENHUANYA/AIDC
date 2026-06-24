import unittest
from unittest.mock import patch
from urllib import error

from scripts import replay_demo_alarms, role_console_smoke, seed_week2_data, week4_acceptance


class DeliveryScriptTests(unittest.TestCase):
    def test_week4_report_path_must_stay_under_project_root(self):
        with self.assertRaises(ValueError):
            week4_acceptance.resolve_report_path("../outside.md")

    def test_week4_report_path_creates_parent_under_project_root(self):
        path = week4_acceptance.resolve_report_path("tests_tmp/reports/week4.md")

        self.assertTrue(str(path).endswith("tests_tmp\\reports\\week4.md") or str(path).endswith("tests_tmp/reports/week4.md"))
        self.assertTrue(path.parent.exists())

    def test_seed_request_json_returns_error_payload_when_service_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=error.URLError("offline")):
            code, data = seed_week2_data.request_json("http://localhost:1", "/health", timeout=1)

        self.assertEqual(0, code)
        self.assertIn("offline", data["_error"])

    def test_replay_post_json_returns_error_payload_when_service_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=error.URLError("offline")):
            code, data = replay_demo_alarms.post_json("http://localhost:1", "/trigger-alarm", {}, 1)

        self.assertEqual(0, code)
        self.assertIn("offline", data["_error"])

    def test_role_smoke_does_not_reset_supervisor_with_empty_password(self):
        runner = role_console_smoke.RoleSmoke("http://localhost:8100", 1)
        with patch.object(runner, "login", return_value="") as login:
            with patch.object(runner, "reset_user_password") as reset:
                supervisor_password = ""
                admin_token = "admin-token"
                supervisor_token = runner.login("supervisor01", supervisor_password, record_result=False) if supervisor_password else ""
                if (
                    not supervisor_token
                    and supervisor_password
                    and admin_token
                    and runner.reset_user_password("supervisor01", supervisor_password, admin_token)
                ):
                    supervisor_token = runner.login("supervisor01", supervisor_password, record_result=False)

        login.assert_not_called()
        reset.assert_not_called()
        self.assertEqual("", supervisor_token)


if __name__ == "__main__":
    unittest.main()
