import argparse
import json
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


@dataclass
class SmokeResult:
    name: str
    status: str
    detail: str


class SmokeRunner:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[SmokeResult] = []
        self.token = ""

    def record(self, name: str, status: str, detail: str) -> None:
        self.results.append(SmokeResult(name=name, status=status, detail=detail))

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_text(self, path: str) -> tuple[int, str]:
        req = request.Request(self.url(path), headers=self.headers(), method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                return resp.getcode(), resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, body
        except (TimeoutError, error.URLError) as exc:
            return 0, str(exc)

    def get_json(self, path: str) -> tuple[int, Any]:
        code, body = self.get_text(path)
        try:
            return code, json.loads(body)
        except json.JSONDecodeError:
            return code, {"_raw": body}

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        req = request.Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.getcode(), json.loads(body)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"_raw": body}
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def patch_json(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        req = request.Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers({"Content-Type": "application/json"}),
            method="PATCH",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.getcode(), json.loads(body)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"_raw": body}
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def delete(self, path: str) -> tuple[int, Any]:
        req = request.Request(self.url(path), headers=self.headers(), method="DELETE")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.getcode(), json.loads(body)
                except json.JSONDecodeError:
                    return resp.getcode(), {"_raw": body}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"_raw": body}
        except (TimeoutError, error.URLError) as exc:
            return 0, {"_error": str(exc)}

    def post_multipart(self, path: str, file_field: str, file_path: Path) -> tuple[int, Any]:
        boundary = f"----alarm-smoke-{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        parts = []
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(file_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        req = request.Request(
            self.url(path),
            data=body,
            method="POST",
            headers=self.headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
        )
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

    def login(self, username: str = "admin01", password: str = "demo1234") -> bool:
        previous_token = self.token
        self.token = ""
        code, data = self.post_json("/auth/login", {"username": username, "password": password})
        token = data.get("token") if isinstance(data, dict) else ""
        self.token = token or previous_token
        self.record("auth:login", "PASS" if code == 200 and bool(token) else "FAIL", f"HTTP {code}")
        return bool(token)


def check_health(runner: SmokeRunner) -> bool:
    try:
        code, data = runner.get_json("/health")
    except error.URLError as exc:
        runner.record("health", "FAIL", f"service unreachable: {exc}")
        return False

    if code != 200 or not isinstance(data, dict):
        runner.record("health", "FAIL", f"unexpected response: HTTP {code}, body={data}")
        return False
    if data.get("status") != "ok":
        runner.record("health", "FAIL", f"status != ok: {data}")
        return False

    collections = ", ".join(sorted((data.get("collections") or {}).keys())) or "(none)"
    runner.record("health", "PASS", f"HTTP 200, collections: {collections}")
    return True


def check_pages(runner: SmokeRunner) -> None:
    for path in ["/dashboard", "/assistant", "/operations", "/alarm-app"]:
        code, body = runner.get_text(path)
        if code == 200 and "<html" in body.lower():
            runner.record(f"page:{path}", "PASS", "HTTP 200")
        else:
            runner.record(f"page:{path}", "FAIL", f"HTTP {code}")


def check_collections(runner: SmokeRunner) -> None:
    code, data = runner.get_json("/collections")
    collections = data.get("collections") if isinstance(data, dict) else None
    if code == 200 and isinstance(collections, list):
        runner.record("collections", "PASS", f"count={len(collections)}")
    else:
        runner.record("collections", "FAIL", f"HTTP {code}, body={data}")


def check_lookup(runner: SmokeRunner, manual: str, alarm_code: str) -> None:
    query = parse.urlencode({"code": alarm_code})
    code, data = runner.get_json(f"/v1/{manual}/lookup?{query}")
    if code == 200 and isinstance(data, dict) and "found" in data:
        found = data.get("found")
        metadata = data.get("metadata") if found else None
        detail = f"HTTP 200, found={found}"
        if isinstance(metadata, dict):
            detail += f", metadata={','.join(sorted(metadata.keys()))}"
        runner.record("lookup", "PASS", detail)
    else:
        runner.record("lookup", "FAIL", f"HTTP {code}, body={data}")


def check_chat(runner: SmokeRunner, manual: str) -> None:
    payload = {
        "messages": [{"role": "user", "content": "請用一句話說明這是 smoke test"}],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 128,
    }
    code, data = runner.post_json(f"/v1/{manual}/chat/completions", payload)
    content = (
        data.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(data, dict)
        else None
    )
    if code == 200 and isinstance(content, str):
        runner.record("chat", "PASS", f"HTTP 200, len={len(content)}")
    else:
        runner.record("chat", "FAIL", f"HTTP {code}, body={data}")


def check_pdf_upload(runner: SmokeRunner, manual: str, pdf_path: str | None) -> None:
    if not pdf_path:
        runner.record("upload:pdf", "SKIP", "no --pdf path provided")
        return

    file_path = Path(pdf_path)
    if not file_path.exists():
        runner.record("upload:pdf", "SKIP", f"file not found: {file_path}")
        return

    code, data = runner.post_multipart(f"/v1/{manual}/ingest", "file", file_path)
    status = data.get("status") if isinstance(data, dict) else None
    if code == 200 and status in {"ok", "duplicate"}:
        runner.record("upload:pdf", "PASS", f"HTTP 200, status={status}")
    else:
        runner.record("upload:pdf", "FAIL", f"HTTP {code}, body={data}")


def check_text_ingest(runner: SmokeRunner, manual: str, alarm_code: str) -> None:
    payload = {
        "text": (
            "Smoke maintenance note: inspect the NC start condition, verify program "
            "selection, confirm emergency-stop chain reset, then clear the alarm."
        ),
        "code": alarm_code,
        "title": f"Smoke maintenance note for alarm {alarm_code}",
        "source": "smoke-test",
    }
    code, data = runner.post_json(f"/v1/{manual}/ingest-text", payload)
    if code == 200 and isinstance(data, dict) and data.get("status") == "ok":
        runner.record("ingest:text", "PASS", f"sections_added={data.get('sections_added')}")
    else:
        runner.record("ingest:text", "FAIL", f"HTTP {code}, body={data}")


def check_work_order_crud(runner: SmokeRunner, manual: str, alarm_code: str) -> None:
    create_payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "SMOKE-M1",
        "priority": "medium",
        "assigned_to": "smoke-bot",
        "description": "smoke test create",
        "source": "smoke",
    }
    code, created = runner.post_json("/work-orders", create_payload)
    if code != 200 or not isinstance(created, dict) or created.get("status") != "ok":
        runner.record("work-order:create", "FAIL", f"HTTP {code}, body={created}")
        runner.record("work-order:update", "SKIP", "create failed")
        runner.record("work-order:delete", "SKIP", "create failed")
        return

    order = created.get("order") or {}
    order_id = order.get("id")
    if not order_id:
        runner.record("work-order:create", "FAIL", f"missing order id: {created}")
        runner.record("work-order:update", "SKIP", "create missing id")
        runner.record("work-order:delete", "SKIP", "create missing id")
        return
    runner.record("work-order:create", "PASS", f"id={order_id}")

    update_payload = {
        "status": "completed",
        "resolution": "smoke test resolution",
        "root_cause": "smoke test root cause",
        "repair_action": "smoke test repair action",
        "notes": "smoke test notes",
    }
    code, updated = runner.patch_json(f"/work-orders/{order_id}", update_payload)
    if code == 200 and isinstance(updated, dict) and updated.get("status") == "ok":
        runner.record("work-order:update", "PASS", f"id={order_id}")
    else:
        runner.record("work-order:update", "FAIL", f"HTTP {code}, body={updated}")

    code, deleted = runner.delete(f"/work-orders/{order_id}")
    if code == 200 and isinstance(deleted, dict) and deleted.get("status") == "ok":
        runner.record("work-order:delete", "PASS", f"id={order_id}")
    else:
        runner.record("work-order:delete", "FAIL", f"HTTP {code}, body={deleted}")


