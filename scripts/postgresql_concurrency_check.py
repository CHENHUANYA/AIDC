from __future__ import annotations

import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, func, select

from db.models import AuditEvent, Issue, WorkOrder
from db.session import session_scope, transaction_scope
from repositories.runtime import require_known_data_store
from services.postgres_workflow import create_issue, escalate_issue


PREFIX = "phase5-concurrency-"


def cleanup(marker: str) -> None:
    with transaction_scope():
        with session_scope() as session:
            issues = session.scalars(select(Issue).where(Issue.description == marker)).all()
            issue_ids = [issue.id for issue in issues]
            orders = session.scalars(select(WorkOrder).where(WorkOrder.issue_id.in_(issue_ids or [None]))).all()
            entity_ids = [item.id for item in issues] + [item.id for item in orders]
            if entity_ids:
                session.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(entity_ids)))
            for order in orders:
                session.delete(order)
            for issue in issues:
                session.delete(issue)


def run_check(workers: int = 4) -> dict:
    if require_known_data_store() not in {"postgres", "postgresql"}:
        raise RuntimeError("DATA_STORE must be postgresql")
    marker = f"{PREFIX}{uuid.uuid4().hex}"
    issue_no = ""
    try:
        issue, _ = create_issue(
            machine_id="PHASE5-CONCURRENCY",
            description=marker,
            source="acceptance",
            alarm_code="3000",
            severity="medium",
            created_by="phase5-acceptance",
        )
        issue_no = issue["issue_id"]

        def escalate(_index: int) -> dict:
            escalated_issue, order, created = escalate_issue(issue_no, "phase5-acceptance")
            return {
                "issue_id": escalated_issue["issue_id"],
                "order_id": order["id"],
                "created": created,
            }

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(escalate, range(workers)))

        with session_scope() as session:
            issue_pk = session.scalar(select(Issue.id).where(Issue.issue_no == issue_no))
            order_count = int(session.scalar(
                select(func.count()).select_from(WorkOrder).where(WorkOrder.issue_id == issue_pk)
            ) or 0)
            audit_count = int(session.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.entity_type == "work_order",
                    AuditEvent.entity_id.in_(select(WorkOrder.id).where(WorkOrder.issue_id == issue_pk)),
                    AuditEvent.action == "created",
                )
            ) or 0)

        order_ids = {result["order_id"] for result in results}
        checks = {
            "one_creator": sum(bool(result["created"]) for result in results) == 1,
            "one_order_id": len(order_ids) == 1,
            "one_database_order": order_count == 1,
            "one_creation_audit": audit_count == 1,
            "all_callers_resolved": len(results) == workers and all(result["issue_id"] == issue_no for result in results),
        }
        return {
            "status": "ok" if all(checks.values()) else "fail",
            "workers": workers,
            "checks": checks,
            "results": results,
            "database_order_count": order_count,
            "creation_audit_count": audit_count,
        }
    finally:
        if marker:
            cleanup(marker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent PostgreSQL Issue escalation check")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    if args.workers < 2 or args.workers > 32:
        parser.error("--workers must be between 2 and 32")
    report = run_check(args.workers)
    if args.report:
        from pathlib import Path

        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
