from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models import AuditEvent, Issue, User, WorkOrder
from db.session import session_scope
from repositories.postgres_workflow import actor_type, issue_dict, order_dict, user_maps


def priority_from_severity(severity: str) -> str:
    return severity if severity in {"low", "high", "critical"} else "medium"


def add_audit(session, entity_type: str, entity_id, action: str, actor_ref: str, fields: list[str], to_status: str) -> None:
    actor_pk = session.scalar(select(User.id).where(User.user_id == actor_ref)) if actor_ref else None
    session.add(AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor_pk,
        actor_ref=actor_ref,
        actor_type=actor_type(actor_ref, actor_pk is not None),
        from_status="",
        to_status=to_status,
        changed_fields=fields,
        changes=[],
        request_id=f"create:{entity_type}:{entity_id}",
        created_at=datetime.now(timezone.utc),
    ))


def create_issue(
    *,
    machine_id: str,
    description: str,
    source: str = "operator",
    manual: str = "808d",
    line_id: str = "",
    alarm_code: str = "",
    severity: str = "medium",
    created_by: str = "",
    assigned_to: str = "",
    rag_suggestion: str = "",
    alarm_event_id=None,
    create_work_order: bool = False,
    priority: str | None = None,
) -> tuple[dict, dict | None]:
    with session_scope() as session:
        users, users_by_pk = user_maps(session)
        now = datetime.now(timezone.utc)
        issue = Issue(
            issue_no=f"ISS-{now.strftime('%Y%m%d')}-{str(uuid.uuid4())[:6]}",
            alarm_event_id=alarm_event_id,
            source=source or "operator",
            manual=manual or "808d",
            machine_id=machine_id,
            line_id=line_id,
            alarm_code=alarm_code,
            description=description,
            original_description=description,
            severity=severity,
            status="assigned" if assigned_to else "open",
            assigned_to_ref=assigned_to,
            assigned_to_user_id=users.get(assigned_to),
            created_by_ref=created_by,
            created_by_user_id=users.get(created_by),
            updated_by_ref=created_by,
            updated_by_user_id=users.get(created_by),
            rag_suggestion=rag_suggestion,
            created_at=now,
            updated_at=now,
        )
        session.add(issue)
        session.flush()
        add_audit(session, "issue", issue.id, "created", created_by, [], issue.status)

        order = None
        if create_work_order:
            order = _new_order(
                session,
                issue,
                alarm_code=alarm_code or "SYMPTOM",
                manual=manual,
                machine_id=machine_id,
                priority=priority or priority_from_severity(severity),
                description=description,
                rag_suggestion=rag_suggestion,
                source=source,
                assigned_to=assigned_to,
                created_by=created_by,
            )
        session.flush()
        return (
            issue_dict(session, issue, users_by_pk, order.work_order_no if order else ""),
            order_dict(session, order, users_by_pk, issue.issue_no) if order else None,
        )


def _new_order(session, issue: Issue, *, alarm_code: str, manual: str, machine_id: str, priority: str, description: str, rag_suggestion: str, source: str, assigned_to: str, created_by: str) -> WorkOrder:
    users, _ = user_maps(session)
    now = datetime.now(timezone.utc)
    status = "assigned" if assigned_to else "pending"
    order = WorkOrder(
        work_order_no=str(uuid.uuid4())[:8],
        issue_id=issue.id,
        alarm_code=alarm_code,
        manual=manual or "808d",
        machine_id=machine_id,
        status=status,
        priority=priority,
        assigned_to_ref=assigned_to,
        assigned_to_user_id=users.get(assigned_to),
        created_by_ref=created_by,
        created_by_user_id=users.get(created_by),
        updated_by_ref=created_by,
        updated_by_user_id=users.get(created_by),
        description=description,
        rag_suggestion=rag_suggestion,
        source=source or "operator",
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    session.flush()
    add_audit(session, "work_order", order.id, "created", created_by, ["assigned_to"] if assigned_to else [], status)
    issue.status = "open" if status == "pending" else status
    issue.updated_by_ref = created_by
    issue.updated_at = now
    return order


def escalate_issue(issue_no: str, actor_ref: str) -> tuple[dict, dict, bool]:
    try:
        with session_scope() as session:
            _, users_by_pk = user_maps(session)
            issue = session.scalar(
                select(Issue).where(Issue.issue_no == issue_no).with_for_update()
            )
            if issue is None:
                raise LookupError(f"Issue {issue_no} not found")
            existing = session.scalar(select(WorkOrder).where(WorkOrder.issue_id == issue.id))
            if existing is not None:
                return issue_dict(session, issue, users_by_pk, existing.work_order_no), order_dict(session, existing, users_by_pk, issue_no), False
            order = _new_order(
                session,
                issue,
                alarm_code=issue.alarm_code or "SYMPTOM",
                manual=issue.manual,
                machine_id=issue.machine_id,
                priority=priority_from_severity(issue.severity),
                description=issue.description,
                rag_suggestion=issue.rag_suggestion,
                source=issue.source,
                assigned_to=issue.assigned_to_ref,
                created_by=actor_ref,
            )
            session.flush()
            return issue_dict(session, issue, users_by_pk, order.work_order_no), order_dict(session, order, users_by_pk, issue_no), True
    except IntegrityError as exc:
        raise ConcurrentUpdateError(f"Issue {issue_no} was escalated concurrently") from exc


class ConcurrentUpdateError(RuntimeError):
    pass
