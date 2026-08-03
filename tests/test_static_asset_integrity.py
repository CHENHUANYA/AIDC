import ast
import os
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
HTML_FILES = sorted(ROOT.glob("*.html"))
PAGE_NAMES = {
    "admin",
    "assistant",
    "dashboard",
    "login",
    "maintenance",
    "operations",
    "operator",
    "supervisor",
}
PAGE_JS_OVERRIDES = {}
REMOVED_ASSETS = {
    "/static/alarm_app.css",
    "/static/login.css",
    "/static/css/howto.css",
    "/static/js/modules/howto.js",
    "/static/js/pages/legacy.js",
    "alarm_app.html",
    "howto.html",
}
PAGE_DOM_DYNAMIC_REFS = {
    "operator": {
        "editIssueAlarmCode",
        "editIssueDescription",
        "editIssueLine",
        "editIssueMachine",
        "editIssueSeverity",
        "operatorNoteInput",
    },
}


class HtmlRefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "link" and data.get("href"):
            self.refs.append(data["href"])
        if tag == "script" and data.get("src"):
            self.refs.append(data["src"])


def html_refs(path: Path) -> list[str]:
    parser = HtmlRefParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.refs


def script_paths_for_html(path: Path) -> list[Path]:
    return [
        ROOT / urlparse(ref).path.lstrip("/")
        for ref in html_refs(path)
        if urlparse(ref).path.startswith("/static/") and urlparse(ref).path.endswith(".js")
    ]


