import argparse
import json
import os
import time
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env
from n8n_workflow_check import DEFAULT_WORKFLOW, load_workflow, validate_workflow


ROOT = Path(__file__).resolve().parents[1]
load_project_env()


@dataclass
class Result:
    name: str
    status: str
    detail: str


class Runner:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[Result] = []
        self.token = ""

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append(Result(name, "PASS" if ok else "FAIL", detail))

    def headers(self, payload: dict[str, Any] | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        trigger_token = os.getenv("ALARM_RAG_TRIGGER_TOKEN", "").strip()
        if trigger_token:
            headers["X-Alarm-RAG-Token"] = trigger_token
        return headers

    def request_json(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(f"{self.base_url}{path}", data=body, headers=self.headers(payload), method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
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

    def login(self, username: str = "admin01", password: str | None = None) -> bool:
        try:
            password = password or admin_initial_password()
        except EnvConfigError as exc:
            self.record("auth:login", False, str(exc))
            return False
        previous_token = self.token
        self.token = ""
        code, data = self.request_json(
            "/auth/login",
            "POST",
            {"username": username, "password": password},
        )
        token = data.get("token") if isinstance(data, dict) else ""
        self.token = token or previous_token
        self.record("auth:login", code == 200 and bool(token), f"HTTP {code}")
        return bool(token)


def count_total(data: dict[str, Any]) -> int:
    value = data.get("total")
    return value if isinstance(value, int) else 0


def check_static_baseline(runner: Runner) -> None:
    required = [
        ROOT / "docs" / "SMOKE_TEST.md",
        ROOT / "docs" / "MVP_ACCEPTANCE_CHECKLIST.md",
        ROOT / "scripts" / "smoke_test.py",
        ROOT / "scripts" / "week4_acceptance.py",
        ROOT / "mock_data" / "n8n_mock_workflow.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    runner.record("static:baseline-files", not missing, f"missing={missing}")

    gitignore_path = ROOT / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8", errors="replace")
    runner.record("static:n8n-data-ignored", "n8n_data/" in gitignore_text, "expects n8n_data/")


def check_n8n_workflow(runner: Runner) -> None:
    try:
        workflow = load_workflow(DEFAULT_WORKFLOW)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        runner.record("n8n:workflow-load", False, str(exc))
        return
    for item in validate_workflow(workflow):
        runner.record(f"n8n:{item.name}", item.status == "PASS", item.detail)


def check_health(runner: Runner) -> bool:
    code, data = runner.request_json("/health")
    ok = code == 200 and data.get("status") == "ok"
    runner.record("live:health", ok, f"HTTP {code}")
    return ok


def check_login_config(runner: Runner) -> None:
    code, data = runner.request_json("/auth/login-config")
    users = data.get("bootstrap_users") if isinstance(data, dict) else []
    roles = {
        str(user.get("role") or "")
        for user in users
        if isinstance(user, dict)
    }
    ok = (
        code == 200
        and data.get("status") == "ok"
        and isinstance(data.get("production"), bool)
        and isinstance(data.get("initial_password_configured"), bool)
        and {"admin", "supervisor"}.issubset(roles)
    )
    runner.record("auth:login-config", ok, f"HTTP {code}, roles={','.join(sorted(roles)) or '-'}")


def check_lookup_metadata(runner: Runner, manual: str, alarm_code: str) -> None:
    query = parse.urlencode({"code": alarm_code})
    code, data = runner.request_json(f"/v1/{manual}/lookup?{query}")
    metadata = data.get("metadata") if isinstance(data, dict) else None
    metadata_ok = (
        code == 200
        and data.get("found") is True
        and isinstance(metadata, dict)
        and metadata.get("collection") == manual
        and metadata.get("code") == alarm_code
        and "page" in metadata
        and "title" in metadata
    )
    runner.record("rag:lookup-metadata", metadata_ok, f"HTTP {code}, found={data.get('found')}")


def check_stats_schema(runner: Runner) -> None:
    expected = {
        "/stats/alarms": {"total", "today", "by_manual", "by_source", "recent"},
        "/stats/queries": {"total", "today", "avg_ms", "recent"},
        "/feedback/stats": {"total", "good", "bad", "entries"},
        "/work-orders/stats": {"total", "by_status", "by_priority", "by_source"},
    }
    for path, fields in expected.items():
        code, data = runner.request_json(path)
        missing = sorted(field for field in fields if field not in data)
        runner.record(f"stats:schema:{path}", code == 200 and not missing, f"HTTP {code}, missing={missing}")


def check_work_order_crud(runner: Runner, manual: str, alarm_code: str, marker: str) -> None:
    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": f"REG-CRUD-{marker}",
        "priority": "medium",
        "assigned_to": "regression-bot",
        "description": f"Regression CRUD check {marker}",
        "source": "regression-crud",
    }
    code, created = runner.request_json("/work-orders", "POST", payload)
    order = created.get("order") if isinstance(created, dict) else None
    order_id = order.get("id") if isinstance(order, dict) else ""
    runner.record("work-order:create", code == 200 and created.get("status") == "ok" and bool(order_id), f"HTTP {code}, id={order_id or '-'}")
    if not order_id:
        runner.record("work-order:get", False, "create failed")
        runner.record("work-order:update", False, "create failed")
        runner.record("work-order:delete", False, "create failed")
        return

    code, fetched = runner.request_json(f"/work-orders/{order_id}")
    runner.record("work-order:get", code == 200 and fetched.get("order", {}).get("id") == order_id, f"HTTP {code}")

    code, updated = runner.request_json(
        f"/work-orders/{order_id}",
        "PATCH",
        {"status": "in_progress", "notes": f"Regression update {marker}"},
    )
    updated_order = updated.get("order") if isinstance(updated, dict) else None
    runner.record(
        "work-order:update",
        code == 200 and isinstance(updated_order, dict) and updated_order.get("status") == "in_progress",
        f"HTTP {code}, status={updated_order.get('status') if isinstance(updated_order, dict) else '-'}",
    )

    code, deleted = runner.request_json(f"/work-orders/{order_id}", "DELETE")
    runner.record("work-order:delete", code == 200 and deleted.get("status") == "ok", f"HTTP {code}")


def check_alarm_flow(runner: Runner, manual: str, alarm_code: str, marker: str) -> None:
    _, alarms_before = runner.request_json("/stats/alarms")
    _, orders_before = runner.request_json("/work-orders/stats")

    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": f"REG-FLOW-{marker}",
        "source": "regression-trigger",
        "severity": "critical",
        "description": f"Regression alarm trigger flow {marker}",
    }
    code, trigger = runner.request_json("/trigger-alarm", "POST", payload)
    order = trigger.get("work_order") if isinstance(trigger, dict) else None
    alarm = trigger.get("alarm") if isinstance(trigger, dict) else None
    order_id = order.get("id") if isinstance(order, dict) else ""
    trigger_ok = (
        code == 200
        and trigger.get("status") == "ok"
        and bool(order_id)
        and isinstance(alarm, dict)
        and alarm.get("source") == "regression-trigger"
        and alarm.get("severity") == "critical"
    )
    runner.record("alarm:trigger-work-order", trigger_ok, f"HTTP {code}, order={order_id or '-'}")

    code, pending = runner.request_json("/pending-alarms")
    queued = pending.get("alarms") if isinstance(pending, dict) else []
    queue_hit = any(isinstance(item, dict) and item.get("machine_id") == f"REG-FLOW-{marker}" for item in queued)
    queue_schema_ok = code == 200 and isinstance(queued, list)
    runner.record(
        "alarm:banner-queue",
        queue_schema_ok,
        f"HTTP {code}, count={len(queued) if isinstance(queued, list) else '-'}, hit={queue_hit}",
    )

    code, cleared = runner.request_json("/pending-alarms")
    cleared_alarms = cleared.get("alarms") if isinstance(cleared, dict) else []
    runner.record("alarm:banner-clear", code == 200 and isinstance(cleared_alarms, list) and not cleared_alarms, f"HTTP {code}, count={len(cleared_alarms) if isinstance(cleared_alarms, list) else '-'}")

    if order_id:
        unique_case = f"Regression unique case {marker}"
        root_cause = f"{unique_case}: simulated alarm path injected for acceptance coverage."
        repair_action = f"{unique_case}: verified metadata, reset simulated condition, and closed this exact work order."
        resolution = f"{unique_case}: source metadata was verified and the simulated alarm condition was cleared."
        code, updated = runner.request_json(
            f"/work-orders/{order_id}",
            "PATCH",
            {
                "status": "completed",
                "assigned_to": "regression-bot",
                "resolution": resolution,
                "root_cause": root_cause,
                "repair_action": repair_action,
                "notes": f"{unique_case}: knowledge review check.",
            },
        )
        updated_order = updated.get("order") if isinstance(updated, dict) else None
        review = updated.get("knowledge_review") if isinstance(updated, dict) else None
        review_status = ""
        if isinstance(review, dict):
            review_status = str(review.get("review_status") or "")
        if not review_status and isinstance(updated_order, dict):
            review_status = str(updated_order.get("kb_review_status") or "")
        pending_review = code == 200 and review_status == "pending_review"
        detail = f"HTTP {code}, review={review}, status={updated.get('status') if isinstance(updated, dict) else '-'}"
        if isinstance(updated, dict) and updated.get("message"):
            detail += f", message={updated.get('message')}"
        runner.record("work-order:knowledge-candidate", pending_review, detail)

        code, approved = runner.request_json(
            f"/work-orders/{order_id}/knowledge-review",
            "POST",
            {"action": "approve", "note": "Regression approved knowledge candidate."},
        )
        ingested = (
            code == 200
            and isinstance(approved, dict)
            and approved.get("status") == "ok"
            and approved.get("order", {}).get("kb_review_status") == "ingested"
        )
        approve_detail = f"HTTP {code}, ingested={ingested}"
        if isinstance(approved, dict):
            if approved.get("message"):
                approve_detail += f", message={approved.get('message')}"
            if approved.get("duplicate_of"):
                approve_detail += f", duplicate_of={approved.get('duplicate_of')}"
        runner.record("work-order:knowledge-approve", ingested, approve_detail)

        code, log = runner.request_json(f"/v1/{manual}/ingest-log")
        entries = log.get("entries") if isinstance(log, dict) else []
        log_hit = any(
            isinstance(entry, dict)
            and entry.get("source") == "workorder"
            and str(entry.get("title", "")).endswith(order_id)
            for entry in entries
        )
        runner.record("ingest-log:approved-workorder", code == 200 and log_hit, f"HTTP {code}, order={order_id}")

    _, alarms_after = runner.request_json("/stats/alarms")
    _, orders_after = runner.request_json("/work-orders/stats")
    alarm_moved = count_total(alarms_after) > count_total(alarms_before)
    order_moved = count_total(orders_after) > count_total(orders_before)
    runner.record(
        "stats:alarm-order-movement",
        alarm_moved and order_moved,
        f"alarms {count_total(alarms_before)}->{count_total(alarms_after)}, orders {count_total(orders_before)}->{count_total(orders_after)}",
    )


def check_feedback_flow(runner: Runner, manual: str, alarm_code: str, marker: str) -> None:
    _, before = runner.request_json("/feedback/stats")
    payload = {
        "query": f"Regression feedback {marker}",
        "collection": manual,
        "feedback": "good",
        "alarm_code": alarm_code,
    }
    code, saved = runner.request_json("/feedback", "POST", payload)
    _, after = runner.request_json("/feedback/stats")
    moved = count_total(after) > count_total(before)
    runner.record("feedback:save-and-stats", code == 200 and saved.get("status") == "ok" and moved, f"HTTP {code}, total {count_total(before)}->{count_total(after)}")


def print_report(results: list[Result]) -> None:
    print("\nAlarm RAG Regression Checks")
    print("-" * 88)
    for result in results:
        print(f"[{result.status:<4}] {result.name:<34} {result.detail}")
    print("-" * 88)
    passed = sum(1 for result in results if result.status == "PASS")
    failed = sum(1 for result in results if result.status == "FAIL")
    print(f"PASS={passed} FAIL={failed}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run focused Alarm RAG regression checks")
    parser.add_argument("--base-url", default="http://localhost:8100", help="Alarm RAG base URL")
    parser.add_argument("--manual", default="808d", help="manual collection")
    parser.add_argument("--alarm-code", default="3000", help="alarm code used in live checks")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    args = parser.parse_args()

    marker = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    runner = Runner(args.base_url, args.timeout)
    check_static_baseline(runner)
    check_n8n_workflow(runner)
    if check_health(runner):
        check_login_config(runner)
        if not runner.login():
            print_report(runner.results)
            return 1
        check_lookup_metadata(runner, args.manual, args.alarm_code)
        check_stats_schema(runner)
        check_work_order_crud(runner, args.manual, args.alarm_code, marker)
        check_alarm_flow(runner, args.manual, args.alarm_code, marker)
        check_feedback_flow(runner, args.manual, args.alarm_code, marker)

    print_report(runner.results)
    return 1 if any(result.status == "FAIL" for result in runner.results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
