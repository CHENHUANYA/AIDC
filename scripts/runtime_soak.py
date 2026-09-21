import argparse
import http.client
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env
from rag_runtime_check import check_stream_chat_with_snapshot, qdrant_count


load_project_env()


@dataclass
class SoakResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: int


class SoakClient:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = ""

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        trigger_token = os.getenv("ALARM_RAG_TRIGGER_TOKEN", "").strip()
        if trigger_token:
            headers["X-Alarm-RAG-Token"] = trigger_token
        return headers

    def request_json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, Any, int]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self.headers({"Content-Type": "application/json"} if payload is not None else None)
        req = request.Request(self.url(path), data=body, headers=headers, method=method)
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.getcode(), data, int((time.monotonic() - started) * 1000)
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"_raw": text}
            return exc.code, data, int((time.monotonic() - started) * 1000)
        except (OSError, http.client.HTTPException, error.URLError) as exc:
            return 0, {"_error": str(exc)}, int((time.monotonic() - started) * 1000)

    def login(self) -> SoakResult:
        try:
            password = admin_initial_password()
        except EnvConfigError as exc:
            return SoakResult("auth:login", False, str(exc), 0)
        code, data, elapsed = self.request_json(
            "/auth/login",
            "POST",
            {"username": "admin01", "password": password},
        )
        self.token = data.get("token", "") if isinstance(data, dict) else ""
        return SoakResult("auth:login", code == 200 and bool(self.token), f"HTTP {code}", elapsed)


def check_health(client: SoakClient) -> SoakResult:
    code, data, elapsed = client.request_json("/health")
    ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    collections = data.get("collections", {}) if isinstance(data, dict) else {}
    return SoakResult("health", ok, f"HTTP {code}, collections={len(collections)}", elapsed)


def wait_for_login(client: SoakClient, wait_seconds: int, interval_seconds: float) -> tuple[SoakResult, int]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    attempts = 0
    while True:
        attempts += 1
        result = client.login()
        if result.ok or time.monotonic() >= deadline:
            return result, attempts
        time.sleep(max(interval_seconds, 0.1))


def check_lookup(client: SoakClient, manual: str, alarm_code: str) -> SoakResult:
    query = parse.urlencode({"code": alarm_code})
    code, data, elapsed = client.request_json(f"/v1/{manual}/lookup?{query}")
    ok = code == 200 and isinstance(data, dict) and data.get("found") is True
    return SoakResult("lookup", ok, f"HTTP {code}, found={data.get('found') if isinstance(data, dict) else '-'}", elapsed)


def check_chat(client: SoakClient, manual: str, alarm_code: str) -> SoakResult:
    payload = {
        "messages": [{"role": "user", "content": f"Alarm {alarm_code}: give one short maintenance hint."}],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 120,
    }
    code, data, elapsed = client.request_json(f"/v1/{manual}/chat/completions", "POST", payload)
    content = ""
    if isinstance(data, dict):
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    answer_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    rag = data.get("rag", {}) if isinstance(data, dict) else {}
    snapshot_code, snapshot_payload, snapshot_elapsed = client.request_json(f"/rag/answers/{answer_id}") if answer_id else (0, {}, 0)
    snapshot = snapshot_payload.get("answer", {}) if isinstance(snapshot_payload, dict) else {}
    state = str(snapshot.get("answer_state") or "")
    snapshot_ok = (
        snapshot_code == 200
        and snapshot.get("answer_id") == answer_id
        and snapshot.get("answer") == content
        and list(snapshot.get("citations") or []) == list(rag.get("citations") or [])
        and state in {"complete", "fallback", "unavailable"}
        and "provider" in snapshot
        and "model" in snapshot
    )
    return SoakResult(
        "chat",
        code == 200 and bool(content) and snapshot_ok,
        f"HTTP {code}, chars={len(content)}, answer_id={bool(answer_id)}, snapshot={snapshot_ok}, state={state or '-'}",
        elapsed + snapshot_elapsed,
    )


