from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import delete, func, or_, select

from db.models import AlarmEvent, AuditEvent, Feedback, Issue, LoginSession, RagAnswer, WorkOrder
from db.session import reset_database_state_for_tests, session_scope, transaction_scope
from repositories.postgres_auth import token_digest
from repositories.runtime import require_known_data_store
from scripts.env_utils import admin_initial_password
from scripts.postgresql_concurrency_check import run_check as run_concurrency_check
from scripts.postgresql_phase4_cutover import compare_fingerprints, legacy_fingerprints
from scripts.postgresql_phase4_runtime_acceptance import database_settings, request_json
from scripts.postgresql_test_cleanup import cleanup_workflow_records, workflow_orphan_audit_count


ROOT = Path(__file__).resolve().parents[1]


def read_container_secret(container: str, path: str) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "cat", "--", path],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    value = result.stdout
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise RuntimeError("PostgreSQL container secret file is empty or malformed")
    return value


def load_postgres_environment_from_container(
    container: str,
    host: str = "127.0.0.1",
    port: int = 5432,
) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    entries = json.loads(result.stdout)
    values = {
        key: value
        for key, value in (
            entry.split("=", 1)
            for entry in entries
            if isinstance(entry, str) and "=" in entry
        )
        if key in {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"}
    }
    missing = {"POSTGRES_DB", "POSTGRES_USER"} - values.keys()
    if missing:
        raise RuntimeError(f"PostgreSQL container environment is missing: {', '.join(sorted(missing))}")
    password = values.get("POSTGRES_PASSWORD", "")
    password_file = values.get("POSTGRES_PASSWORD_FILE", "")
    if bool(password) == bool(password_file):
        raise RuntimeError(
            "PostgreSQL container must configure exactly one of POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE"
        )
    if password_file:
        password = read_container_secret(container, password_file)
    os.environ.update({
        "POSTGRES_DB": values["POSTGRES_DB"],
        "POSTGRES_USER": values["POSTGRES_USER"],
        "POSTGRES_PASSWORD": password,
    })
    os.environ.pop("POSTGRES_PASSWORD_FILE", None)
    os.environ["POSTGRES_HOST"] = host
    os.environ["POSTGRES_PORT"] = str(port)
    os.environ["POSTGRES_ENABLED"] = "true"
    os.environ["DATA_STORE"] = "postgresql"
    reset_database_state_for_tests()
    return {
        "container": container,
        "host": host,
        "port": str(port),
        "database": values["POSTGRES_DB"],
        "user": values["POSTGRES_USER"],
    }


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * fraction)), len(ordered) - 1)
    return ordered[index]


class RateLimiter:
    def __init__(self, requests_per_second: float):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self.next_slot = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, deadline: float) -> bool:
        with self.lock:
            now = time.monotonic()
            scheduled = max(now, self.next_slot)
            if scheduled >= deadline:
                return False
            self.next_slot = scheduled + self.interval
        delay = scheduled - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return True


@dataclass
class Metrics:
    sample_limit: int = 100
    request_count: int = 0
    failure_count: int = 0
    latencies: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    failures: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        operation: str,
        latency_ms: int,
        ok: bool,
        status_code: int = 0,
        error: str = "",
        worker: int = 0,
    ) -> None:
        with self.lock:
            self.request_count += 1
            self.latencies[operation].append(latency_ms)
            if ok:
                return
            self.failure_count += 1
            if len(self.failures) < self.sample_limit:
                self.failures.append(
                    {
                        "worker": worker,
                        "operation": operation,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                        "error": error[:500],
                    }
                )

    def failure_total(self) -> int:
        with self.lock:
            return self.failure_count

    def latency_report(self) -> dict[str, Any]:
        with self.lock:
            by_operation = {
                operation: summarize_latencies(values)
                for operation, values in sorted(self.latencies.items())
            }
            all_values = [value for values in self.latencies.values() for value in values]
        return {"overall": summarize_latencies(all_values), "by_operation": by_operation}


