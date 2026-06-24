import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs" / "MVP_WEEK4_ACCEPTANCE_REPORT.md"


load_project_env()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def request_json(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 180,
    token: str = "",
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), json.loads(text)
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, {"_raw": text}
    except (TimeoutError, error.URLError) as exc:
        return 0, {"_error": str(exc)}


def login(base_url: str, timeout: int) -> str:
    try:
        password = admin_initial_password()
    except EnvConfigError as exc:
        print(f"[FAIL] {exc}")
        return ""
    code, data = request_json(
        base_url,
        "/auth/login",
        "POST",
        {"username": "admin01", "password": password},
        timeout,
    )
    return data.get("token") if code == 200 and isinstance(data, dict) else ""


def pass_fail(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def count_records(path: str) -> int:
    data = load_json(ROOT / path)
    return len(data) if isinstance(data, list) else 0


def workflow_nodes() -> set[str]:
    data = load_json(ROOT / "mock_data" / "n8n_mock_workflow.json")
    nodes = data.get("nodes") if isinstance(data, dict) else []
    return {str(node.get("name")) for node in nodes if isinstance(node, dict)}


def static_checks() -> list[tuple[str, str, str]]:
    required_files = [
        "docs/DEMO_SCRIPT.md",
        "docs/MOCK_DATA_SPEC.md",
        "docs/N8N_MOCK_WORKFLOW.md",
        "docs/MVP_ACCEPTANCE_CHECKLIST.md",
        "docs/DEMO_RECORDING_SCRIPT.md",
        "docs/VENDOR_DATA_FIELD_CHECKLIST.md",
        "scripts/smoke_test.py",
        "scripts/seed_week2_data.py",
        "scripts/replay_demo_alarms.py",
        "mock_data/demo_alarm_events.json",
        "mock_data/week2_work_orders.json",
        "mock_data/week2_knowledge_records.json",
        "mock_data/n8n_mock_workflow.json",
    ]
    results = [
        (f"file:{path}", pass_fail((ROOT / path).exists()), path)
        for path in required_files
    ]

    alarm_events = count_records("mock_data/demo_alarm_events.json")
    work_orders = count_records("mock_data/week2_work_orders.json")
    knowledge = count_records("mock_data/week2_knowledge_records.json")
    nodes = workflow_nodes()
    required_nodes = {"Schedule Trigger", "Manual Trigger", "Set Mock Alarm Payload", "POST /trigger-alarm"}

    results.extend([
        ("mock:alarm-events", pass_fail(alarm_events >= 20), f"count={alarm_events}, required>=20"),
        ("mock:work-orders", pass_fail(work_orders >= 10), f"count={work_orders}, required>=10"),
        ("mock:knowledge", pass_fail(knowledge >= 5), f"count={knowledge}, required>=5"),
        ("n8n:nodes", pass_fail(required_nodes <= nodes), f"nodes={len(nodes)}"),
    ])
    return results


def metric(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) else 0


def live_checks(base_url: str, manual: str, alarm_code: str, timeout: int) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    code, health = request_json(base_url, "/health", timeout=timeout)
    service_ok = code == 200 and health.get("status") == "ok"
    results.append(("live:health", pass_fail(service_ok), f"HTTP {code}"))
    if not service_ok:
        return results
    token = login(base_url, timeout)
    results.append(("live:auth", pass_fail(bool(token)), f"token={'yes' if token else 'no'}"))
    if not token:
        return results

    _, alarms_before = request_json(base_url, "/stats/alarms", timeout=timeout, token=token)
    _, queries_before = request_json(base_url, "/stats/queries", timeout=timeout, token=token)
    _, orders_before = request_json(base_url, "/work-orders/stats", timeout=timeout, token=token)
    _, feedback_before = request_json(base_url, "/feedback/stats", timeout=timeout, token=token)

    lookup_path = f"/v1/{manual}/lookup?{parse.urlencode({'code': alarm_code})}"
    code, lookup = request_json(base_url, lookup_path, timeout=timeout, token=token)
    results.append(("live:lookup", pass_fail(code == 200 and "found" in lookup), f"HTTP {code}, found={lookup.get('found')}"))

    trigger_payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "WEEK4-DEMO-01",
        "source": "week4-acceptance",
        "severity": "high",
        "description": "Week 4 acceptance trigger validates alarm, work-order, feedback, and BI movement.",
    }
    code, trigger = request_json(base_url, "/trigger-alarm", "POST", trigger_payload, timeout, token)
    order = trigger.get("work_order") if isinstance(trigger, dict) else None
    order_id = order.get("id") if isinstance(order, dict) else ""
    results.append(("live:trigger", pass_fail(code == 200 and trigger.get("status") == "ok" and bool(order_id)), f"HTTP {code}, order={order_id or '-'}"))

    code, pending = request_json(base_url, "/pending-alarms", timeout=timeout, token=token)
    pending_alarms = pending.get("alarms") if isinstance(pending, dict) else []
    results.append(("live:banner-queue", pass_fail(code == 200 and isinstance(pending_alarms, list)), f"count={len(pending_alarms) if isinstance(pending_alarms, list) else '-'}"))

    if order_id:
        patch_payload = {
            "status": "completed",
            "assigned_to": "week4-demo",
            "resolution": "Verified the alarm path, checked the source metadata, reset the simulated condition, and closed the work order.",
            "root_cause": "Week 4 simulated alarm path",
            "repair_action": "Verified metadata, reset the simulated condition, and closed the work order.",
            "notes": "Week 4 acceptance completion path.",
        }
        code, updated = request_json(base_url, f"/work-orders/{order_id}", "PATCH", patch_payload, timeout, token)
        review = updated.get("knowledge_review") if isinstance(updated, dict) else None
        review_pending = isinstance(review, dict) and review.get("review_status") == "pending_review"
        results.append((
            "live:work-order-close",
            pass_fail(code == 200 and updated.get("status") == "ok" and review_pending),
            f"HTTP {code}, review={review}",
        ))

    feedback_payload = {
        "query": f"Week 4 acceptance lookup for alarm {alarm_code}",
        "collection": manual,
        "feedback": "good",
        "alarm_code": alarm_code,
    }
    code, feedback = request_json(base_url, "/feedback", "POST", feedback_payload, timeout, token)
    results.append(("live:feedback", pass_fail(code == 200 and feedback.get("status") == "ok"), f"HTTP {code}"))

    _, alarms_after = request_json(base_url, "/stats/alarms", timeout=timeout, token=token)
    _, queries_after = request_json(base_url, "/stats/queries", timeout=timeout, token=token)
    _, orders_after = request_json(base_url, "/work-orders/stats", timeout=timeout, token=token)
    _, feedback_after = request_json(base_url, "/feedback/stats", timeout=timeout, token=token)

    movements = [
        ("alarm total", metric(alarms_before, "total"), metric(alarms_after, "total")),
        ("query total", metric(queries_before, "total"), metric(queries_after, "total")),
        ("work-order total", metric(orders_before, "total"), metric(orders_after, "total")),
        ("feedback total", metric(feedback_before, "total"), metric(feedback_after, "total")),
    ]
    changed = [name for name, before, after in movements if after > before]
    detail = ", ".join(f"{name}:{before}->{after}" for name, before, after in movements)
    results.append(("live:bi-movement", pass_fail(len(changed) >= 4), detail))
    return results


