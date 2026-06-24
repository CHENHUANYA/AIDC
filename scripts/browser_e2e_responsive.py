"""
Browser E2E and responsive acceptance check for the Alarm RAG UI.

The script starts FastAPI against an isolated test DB, drives a real browser
through role login and issue/work-order flows, and captures screenshots plus a
JSON report under tests_tmp/browser_e2e/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tests_tmp" / "browser_e2e"
SCREENSHOT_DIR = OUT_DIR / "screenshots"
TEST_PASSWORD = "BrowserPass123"
ROLES = {
    "operator01": "/operator",
    "maintenance01": "/maintenance",
    "supervisor01": "/dashboard",
    "admin01": "/admin",
}
VIEWPORTS = {
    "mobile": {"width": 390, "height": 844},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1440, "height": 950},
}
LOCAL_BROWSER_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base_url: str, process: subprocess.Popen[str], seconds: int = 45) -> None:
    deadline = time.time() + seconds
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            data = http_json(f"{base_url}/auth/login-config")
            if data.get("status") == "ok":
                return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"server did not become ready: {last_error}")


def start_server(port: int, preserve_db: bool) -> subprocess.Popen[str]:
    db_dir = OUT_DIR / "db"
    if not preserve_db:
        shutil.rmtree(db_dir, ignore_errors=True)
    db_dir.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "ALARM_RAG_ENV": "development",
            "ADMIN_INITIAL_PASSWORD": TEST_PASSWORD,
            "DB_PATH": str(db_dir),
            "ALARM_RAG_CORS_ORIGINS": f"http://127.0.0.1:{port},http://localhost:{port}",
            "RAG_PRELOAD_MODELS": "0",
            "VECTOR_STORE": "none",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_server(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    output = ""
    if process.stdout:
        try:
            output = process.stdout.read()
        except Exception:
            output = ""
    return output


def visible_layout_issues(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const vw = window.innerWidth;
          const vh = window.innerHeight;
          const doc = document.documentElement;
          const overflow = Math.max(doc.scrollWidth, document.body.scrollWidth) - vw;
          const bad = [];
          const selectors = [
            'button', 'input', 'select', 'textarea', '.card', '.wo-card',
            '.wo-modal-card', '.wo-board', '.tabs', 'header', '.role-row'
          ];
          for (const el of document.querySelectorAll(selectors.join(','))) {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (
              style.visibility === 'hidden' ||
              style.display === 'none' ||
              rect.width < 1 ||
              rect.height < 1 ||
              rect.bottom < 0 ||
              rect.top > vh
            ) continue;
            if (rect.left < -2 || rect.right > vw + 2) {
              bad.push({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                className: String(el.className || '').slice(0, 80),
                text: String(el.innerText || el.value || '').trim().slice(0, 90),
                left: Math.round(rect.left),
                right: Math.round(rect.right),
                width: Math.round(rect.width)
              });
            }
          }
          return {
            viewport: { width: vw, height: vh },
            horizontalOverflowPx: Math.max(0, Math.round(overflow)),
            clippedElements: bad.slice(0, 25)
          };
        }"""
    )


def screenshot(page, label: str) -> str:
    path = SCREENSHOT_DIR / f"{label}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path.relative_to(ROOT))


