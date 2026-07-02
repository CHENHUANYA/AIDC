from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from scripts.postgresql_concurrency_check import run_check as run_concurrency_check
from scripts.postgresql_phase4_cutover import compare_fingerprints, legacy_fingerprints
from scripts.postgresql_phase4_runtime_acceptance import database_counts, database_settings, run as run_runtime_acceptance


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * fraction)), len(ordered) - 1)
    return ordered[index]


def run_soak(base_url: str, source: Path, duration_seconds: int, interval_seconds: float, max_failures: int) -> dict:
    before_counts = database_counts()
    before_settings = database_settings()
    before_fingerprints = legacy_fingerprints(source)
    latencies: list[int] = []
    failures: list[dict] = []
    iterations = 0
    deadline = time.monotonic() + max(duration_seconds, 1)
    while time.monotonic() < deadline:
        iterations += 1
        started = time.monotonic()
        try:
            report = run_runtime_acceptance(base_url, timeout=60, keep_data=False)
            ok = report["status"] == "ok"
            if not ok:
                failures.append({"iteration": iterations, "report": report})
        except Exception as exc:
            failures.append({"iteration": iterations, "error": f"{type(exc).__name__}: {exc}"})
        latencies.append(int((time.monotonic() - started) * 1000))
        if len(failures) > max_failures:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(max(interval_seconds, 0), remaining))

    concurrency = run_concurrency_check(4)
    after_counts = database_counts()
    after_settings = database_settings()
    fingerprint_comparison = compare_fingerprints(before_fingerprints, legacy_fingerprints(source))
    checks = {
        "runtime_failures_within_limit": len(failures) <= max_failures,
        "database_counts_restored": after_counts == before_counts,
        "settings_restored": after_settings == before_settings,
        "legacy_source_unchanged": fingerprint_comparison["unchanged"],
        "concurrency": concurrency["status"] == "ok",
    }
    return {
        "status": "ok" if all(checks.values()) else "fail",
        "duration_seconds": duration_seconds,
        "iterations": iterations,
        "failures": failures,
        "checks": checks,
        "latency_ms": {
            "min": min(latencies) if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "before_counts": before_counts,
        "after_counts": after_counts,
        "fingerprint_comparison": fingerprint_comparison,
        "concurrency": concurrency,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Short PostgreSQL runtime soak with automatic cleanup")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--source", default=str(ROOT / "alarm_db"))
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--interval-seconds", type=float, default=1)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    report = run_soak(
        args.base_url,
        Path(args.source),
        args.duration_seconds,
        args.interval_seconds,
        args.max_failures,
    )
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
