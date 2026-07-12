from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rag_runtime_check import RuntimeClient, check_health, check_lookup, qdrant_count


ALLOWED_SERVICES = {"alarm_rag", "qdrant"}


def restart_service(service: str, project_dir: Path) -> subprocess.CompletedProcess[str]:
    if service not in ALLOWED_SERVICES:
        raise ValueError(f"Unsupported restart service: {service}")
    return subprocess.run(
        ["docker", "compose", "restart", service],
        cwd=project_dir,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def wait_for_app(client: RuntimeClient, deadline: float, interval: float) -> tuple[bool, str]:
    last_detail = "not checked"
    while time.monotonic() < deadline:
        check, _ = check_health(client)
        last_detail = check.detail
        if check.status == "PASS":
            return True, last_detail
        time.sleep(interval)
    return False, last_detail


def wait_for_qdrant(
    url: str,
    collection: str,
    timeout: int,
    api_key: str,
    deadline: float,
    interval: float,
    minimum_points: int,
) -> tuple[bool, int | None]:
    last_count = None
    while time.monotonic() < deadline:
        last_count = qdrant_count(url, collection, timeout, api_key)
        if last_count is not None and last_count >= minimum_points:
            return True, last_count
        time.sleep(interval)
    return False, last_count


def validate_runtime(client: RuntimeClient, manual: str, alarm_code: str) -> dict[str, Any]:
    login = client.login("admin01")
    lookup = check_lookup(client, manual, alarm_code) if login.status == "PASS" else None
    code, retrieval = client.request_json(
        f"/v1/{manual}/retrieve?query=hydraulic%20clamp%20pressure%20switch&top_k=5"
    ) if login.status == "PASS" else (0, {})
    retrieval_ok = code == 200 and isinstance(retrieval, dict) and retrieval.get("ready") is True and bool(retrieval.get("results"))
    return {
        "login": {"status": login.status, "detail": login.detail},
        "lookup": {"status": lookup.status, "detail": lookup.detail} if lookup else {"status": "SKIP"},
        "retrieval": {
            "status": "PASS" if retrieval_ok else "FAIL",
            "detail": f"HTTP {code}, results={len(retrieval.get('results', [])) if isinstance(retrieval, dict) else 0}",
        },
        "ok": login.status == "PASS" and lookup is not None and lookup.status == "PASS" and retrieval_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart local Alarm RAG services and verify recovery")
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--manual", default="808d")
    parser.add_argument("--alarm-code", default="3000")
    parser.add_argument("--services", default="alarm_rag,qdrant")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--recovery-seconds", type=int, default=180)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report-json", type=Path, default=Path("tests_tmp/runtime-restart-recovery/report.json"))
    args = parser.parse_args()
    if args.timeout <= 0 or args.recovery_seconds <= 0 or args.interval_seconds <= 0:
        parser.error("timeout, recovery-seconds, and interval-seconds must be positive")

    services = [value.strip() for value in args.services.split(",") if value.strip()]
    invalid = [value for value in services if value not in ALLOWED_SERVICES]
    if not services or invalid:
        parser.error(f"services must be drawn from {sorted(ALLOWED_SERVICES)}; invalid={invalid}")

    api_key = os.environ.get("QDRANT_API_KEY", "").strip()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "qdrant_url": args.qdrant_url,
        "services": [],
    }
    all_ok = True
    for service in services:
        started = time.monotonic()
        preflight_health, health_payload = check_health(RuntimeClient(args.base_url, args.timeout))
        preflight_points = qdrant_count(args.qdrant_url, args.manual, args.timeout, api_key)
        collection = health_payload.get("collections", {}).get(args.manual, {}) if isinstance(health_payload, dict) else {}
        expected_points = int(collection.get("alarms_indexed") or 0) if isinstance(collection, dict) else 0
        preflight_ok = (
            preflight_health.status == "PASS"
            and expected_points > 0
            and preflight_points is not None
            and preflight_points >= expected_points
        )
        service_result: dict[str, Any] = {
            "service": service,
            "preflight_health": preflight_health.status,
            "preflight_qdrant_points": preflight_points,
            "expected_qdrant_points": expected_points,
        }
        if not preflight_ok:
            service_result.update({
                "restart_exit_code": None,
                "restart_output": "restart skipped because preflight failed",
                "recovered": False,
                "validation": {"ok": False},
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "status": "fail",
            })
            report["services"].append(service_result)
            all_ok = False
            print(f"[FAIL] {service} restart skipped: preflight failed")
            continue
        completed = restart_service(service, args.project_dir.resolve())
        service_result.update({
            "restart_exit_code": completed.returncode,
            "restart_output": completed.stdout.strip()[-2000:],
        })
        recovered = completed.returncode == 0
        deadline = time.monotonic() + max(args.recovery_seconds, 1)
        if recovered and service == "qdrant":
            recovered, count = wait_for_qdrant(
                args.qdrant_url,
                args.manual,
                args.timeout,
                api_key,
                deadline,
                args.interval_seconds,
                expected_points,
            )
            service_result["qdrant_points"] = count
        if recovered:
            recovered, detail = wait_for_app(
                RuntimeClient(args.base_url, args.timeout), deadline, args.interval_seconds
            )
            service_result["health_detail"] = detail
        validation = validate_runtime(RuntimeClient(args.base_url, args.timeout), args.manual, args.alarm_code) if recovered else {"ok": False}
        service_result.update({
            "recovered": recovered,
            "validation": validation,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        service_result["status"] = "pass" if recovered and validation.get("ok") else "fail"
        all_ok = all_ok and service_result["status"] == "pass"
        report["services"].append(service_result)
        print(f"[{service_result['status'].upper()}] {service} recovery {service_result['elapsed_ms']} ms")

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "pass" if all_ok else "fail"
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"json_report={args.report_json}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
