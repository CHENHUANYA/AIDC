from __future__ import annotations

from typing import Iterable

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from db.models import AuditEvent, Feedback, Issue, WorkOrder


def cleanup_workflow_records(session: Session, issue_ids: Iterable) -> dict[str, int]:
    issue_ids = list(issue_ids)
    if not issue_ids:
        return {"feedback": 0, "audits": 0, "work_orders": 0, "issues": 0}

    order_ids = list(session.scalars(select(WorkOrder.id).where(WorkOrder.issue_id.in_(issue_ids))).all())
    feedback_filter = Feedback.issue_id.in_(issue_ids)
    if order_ids:
        feedback_filter = or_(feedback_filter, Feedback.work_order_id.in_(order_ids))
    feedback_result = session.execute(delete(Feedback).where(feedback_filter))

    audit_filters = [and_(AuditEvent.entity_type == "issue", AuditEvent.entity_id.in_(issue_ids))]
    if order_ids:
        audit_filters.append(and_(AuditEvent.entity_type == "work_order", AuditEvent.entity_id.in_(order_ids)))
    audit_result = session.execute(delete(AuditEvent).where(or_(*audit_filters)))

    order_result = session.execute(delete(WorkOrder).where(WorkOrder.id.in_(order_ids))) if order_ids else None
    issue_result = session.execute(delete(Issue).where(Issue.id.in_(issue_ids)))
    return {
        "feedback": int(feedback_result.rowcount or 0),
        "audits": int(audit_result.rowcount or 0),
        "work_orders": int(order_result.rowcount or 0) if order_result is not None else 0,
        "issues": int(issue_result.rowcount or 0),
    }


def workflow_orphan_audit_count(session: Session) -> int:
    issue_exists = select(Issue.id).where(Issue.id == AuditEvent.entity_id).exists()
    order_exists = select(WorkOrder.id).where(WorkOrder.id == AuditEvent.entity_id).exists()
    return int(session.scalar(
        select(func.count()).select_from(AuditEvent).where(or_(
            and_(AuditEvent.entity_type == "issue", ~issue_exists),
            and_(AuditEvent.entity_type == "work_order", ~order_exists),
        ))
    ) or 0)