def launch_browser(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as bundled_error:
        for executable in LOCAL_BROWSER_CANDIDATES:
            if executable.exists():
                return playwright.chromium.launch(headless=True, executable_path=str(executable))
        raise bundled_error


def assert_no_browser_errors(report: dict[str, Any], context: str) -> None:
    errors = report["browser_errors"] + report["http_errors"]
    if errors:
        raise AssertionError(f"{context} browser errors: {errors[-3:]}")


def login(page, base_url: str, username: str, report: dict[str, Any]) -> None:
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.fill("#loginUsername", username)
    page.fill("#loginPassword", TEST_PASSWORD)
    page.click("#loginSubmit")
    page.wait_for_url(f"**{ROLES[username]}", timeout=10000)
    report["flows"].append({"name": f"login:{username}", "status": "ok", "url": page.url})


def create_operator_issue(page, *, create_work_order: bool, machine: str, alarm: str, description: str) -> str:
    existing_ids = set(page.locator("[data-issue-id]").evaluate_all("(nodes) => nodes.map((node) => node.dataset.issueId)"))
    page.fill("#issueMachine", machine)
    page.fill("#issueAlarmCode", alarm)
    page.fill("#issueDescription", description)
    button = 'button[onclick="createOperatorIssue(true)"]' if create_work_order else 'button[onclick="createOperatorIssue(false)"]'
    page.click(button)
    page.wait_for_function(
        """(knownIds) => [...document.querySelectorAll('[data-issue-id]')]
          .some((node) => !knownIds.includes(node.dataset.issueId))""",
        arg=list(existing_ids),
        timeout=10000,
    )
    current_ids = page.locator("[data-issue-id]").evaluate_all("(nodes) => nodes.map((node) => node.dataset.issueId)")
    return next((issue_id for issue_id in current_ids if issue_id not in existing_ids), "")


def complete_first_maintenance_order(page) -> None:
    page.wait_for_selector("#maintenanceWorkBoard .wo-card", timeout=10000)
    action = page.locator('[onclick^="event.stopPropagation(); acceptWorkOrder"]')
    if action.count():
        action.first.click()
        page.wait_for_timeout(600)
    page.locator('[onclick^="event.stopPropagation(); completeWorkOrder"]').first.click()
    page.wait_for_selector("#maintenanceModal.show", timeout=10000)
    page.fill("#mtEditResolution", "Replaced test spindle sensor and confirmed alarm cleared.")
    page.fill("#mtEditRootCause", "Loose sensor cable")
    page.fill("#mtEditRepairAction", "Reseated connector and verified signal stability")
    page.click('button[onclick="saveMaintenanceWorkOrder()"]')
    page.wait_for_timeout(1200)


def scan_page(page, name: str, report: dict[str, Any]) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(500)
    layout = visible_layout_issues(page)
    shot = screenshot(page, name)
    report["responsive"].append({"name": name, "screenshot": shot, **layout})


def scan_responsive(playwright, base_url: str, report: dict[str, Any]) -> None:
    browser = launch_browser(playwright)
    try:
        pages = [
            ("operator01", "/operator"),
            ("maintenance01", "/maintenance"),
            ("supervisor01", "/supervisor"),
            ("admin01", "/admin"),
            ("admin01", "/operations"),
        ]
        for viewport_name, viewport in VIEWPORTS.items():
            for username, path in pages:
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                attach_error_capture(page, report)
                login(page, base_url, username, report)
                page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                scan_page(page, f"{viewport_name}-{path.strip('/') or 'root'}", report)
                context.close()
    finally:
        browser.close()


def attach_error_capture(page, report: dict[str, Any]) -> None:
    page.on("pageerror", lambda exc: report["browser_errors"].append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: report["browser_errors"].append(f"console {msg.type}: {msg.text}")
        if msg.type == "error" and "Failed to load resource" not in msg.text
        else None,
    )
    page.on(
        "response",
        lambda response: report["http_errors"].append(f"{response.status} {response.url}")
        if response.status >= 400 and not response.url.endswith("/favicon.ico")
        else None,
    )


def run_issue_flow(playwright, base_url: str, report: dict[str, Any]) -> None:
    browser = launch_browser(playwright)
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    page = context.new_page()
    attach_error_capture(page, report)
    try:
        login(page, base_url, "operator01", report)
        issue_id = create_operator_issue(
            page,
            create_work_order=True,
            machine="E2E-NC-01",
            alarm="5000",
            description="Browser E2E spindle alarm verification",
        )
        report["flows"].append({"name": "operator:create_issue_with_work_order", "status": "ok", "issue_id": issue_id})
        scan_page(page, "flow-operator-created", report)

        login(page, base_url, "maintenance01", report)
        complete_first_maintenance_order(page)
        report["flows"].append({"name": "maintenance:accept_complete", "status": "ok"})
        scan_page(page, "flow-maintenance-completed", report)

        login(page, base_url, "operator01", report)
        page.wait_for_selector("[data-issue-id]", timeout=10000)
        page.locator("[data-issue-id]").first.click()
        page.wait_for_selector("#operatorIssueModal.show", timeout=10000)
        page.click('[onclick^="verifyOperatorIssue"]')
        page.wait_for_timeout(1000)
        report["flows"].append({"name": "operator:verify_completed_issue", "status": "ok"})
        scan_page(page, "flow-operator-verified", report)

        login(page, base_url, "operator01", report)
        reopen_issue_id = create_operator_issue(
            page,
            create_work_order=True,
            machine="E2E-NC-REOPEN",
            alarm="5100",
            description="Browser E2E reopen path verification",
        )
        login(page, base_url, "maintenance01", report)
        complete_first_maintenance_order(page)
        login(page, base_url, "operator01", report)
        page.wait_for_selector("[data-issue-id]", timeout=10000)
        page.locator(f'[data-issue-id="{reopen_issue_id}"]').click()
        page.wait_for_selector("#operatorIssueModal.show", timeout=10000)
        page.fill("#operatorNoteInput", "Alarm returned during browser E2E validation.")
        page.click('[onclick^="reopenOperatorIssue"]')
        page.wait_for_timeout(1000)
        report["flows"].append({"name": "operator:reopen_completed_issue", "status": "ok", "issue_id": reopen_issue_id})
        scan_page(page, "flow-operator-reopened", report)

        login(page, base_url, "operator01", report)
        supervisor_issue_id = create_operator_issue(
            page,
            create_work_order=True,
            machine="E2E-NC-SV",
            alarm="5200",
            description="Browser E2E supervisor verification path",
        )
        login(page, base_url, "maintenance01", report)
        complete_first_maintenance_order(page)
        login(page, base_url, "supervisor01", report)
        page.goto(f"{base_url}/supervisor", wait_until="domcontentloaded")
        page.click('[data-supervisor-section-target="verification"]')
        page.wait_for_selector("#svVerificationQueue .role-row", timeout=10000)
        page.locator('[onclick^="verifySupervisorOrder"]').first.click()
        page.wait_for_timeout(1200)
        report["flows"].append({"name": "supervisor:verify_completed_order", "status": "ok", "issue_id": supervisor_issue_id})
        scan_page(page, "flow-supervisor-verified", report)

        login(page, base_url, "admin01", report)
        page.on("dialog", lambda dialog: dialog.accept())
        page.click('[data-admin-section-target="knowledge"]')
        page.fill("#adminIngestCode", "E2E-5000")
        page.fill("#adminIngestTitle", "Browser E2E note")
        page.fill("#adminIngestText", "Browser E2E knowledge note for alarm acceptance.")
        page.click("#adminIngestBtn")
        page.wait_for_timeout(1200)
        page.click('[data-admin-section-target="sessions"]')
        page.click('button[onclick="loadAdminSessions()"]')
        page.wait_for_timeout(500)
        report["flows"].append({"name": "admin:kb_ingest_sessions", "status": "ok"})
        scan_page(page, "flow-admin-kb-ingest", report)

        doc_id = page.evaluate(
            """() => {
              const docs = window.AlarmApp?.getState('adminKbDocuments') || [];
              const doc = docs.find((item) => item.kind === 'text' && !item.legacy);
              return doc?.doc_id || '';
            }"""
        )
        if not doc_id:
            raise AssertionError("admin KB text document id was not found after ingest")
        page.evaluate("(docId) => window.deleteAdminKbDocument(docId)", doc_id)
        page.wait_for_timeout(1200)
        report["flows"].append({"name": "admin:kb_delete_document", "status": "ok", "doc_id": doc_id})
        scan_page(page, "flow-admin-kb-delete", report)

        page.evaluate("() => window.rebuildAdminKb()")
        page.wait_for_timeout(1200)
        report["flows"].append({"name": "admin:kb_rebuild", "status": "ok"})
        scan_page(page, "flow-admin-kb-rebuild", report)

        page.goto(f"{base_url}/operations", wait_until="domcontentloaded")
        for tab in ["kb", "settings", "work"]:
            page.click(f'.tab-btn[data-tab="{tab}"]')
            page.wait_for_timeout(500)
            if not page.locator(f"#panel-{tab}.active").count():
                raise AssertionError(f"operations tab {tab} did not become active")
        report["flows"].append({"name": "operations:legacy_tabs", "status": "ok"})
        scan_page(page, "flow-operations-tabs", report)

        assert_no_browser_errors(report, "issue flow")
    finally:
        context.close()
        browser.close()


def write_report(report: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "browser_e2e_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--preserve-db", action="store_true")
    args = parser.parse_args()

    port = args.port or find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    report: dict[str, Any] = {
        "base_url": base_url,
        "flows": [],
        "responsive": [],
        "browser_errors": [],
        "http_errors": [],
        "server_output_tail": "",
    }

    server = start_server(port, args.preserve_db)
    server_output = ""
    try:
        wait_for_server(base_url, server)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            run_issue_flow(playwright, base_url, report)
            scan_responsive(playwright, base_url, report)

        layout_failures = [
            item
            for item in report["responsive"]
            if item["horizontalOverflowPx"] > 2 or item["clippedElements"]
        ]
        if layout_failures:
            report["layout_failures"] = layout_failures
            raise AssertionError(f"{len(layout_failures)} responsive scans found overflow/clipped elements")
        assert_no_browser_errors(report, "responsive scan")
        return 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        print(f"[FAIL] {exc}")
        return 1
    finally:
        server_output = stop_server(server)
        report["server_output_tail"] = server_output[-4000:]
        if "status" not in report:
            report["status"] = "ok"
        report_path = write_report(report)
        print(f"[REPORT] {report_path}")
        print(f"[SCREENSHOTS] {SCREENSHOT_DIR}")


if __name__ == "__main__":
    raise SystemExit(main())
