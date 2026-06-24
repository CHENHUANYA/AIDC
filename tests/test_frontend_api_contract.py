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


if __name__ == "__main__":
    unittest.main()