def write_report(path: Path, results: list[tuple[str, str, str]], base_url: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    passed = sum(1 for _, status, _ in results if status == "PASS")
    failed = sum(1 for _, status, _ in results if status == "FAIL")
    lines = [
        "# MVP Week 4 Acceptance Report",
        "",
        f"- Generated: {now}",
        f"- Base URL: `{base_url}`",
        f"- Result: `{passed} PASS / {failed} FAIL`",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    lines.extend(f"| `{name}` | {status} | {detail} |" for name, status, detail in results)
    lines.extend([
        "",
        "## Decision",
        "",
        "Pass when every row is `PASS`. If live checks fail because the API is offline, start the backend and rerun this script.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def resolve_report_path(report: str) -> Path:
    path = Path(report)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    root = ROOT.resolve()
    if root not in [resolved, *resolved.parents]:
        raise ValueError("Report path must stay under the project root")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def print_report(results: list[tuple[str, str, str]]) -> None:
    print("\nWeek 4 Acceptance")
    print("-" * 72)
    for name, status, detail in results:
        print(f"[{status:<4}] {name:<24} {detail}")
    print("-" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Week 4 MVP acceptance packaging checks")
    parser.add_argument("--base-url", default="http://localhost:8100", help="Alarm RAG base URL")
    parser.add_argument("--manual", default="808d", help="manual collection")
    parser.add_argument("--alarm-code", default="3000", help="alarm code used in live checks")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--offline", action="store_true", help="run static packaging checks only")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="markdown report path")
    args = parser.parse_args()

    results = static_checks()
    if not args.offline:
        results.extend(live_checks(args.base_url, args.manual, args.alarm_code, args.timeout))

    print_report(results)
    try:
        report_path = resolve_report_path(args.report)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1
    write_report(report_path, results, args.base_url)
    print(f"Report written: {report_path}")

    return 1 if any(status == "FAIL" for _, status, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
