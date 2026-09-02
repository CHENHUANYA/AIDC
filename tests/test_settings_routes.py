import shutil
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from routes import settings_routes


ADMIN = {"user_id": "admin01", "role": "admin", "line_scope": ["*"], "team": "admin"}
UNAUTHENTICATED = {"user_id": "", "role": "", "line_scope": [], "team": ""}


class SettingsRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_root = Path("tests_tmp") / f"settings_{uuid.uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=False)
        self.patches = [
            patch.object(settings_routes, "DB_DIR", str(self.tmp_root)),
            patch.object(settings_routes, "SETTINGS_FILE", str(self.tmp_root / "system_settings.json")),
            patch.dict(os.environ, {"SESSION_TTL_HOURS": ""}, clear=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    async def test_settings_require_authenticated_admin(self):
        result = await settings_routes.get_system_settings(actor=UNAUTHENTICATED)

        self.assertEqual("error", result["status"])
        self.assertEqual("Not authenticated", result["message"])

    async def test_update_settings_rejects_invalid_default_manual(self):
        result = await settings_routes.update_system_settings(
            settings_routes.UpdateSystemSettings(default_manual="../bad"),
            actor=ADMIN,
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid default_manual", result["message"])
        self.assertFalse((self.tmp_root / "system_settings.json").exists())

    async def test_update_settings_clamps_session_hours_and_persists(self):
        result = await settings_routes.update_system_settings(
            settings_routes.UpdateSystemSettings(default_manual="840d", session_hours=999),
            actor=ADMIN,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual("840d", result["settings"]["default_manual"])
        self.assertEqual(72, result["settings"]["session_hours"])
        self.assertTrue(result["settings"]["revision"])
        self.assertTrue((self.tmp_root / "system_settings.json").exists())

    async def test_update_settings_rejects_stale_revision(self):
        first = await settings_routes.update_system_settings(
            settings_routes.UpdateSystemSettings(default_manual="840d"),
            actor=ADMIN,
        )

        stale = await settings_routes.update_system_settings(
            settings_routes.UpdateSystemSettings(
                session_hours=24,
                expected_revision="stale-revision",
            ),
            actor=ADMIN,
        )

        self.assertEqual("error", stale["status"])
        self.assertIn("Reload and retry", stale["message"])
        current = await settings_routes.get_system_settings(actor=ADMIN)
        self.assertEqual(first["settings"]["revision"], current["settings"]["revision"])
        self.assertEqual(12, current["settings"]["session_hours"])

    async def test_environment_session_ttl_is_visible_and_read_only(self):
        with patch.dict(os.environ, {"SESSION_TTL_HOURS": "3"}, clear=False):
            current = await settings_routes.get_system_settings(actor=ADMIN)
            rejected = await settings_routes.update_system_settings(
                settings_routes.UpdateSystemSettings(session_hours=4), actor=ADMIN
            )

        self.assertEqual(3, current["settings"]["session_hours"])
        self.assertEqual("environment", current["settings"]["session_hours_source"])
        self.assertEqual("error", rejected["status"])
        self.assertIn("SESSION_TTL_HOURS", rejected["message"])


if __name__ == "__main__":
    unittest.main()
