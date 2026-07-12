import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_PAGE_PREFIXES = {
    "/admin",
    "/assistant",
    "/dashboard",
    "/login",
    "/maintenance",
    "/operations",
    "/operator",
    "/supervisor",
}


def backend_routes() -> list[str]:
    routes = []
    for path in sorted([*ROOT.glob("routes/*.py"), *ROOT.glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr not in {"get", "post", "patch", "delete", "put"}:
                        continue
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        routes.append(decorator.args[0].value)
    return routes


def normalize_frontend_path(path: str) -> str:
    clean = path.split("?", 1)[0]
    return re.sub(r"\$\{[^}]+\}", "{param}", clean)


def route_matches(frontend_path: str, backend_path: str) -> bool:
    frontend_parts = frontend_path.strip("/").split("/") if frontend_path.strip("/") else []
    backend_parts = backend_path.strip("/").split("/") if backend_path.strip("/") else []
    if len(frontend_parts) != len(backend_parts):
        return False
    for frontend_part, backend_part in zip(frontend_parts, backend_parts):
        if backend_part.startswith("{") and backend_part.endswith("}"):
            continue
        if frontend_part.startswith("{") and frontend_part.endswith("}") and backend_part:
            continue
        if frontend_part != backend_part:
            return False
    return True


def frontend_api_paths() -> set[str]:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [*ROOT.glob("static/**/*.js"), *ROOT.glob("*.html")]
    )
    raw_paths = set()
    patterns = [
        r"apiJson\(`([^`]+)`",
        r"apiJson\('([^']+)'",
        r'apiJson\("([^"]+)"',
        r"apiPaged\(`([^`]+)`",
        r"apiPaged\('([^']+)'",
        r'apiPaged\("([^"]+)"',
        r"fetch\(`(?:\$\{[^}]+\})?(/[^`]+)`",
    ]
    for pattern in patterns:
        raw_paths.update(re.findall(pattern, source))
    return {
        normalize_frontend_path(path)
        for path in raw_paths
        if path.startswith("/")
        and not path.startswith("/static/")
        and not any(path.startswith(prefix) for prefix in STATIC_PAGE_PREFIXES)
    }


class FrontendApiContractTests(unittest.TestCase):
    def test_frontend_api_paths_match_backend_routes(self):
        routes = backend_routes()
        unmatched = [
            path
            for path in sorted(frontend_api_paths())
            if not any(route_matches(path, route) for route in routes)
        ]

        self.assertEqual([], unmatched)

    def test_role_home_routes_keep_supervisor_workbench_distinct_from_dashboard(self):
        source = (ROOT / "static" / "js" / "core" / "api.js").read_text(encoding="utf-8")

        self.assertIn("supervisor: '/supervisor'", source)
        self.assertNotIn("supervisor: '/dashboard'", source)

    def test_admin_user_updates_send_optimistic_lock_version(self):
        source = (ROOT / "static" / "js" / "pages" / "admin.js").read_text(encoding="utf-8")

        self.assertIn("expected_updated_at", source)
        self.assertIn("user.updated_at", source)
        self.assertIn("JSON.stringify(body)", source)

    def test_admin_settings_and_documents_send_optimistic_lock_versions(self):
        source = (ROOT / "static" / "js" / "pages" / "admin.js").read_text(encoding="utf-8")

        self.assertIn("adminSettingsRevision", source)
        self.assertIn("expected_revision", source)
        self.assertIn("document?.revision", source)
        self.assertIn("expected_revision=${encodeURIComponent(revision)}", source)


    def test_issue_and_work_order_updates_send_optimistic_lock_versions(self):
        operator_source = (ROOT / "static" / "js" / "pages" / "operator.js").read_text(encoding="utf-8")
        maintenance_source = (ROOT / "static" / "js" / "pages" / "maintenance.js").read_text(encoding="utf-8")
        supervisor_source = (ROOT / "static" / "js" / "pages" / "supervisor.js").read_text(encoding="utf-8")

        self.assertIn("issue?.version", operator_source)
        self.assertIn("version: issue.version", operator_source)
        self.assertIn("order?.version", maintenance_source)
        self.assertIn("version: order.version", maintenance_source)
        self.assertIn("supervisorOrderVersion", supervisor_source)
        self.assertIn("supervisorIssueVersion", supervisor_source)
        self.assertIn("{ version }", supervisor_source)

    def test_role_pages_use_cursor_pagination_for_lists(self):
        core = (ROOT / "static" / "js" / "core" / "api.js").read_text(encoding="utf-8")
        alarm_app = (ROOT / "static" / "alarm_app.js").read_text(encoding="utf-8")
        role_sources = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in [
                "static/js/pages/operator.js",
                "static/js/pages/maintenance.js",
                "static/js/pages/supervisor.js",
                "static/js/pages/admin.js",
                "static/js/modules/operations.js",
            ]
        )

        self.assertIn("async function apiPaged", core)
        self.assertIn("apiPaged,", alarm_app)
        self.assertIn("/issues/page", role_sources)
        self.assertIn("/work-orders/page", role_sources)

    def test_answer_id_is_carried_into_feedback_issue_and_work_order_payloads(self):
        lookup = (ROOT / "static" / "js" / "modules" / "lookup.js").read_text(encoding="utf-8")
        operator = (ROOT / "static" / "js" / "pages" / "operator.js").read_text(encoding="utf-8")
        operations = (ROOT / "static" / "js" / "modules" / "operations.js").read_text(encoding="utf-8")

        self.assertIn("data?.rag?.answer_id || data?.id", lookup)
        self.assertIn("answer_id: app.getState('lastAnswerId')", lookup)
        self.assertIn("operatorLastAnswerId", operator)
        self.assertIn("rag_answer_id: app.getState('operatorLastAnswerId')", operator)
        self.assertNotIn("rag_answer_id: app.getState('lastAnswerId')", operations)

    def test_admin_quality_review_keeps_answer_traceability(self):
        admin = (ROOT / "static" / "js" / "pages" / "admin.js").read_text(encoding="utf-8")

        self.assertIn("answer_id: entry.answer_id", admin)
        self.assertIn("answer_id: order.rag_answer_id", admin)
        self.assertIn("'answer_id'", admin)
        self.assertIn("Answer ${app.esc(item.answer_id)}", admin)

if __name__ == "__main__":
    unittest.main()
