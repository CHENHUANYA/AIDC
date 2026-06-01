import ast
import os
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUTH_PATH = os.path.join(ROOT, "auth.py")
ADMIN_JS_PATH = os.path.join(ROOT, "static", "js", "pages", "admin.js")


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
        cls.admin_source = read_text(ADMIN_JS_PATH)
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
        self.assertIn(("router", "delete", "/mock-users/{user_id}/sessions"), routes)
        self.assertIn(("router", "patch", "/mock-users/{user_id}/password"), routes)

    def test_auth_security_helpers_are_present(self):
        expected = {
            "api_ok",
            "api_error",
            "permission_denied",
            "active_admin_count",
            "is_active_admin",
            "is_last_active_admin",
            "validate_admin_role_change",
            "validate_create_mock_user",
            "revoke_user_sessions",
        }

        self.assertTrue(expected.issubset(self.auth_functions))

    def test_last_admin_and_ambiguous_token_guards_are_kept(self):
        self.assertIn("Cannot deactivate the last active admin", self.auth_source)
        self.assertIn("Cannot demote the last active admin", self.auth_source)
        self.assertIn("Ambiguous token prefix", self.auth_source)
        self.assertIn("SESSION_TOKEN_PREFIX_LENGTH", self.auth_source)

    def test_admin_ui_exposes_user_session_revoke(self):
        self.assertIn("revokeAdminUserSessions", self.admin_source)
        self.assertIn("/mock-users/${encodeURIComponent(userId)}/sessions", self.admin_source)
        self.assertIn("Revoke Sessions", self.admin_source)


if __name__ == "__main__":
    unittest.main()