def check_stream_chat(client: SoakClient, manual: str, alarm_code: str) -> SoakResult:
    started = time.monotonic()
    result, answer_id, content = check_stream_chat_with_snapshot(
        client,
        manual,
        f"Alarm {alarm_code} stream response soak validation",
        alarm_code,
    )
    snapshot_code, snapshot_payload, snapshot_elapsed = client.request_json(f"/rag/answers/{answer_id}") if answer_id else (0, {}, 0)
    snapshot = snapshot_payload.get("answer", {}) if isinstance(snapshot_payload, dict) else {}
    snapshot_ok = (
        snapshot_code == 200
        and snapshot.get("answer_id") == answer_id
        and snapshot.get("answer") == content
        and str(snapshot.get("answer_state") or "") in {"complete", "fallback", "unavailable"}
    )
    return SoakResult(
        "stream-chat",
        result.status == "PASS" and snapshot_ok,
        f"{result.detail}, snapshot={snapshot_ok}",
        int((time.monotonic() - started) * 1000) + snapshot_elapsed,
    )


def check_vector_coverage(client: SoakClient, qdrant_url: str, collections: list[str], timeout: int) -> SoakResult:
    started = time.monotonic()
    code, health, _ = client.request_json("/health/details")
    health_collections = health.get("collections", {}) if isinstance(health, dict) else {}
    api_key = os.environ.get("QDRANT_API_KEY", "").strip()
    details = []
    ok = code == 200
    for collection_name in collections:
        collection = health_collections.get(collection_name, {})
        expected = int(collection.get("alarms_indexed") or 0) if isinstance(collection, dict) else 0
        points = qdrant_count(qdrant_url, collection_name, timeout, api_key)
        covered = expected > 0 and points is not None and points >= expected
        ok = ok and covered
        details.append(f"{collection_name}={points}/{expected}")
    return SoakResult(
        "vector-coverage",
        ok,
        ", ".join(details),
        int((time.monotonic() - started) * 1000),
    )


def check_alarm_roundtrip(client: SoakClient, manual: str, alarm_code: str, iteration: int) -> list[SoakResult]:
    payload = {
        "alarm_code": alarm_code,
        "manual": manual,
        "machine_id": "SOAK-RIG",
        "source": "runtime-soak",
        "severity": "medium",
        "description": f"Runtime soak alarm iteration {iteration}",
    }
    code, data, elapsed = client.request_json("/trigger-alarm", "POST", payload)
    trigger_ok = code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    trigger = SoakResult("alarm:trigger", trigger_ok, f"HTTP {code}", elapsed)

    code, data, elapsed = client.request_json("/pending-alarms")
    alarms = data.get("alarms", []) if isinstance(data, dict) else []
    hit = any(isinstance(item, dict) and item.get("source") == "runtime-soak" for item in alarms)
    pending = SoakResult("alarm:pending", code == 200 and hit, f"HTTP {code}, count={len(alarms)}", elapsed)
    return [trigger, pending]


def run_iteration(
    client: SoakClient,
    manual: str,
    alarm_code: str,
    iteration: int,
    include_chat: bool,
    include_stream: bool,
    include_alarm: bool,
    qdrant_url: str,
    coverage_collections: list[str],
    coverage_every: int,
) -> list[SoakResult]:
    results = [check_health(client), check_lookup(client, manual, alarm_code)]
    if include_chat:
        results.append(check_chat(client, manual, alarm_code))
    if include_stream:
        results.append(check_stream_chat(client, manual, alarm_code))
    if include_alarm:
        results.extend(check_alarm_roundtrip(client, manual, alarm_code, iteration))
    if coverage_collections and (iteration == 1 or iteration % coverage_every == 0):
        results.append(check_vector_coverage(client, qdrant_url, coverage_collections, client.timeout))
    return results