def summarize_latencies(values: list[int]) -> dict[str, int]:
    return {
        "count": len(values),
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def operational_counts() -> dict[str, int]:
    models = {
        "alarms": AlarmEvent,
        "feedback": Feedback,
        "issues": Issue,
        "work_orders": WorkOrder,
        "audits": AuditEvent,
        "sessions": LoginSession,
        "rag_answers": RagAnswer,
    }
    with session_scope() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }


def pilot_residue_counts() -> dict[str, int]:
    with session_scope() as session:
        return {
            "alarms": int(session.scalar(
                select(func.count()).select_from(AlarmEvent).where(AlarmEvent.source.like("pilot-load-%"))
            ) or 0),
            "issues": int(session.scalar(
                select(func.count()).select_from(Issue).where(Issue.source.like("pilot-load-%"))
            ) or 0),
            "work_orders": int(session.scalar(
                select(func.count()).select_from(WorkOrder).where(WorkOrder.source.like("pilot-load-%"))
            ) or 0),
            "feedback": int(session.scalar(
                select(func.count()).select_from(Feedback).where(Feedback.query.like("Pilot load worker %"))
            ) or 0),
            "rag_answers": int(session.scalar(
                select(func.count()).select_from(RagAnswer).where(RagAnswer.query.like("Pilot load worker %"))
            ) or 0),
        }


def orphan_audit_count() -> int:
    with session_scope() as session:
        return workflow_orphan_audit_count(session)


def orphan_answer_link_count() -> int:
    with session_scope() as session:
        answer_ids = set(session.scalars(select(RagAnswer.answer_id)).all())
        references = (
            list(session.scalars(select(Issue.rag_answer_id)).all())
            + list(session.scalars(select(WorkOrder.rag_answer_id)).all())
            + list(session.scalars(select(Feedback.answer_id)).all())
        )
        return sum(1 for value in references if value and value not in answer_ids)


def cleanup_iteration(marker: str, issue_no: str = "") -> None:
    with transaction_scope():
        with session_scope() as session:
            issue = None
            if issue_no:
                issue = session.scalar(select(Issue).where(Issue.issue_no == issue_no))
            if issue is None:
                issue = session.scalar(select(Issue).where(Issue.source == marker))
            if issue is not None:
                cleanup_workflow_records(session, [issue.id])
            else:
                order_ids = list(session.scalars(select(WorkOrder.id).where(WorkOrder.source == marker)).all())
                if order_ids:
                    session.execute(delete(Feedback).where(Feedback.work_order_id.in_(order_ids)))
                    session.execute(
                        delete(AuditEvent).where(
                            AuditEvent.entity_type == "work_order",
                            AuditEvent.entity_id.in_(order_ids),
                        )
                    )
                    session.execute(delete(WorkOrder).where(WorkOrder.id.in_(order_ids)))
            session.execute(delete(AlarmEvent).where(AlarmEvent.source == marker))
            session.execute(delete(Feedback).where(Feedback.answer_id == marker))


def cleanup_session(token: str) -> None:
    if not token:
        return
    with transaction_scope():
        with session_scope() as session:
            session.execute(delete(LoginSession).where(LoginSession.token_hash == token_digest(token)))


def cleanup_answer(answer_id: str) -> None:
    if not answer_id:
        return
    with transaction_scope():
        with session_scope() as session:
            session.execute(delete(RagAnswer).where(RagAnswer.answer_id == answer_id))


