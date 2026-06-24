import shutil
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
        self.assertTrue((self.tmp_root / "system_settings.json").exists())


if __name__ == "__main__":
    unittest.main()