def callable_names_from_scripts(html_path: Path) -> set[str]:
    html = html_path.read_text(encoding="utf-8")
    source = "\n".join(path.read_text(encoding="utf-8") for path in script_paths_for_html(html_path) if path.exists())
    source += "\n".join(re.findall(r"<script>([\s\S]*?)</script>", html))
    names = set(re.findall(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", source))
    names |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", source))
    alarm_app_match = re.search(r"window\.AlarmApp\s*=\s*\{([\s\S]*?)\};", source)
    if alarm_app_match:
        names |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*,", alarm_app_match.group(1), re.MULTILINE))
    return names


def inline_handler_calls(path: Path) -> set[str]:
    html = path.read_text(encoding="utf-8")
    calls = set()
    for handler in re.findall(r"on(?:click|change|keydown|input)=[\"']([^\"']+)[\"']", html):
        calls |= set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", handler))
    return calls - {"alert", "confirm", "document", "if"}


def callable_names_from_source(source: str) -> set[str]:
    names = set(re.findall(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", source))
    names |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", source))
    for alarm_app_match in re.finditer(r"window\.AlarmApp\s*=\s*\{([\s\S]*?)\};", source):
        names |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*,", alarm_app_match.group(1), re.MULTILINE))
    return names


def handler_calls_from_source(source: str) -> set[str]:
    calls = set()
    for handler in re.findall(r"on(?:click|change|keydown|input)=[\"']([^\"']+)[\"']", source):
        calls |= set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", handler))
    return calls - {"alert", "confirm", "document", "event", "if", "setTimeout", "window"}


def page_access_keys() -> set[str]:
    source = (ROOT / "static" / "js" / "core" / "api.js").read_text(encoding="utf-8")
    match = re.search(r"const PAGE_ACCESS\s*=\s*\{([\s\S]*?)\};", source)
    return set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", match.group(1), re.MULTILINE)) if match else set()


def page_path_keys() -> set[str]:
    source = (ROOT / "static" / "alarm_app.js").read_text(encoding="utf-8")
    match = re.search(r"const PAGE_PATHS\s*=\s*\{([\s\S]*?)\};", source)
    return set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", match.group(1), re.MULTILINE)) if match else set()


def css_rule_property(source: str, selector: str, property_name: str) -> str:
    rule = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", source)
    if not rule:
        return ""
    declaration = re.search(rf"{re.escape(property_name)}\s*:\s*([^;]+)", rule.group(1))
    return declaration.group(1).strip() if declaration else ""


class StaticAssetIntegrityTests(unittest.TestCase):
    def test_html_static_references_exist(self):
        missing = []
        for path in HTML_FILES:
            for ref in html_refs(path):
                asset_path = urlparse(ref).path
                if asset_path.startswith("/static/") and not (ROOT / asset_path.lstrip("/")).exists():
                    missing.append(f"{path.name}: {ref}")

        self.assertEqual([], missing)

    def test_pages_do_not_load_third_party_styles_scripts_or_fonts(self):
        external = []
        for path in HTML_FILES:
            for ref in html_refs(path):
                if urlparse(ref).scheme in {"http", "https"}:
                    external.append(f"{path.name}: {ref}")

        self.assertEqual([], external)

    def test_pages_load_shared_tokens_before_page_styles(self):
        errors = []
        tokens_path = "/static/css/tokens.css"
        for page in sorted(PAGE_NAMES):
            refs = [urlparse(ref).path for ref in html_refs(ROOT / f"{page}.html")]
            page_path = f"/static/css/{page}.css"
            if refs.count(tokens_path) != 1:
                errors.append(f"{page}: expected one {tokens_path} reference")
                continue
            if refs.count(page_path) != 1:
                errors.append(f"{page}: expected one {page_path} reference")
                continue
            if refs.index(tokens_path) > refs.index(page_path):
                errors.append(f"{page}: tokens.css must load before the page stylesheet")

        self.assertEqual([], errors)

    def test_design_tokens_have_one_canonical_source(self):
        token_source = (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
        required_tokens = {
            "--bg",
            "--surface",
            "--border",
            "--acc",
            "--grn",
            "--red",
            "--text",
            "--mono",
            "--sans",
            "--shadow",
            "--r-md",
            "--ease",
        }
        missing = sorted(
            token
            for token in required_tokens
            if not re.search(rf"^\s*{re.escape(token)}\s*:", token_source, re.MULTILINE)
        )
        duplicates = []
        for page in sorted(PAGE_NAMES):
            page_source = (ROOT / "static" / "css" / f"{page}.css").read_text(encoding="utf-8")
            for token in sorted(required_tokens):
                if re.search(rf"^\s*{re.escape(token)}\s*:", page_source, re.MULTILINE):
                    duplicates.append(f"{page}.css: {token}")

        self.assertEqual([], missing)
        self.assertEqual([], duplicates)

    def test_pages_have_keyboard_navigation_landmarks(self):
        errors = []
        accessibility_path = "/static/css/accessibility.css"
        for page in sorted(PAGE_NAMES):
            html_path = ROOT / f"{page}.html"
            source = html_path.read_text(encoding="utf-8")
            stylesheet_paths = [
                urlparse(ref).path
                for ref in html_refs(html_path)
                if urlparse(ref).path.endswith(".css")
            ]
            if source.count("<main") != 1:
                errors.append(f"{page}: expected exactly one main landmark")
            if not re.search(r'<main\b[^>]*\bid="main-content"[^>]*\btabindex="-1"', source):
                errors.append(f"{page}: main#main-content must be programmatically focusable")
            if not re.search(
                r'<body\b[^>]*>\s*<a class="skip-link" href="#main-content">',
                source,
            ):
                errors.append(f"{page}: skip link must be the first body content")
            if not stylesheet_paths or stylesheet_paths[-1] != accessibility_path:
                errors.append(f"{page}: accessibility.css must be the last stylesheet")

        self.assertEqual([], errors)

    def test_static_modals_publish_dialog_semantics(self):
        errors = []
        for page in ("maintenance", "operations", "operator"):
            source = (ROOT / f"{page}.html").read_text(encoding="utf-8")
            for tag in re.findall(r'<div class="wo-modal"[^>]*>', source):
                if 'role="dialog"' not in tag or 'aria-modal="true"' not in tag:
                    errors.append(f"{page}: modal is missing dialog semantics")
                if not re.search(r'\baria-labelledby="[^"]+"', tag):
                    errors.append(f"{page}: modal is missing an accessible name")

        self.assertEqual([], errors)

    def test_accessibility_styles_cover_focus_and_reduced_motion(self):
        source = (ROOT / "static" / "css" / "accessibility.css").read_text(encoding="utf-8")

        self.assertIn(":focus-visible", source)
        self.assertIn(".skip-link:focus", source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", source)
        self.assertIn("outline:", source)

    def test_each_page_has_matching_css_and_js_bundle(self):
        missing = []
        for page in sorted(PAGE_NAMES):
            html_path = ROOT / f"{page}.html"
            css_path = ROOT / "static" / "css" / f"{page}.css"
            js_path = PAGE_JS_OVERRIDES.get(page, ROOT / "static" / "js" / "pages" / f"{page}.js")
            if not html_path.exists():
                missing.append(str(html_path.relative_to(ROOT)))
            if not css_path.exists():
                missing.append(str(css_path.relative_to(ROOT)))
            if not js_path.exists():
                missing.append(str(js_path.relative_to(ROOT)))

        self.assertEqual([], missing)

    def test_admin_and_supervisor_shell_backgrounds_match(self):
        admin = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")
        supervisor = (ROOT / "static" / "css" / "supervisor.css").read_text(encoding="utf-8")

        for selector in ("header", ".tabs"):
            self.assertEqual(
                css_rule_property(supervisor, selector, "background"),
                css_rule_property(admin, selector, "background"),
                f"{selector} background must match across the admin and supervisor consoles",
            )

    def test_removed_legacy_assets_are_not_referenced(self):
        offenders = []
        for path in [*HTML_FILES, *ROOT.glob("static/**/*.js"), *ROOT.glob("static/**/*.css")]:
            text = path.read_text(encoding="utf-8")
            for removed in REMOVED_ASSETS:
                if removed in text:
                    offenders.append(f"{path.relative_to(ROOT)} references {removed}")

        self.assertEqual([], offenders)

    def test_static_html_routes_point_to_existing_files(self):
        source = (ROOT / "routes" / "static_reference_routes.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        referenced = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "_read_html":
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                referenced.append(node.args[0].value)

        missing = [path for path in referenced if not (ROOT / path).exists()]
        self.assertEqual([], missing)
        self.assertTrue({"dashboard.html", "login.html"}.issubset(set(referenced)))

    def test_maintenance_modal_does_not_offer_verification_controls(self):
        html = (ROOT / "maintenance.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "pages" / "maintenance.js").read_text(encoding="utf-8")

        self.assertNotIn('id="mtEditVerifiedBy"', html)
        self.assertNotIn('<option value="verified">已驗證</option>', html)
        self.assertNotIn("mtEditVerifiedBy", js)

    def test_operations_work_order_modal_does_not_offer_verification_status(self):
        html = (ROOT / "operations.html").read_text(encoding="utf-8")

        self.assertNotIn('value="verified"', html)

    def test_operator_ui_does_not_offer_blocked_core_issue_edits(self):
        js = (ROOT / "static" / "js" / "pages" / "operator.js").read_text(encoding="utf-8")

        self.assertIn("function canEditOperatorCore(issue) {\n  return false;\n}", js)

    def test_page_js_literal_dom_refs_exist_or_are_dynamic(self):
        missing = []
        for page in sorted(PAGE_NAMES):
            html_path = ROOT / f"{page}.html"
            js_path = PAGE_JS_OVERRIDES.get(page, ROOT / "static" / "js" / "pages" / f"{page}.js")
            if not html_path.exists() or not js_path.exists():
                continue
            html_ids = set(re.findall(r"id=[\"']([^\"']+)[\"']", html_path.read_text(encoding="utf-8")))
            js_refs = set(re.findall(
                r"(?:app\?\.\$|app\.\$)\(['\"]([A-Za-z][A-Za-z0-9_:-]*)['\"]\)",
                js_path.read_text(encoding="utf-8"),
            ))
            allowed_dynamic = PAGE_DOM_DYNAMIC_REFS.get(page, set())
            for ref in sorted(js_refs - html_ids - allowed_dynamic):
                missing.append(f"{page}: {ref}")

        self.assertEqual([], missing)

    def test_static_javascript_has_valid_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        failures = []
        for path in sorted(ROOT.glob("static/**/*.js")):
            result = subprocess.run(
                [node, "--check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                failures.append(f"{path.relative_to(ROOT)}: {result.stderr.strip()}")

        self.assertEqual([], failures)

    def test_routed_pages_have_nonempty_access_rules(self):
        source = (ROOT / "static" / "js" / "core" / "api.js").read_text(encoding="utf-8")
        missing = []
        for page in sorted(PAGE_NAMES - {"login"}):
            if f"{page}: []" in source:
                missing.append(page)

        self.assertEqual([], missing)

    def test_static_html_routes_have_frontend_access_rules(self):
        source = (ROOT / "routes" / "static_reference_routes.py").read_text(encoding="utf-8")
        routed_pages = set(re.findall(r'_read_html\("([^"]+)\.html"\)', source))

        self.assertEqual(set(), routed_pages - page_access_keys() - {"login"})

    def test_alarm_app_navigation_paths_cover_routed_pages(self):
        source = (ROOT / "routes" / "static_reference_routes.py").read_text(encoding="utf-8")
        routed_pages = set(re.findall(r'_read_html\("([^"]+)\.html"\)', source)) - {"login"}

        self.assertEqual(set(), routed_pages - page_path_keys())

    def test_inline_handlers_reference_loaded_functions(self):
        allowed_globals = {"AlarmApp", "AlarmCoreApi"}
        missing = []
        for path in HTML_FILES:
            callable_names = callable_names_from_scripts(path)
            for name in sorted(inline_handler_calls(path) - callable_names - allowed_globals):
                missing.append(f"{path.name}: {name}")

        self.assertEqual([], missing)

    def test_generated_inline_handlers_reference_static_functions(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("static/**/*.js")))
        missing = sorted(handler_calls_from_source(source) - callable_names_from_source(source))

        self.assertEqual([], missing)

    def test_event_handlers_are_declarative_and_allowlisted(self):
        sources = [
            *(path.read_text(encoding="utf-8") for path in HTML_FILES),
            *(path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("static/**/*.js"))),
        ]
        combined = "\n".join(sources)
        inline_handlers = re.findall(
            r"<[^>]+\bon(?:click|change|keydown|input)\s*=",
            combined,
            re.IGNORECASE,
        )
        declared_actions = set(re.findall(
            r'data-on-(?:click|change|keydown|input)=["\']([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)',
            combined,
        ))
        alarm_app = (ROOT / "static" / "alarm_app.js").read_text(encoding="utf-8")
        allowlist_match = re.search(
            r"const DECLARATIVE_ACTIONS = new Set\(\[([\s\S]*?)\]\);",
            alarm_app,
        )
        allowlisted_actions = (
            set(re.findall(r"'([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)'", allowlist_match.group(1)))
            if allowlist_match
            else set()
        )

        self.assertEqual([], inline_handlers)
        self.assertEqual(set(), declared_actions - allowlisted_actions)

    def test_scripts_are_external(self):
        offenders = []
        for path in HTML_FILES:
            source = path.read_text(encoding="utf-8")
            if re.search(r"<script>([\s\S]*?)</script>", source):
                offenders.append(path.name)

        self.assertEqual([], offenders)

    def test_styles_are_external(self):
        sources = [
            *(path.read_text(encoding="utf-8") for path in HTML_FILES),
            *(path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("static/**/*.js"))),
        ]
        combined = "\n".join(sources)
        inline_blocks = [
            path.name
            for path in HTML_FILES
            if re.search(r"<style>([\s\S]*?)</style>", path.read_text(encoding="utf-8"))
        ]

        self.assertIsNone(re.search(r"\bstyle\s*=", combined, re.IGNORECASE))
        self.assertNotIn(".style.", combined)
        self.assertEqual([], inline_blocks)


if __name__ == "__main__":
    unittest.main()
