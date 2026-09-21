import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MUTATION_METHODS = {"post", "patch", "delete", "put"}
MUTATION_AUTH_EXCEPTIONS = {
    ("auth.py", "login"),
    ("auth.py", "logout"),
    # Credential-gated enrollment endpoint intentionally precedes session issuance.
    ("auth.py", "change_initial_password"),
}
PUBLIC_GET_EXCEPTIONS = {
    ("auth.py", "login_config"),
    ("auth.py", "me"),
    ("routes/chat_lookup_routes.py", "models_collection"),
    ("routes/chat_lookup_routes.py", "models_default"),
    ("routes/stats_routes.py", "health"),
    ("routes/stats_routes.py", "ready"),
    ("routes/static_reference_routes.py", "serve_admin"),
    ("routes/static_reference_routes.py", "serve_app"),
    ("routes/static_reference_routes.py", "serve_assistant"),
    ("routes/static_reference_routes.py", "serve_dashboard"),
    ("routes/static_reference_routes.py", "serve_login"),
    ("routes/static_reference_routes.py", "serve_maintenance"),
    ("routes/static_reference_routes.py", "serve_operations"),
    ("routes/static_reference_routes.py", "serve_operator"),
    ("routes/static_reference_routes.py", "serve_supervisor"),
}


def routed_functions():
    for path in sorted([*ROOT.glob("routes/*.py"), *ROOT.glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods = set()
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr in MUTATION_METHODS:
                        methods.add(decorator.func.attr)
            if methods:
                yield path, node


def get_routed_functions():
    for path in sorted([*ROOT.glob("routes/*.py"), *ROOT.glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_get = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "get"
                for decorator in node.decorator_list
            )
            if has_get:
                yield path, node


def route_declarations(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    declarations = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    declarations.append((decorator.func.attr.upper(), decorator.args[0].value, node.name))
    return declarations


def shadows(static_route: str, dynamic_route: str) -> bool:
    static_parts = static_route.strip("/").split("/")
    dynamic_parts = dynamic_route.strip("/").split("/")
    if len(static_parts) != len(dynamic_parts):
        return False
    return all(
        dynamic.startswith("{") and dynamic.endswith("}") or static == dynamic
        for static, dynamic in zip(static_parts, dynamic_parts)
    ) and static_route != dynamic_route


class RouteAuthContractTests(unittest.TestCase):
    def test_mutation_routes_have_actor_dependency_or_explicit_exception(self):
        missing = []
        for path, node in routed_functions():
            key = (path.relative_to(ROOT).as_posix(), node.name)
            if key in MUTATION_AUTH_EXCEPTIONS:
                continue
            arg_names = {arg.arg for arg in node.args.args}
            if "actor" not in arg_names:
                missing.append(f"{key[0]}:{key[1]}")

        self.assertEqual([], missing)

    def test_data_get_routes_have_actor_dependency_or_explicit_public_exception(self):
        missing = []
        for path, node in get_routed_functions():
            key = (path.relative_to(ROOT).as_posix(), node.name)
            if key in PUBLIC_GET_EXCEPTIONS:
                continue
            arg_names = {arg.arg for arg in node.args.args}
            if "actor" not in arg_names:
                missing.append(f"{key[0]}:{key[1]}")

        self.assertEqual([], missing)

    def test_static_routes_are_declared_before_shadowing_dynamic_routes(self):
        offenders = []
        for path in sorted([*ROOT.glob("routes/*.py"), *ROOT.glob("*.py")]):
            declarations = route_declarations(path)
            for index, (method, route, name) in enumerate(declarations):
                if "{" in route:
                    continue
                for prior_method, prior_route, prior_name in declarations[:index]:
                    if prior_method == method and shadows(route, prior_route):
                        offenders.append(
                            f"{path.relative_to(ROOT).as_posix()}:{name} {route} shadowed by {prior_name} {prior_route}"
                        )

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
