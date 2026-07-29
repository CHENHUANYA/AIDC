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
    "supervisor01": "/supervisor",
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
TRACE_ANSWER_ID = "chatcmpl_browser_answer_trace"
TRACE_ANSWER = {
    "answer_id": TRACE_ANSWER_ID,
    "query": "Alarm 5200 spindle feedback signal is unstable after startup",
    "collection": "808d",
    "answer": (
        "Stop the spindle and isolate energy before inspecting the feedback circuit.\n\n"
        "Check the encoder connector, cable shield, and supply voltage. Reseat the connector, "
        "then verify the feedback signal at low speed before returning the machine to service."
    ),
    "citations": [
        {"code": "5200", "source_file": "808D-alarm-manual.pdf", "page": 321},
        {"title": "Spindle encoder inspection", "source_file": "maintenance-sop.md", "page": 8},
        {"title": "Electrical isolation", "source_file": "plant-safety-standard.pdf", "page": 14},
    ],
    "provider": "ollama",
    "model": "qwen2.5:7b",
    "tokenizer_version": "multilingual-bm25-v1",
    "retrieval_version": "hybrid-rerank-v2",
    "elapsed_ms": 1842,
    "answer_state": "complete",
    "created_by": "operator01",
    "created_at": "2026-07-13T10:30:00+08:00",
}


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
    shutil.rmtree(SCREENSHOT_DIR, ignore_errors=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not preserve_db:
        (db_dir / "rag_answers.jsonl").write_text(
            json.dumps(TRACE_ANSWER, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    env = os.environ.copy()
    env.update(
        {
            "ALARM_RAG_ENV": "development",
            "ALARM_RAG_LOG_LEVEL": "WARNING",
            "ADMIN_INITIAL_PASSWORD": TEST_PASSWORD,
            "DB_PATH": str(db_dir),
            "ALARM_RAG_CORS_ORIGINS": f"http://127.0.0.1:{port},http://localhost:{port}",
            "RAG_PRELOAD_MODELS": "0",
            "VECTOR_STORE": "none",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "LOGIN_FAILURE_LIMIT": "3",
            "LOGIN_FAILURE_WINDOW_SECONDS": "300",
            "LOGIN_LOCKOUT_SECONDS": "30",
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
            '.wo-modal-card', '.answer-trace-card', '.answer-trace-grid',
            '.answer-trace-citations li', '.wo-board', '.tabs', 'header', '.role-row'
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


def screenshot(page, label: str, *, full_page: bool = True) -> str:
    path = SCREENSHOT_DIR / f"{label}.png"
    page.screenshot(path=str(path), full_page=full_page)
    return str(path.relative_to(ROOT))


def launch_browser(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as bundled_error:
        for executable in LOCAL_BROWSER_CANDIDATES:
            if executable.exists():
                return playwright.chromium.launch(headless=True, executable_path=str(executable))
        raise bundled_error


def block_external_fonts(context) -> None:
    context.route(
        "https://fonts.googleapis.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    context.route("https://fonts.gstatic.com/**", lambda route: route.abort())


def assert_no_browser_errors(report: dict[str, Any], context: str) -> None:
    errors = report["browser_errors"] + report["http_errors"]
    if errors:
        raise AssertionError(f"{context} browser errors: {errors[-3:]}")


def login(page, base_url: str, username: str, report: dict[str, Any]) -> None:
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.fill("#loginUsername", username)
    page.fill("#loginPassword", TEST_PASSWORD)
    page.click("#loginSubmit", no_wait_after=True)
    page.wait_for_function(
        "(expectedPath) => window.location.pathname === expectedPath",
        arg=ROLES[username],
        timeout=15000,
    )
    page.evaluate("() => window.stop()")
    page.goto(f"{base_url}{ROLES[username]}", wait_until="domcontentloaded")
    report["flows"].append({"name": f"login:{username}", "status": "ok", "url": page.url})


def create_operator_issue(
    page,
    *,
    create_work_order: bool,
    machine: str,
    alarm: str,
    description: str,
    answer_id: str = "",
) -> str:
    page.evaluate(
        """([answerId, suggestion]) => {
          window.AlarmApp?.setState('operatorLastAnswerId', answerId);
          window.AlarmApp?.setState('operatorLastSuggestion', suggestion);
        }""",
        [answer_id, TRACE_ANSWER["answer"] if answer_id else ""],
    )
    page.fill("#issueMachine", machine)
    page.fill("#issueAlarmCode", alarm)
    page.fill("#issueDescription", description)
    action_args = "[true]" if create_work_order else "[false]"
    button = f'button[data-on-click="createOperatorIssue"][data-action-args="{action_args}"]'
    previous_result = page.locator("#issueResult").text_content() or ""
    page.click(button)
    page.wait_for_function(
        """(previous) => {
          const text = document.querySelector('#issueResult')?.textContent || '';
          return text !== previous && /已建立問題 ISS-[A-Za-z0-9-]+/.test(text);
        }""",
        arg=previous_result,
        timeout=10000,
    )
    issue_id = page.evaluate(
        """() => {
          const text = document.querySelector('#issueResult')?.textContent || '';
          return text.match(/已建立問題 (ISS-[A-Za-z0-9-]+)/)?.[1] || '';
        }"""
    )
    if not issue_id:
        raise AssertionError("created issue id was not present in the success result")
    page.wait_for_selector(f'[data-issue-id="{issue_id}"]', timeout=10000)
    return str(issue_id)


def linked_work_order_id(page, issue_id: str) -> str:
    work_order_id = page.evaluate(
        """(issueId) => {
          const issues = window.AlarmApp?.getState('operatorIssues') || [];
          return issues.find((issue) => issue.issue_id === issueId)?.work_order_id || '';
        }""",
        issue_id,
    )
    if not work_order_id:
        raise AssertionError(f"linked work order was not found for issue {issue_id}")
    return str(work_order_id)


def complete_maintenance_order(page, order_id: str) -> None:
    page.wait_for_selector(
        f'#maintenanceWorkBoard .wo-card[data-action-args*="{order_id}"]',
        timeout=10000,
    )
    action = page.locator(
        f'[data-on-click="acceptWorkOrder"][data-action-args*="{order_id}"]'
    )
    if action.count():
        action.first.click()
        page.wait_for_timeout(600)
    start_action = page.locator(
        f'[data-on-click="startWorkOrder"][data-action-args*="{order_id}"]'
    )
    if start_action.count():
        start_action.first.click()
        page.wait_for_timeout(600)
    complete_action = page.locator(
        f'[data-on-click="completeWorkOrder"][data-action-args*="{order_id}"]'
    )
    complete_action.wait_for(state="visible", timeout=10000)
    complete_action.click()
    page.wait_for_selector("#maintenanceModal.show", timeout=10000)
    page.fill("#mtEditResolution", "Replaced test spindle sensor and confirmed alarm cleared.")
    page.fill("#mtEditRootCause", "Loose sensor cable")
    page.fill("#mtEditRepairAction", "Reseated connector and verified signal stability")
    page.click('button[data-on-click="saveMaintenanceWorkOrder"]')
    page.wait_for_timeout(1200)


def scan_page(page, name: str, report: dict[str, Any], *, full_page: bool = True) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(500)
    layout = visible_layout_issues(page)
    shot = screenshot(page, name, full_page=full_page)
    report["responsive"].append({"name": name, "screenshot": shot, **layout})


def inspect_answer_trace_modal(page, expected_answer_id: str) -> dict[str, Any]:
    page.wait_for_selector("#answerTraceModal.show .state-complete", timeout=10000)
    details = page.evaluate(
        """(expectedId) => {
          const modal = document.querySelector('#answerTraceModal.show');
          const card = modal?.querySelector('.answer-trace-card');
          const rect = card?.getBoundingClientRect();
          const style = card ? getComputedStyle(card) : null;
          const sections = [...(modal?.querySelectorAll('.answer-trace-body section') || [])];
          const sectionText = (heading) => sections.find(
            (section) => section.querySelector('h3')?.textContent === heading
          )?.querySelector('.answer-trace-text')?.textContent || '';
          return {
            expectedId,
            answerId: modal?.querySelector('#answerTraceId')?.textContent || '',
            title: modal?.querySelector('#answerTraceTitle')?.textContent || '',
            state: modal?.querySelector('.answer-trace-state')?.textContent || '',
            provider: modal?.querySelector('.answer-trace-field b')?.textContent || '',
            queryText: sectionText('Query'),
            answerText: sectionText('Answer'),
            citationCount: modal?.querySelectorAll('.answer-trace-citations li').length || 0,
            closeVisible: Boolean(modal?.querySelector('[data-answer-trace-close]')?.offsetParent),
            viewport: {width: innerWidth, height: innerHeight},
            card: rect ? {
              left: Math.round(rect.left), right: Math.round(rect.right),
              top: Math.round(rect.top), bottom: Math.round(rect.bottom),
              clientHeight: card.clientHeight, scrollHeight: card.scrollHeight,
              overflowY: style?.overflowY || '',
            } : null,
          };
        }""",
        expected_answer_id,
    )
    card = details.get("card") or {}
    viewport = details.get("viewport") or {}
    if details.get("answerId") != expected_answer_id:
        raise AssertionError(f"answer trace id mismatch: {details.get('answerId')}")
    if details.get("title") != "RAG 回答快照" or details.get("state") != "complete":
        raise AssertionError(f"answer trace header/state mismatch: {details}")
    if details.get("provider") != TRACE_ANSWER["provider"] or details.get("citationCount") != 3:
        raise AssertionError(f"answer trace metadata/citations mismatch: {details}")
    if details.get("queryText") != TRACE_ANSWER["query"] or "Stop the spindle" not in details.get("answerText", ""):
        raise AssertionError(f"answer trace query/answer mismatch: {details}")
    if not details.get("closeVisible") or not card:
        raise AssertionError(f"answer trace controls/card are not visible: {details}")
    if card.get("left", -1) < 0 or card.get("right", 0) > viewport.get("width", 0):
        raise AssertionError(f"answer trace card is horizontally clipped: {details}")
    if card.get("top", -1) < 0 or card.get("bottom", 0) > viewport.get("height", 0):
        raise AssertionError(f"answer trace card is vertically clipped: {details}")
    if viewport.get("width", 0) <= VIEWPORTS["mobile"]["width"]:
        if card.get("overflowY") != "auto" or card.get("scrollHeight", 0) <= card.get("clientHeight", 0):
            raise AssertionError(f"mobile answer trace card is not independently scrollable: {details}")
    return details


def open_answer_trace(page, button, name: str, report: dict[str, Any]) -> None:
    button.click()
    details = inspect_answer_trace_modal(page, TRACE_ANSWER_ID)
    scan_page(page, name, report, full_page=False)
    report["modal_checks"].append({"name": name, "status": "ok", **details})


def close_answer_trace(page, method: str) -> None:
    if method == "button":
        page.click("#answerTraceModal [data-answer-trace-close]")
    elif method == "backdrop":
        page.locator("#answerTraceModal").click(position={"x": 4, "y": 4})
    else:
        raise ValueError(f"unsupported answer trace close method: {method}")
    page.wait_for_selector("#answerTraceModal", state="hidden", timeout=5000)


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
                block_external_fonts(context)
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


def run_core_smoke(playwright, base_url: str, report: dict[str, Any]) -> None:
    browser = launch_browser(playwright)
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    block_external_fonts(context)
    page = context.new_page()
    attach_error_capture(page, report)
    chat_history_lengths: list[int] = []

    def mock_chat(route) -> None:
        payload = route.request.post_data_json or {}
        messages = payload.get("messages") or []
        chat_history_lengths.append(len(messages))
        answer_number = len(chat_history_lengths)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "id": f"browser-smoke-answer-{answer_number}",
                    "object": "chat.completion",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"Browser smoke reply {answer_number}",
                        },
                        "finish_reason": "stop",
                    }],
                    "rag": {"answer_id": f"browser-smoke-answer-{answer_number}"},
                }
            ),
        )

    try:
        login(page, base_url, "supervisor01", report)
        dashboard_response = page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
        if dashboard_response is None:
            raise AssertionError("dashboard navigation did not return a response")
        csp = dashboard_response.headers.get("content-security-policy", "")
        required_directives = ("script-src 'self';", "style-src 'self' https://fonts.googleapis.com;")
        if any(directive not in csp for directive in required_directives):
            raise AssertionError(f"dashboard CSP is missing a self-only directive: {csp}")
        if "unsafe-inline" in csp or "sha256-" in csp:
            raise AssertionError(f"dashboard CSP still allows inline resources: {csp}")
        page.wait_for_selector("#trendBars .trend-col", timeout=10000)
        if page.locator("#trendBars .trend-col").count() != 7:
            raise AssertionError("dashboard weekly trend did not render seven columns")
        page.click('[data-on-click="toggleTestTools"]')
        page.wait_for_selector("#toolsBody.show", timeout=5000)
        scan_page(page, "smoke-dashboard", report)
        report["security_checks"].append({
            "name": "dashboard:csp-self-only",
            "status": "ok",
            "content_security_policy": csp,
        })

        page.route("**/v1/808d/chat", mock_chat)
        page.goto(f"{base_url}/assistant", wait_until="domcontentloaded")
        page.click('[data-tab="chat"]')
        page.fill("#chatInput", "請說明警報 3000")
        page.click("#chatSendBtn")
        page.wait_for_selector(".msg.assistant:has-text('Browser smoke reply 1')", timeout=10000)
        page.fill("#chatInput", "請接續上一題提供檢查步驟")
        page.click("#chatSendBtn")
        page.wait_for_selector(".msg.assistant:has-text('Browser smoke reply 2')", timeout=10000)
        if chat_history_lengths != [1, 3]:
            raise AssertionError(f"assistant did not preserve multi-turn history: {chat_history_lengths}")
        if page.locator("#chatMessages .msg.user").count() != 2:
            raise AssertionError("assistant did not render both user questions")
        scan_page(page, "smoke-assistant-chat", report)
        report["chat_checks"].append({
            "name": "assistant:multi-turn-history",
            "status": "ok",
            "request_message_counts": chat_history_lengths,
        })

        login(page, base_url, "operator01", report)
        page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.location.pathname === '/operator'",
            timeout=15000,
        )
        report["security_checks"].append({
            "name": "roles:operator-dashboard-denied",
            "status": "ok",
            "redirected_to": page.url,
        })
        page.click('[data-on-click="AlarmApp.logout"]')
        page.wait_for_function(
            "() => window.location.pathname === '/login'",
            timeout=15000,
        )
        if page.evaluate("() => localStorage.getItem('alarmAuthUser')") is not None:
            raise AssertionError("logout left the browser auth user in localStorage")
        report["flows"].append({"name": "logout:operator01", "status": "ok", "url": page.url})

        throttle_statuses = []
        throttle_headers: dict[str, str] = {}
        for _ in range(3):
            response = page.request.post(
                f"{base_url}/auth/login",
                data={"username": "browser-e2e-throttle", "password": "invalid-password"},
            )
            throttle_statuses.append(response.status)
            throttle_headers = response.headers
        if throttle_statuses != [401, 401, 429]:
            raise AssertionError(f"unexpected login throttle statuses: {throttle_statuses}")
        if not throttle_headers.get("retry-after"):
            raise AssertionError("login throttle response did not include Retry-After")
        report["security_checks"].append({
            "name": "login:throttle",
            "status": "ok",
            "statuses": throttle_statuses,
            "retry_after": throttle_headers["retry-after"],
        })

        login(page, base_url, "admin01", report)
        metrics_response = page.request.get(f"{base_url}/metrics/runtime")
        if metrics_response.status != 200:
            raise AssertionError(f"runtime metrics endpoint returned {metrics_response.status}")
        metrics_payload = metrics_response.json()
        auth_metrics = metrics_payload.get("auth") or {}
        if auth_metrics.get("login_failures", 0) < 2 or auth_metrics.get("throttle_triggers", 0) < 1:
            raise AssertionError(f"runtime login metrics are incomplete: {auth_metrics}")
        if "postgres" not in metrics_payload or "http" not in metrics_payload or "rag" not in metrics_payload:
            raise AssertionError("runtime metrics payload is missing required sections")
        report["security_checks"].append({
            "name": "metrics:admin-runtime-snapshot",
            "status": "ok",
            "http_requests": metrics_payload["http"].get("requests", 0),
            "throttle_triggers": auth_metrics.get("throttle_triggers", 0),
            "postgres_status": metrics_payload["postgres"].get("status", ""),
        })

        assert_no_browser_errors(report, "core smoke")
    finally:
        context.close()
        browser.close()