def prepare_worker_answers(base_url: str, workers: int, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    code, login = request_json(
        base_url,
        "/auth/login",
        "POST",
        {"username": "admin01", "password": admin_initial_password()},
        timeout=timeout,
    )
    token = str(login.get("token") or "")
    if code != 200 or not token:
        raise RuntimeError("Pilot load answer setup login failed")

    started = time.monotonic()

    def prepare(worker: int) -> dict[str, Any]:
        query = f"Pilot load worker {worker}: Alarm 3000 maintenance hint"
        item_started = time.monotonic()
        answer_code, answer = request_json(
            base_url,
            "/v1/808d/chat/completions",
            "POST",
            {
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "temperature": 0.1,
                "max_tokens": 120,
            },
            token,
            timeout,
        )
        answer_id = str(answer.get("id") or "")
        snapshot_code, snapshot = request_json(
            base_url,
            f"/rag/answers/{answer_id}",
            token=token,
            timeout=timeout,
        ) if answer_id else (0, {})
        record = snapshot.get("answer", {}) if isinstance(snapshot, dict) else {}
        ok = (
            answer_code == 200
            and snapshot_code == 200
            and record.get("answer_id") == answer_id
            and record.get("answer_state") in {"complete", "fallback", "unavailable"}
        )
        return {
            "worker": worker,
            "answer_id": answer_id,
            "query": query,
            "ok": ok,
            "elapsed_ms": int((time.monotonic() - item_started) * 1000),
        }

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            contexts = list(executor.map(prepare, range(workers)))
    finally:
        cleanup_session(token)
    if not all(item["ok"] for item in contexts):
        for item in contexts:
            cleanup_answer(str(item.get("answer_id") or ""))
        raise RuntimeError("Pilot load failed to create and read one real answer snapshot per worker")
    return contexts, {
        "status": "ok",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "workers": [{"worker": item["worker"], "elapsed_ms": item["elapsed_ms"]} for item in contexts],
    }


def call_api(
    limiter: RateLimiter,
    metrics: Metrics,
    deadline: float,
    worker: int,
    base_url: str,
    operation: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str = "",
    timeout: int = 60,
    predicate: Callable[[int, dict[str, Any]], bool] | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    if not limiter.acquire(deadline):
        return False, 0, {}
    started = time.monotonic()
    try:
        code, data = request_json(base_url, path, method, payload, token, timeout)
        ok = predicate(code, data) if predicate is not None else 200 <= code < 300
        metrics.record(operation, int((time.monotonic() - started) * 1000), ok, code, worker=worker)
        return True, code, data
    except Exception as exc:
        metrics.record(
            operation,
            int((time.monotonic() - started) * 1000),
            False,
            error=f"{type(exc).__name__}: {exc}",
            worker=worker,
        )
        return True, 0, {}


def worker_loop(
    worker: int,
    base_url: str,
    deadline: float,
    limiter: RateLimiter,
    metrics: Metrics,
    stop: threading.Event,
    max_failures: int,
    timeout: int,
    answer_id: str,
    answer_query: str,
) -> int:
    token = ""
    iterations = 0
    try:
        attempted, code, login = call_api(
            limiter,
            metrics,
            deadline,
            worker,
            base_url,
            "login",
            "/auth/login",
            "POST",
            {"username": "admin01", "password": admin_initial_password()},
            timeout=timeout,
            predicate=lambda status, data: status == 200 and bool(data.get("token")),
        )
        if not attempted:
            return 0
        token = str(login.get("token") or "")
        if code != 200 or not token:
            stop.set()
            return 0

        while not stop.is_set() and time.monotonic() < deadline:
            marker = f"pilot-load-{worker}-{uuid.uuid4().hex[:12]}"
            issue_no = ""
            try:
                attempted, _, _ = call_api(
                    limiter,
                    metrics,
                    deadline,
                    worker,
                    base_url,
                    "documents",
                    "/v1/808d/documents",
                    token=token,
                    timeout=timeout,
                    predicate=lambda status, data: status == 200 and bool(data.get("documents")),
                )
                if not attempted:
                    break
                attempted, _, _ = call_api(
                    limiter,
                    metrics,
                    deadline,
                    worker,
                    base_url,
                    "settings",
                    "/system-settings",
                    token=token,
                    timeout=timeout,
                    predicate=lambda status, data: status == 200 and data.get("status") == "ok",
                )
                if not attempted:
                    break
                attempted, code, alarm = call_api(
                    limiter,
                    metrics,
                    deadline,
                    worker,
                    base_url,
                    "trigger_alarm",
                    "/trigger-alarm",
                    "POST",
                    {
                        "alarm_code": "3000",
                        "manual": "phase4smoke",
                        "machine_id": f"PILOT-LOAD-{worker}",
                        "source": marker,
                        "severity": "low",
                        "description": f"Pilot load iteration {marker}",
                        "rag_answer_id": answer_id,
                    },
                    token,
                    timeout,
                    lambda status, data: (
                        status == 200
                        and data.get("status") == "ok"
                        and bool((data.get("issue") or {}).get("issue_id"))
                        and bool((data.get("work_order") or {}).get("id"))
                    ),
                )
                if not attempted:
                    break
                issue_no = str((alarm.get("issue") or {}).get("issue_id") or "")
                order_no = str((alarm.get("work_order") or {}).get("id") or "")
                if code == 200 and issue_no and order_no:
                    attempted, _, _ = call_api(
                        limiter,
                        metrics,
                        deadline,
                        worker,
                        base_url,
                        "feedback",
                        "/feedback",
                        "POST",
                        {
                            "query": answer_query,
                            "collection": "808d",
                            "alarm_code": "3000",
                            "feedback": "good",
                            "answer_id": answer_id,
                            "issue_id": issue_no,
                            "work_order_id": order_no,
                            "correctness": "correct",
                            "coverage": "complete",
                        },
                        token,
                        timeout,
                        lambda status, data: status == 200 and data.get("status") == "ok",
                    )
                    if not attempted:
                        break
                attempted, _, _ = call_api(
                    limiter,
                    metrics,
                    deadline,
                    worker,
                    base_url,
                    "alarm_stats",
                    "/stats/alarms",
                    token=token,
                    timeout=timeout,
                    predicate=lambda status, _data: status == 200,
                )
                if not attempted:
                    break
                attempted, _, _ = call_api(
                    limiter,
                    metrics,
                    deadline,
                    worker,
                    base_url,
                    "feedback_stats",
                    "/feedback/stats",
                    token=token,
                    timeout=timeout,
                    predicate=lambda status, _data: status == 200,
                )
                if not attempted:
                    break
                iterations += 1
            finally:
                cleanup_iteration(marker, issue_no)
            if metrics.failure_total() > max_failures:
                stop.set()
    finally:
        cleanup_session(token)
    return iterations


def run_load(
    base_url: str,
    source: Path,
    duration_seconds: int,
    workers: int,
    expected_peak_rps: float,
    load_multiplier: float,
    max_failures: int,
    timeout: int,
    environment: str,
) -> dict[str, Any]:
    if require_known_data_store() not in {"postgres", "postgresql"}:
        raise RuntimeError("DATA_STORE must be postgresql")
    if duration_seconds < 1:
        raise ValueError("duration_seconds must be positive")
    if workers < 2 or workers > 32:
        raise ValueError("workers must be between 2 and 32")
    if expected_peak_rps <= 0 or load_multiplier < 1:
        raise ValueError("expected_peak_rps must be positive and load_multiplier must be at least 1")

    target_rps = expected_peak_rps * load_multiplier
    before_counts = operational_counts()
    before_residue = pilot_residue_counts()
    before_settings = database_settings()
    before_fingerprints = legacy_fingerprints(source)
    before_orphans = orphan_audit_count()
    before_answer_orphans = orphan_answer_link_count()
    started_at = datetime.now(timezone.utc)
    answer_contexts, answer_setup = prepare_worker_answers(base_url, workers, timeout)
    workload_started = time.monotonic()
    deadline = workload_started + duration_seconds
    limiter = RateLimiter(target_rps)
    metrics = Metrics()
    stop = threading.Event()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                worker_loop,
                index,
                base_url,
                deadline,
                limiter,
                metrics,
                stop,
                max_failures,
                timeout,
                answer_contexts[index]["answer_id"],
                answer_contexts[index]["query"],
            )
            for index in range(workers)
        ]
        iterations_by_worker = [future.result() for future in futures]

    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    workload_elapsed = time.monotonic() - workload_started
    for context in answer_contexts:
        cleanup_answer(context["answer_id"])
    concurrency = run_concurrency_check(max(4, workers))
    after_counts = operational_counts()
    after_residue = pilot_residue_counts()
    after_settings = database_settings()
    after_orphans = orphan_audit_count()
    after_answer_orphans = orphan_answer_link_count()
    fingerprints = compare_fingerprints(before_fingerprints, legacy_fingerprints(source))
    achieved_rps = metrics.request_count / workload_elapsed if workload_elapsed > 0 else 0.0
    checks = {
        "duration_reached": workload_elapsed >= duration_seconds * 0.99,
        "failure_budget": metrics.failure_count <= max_failures,
        "target_rate_reached": achieved_rps >= target_rps * 0.90,
        "worker_iterations": sum(iterations_by_worker) > 0,
        "database_counts_restored": after_residue == before_residue,
        "settings_unchanged": after_settings == before_settings,
        "legacy_source_unchanged": fingerprints["unchanged"],
        "orphan_audits_unchanged": after_orphans == before_orphans,
        "answer_links_valid": before_answer_orphans == 0 and after_answer_orphans == 0,
        "concurrency": concurrency["status"] == "ok",
    }
    completed_at = datetime.now(timezone.utc)
    return {
        "status": "ok" if all(checks.values()) else "fail",
        "environment": environment,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": duration_seconds,
        "elapsed_seconds": round(workload_elapsed, 3),
        "workers": workers,
        "expected_peak_rps": expected_peak_rps,
        "load_multiplier": load_multiplier,
        "target_rps": round(target_rps, 3),
        "achieved_rps": round(achieved_rps, 3),
        "request_count": metrics.request_count,
        "failure_count": metrics.failure_count,
        "failures": metrics.failures,
        "iterations": sum(iterations_by_worker),
        "iterations_by_worker": iterations_by_worker,
        "answer_setup": answer_setup,
        "checks": checks,
        "latency_ms": metrics.latency_report(),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "before_pilot_residue": before_residue,
        "after_pilot_residue": after_residue,
        "before_orphan_audits": before_orphans,
        "after_orphan_audits": after_orphans,
        "before_orphan_answer_links": before_answer_orphans,
        "after_orphan_answer_links": after_answer_orphans,
        "fingerprint_comparison": fingerprints,
        "concurrency": concurrency,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent PostgreSQL Pilot load and soak harness")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--source", default=str(ROOT / "alarm_db"))
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--expected-peak-rps", type=float, default=1.0)
    parser.add_argument("--load-multiplier", type=float, default=2.0)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--environment", choices=("local", "pilot", "production"), default="local")
    parser.add_argument(
        "--postgres-container",
        default="",
        help="load PostgreSQL connection values from a local container without printing its password",
    )
    parser.add_argument("--postgres-host", default="127.0.0.1")
    parser.add_argument("--postgres-port", type=int, default=5432)
    parser.add_argument("--report", default=str(ROOT / "exports" / "postgresql_pilot_load_local.json"))
    args = parser.parse_args()
    if args.postgres_container:
        load_postgres_environment_from_container(
            args.postgres_container,
            args.postgres_host,
            args.postgres_port,
        )
    report = run_load(
        args.base_url,
        Path(args.source),
        args.duration_seconds,
        args.workers,
        args.expected_peak_rps,
        args.load_multiplier,
        args.max_failures,
        args.timeout,
        args.environment,
    )
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