def check_banner_polling(runner: SmokeRunner, manual: str, alarm_code: str) -> None:
    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "SMOKE-M2",
        "source": "smoke",
    }
    code, trigger = runner.post_json("/trigger-alarm", payload)
    if code != 200 or not isinstance(trigger, dict) or trigger.get("status") != "ok":
        runner.record("banner:trigger", "FAIL", f"HTTP {code}, body={trigger}")
        runner.record("banner:poll", "SKIP", "trigger failed")
        return
    runner.record("banner:trigger", "PASS", "alarm queued")

    code, first = runner.get_json("/pending-alarms")
    alarms = first.get("alarms") if isinstance(first, dict) else None
    if code != 200 or not isinstance(alarms, list):
        runner.record("banner:poll", "FAIL", f"HTTP {code}, body={first}")
        return
    if not alarms:
        runner.record("banner:poll", "FAIL", "first poll returned empty alarms")
        return

    code2, second = runner.get_json("/pending-alarms")
    alarms2 = second.get("alarms") if isinstance(second, dict) else None
    if code2 == 200 and isinstance(alarms2, list) and len(alarms2) == 0:
        runner.record("banner:poll", "PASS", "first poll got alarms, second poll cleared queue")
    else:
        runner.record("banner:poll", "FAIL", f"second poll not cleared: HTTP {code2}, body={second}")


