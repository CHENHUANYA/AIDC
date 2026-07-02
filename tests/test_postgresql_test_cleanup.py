import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from db.base import Base
from db.models import AuditEvent, Feedback, Issue, WorkOrder
from scripts.postgresql_test_cleanup import cleanup_workflow_records, workflow_orphan_audit_count


def count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_cleanup_removes_workflow_dependents_without_touching_unrelated_audit():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        issue = Issue(
            issue_no="ISS-CLEANUP",
            machine_id="M",
            description="cleanup",
            created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        session.add(issue)
        session.flush()
        order = WorkOrder(work_order_no="WO-CLEANUP", issue_id=issue.id, alarm_code="3000")
        session.add(order)
        session.flush()
        session.add_all([
            AuditEvent(entity_type="issue", entity_id=issue.id, action="created"),
            AuditEvent(entity_type="work_order", entity_id=order.id, action="created"),
            AuditEvent(entity_type="other", entity_id=issue.id, action="keep"),
            Feedback(issue_id=issue.id, work_order_id=order.id),
        ])
        session.commit()

        removed = cleanup_workflow_records(session, [issue.id])
        session.commit()

        assert removed == {"feedback": 1, "audits": 2, "work_orders": 1, "issues": 1}
        assert count(session, Feedback) == 0
        assert count(session, WorkOrder) == 0
        assert count(session, Issue) == 0
        assert count(session, AuditEvent) == 1
        remaining = session.scalar(select(AuditEvent))
        assert remaining.entity_type == "other"


def test_orphan_count_only_includes_issue_and_work_order_entities():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        missing = uuid.uuid4()
        session.add_all([
            AuditEvent(entity_type="issue", entity_id=missing, action="orphan"),
            AuditEvent(entity_type="other", entity_id=missing, action="ignored"),
        ])
        session.commit()

        assert workflow_orphan_audit_count(session) == 1
