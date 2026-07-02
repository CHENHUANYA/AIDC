from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select

from db.models import Issue
from db.session import session_scope, transaction_scope
from scripts.postgresql_test_cleanup import cleanup_workflow_records, workflow_orphan_audit_count
from services.postgres_workflow import create_issue


PREFIX = "phase2-uow-"


def cleanup() -> dict[str, int]:
    with session_scope() as session:
        issue_ids = session.scalars(select(Issue.id).where(Issue.description.like(f"{PREFIX}%"))).all()
        return cleanup_workflow_records(session, issue_ids)


def run_checks() -> dict:
    cleanup()
    with session_scope() as session:
        baseline_orphans = workflow_orphan_audit_count(session)
    rollback_marker = f"{PREFIX}{uuid.uuid4().hex}-rollback"
    commit_marker = f"{PREFIX}{uuid.uuid4().hex}-commit"
    results: dict[str, object] = {}

    try:
        try:
            with transaction_scope() as outer:
                issue, _ = create_issue(
                    machine_id="PHASE2-UOW",
                    description=rollback_marker,
                    created_by="automation",
                )
                with session_scope() as inner:
                    results["shared_session"] = inner is outer
                    assert issue["issue_id"]
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

        with session_scope() as session:
            rollback_count = session.scalar(
                select(func.count()).select_from(Issue).where(Issue.description == rollback_marker)
            )
        results["cross_repository_rollback"] = rollback_count == 0

        with transaction_scope():
            create_issue(
                machine_id="PHASE2-UOW",
                description=commit_marker,
                created_by="automation",
            )
        with session_scope() as session:
            commit_count = session.scalar(
                select(func.count()).select_from(Issue).where(Issue.description == commit_marker)
            )
        results["outer_commit"] = commit_count == 1
    finally:
        cleanup()

    with session_scope() as session:
        remaining = int(session.scalar(
            select(func.count()).select_from(Issue).where(Issue.description.like(f"{PREFIX}%"))
        ) or 0)
        final_orphans = workflow_orphan_audit_count(session)
    results["cleanup_removed_test_issues"] = remaining == 0
    results["cleanup_no_audit_drift"] = final_orphans == baseline_orphans
    results["status"] = "ok" if all(value is True for value in results.values()) else "fail"
    return results


def main() -> int:
    report = run_checks()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
