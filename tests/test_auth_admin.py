import ast
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from auth import can_view_work_order


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUTH_PATH = os.path.join(ROOT, "auth.py")
ACCOUNT_SERVICE_PATH = os.path.join(ROOT, "services", "account_management.py")
ADMIN_JS_PATH = os.path.join(ROOT, "static", "js", "pages", "admin.js")
LOGIN_JS_PATH = os.path.join(ROOT, "static", "js", "pages", "login.js")
TEST_TMP = Path(ROOT) / "tests_tmp" / "auth_admin"


def read_text(path):
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def function_names(tree):
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def decorator_paths(node):
    paths = []
    for decorator in getattr(node, "decorator_list", []):
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        paths.append((func.value.id, func.attr, decorator.args[0].value if decorator.args else ""))
    return paths


class AuthAdminStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_source = read_text(AUTH_PATH)
        cls.account_service_source = read_text(ACCOUNT_SERVICE_PATH)
        cls.admin_source = read_text(ADMIN_JS_PATH)
        cls.login_source = read_text(LOGIN_JS_PATH)
        cls.auth_tree = ast.parse(cls.auth_source)
        cls.auth_functions = function_names(cls.auth_tree)

    def test_admin_session_management_routes_exist(self):
        routes = {
            (owner, method, path)
            for node in ast.walk(self.auth_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for owner, method, path in decorator_paths(node)
        }

        self.assertIn(("router", "get", "/sessions"), routes)
        self.assertIn(("router", "delete", "/sessions/{token_prefix}"), routes)
        self.assertIn(("router", "get", "/users"), routes)
        self.assertIn(("router", "post", "/users"), routes)
        self.assertIn(("router", "delete", "/users/{user_id}/sessions"), routes)
        self.assertIn(("router", "patch", "/users/{user_id}/password"), routes)
        self.assertIn(("router", "get", "/auth/login-config"), routes)

    def test_auth_security_helpers_are_present(self):
        expected = {
            "api_ok",
            "api_error",
            "permission_denied",
            "active_admin_count",
            "is_active_admin",
            "is_last_active_admin",
            "validate_admin_role_change",
            "revoke_user_sessions",
        }

        self.assertTrue(expected.issubset(self.auth_functions))

    def test_last_admin_and_ambiguous_token_guards_are_kept(self):
        self.assertIn("Cannot deactivate the last active admin", self.account_service_source)
        self.assertIn("Cannot demote the last active admin", self.account_service_source)
        self.assertIn("Ambiguous token prefix", self.auth_source)
        self.assertIn("SESSION_TOKEN_PREFIX_LENGTH", self.auth_source)

    def test_admin_ui_exposes_user_session_revoke(self):
        self.assertIn("revokeAdminUserSessions", self.admin_source)
        self.assertIn("/users/${encodeURIComponent(userId)}/sessions", self.admin_source)
        self.assertIn("撤銷 Session", self.admin_source)

    def test_login_ui_does_not_publish_bootstrap_account_ids(self):
        self.assertIn("/auth/login-config", self.login_source)
        self.assertIn("safeNextPath", self.login_source)
        self.assertNotIn("preselectUserFromNextPath", self.login_source)
        self.assertNotIn("admin01", self.login_source)
        self.assertNotIn("supervisor01", self.login_source)
        self.assertIn("ROLE_NEXT_PATHS", self.login_source)


class AuthPermissionRuntimeTests(unittest.TestCase):
    def test_maintenance_cannot_view_closed_work_orders(self):
        actor = {
            "user_id": "maintenance01",
            "role": "maintenance",
            "line_scope": ["LINE-A"],
            "team": "maintenance",
        }

        self.assertFalse(can_view_work_order(actor, {"status": "completed", "assigned_to": "maintenance01"}))
        self.assertFalse(can_view_work_order(actor, {"status": "verified", "assigned_to": "maintenance01"}))
        self.assertTrue(can_view_work_order(actor, {"status": "in_progress", "assigned_to": "maintenance01"}))


class AuthBootstrapRuntimeTests(unittest.TestCase):
    def make_case_dir(self) -> Path:
        base = TEST_TMP / uuid.uuid4().hex
        base.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        return base

    def test_production_bootstrap_rejects_placeholder_initial_password(self):
        import auth

        base = self.make_case_dir()
        with patch.dict(os.environ, {"ALARM_RAG_ENV": "production", "ADMIN_INITIAL_PASSWORD": "change-me-now"}):
            with patch.object(auth, "DB_DIR", str(base)):
                with patch.object(auth, "USER_FILE", str(base / "users.json")):
                    with self.assertRaises(RuntimeError):
                        auth.ensure_user_store()

        self.assertFalse((base / "users.json").exists())

    def test_development_bootstrap_allows_default_initial_password(self):
        import auth

        base = self.make_case_dir()
        with patch.dict(os.environ, {"ALARM_RAG_ENV": "development"}, clear=False):
            with patch.object(auth, "DB_DIR", str(base)):
                with patch.object(auth, "USER_FILE", str(base / "users.json")):
                    auth.ensure_user_store()

        self.assertTrue((base / "users.json").exists())


if __name__ == "__main__":
    unittest.main()