def check_n8n_workflow_file(runner: SmokeRunner) -> None:
    workflow_path = Path(__file__).resolve().parents[1] / "mock_data" / "n8n_mock_workflow.json"
    if not workflow_path.exists():
        runner.record("n8n:workflow-file", "FAIL", f"missing {workflow_path}")
        return

    try:
        data = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        runner.record("n8n:workflow-file", "FAIL", f"invalid JSON: {exc}")
        return

    nodes = data.get("nodes") if isinstance(data, dict) else None
    node_names = {str(node.get("name")) for node in nodes or [] if isinstance(node, dict)}
    required = {"Schedule Trigger", "Manual Trigger", "Set Mock Alarm Payload", "POST /trigger-alarm"}
    missing = sorted(required - node_names)
    if isinstance(nodes, list) and not missing:
        runner.record("n8n:workflow-file", "PASS", f"nodes={len(nodes)}")
        return

    runner.record("n8n:workflow-file", "FAIL", f"missing nodes={missing}")


def check_n8n_trigger_sync(runner: SmokeRunner, manual: str, alarm_code: str) -> None:
    _, alarm_before = runner.get_json("/stats/alarms")
    _, order_before = runner.get_json("/work-orders/stats")
    alarm_total_before = alarm_before.get("total", 0) if isinstance(alarm_before, dict) else 0
    order_total_before = order_before.get("total", 0) if isinstance(order_before, dict) else 0

    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "N8N-SMOKE-01",
        "source": "n8n-mock",
        "severity": "high",
        "description": "n8n smoke trigger should update alarm, work-order, and BI stats.",
    }
    code, trigger = runner.post_json("/trigger-alarm", payload)
    work_order = trigger.get("work_order") if isinstance(trigger, dict) else None
    order_id = work_order.get("id") if isinstance(work_order, dict) else None
    alarm = trigger.get("alarm") if isinstance(trigger, dict) else None
    if code != 200 or trigger.get("status") != "ok" or not order_id:
        runner.record("n8n:trigger-sync", "FAIL", f"HTTP {code}, body={trigger}")
        return

    _, alarm_after = runner.get_json("/stats/alarms")
    _, order_after = runner.get_json("/work-orders/stats")
    alarm_total_after = alarm_after.get("total", 0) if isinstance(alarm_after, dict) else 0
    order_total_after = order_after.get("total", 0) if isinstance(order_after, dict) else 0
    by_source = alarm_after.get("by_source", {}) if isinstance(alarm_after, dict) else {}
    order_sources = order_after.get("by_source", {}) if isinstance(order_after, dict) else {}

    updated = alarm_total_after > alarm_total_before and order_total_after > order_total_before
    source_visible = by_source.get("n8n-mock", 0) > 0 and order_sources.get("n8n-mock", 0) > 0
    severity_ok = isinstance(alarm, dict) and alarm.get("severity") == "high"
    if updated and source_visible and severity_ok:
        runner.record("n8n:trigger-sync", "PASS", f"order={order_id}")
        return

    detail = (
        f"alarm_total {alarm_total_before}->{alarm_total_after}, "
        f"order_total {order_total_before}->{order_total_after}, "
        f"alarm_sources={by_source}, order_sources={order_sources}, alarm={alarm}"
    )
    runner.record("n8n:trigger-sync", "FAIL", detail)


def check_stats(runner: SmokeRunner) -> None:
    expected_fields = {
        "/stats/alarms": ["total", "today", "by_manual", "recent"],
        "/stats/queries": ["total", "today", "avg_ms", "recent"],
        "/feedback/stats": ["total", "good", "bad", "entries"],
        "/work-orders/stats": ["total", "by_status", "by_priority"],
    }
    for path, fields in expected_fields.items():
        code, data = runner.get_json(path)
        missing = [field for field in fields if not isinstance(data, dict) or field not in data]
        if code == 200 and not missing:
            runner.record(f"stats:{path}", "PASS", "HTTP 200")
            continue
        runner.record(f"stats:{path}", "FAIL", f"HTTP {code}, missing={missing}, body={data}")