def print_result(iteration: int, result: SoakResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(f"[{status}] iter={iteration:<4} {result.name:<14} {result.elapsed_ms:>6} ms  {result.detail}")


def percentile(values: list[int], percent: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = math.ceil(min(max(percent, 0.0), 1.0) * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def build_report(
    results: list[tuple[int, SoakResult]],
    *,
    base_url: str,
    manual: str,
    alarm_code: str,
    started_at: str,
    finished_at: str,
    configured_duration_seconds: int,
    max_failures: int,
    latency_slos_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    grouped: dict[str, list[SoakResult]] = {}
    for _, result in results:
        grouped.setdefault(result.name, []).append(result)
    checks = {}
    for name, entries in sorted(grouped.items()):
        elapsed = [entry.elapsed_ms for entry in entries]
        checks[name] = {
            "count": len(entries),
            "failures": sum(not entry.ok for entry in entries),
            "min_ms": min(elapsed, default=0),
            "avg_ms": round(sum(elapsed) / len(elapsed)) if elapsed else 0,
            "p50_ms": percentile(elapsed, 0.50),
            "p95_ms": percentile(elapsed, 0.95),
            "max_ms": max(elapsed, default=0),
        }
    failures = sum(not result.ok for _, result in results)
    latency_slos = {}
    for name, threshold_ms in sorted((latency_slos_ms or {}).items()):
        metrics = checks.get(name)
        actual_ms = metrics.get("p95_ms") if metrics else None
        met = isinstance(actual_ms, int) and actual_ms <= threshold_ms
        latency_slos[name] = {
            "metric": "p95_ms",
            "threshold_ms": threshold_ms,
            "actual_ms": actual_ms,
            "met": met,
        }
    slo_failures = [name for name, result in latency_slos.items() if not result["met"]]
    return {
        "status": "pass" if failures <= max_failures and not slo_failures else "fail",
        "started_at": started_at,
        "finished_at": finished_at,
        "base_url": base_url,
        "manual": manual,
        "alarm_code": alarm_code,
        "configured_duration_seconds": configured_duration_seconds,
        "iterations": max((iteration for iteration, _ in results), default=0),
        "total_checks": len(results),
        "failures": failures,
        "max_failures": max_failures,
        "checks": checks,
        "latency_slos": latency_slos,
        "slo_failures": slo_failures,
        "failure_details": [
            {"iteration": iteration, "name": result.name, "detail": result.detail, "elapsed_ms": result.elapsed_ms}
            for iteration, result in results
            if not result.ok
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Alarm RAG Runtime Soak Report",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Started: `{report['started_at']}`",
        f"- Finished: `{report['finished_at']}`",
        f"- Configured duration: `{report['configured_duration_seconds']}` seconds",
        f"- Actual elapsed: `{report.get('elapsed_seconds', '-')}` seconds",
        f"- Iterations: `{report['iterations']}`",
        f"- Checks: `{report['total_checks']}`",
        f"- Failures: `{report['failures']}` / allowed `{report['max_failures']}`",
        "",
        "| Check | Count | Failures | Min ms | Avg ms | P50 ms | P95 ms | Max ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in report["checks"].items():
        lines.append(
            f"| {name} | {metrics['count']} | {metrics['failures']} | {metrics['min_ms']} | "
            f"{metrics['avg_ms']} | {metrics['p50_ms']} | {metrics['p95_ms']} | {metrics['max_ms']} |"
        )
    if report.get("latency_slos"):
        lines.extend([
            "",
            "## Latency SLOs",
            "",
            "| Check | Metric | Actual ms | Threshold ms | Met |",
            "|---|---|---:|---:|---|",
        ])
        for name, slo in report["latency_slos"].items():
            actual = slo["actual_ms"] if slo["actual_ms"] is not None else "not observed"
            lines.append(
                f"| {name} | {slo['metric']} | {actual} | {slo['threshold_ms']} | "
                f"{'yes' if slo['met'] else 'no'} |"
            )
    return "\n".join(lines) + "\n"


def write_reports(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated Alarm RAG runtime checks for soak testing")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--startup-wait-seconds", type=int, default=120)
    parser.add_argument("--skip-chat", action="store_true", help="skip repeated chat calls")
    parser.add_argument("--include-stream", action="store_true", help="include incremental SSE chat validation")
    parser.add_argument("--skip-alarm", action="store_true", help="skip repeated trigger/pending queue calls")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--coverage-collections", default="808d,840d,840dsl")
    parser.add_argument("--coverage-every", type=int, default=10, help="check vector coverage every N iterations")
    parser.add_argument("--skip-vector-coverage", action="store_true")
    parser.add_argument(
        "--chat-p95-slo-ms",
        type=int,
        default=0,
        help="fail when chat P95 latency exceeds this threshold; 0 disables the SLO",
    )
    parser.add_argument("--report-json", type=Path, default=Path("tests_tmp/runtime-soak/report.json"))
    parser.add_argument("--report-md", type=Path, default=Path("tests_tmp/runtime-soak/report.md"))
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    if args.max_failures < 0:
        parser.error("--max-failures cannot be negative")
    if args.coverage_every <= 0:
        parser.error("--coverage-every must be positive")
    if args.chat_p95_slo_ms < 0:
        parser.error("--chat-p95-slo-ms cannot be negative")

    run_started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    client = SoakClient(args.base_url, args.timeout)
    coverage_collections = [] if args.skip_vector_coverage else [
        value.strip() for value in args.coverage_collections.split(",") if value.strip()
    ]
    latency_slos_ms = {}
    if args.chat_p95_slo_ms:
        if not args.skip_chat:
            latency_slos_ms["chat"] = args.chat_p95_slo_ms
        if args.include_stream:
            latency_slos_ms["stream-chat"] = args.chat_p95_slo_ms
    login, login_attempts = wait_for_login(client, args.startup_wait_seconds, args.interval_seconds)
    login.detail = f"{login.detail}, attempts={login_attempts}"
    print_result(0, login)
    recorded: list[tuple[int, SoakResult]] = [(0, login)]
    if not login.ok:
        report = build_report(
            recorded,
            base_url=args.base_url,
            manual=args.manual,
            alarm_code=args.alarm_code,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            configured_duration_seconds=args.duration_seconds,
            max_failures=args.max_failures,
            latency_slos_ms=latency_slos_ms,
        )
        report["elapsed_seconds"] = round(time.monotonic() - run_started, 3)
        write_reports(report, args.report_json, args.report_md)
        return 1

    deadline = time.monotonic() + max(args.duration_seconds, 1)
    iteration = 0
    failures = 0
    total = 0
    while time.monotonic() < deadline:
        iteration += 1
        for result in run_iteration(
            client,
            args.manual,
            args.alarm_code,
            iteration,
            include_chat=not args.skip_chat,
            include_stream=args.include_stream,
            include_alarm=not args.skip_alarm,
            qdrant_url=args.qdrant_url,
            coverage_collections=coverage_collections,
            coverage_every=args.coverage_every,
        ):
            recorded.append((iteration, result))
            total += 1
            failures += 0 if result.ok else 1
            print_result(iteration, result)
        if failures > args.max_failures:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(args.interval_seconds, remaining))

    print("-" * 72)
    print(f"iterations={iteration} checks={total} failures={failures} max_failures={args.max_failures}")
    report = build_report(
        recorded,
        base_url=args.base_url,
        manual=args.manual,
        alarm_code=args.alarm_code,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        configured_duration_seconds=args.duration_seconds,
        max_failures=args.max_failures,
        latency_slos_ms=latency_slos_ms,
    )
    report["elapsed_seconds"] = round(time.monotonic() - run_started, 3)
    write_reports(report, args.report_json, args.report_md)
    print(f"json_report={args.report_json}")
    print(f"markdown_report={args.report_md}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