def run_issue_flow(playwright, base_url: str, report: dict[str, Any]) -> None:
    browser = launch_browser(playwright)
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    block_external_fonts(context)
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
        work_order_id = linked_work_order_id(page, issue_id)
        report["flows"].append({"name": "operator:create_issue_with_work_order", "status": "ok", "issue_id": issue_id})
        scan_page(page, "flow-operator-created", report)

        login(page, base_url, "maintenance01", report)
        complete_maintenance_order(page, work_order_id)
        report["flows"].append({"name": "maintenance:accept_complete", "status": "ok"})
        scan_page(page, "flow-maintenance-completed", report)

        login(page, base_url, "operator01", report)
        page.wait_for_selector("[data-issue-id]", timeout=10000)
        page.locator(f'[data-issue-id="{issue_id}"]').click()
        page.wait_for_selector("#operatorIssueModal.show", timeout=10000)
        page.click('[data-on-click="verifyOperatorIssue"]')
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
        reopen_work_order_id = linked_work_order_id(page, reopen_issue_id)
        login(page, base_url, "maintenance01", report)
        complete_maintenance_order(page, reopen_work_order_id)
        login(page, base_url, "operator01", report)
        page.wait_for_selector("[data-issue-id]", timeout=10000)
        page.locator(f'[data-issue-id="{reopen_issue_id}"]').click()
        page.wait_for_selector("#operatorIssueModal.show", timeout=10000)
        page.fill("#operatorNoteInput", "Alarm returned during browser E2E validation.")
        page.click('[data-on-click="reopenOperatorIssue"]')
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
            answer_id=TRACE_ANSWER_ID,
        )
        supervisor_work_order_id = linked_work_order_id(page, supervisor_issue_id)
        login(page, base_url, "maintenance01", report)
        complete_maintenance_order(page, supervisor_work_order_id)
        login(page, base_url, "supervisor01", report)
        page.goto(f"{base_url}/supervisor", wait_until="domcontentloaded")
        page.click('[data-supervisor-section-target="verification"]')
        page.wait_for_selector("#svVerificationQueue .role-row", timeout=10000)
        trace_button = page.locator(
            f'#svVerificationQueue button[data-on-click="AnswerTrace.open"]'
            f'[data-action-args*="{TRACE_ANSWER_ID}"]'
        )
        trace_button.wait_for(state="visible", timeout=10000)
        trace_row = trace_button.locator(
            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' role-row ')][1]"
        )
        open_answer_trace(
            page,
            trace_button,
            "flow-supervisor-answer-trace",
            report,
        )
        close_answer_trace(page, "button")
        trace_button.click()
        inspect_answer_trace_modal(page, TRACE_ANSWER_ID)
        close_answer_trace(page, "backdrop")
        trace_row.locator('[data-on-click="verifySupervisorOrder"]').click()
        page.wait_for_timeout(1200)
        report["flows"].append({"name": "supervisor:verify_completed_order", "status": "ok", "issue_id": supervisor_issue_id})
        scan_page(page, "flow-supervisor-verified", report)

        login(page, base_url, "admin01", report)
        page.on("dialog", lambda dialog: dialog.accept())
        page.click('[data-admin-section-target="quality"]')
        admin_trace_button = page.locator(
            f'#adminQualityList .role-row:has-text("{TRACE_ANSWER_ID}") '
            'button[data-on-click="AnswerTrace.open"]'
        )
        admin_trace_button.wait_for(state="visible", timeout=10000)
        open_answer_trace(page, admin_trace_button, "flow-admin-answer-trace", report)
        close_answer_trace(page, "backdrop")

        page.set_viewport_size(VIEWPORTS["mobile"])
        admin_trace_button.click()
        details = inspect_answer_trace_modal(page, TRACE_ANSWER_ID)
        scan_page(page, "mobile-admin-answer-trace", report, full_page=False)
        scroll_check = page.evaluate(
            """() => {
              const card = document.querySelector('#answerTraceModal.show .answer-trace-card');
              const lastCitation = document.querySelector('#answerTraceModal.show .answer-trace-citations li:last-child');
              card.scrollTop = card.scrollHeight;
              const cardRect = card.getBoundingClientRect();
              const citationRect = lastCitation.getBoundingClientRect();
              return {
                scrollTop: card.scrollTop,
                citationTop: Math.round(citationRect.top),
                citationBottom: Math.round(citationRect.bottom),
                cardTop: Math.round(cardRect.top),
                cardBottom: Math.round(cardRect.bottom),
              };
            }"""
        )
        if (
            scroll_check["scrollTop"] <= 0
            or scroll_check["citationTop"] < scroll_check["cardTop"]
            or scroll_check["citationBottom"] > scroll_check["cardBottom"]
        ):
            raise AssertionError(f"mobile answer trace citations are not reachable by scrolling: {scroll_check}")
        details["scrollVerified"] = True
        scan_page(page, "mobile-admin-answer-trace-scrolled", report, full_page=False)
        report["modal_checks"].append({"name": "mobile-admin-answer-trace", "status": "ok", **details})
        close_answer_trace(page, "button")
        page.set_viewport_size(VIEWPORTS["desktop"])

        page.click('[data-admin-section-target="knowledge"]')
        page.fill("#adminIngestCode", "E2E-5000")
        page.fill("#adminIngestTitle", "Browser E2E note")
        page.fill("#adminIngestText", "Browser E2E knowledge note for alarm acceptance.")
        page.click("#adminIngestBtn")
        page.wait_for_function(
            """() => {
              const docs = window.AlarmApp?.getState('adminKbDocuments') || [];
              return docs.some((item) => item.kind === 'text' && !item.legacy);
            }""",
            timeout=15000,
        )
        doc_id = page.evaluate(
            """() => {
              const docs = window.AlarmApp?.getState('adminKbDocuments') || [];
              const doc = docs.find((item) => item.kind === 'text' && !item.legacy);
              return doc?.doc_id || '';
            }"""
        )
        if not doc_id:
            raise AssertionError("admin KB text document id was not found after ingest")
        page.click('[data-admin-section-target="sessions"]')
        page.click('button[data-on-click="loadAdminSessions"]')
        page.wait_for_timeout(500)
        report["flows"].append({"name": "admin:kb_ingest_sessions", "status": "ok", "doc_id": doc_id})
        scan_page(page, "flow-admin-kb-ingest", report)

        page.evaluate("(docId) => window.deleteAdminKbDocument(docId)", doc_id)
        page.wait_for_timeout(1200)
        report["flows"].append({"name": "admin:kb_delete_document", "status": "ok", "doc_id": doc_id})
        scan_page(page, "flow-admin-kb-delete", report)

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
        "modal_checks": [],
        "security_checks": [],
        "chat_checks": [],
        "server_output_tail": "",
    }

    server = start_server(port, args.preserve_db)
    server_output = ""
    try:
        wait_for_server(base_url, server)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            run_core_smoke(playwright, base_url, report)
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
