from __future__ import annotations

import json
import secrets
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from db.models import AuditEvent, Issue, LoginSession, User, WorkOrder
from db.session import session_scope
from repositories.postgres_auth import PostgresSessionRepository, PostgresUserRepository, token_digest
from repositories.postgres_workflow import PostgresIssueRepository, PostgresWorkOrderRepository
from repositories.runtime import postgres_store_enabled
from services.postgres_workflow import create_issue, escalate_issue


TEST_PREFIX = "phase2-check-"


def cleanup() -> None:
    with session_scope() as session:
        issue_ids = list(session.scalars(select(Issue.id).where(Issue.description.like(f"{TEST_PREFIX}%"))).all())
        order_ids = list(session.scalars(select(WorkOrder.id).where(WorkOrder.issue_id.in_(issue_ids or [uuid.uuid4()]))).all())
        entity_ids = [*issue_ids, *order_ids]
        if entity_ids:
            session.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(entity_ids)))
        if order_ids:
            session.execute(delete(WorkOrder).where(WorkOrder.id.in_(order_ids)))
        if issue_ids:
            session.execute(delete(Issue).where(Issue.id.in_(issue_ids)))
        user_ids = list(session.scalars(select(User.id).where(User.user_id.like(f"{TEST_PREFIX}%"))).all())
        if user_ids:
            session.execute(delete(LoginSession).where(LoginSession.user_id.in_(user_ids)))
            session.execute(delete(User).where(User.id.in_(user_ids)))


def run_checks() -> dict:
    if not postgres_store_enabled():
        raise RuntimeError("DATA_STORE must be postgresql for the Phase 2 check")

    cleanup()
    suffix = uuid.uuid4().hex[:8]
    user_id = f"{TEST_PREFIX}{suffix}"
    raw_token = secrets.token_urlsafe(32)
    marker = f"{TEST_PREFIX}{suffix}-issue"
    rollback_marker = f"{TEST_PREFIX}{suffix}-rollback"

    users = PostgresUserRepository()
    sessions = PostgresSessionRepository()
    issue_repo = PostgresIssueRepository()
    order_repo = PostgresWorkOrderRepository()
    results: dict[str, object] = {}

    try:
        users.save_all({
            user_id: {
                "user_id": user_id,
                "name": "Phase 2 Check",
                "role": "admin",
                "line_scope": ["*"],
                "team": "test",
                "active": True,
                "password_hash": "test-only-hash",
            }
        })
        results["user_roundtrip"] = user_id in users.load_all()

        sessions.create(raw_token, user_id, "2026-06-30T14:00:00+00:00", "2099-06-30T14:00:00+00:00")
        session_payload = sessions.get(raw_token)
        with session_scope() as db:
            stored_hash = db.scalar(select(LoginSession.token_hash).where(LoginSession.token_hash == token_digest(raw_token)))
        results["session_lookup"] = bool(session_payload and session_payload["user_id"] == user_id)
        results["token_hashed"] = stored_hash == token_digest(raw_token) and stored_hash != raw_token

        issue, work_order = create_issue(
            machine_id="PHASE2-MACHINE",
            description=marker,
            severity="high",
            created_by=user_id,
            create_work_order=False,
        )
        first_issue, first_order, first_created = escalate_issue(issue["issue_id"], user_id)
        second_issue, second_order, second_created = escalate_issue(issue["issue_id"], user_id)
        results["atomic_escalation"] = bool(first_created and first_order["id"] == second_order["id"])
        results["idempotent_escalation"] = second_created is False
        results["bidirectional_link"] = first_issue["work_order_id"] == first_order["id"] and first_order["issue_id"] == first_issue["issue_id"]
        results["issue_repository"] = any(item["issue_id"] == issue["issue_id"] for item in issue_repo.load_all())
        results["work_order_repository"] = any(item["id"] == first_order["id"] for item in order_repo.load_all())

        rollback_ok = False
        try:
            create_issue(
                machine_id="PHASE2-MACHINE",
                description=rollback_marker,
                severity="not-valid",
                created_by=user_id,
            )
        except IntegrityError:
            with session_scope() as db:
                rollback_ok = db.scalar(select(func.count()).select_from(Issue).where(Issue.description == rollback_marker)) == 0
        results["constraint_rollback"] = rollback_ok
        results["status"] = "ok" if all(value is True for value in results.values()) else "fail"
        return results
    finally:
        cleanup()


def main() -> int:
    report = run_checks()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
