import os
import unittest
from unittest.mock import patch

from scripts.env_utils import EnvConfigError, admin_initial_password


class EnvUtilsTests(unittest.TestCase):
    def test_admin_initial_password_rejects_placeholder_when_required(self):
        with patch.dict(os.environ, {"ADMIN_INITIAL_PASSWORD": "change-me-now"}, clear=False):
            with self.assertRaises(EnvConfigError):
                admin_initial_password()

    def test_admin_initial_password_accepts_configured_secret(self):
        with patch.dict(os.environ, {"ADMIN_INITIAL_PASSWORD": "configured-secret"}, clear=False):
            self.assertEqual("configured-secret", admin_initial_password())

    def test_admin_initial_password_optional_returns_empty_for_placeholder(self):
        with patch.dict(os.environ, {"ADMIN_INITIAL_PASSWORD": "change-me-now"}, clear=False):
            self.assertEqual("", admin_initial_password(required=False))


if __name__ == "__main__":
    unittest.main()