def check_week2_seed_data(runner: SmokeRunner, manual: str) -> None:
    code, orders_data = runner.get_json("/work-orders")
    orders = orders_data.get("orders") if isinstance(orders_data, dict) else None
    week2_orders = [
        order for order in orders or []
        if isinstance(order, dict) and order.get("source") == "week2-history"
    ]
    if code == 200 and len(week2_orders) >= 10:
        runner.record("week2:work-orders", "PASS", f"count={len(week2_orders)}")
    else:
        runner.record("week2:work-orders", "FAIL", f"HTTP {code}, count={len(week2_orders)}, body={orders_data}")

    code, ingest_data = runner.get_json(f"/v1/{manual}/ingest-log")
    entries = ingest_data.get("entries") if isinstance(ingest_data, dict) else None
    sources = {
        str(entry.get("source") or "")
        for entry in entries or []
        if isinstance(entry, dict)
    }
    required_sources = {"week2-sop", "week2-bulletin"}
    missing_sources = sorted(required_sources - sources)
    week2_entries = [
        entry for entry in entries or []
        if isinstance(entry, dict) and str(entry.get("source") or "") in required_sources
    ]
    if code == 200 and not missing_sources and len(week2_entries) >= 5:
        runner.record("week2:knowledge", "PASS", f"count={len(week2_entries)}")
    else:
        runner.record("week2:knowledge", "FAIL", f"HTTP {code}, count={len(week2_entries)}, missing={missing_sources}, body={ingest_data}")


def print_report(results: list[SmokeResult]) -> None:
    print("\nAlarm RAG Smoke Test Report")
    print("-" * 72)
    for r in results:
        print(f"[{r.status:<4}] {r.name:<18} {r.detail}")
    print("-" * 72)
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")
    print(f"Total={total} PASS={passed} FAIL={failed} SKIP={skipped}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Alarm RAG minimal smoke test runner")
    parser.add_argument("--base-url", default="http://localhost:8100", help="Alarm RAG base URL")
    parser.add_argument("--manual", default="808d", help="manual collection for lookup/chat/upload")
    parser.add_argument("--alarm-code", default="3000", help="alarm code used in tests")
    parser.add_argument("--pdf", default=None, help="optional PDF path for ingest upload smoke test")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--require-week2-data", action="store_true", help="fail unless week-2 seeded data exists")
    args = parser.parse_args()

    runner = SmokeRunner(base_url=args.base_url, timeout=args.timeout)
    ready = check_health(runner)
    if not ready:
        runner.record("pages", "SKIP", "service unavailable")
        runner.record("collections", "SKIP", "service unavailable")
        runner.record("lookup", "SKIP", "service unavailable")
        runner.record("chat", "SKIP", "service unavailable")
        runner.record("upload:pdf", "SKIP", "service unavailable")
        runner.record("ingest:text", "SKIP", "service unavailable")
        runner.record("work-order:create", "SKIP", "service unavailable")
        runner.record("work-order:update", "SKIP", "service unavailable")
        runner.record("work-order:delete", "SKIP", "service unavailable")
        runner.record("banner:trigger", "SKIP", "service unavailable")
        runner.record("banner:poll", "SKIP", "service unavailable")
        runner.record("n8n:workflow-file", "SKIP", "service unavailable")
        runner.record("n8n:trigger-sync", "SKIP", "service unavailable")
        runner.record("stats", "SKIP", "service unavailable")
        if args.require_week2_data:
            runner.record("week2:data", "SKIP", "service unavailable")
        print_report(runner.results)
        return 1
    if not runner.login():
        runner.record("auth-dependent-checks", "SKIP", "login failed")
        print_report(runner.results)
        return 1

    check_pages(runner)
    check_collections(runner)
    check_lookup(runner, args.manual, args.alarm_code)
    check_chat(runner, args.manual)
    check_pdf_upload(runner, args.manual, args.pdf)
    check_text_ingest(runner, args.manual, args.alarm_code)
    check_work_order_crud(runner, args.manual, args.alarm_code)
    check_banner_polling(runner, args.manual, args.alarm_code)
    check_n8n_workflow_file(runner)
    check_n8n_trigger_sync(runner, args.manual, args.alarm_code)
    check_stats(runner)
    if args.require_week2_data:
        check_week2_seed_data(runner, args.manual)
    print_report(runner.results)

    failed = any(r.status == "FAIL" for r in runner.results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
