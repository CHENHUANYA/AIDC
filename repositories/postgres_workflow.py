from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, List

from sqlalchemy import and_, false, func, or_, select

from db.models import AuditEvent, Issue, IssueNote, User, WorkOrder
from db.session import session_scope


class ConcurrentUpdateError(RuntimeError):
    pass


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def actor_type(actor_ref: str, has_user: bool) -> str:
    if has_user:
        return "user"
    lowered = actor_ref.lower()
    if any(token in lowered for token in ("n8n", "smoke", "bot", "acceptance")):
        return "automation"
    return "legacy"


def audit_key(entity_type: str, business_key: str, entry: dict) -> str:
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "legacy:" + hashlib.sha256(f"{entity_type}:{business_key}:{canonical}".encode("utf-8")).hexdigest()[:48]


def user_maps(session) -> tuple[dict[str, Any], dict[Any, str]]:
    pairs = session.execute(select(User.id, User.user_id)).all()
    return ({user_id: user_pk for user_pk, user_id in pairs}, {user_pk: user_id for user_pk, user_id in pairs})


def audits_for(session, entity_type: str, entity_id) -> list[dict]:
    records = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    return [
        {
            "action": record.action,
            "user_id": record.actor_ref,
            "fields": list(record.changed_fields or []),
            "from_status": record.from_status,
            "to_status": record.to_status,
            "changes": list(record.changes or []),
            "created_at": iso(record.created_at),
            "_request_id": record.request_id,
        }
        for record in records
    ]


def issue_dict(session, issue: Issue, users_by_pk: dict, work_order_no: str = "") -> dict:
    notes = session.scalars(
        select(IssueNote).where(IssueNote.issue_id == issue.id).order_by(IssueNote.created_at, IssueNote.id)
    ).all()
    return {
        "issue_id": issue.issue_no,
        "source": issue.source,
        "manual": issue.manual,
        "machine_id": issue.machine_id,
        "line_id": issue.line_id,
        "alarm_code": issue.alarm_code,
        "description": issue.description,
        "original_description": issue.original_description,
        "severity": issue.severity,
        "status": issue.status,
        "created_by": issue.created_by_ref or users_by_pk.get(issue.created_by_user_id, ""),
        "updated_by": issue.updated_by_ref or users_by_pk.get(issue.updated_by_user_id, ""),
        "assigned_to": issue.assigned_to_ref or users_by_pk.get(issue.assigned_to_user_id, ""),
        "work_order_id": work_order_no,
        "rag_suggestion": issue.rag_suggestion,
        "rag_answer_id": issue.rag_answer_id,
        "operator_notes": [
            {"note": note.note, "created_by": note.created_by_ref, "created_at": iso(note.created_at)}
            for note in notes
        ],
        "issue_history": audits_for(session, "issue", issue.id),
        "resolution_summary": issue.resolution_summary,
        "created_at": iso(issue.created_at),
        "updated_at": iso(issue.updated_at),
        "completed_at": iso(issue.completed_at),
        "version": issue.version,
    }


def order_dict(session, order: WorkOrder, users_by_pk: dict, issue_no: str = "") -> dict:
    return {
        "id": order.work_order_no,
        "issue_id": issue_no,
        "alarm_code": order.alarm_code,
        "manual": order.manual,
        "machine_id": order.machine_id,
        "status": order.status,
        "priority": order.priority,
        "assigned_to": order.assigned_to_ref or users_by_pk.get(order.assigned_to_user_id, ""),
        "created_by": order.created_by_ref or users_by_pk.get(order.created_by_user_id, ""),
        "updated_by": order.updated_by_ref or users_by_pk.get(order.updated_by_user_id, ""),
        "accepted_by": order.accepted_by_ref,
        "completed_by": order.completed_by_ref,
        "verified_by": order.verified_by_ref,
        "description": order.description,
        "resolution": order.resolution,
        "notes": order.notes,
        "root_cause": order.root_cause,
        "repair_action": order.repair_action,
        "failure_category": order.failure_category,
        "llm_correctness": order.llm_correctness,
        "llm_coverage": order.llm_coverage,
        "llm_missing_info": order.llm_missing_info,
        "llm_expected_fix": order.llm_expected_fix,
        "llm_answer_used": order.llm_answer_used,
        "kb_candidate": order.kb_candidate,
        "kb_review_status": order.kb_review_status,
        "kb_review_note": order.kb_review_note,
        "kb_reviewed_by": order.kb_reviewed_by_ref,
        "kb_reviewed_at": iso(order.kb_reviewed_at),
        "kb_ingested_at": iso(order.kb_ingested_at),
        "kb_ingest_result": order.kb_ingest_result,
        "kb_duplicate_of": order.kb_duplicate_of,
        "rag_suggestion": order.rag_suggestion,
        "rag_answer_id": order.rag_answer_id,
        "source": order.source,
        "created_at": iso(order.created_at),
        "updated_at": iso(order.updated_at),
        "completed_at": iso(order.completed_at),
        "deleted_at": iso(order.deleted_at),
        "work_order_history": audits_for(session, "work_order", order.id),
        "version": order.version,
    }


def add_missing_audits(session, entity_type: str, entity_id, business_key: str, entries: Iterable[dict], users: dict[str, Any]) -> None:
    existing = set(session.scalars(
        select(AuditEvent.request_id).where(
            AuditEvent.entity_type == entity_type,
            AuditEvent.entity_id == entity_id,
            AuditEvent.request_id.like("legacy:%"),
        )
    ).all())
    for entry in entries:
        request_id = str(entry.get("_request_id") or audit_key(entity_type, business_key, entry))
        if request_id in existing:
            continue
        actor_ref = str(entry.get("user_id") or "")
        actor_pk = users.get(actor_ref)
        session.add(AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=str(entry.get("action") or "updated"),
            actor_user_id=actor_pk,
            actor_ref=actor_ref,
            actor_type=actor_type(actor_ref, actor_pk is not None),
            from_status=str(entry.get("from_status") or ""),
            to_status=str(entry.get("to_status") or ""),
            changed_fields=list(entry.get("fields") or []),
            changes=list(entry.get("changes") or []),
            request_id=request_id,
            created_at=parse_datetime(entry.get("created_at")) or datetime.now(timezone.utc),
        ))
        existing.add(request_id)


class PostgresIssueRepository:
    def get_one(self, issue_id: str) -> dict | None:
        with session_scope() as session:
            issue = session.scalar(select(Issue).where(Issue.issue_no == issue_id))
            if issue is None:
                return None
            _, users_by_pk = user_maps(session)
            order_no = session.scalar(select(WorkOrder.work_order_no).where(WorkOrder.issue_id == issue.id)) or ""
            return issue_dict(session, issue, users_by_pk, order_no)

    def load_all(self) -> List[dict]:
        with session_scope() as session:
            _, users_by_pk = user_maps(session)
            order_numbers = {
                issue_id: order_no
                for issue_id, order_no in session.execute(
                    select(WorkOrder.issue_id, WorkOrder.work_order_no).where(WorkOrder.issue_id.is_not(None))
                ).all()
            }
            records = session.scalars(select(Issue).order_by(Issue.created_at.desc(), Issue.id)).all()
            return [issue_dict(session, issue, users_by_pk, order_numbers.get(issue.id, "")) for issue in records]

    def load_page(
        self,
        *,
        limit: int,
        cursor_created_at: str = "",
        cursor_id: str = "",
        role: str,
        user_id: str,
        line_scope: list[str],
        status: str = "",
        line_id: str = "",
        machine_id: str = "",
        assigned_to: str = "",
        unresolved: bool = False,
    ) -> tuple[list[dict], int, tuple[str, str] | None]:
        conditions = []
        if role not in {"supervisor", "admin"}:
            if role == "operator":
                if "*" not in line_scope:
                    conditions.append(Issue.line_id.in_(line_scope) if line_scope else false())
            elif role == "maintenance":
                conditions.extend([
                    Issue.status.not_in(("completed", "verified", "cancelled")),
                    or_(Issue.assigned_to_ref == "", Issue.assigned_to_ref == user_id),
                ])
            else:
                conditions.append(false())
        if unresolved:
            conditions.append(Issue.status.not_in(("completed", "verified", "cancelled")))
        if status:
            conditions.append(Issue.status == status)
        if line_id:
            conditions.append(Issue.line_id == line_id)
        if machine_id:
            conditions.append(Issue.machine_id == machine_id)
        if assigned_to:
            conditions.append(Issue.assigned_to_ref == assigned_to)

        with session_scope() as session:
            total = int(session.scalar(select(func.count()).select_from(Issue).where(*conditions)) or 0)
            statement = select(Issue).where(*conditions)
            if cursor_created_at and cursor_id:
                cursor_time = parse_datetime(cursor_created_at)
                try:
                    cursor_pk = uuid.UUID(cursor_id)
                except ValueError:
                    cursor_pk = None
                if cursor_time is None or cursor_pk is None:
                    raise ValueError("Invalid pagination cursor")
                statement = statement.where(
                    or_(Issue.created_at < cursor_time, and_(Issue.created_at == cursor_time, Issue.id < cursor_pk))
                )
            records = session.scalars(
                statement.order_by(Issue.created_at.desc(), Issue.id.desc()).limit(limit + 1)
            ).all()
            has_more = len(records) > limit
            records = records[:limit]
            _, users_by_pk = user_maps(session)
            record_ids = [record.id for record in records]
            order_numbers = {
                issue_id: order_no
                for issue_id, order_no in session.execute(
                    select(WorkOrder.issue_id, WorkOrder.work_order_no).where(WorkOrder.issue_id.in_(record_ids or [None]))
                ).all()
            }
            items = [issue_dict(session, issue, users_by_pk, order_numbers.get(issue.id, "")) for issue in records]
            next_key = None
            if has_more and records:
                next_key = (records[-1].created_at.isoformat(), str(records[-1].id))
            return items, total, next_key

    def save_all(self, issues: List[dict]) -> None:
        with session_scope() as session:
            users, _ = user_maps(session)
            existing = {
                issue.issue_no: issue
                for issue in session.scalars(select(Issue).where(Issue.issue_no.in_([str(item.get("issue_id")) for item in issues] or [""]))).all()
            }
            for payload in issues:
                issue_no = str(payload.get("issue_id") or "")
                if not issue_no:
                    continue
                issue = existing.get(issue_no)
                if issue is None:
                    issue = Issue(issue_no=issue_no, machine_id=str(payload.get("machine_id") or ""), description=str(payload.get("description") or ""))
                    session.add(issue)
                    session.flush()
                    existing[issue_no] = issue
                expected = int(payload.get("version") or issue.version)
                updates = {
                    "source": str(payload.get("source") or "operator"),
                    "manual": str(payload.get("manual") or "808d"),
                    "machine_id": str(payload.get("machine_id") or ""),
                    "line_id": str(payload.get("line_id") or ""),
                    "alarm_code": str(payload.get("alarm_code") or ""),
                    "description": str(payload.get("description") or ""),
                    "original_description": str(payload.get("original_description") or payload.get("description") or ""),
                    "severity": str(payload.get("severity") or "medium"),
                    "status": str(payload.get("status") or "open"),
                    "assigned_to_ref": str(payload.get("assigned_to") or ""),
                    "created_by_ref": str(payload.get("created_by") or ""),
                    "updated_by_ref": str(payload.get("updated_by") or ""),
                    "rag_suggestion": str(payload.get("rag_suggestion") or ""),
                    "rag_answer_id": str(payload.get("rag_answer_id") or ""),
                    "resolution_summary": str(payload.get("resolution_summary") or ""),
                    "completed_at": parse_datetime(payload.get("completed_at")),
                }
                changed = any(getattr(issue, field) != value for field, value in updates.items())
                if changed and issue.version != expected:
                    raise ConcurrentUpdateError(f"Issue {issue_no} was updated by another transaction")
                for field, value in updates.items():
                    setattr(issue, field, value)
                issue.assigned_to_user_id = users.get(issue.assigned_to_ref)
                issue.created_by_user_id = users.get(issue.created_by_ref)
                issue.updated_by_user_id = users.get(issue.updated_by_ref)
                if changed:
                    issue.version += 1
                add_missing_audits(session, "issue", issue.id, issue_no, payload.get("issue_history") or [], users)
                existing_notes = {
                    (note.note, note.created_by_ref, iso(parse_datetime(note.created_at)))
                    for note in session.scalars(select(IssueNote).where(IssueNote.issue_id == issue.id)).all()
                }
                for note in payload.get("operator_notes") or []:
                    key = (
                        str(note.get("note") or ""),
                        str(note.get("created_by") or ""),
                        iso(parse_datetime(note.get("created_at"))),
                    )
                    if key in existing_notes or not key[0]:
                        continue
                    session.add(IssueNote(
                        issue_id=issue.id,
                        note=key[0],
                        created_by_ref=key[1],
                        created_by_user_id=users.get(key[1]),
                        created_at=parse_datetime(key[2]) or datetime.now(timezone.utc),
                    ))

    def save_one(self, payload: dict) -> dict:
        issue_id = str(payload.get("issue_id") or "")
        if not issue_id:
            raise ValueError("issue_id is required")
        self.save_all([payload])
        saved = self.get_one(issue_id)
        if saved is None:
            raise LookupError(f"Issue {issue_id} not found after save")
        return saved


class PostgresWorkOrderRepository:
    def get_one(self, order_id: str) -> dict | None:
        with session_scope() as session:
            order = session.scalar(select(WorkOrder).where(WorkOrder.work_order_no == order_id))
            if order is None:
                return None
            _, users_by_pk = user_maps(session)
            issue_no = session.scalar(select(Issue.issue_no).where(Issue.id == order.issue_id)) if order.issue_id else ""
            return order_dict(session, order, users_by_pk, issue_no or "")

    def load_all(self) -> List[dict]:
        with session_scope() as session:
            _, users_by_pk = user_maps(session)
            issue_numbers = {
                issue_pk: issue_no
                for issue_pk, issue_no in session.execute(select(Issue.id, Issue.issue_no)).all()
            }
            records = session.scalars(select(WorkOrder).order_by(WorkOrder.created_at.desc(), WorkOrder.id)).all()
            return [order_dict(session, order, users_by_pk, issue_numbers.get(order.issue_id, "")) for order in records]

    def load_page(
        self,
        *,
        limit: int,
        cursor_created_at: str = "",
        cursor_id: str = "",
        role: str,
        user_id: str,
        line_scope: list[str],
        status: str = "",
    ) -> tuple[list[dict], int, tuple[str, str] | None]:
        conditions = [WorkOrder.deleted_at.is_(None)]
        if role not in {"supervisor", "admin"}:
            if role == "operator":
                if "*" not in line_scope:
                    conditions.append(Issue.line_id.in_(line_scope) if line_scope else false())
            elif role == "maintenance":
                conditions.extend([
                    WorkOrder.status.not_in(("completed", "verified", "cancelled")),
                    or_(WorkOrder.assigned_to_ref == "", WorkOrder.assigned_to_ref == user_id),
                ])
            else:
                conditions.append(false())
        if status:
            conditions.append(WorkOrder.status == status)

        with session_scope() as session:
            total_statement = (
                select(func.count())
                .select_from(WorkOrder)
                .outerjoin(Issue, Issue.id == WorkOrder.issue_id)
                .where(*conditions)
            )
            total = int(session.scalar(total_statement) or 0)
            statement = (
                select(WorkOrder, Issue.issue_no)
                .outerjoin(Issue, Issue.id == WorkOrder.issue_id)
                .where(*conditions)
            )
            if cursor_created_at and cursor_id:
                cursor_time = parse_datetime(cursor_created_at)
                try:
                    cursor_pk = uuid.UUID(cursor_id)
                except ValueError:
                    cursor_pk = None
                if cursor_time is None or cursor_pk is None:
                    raise ValueError("Invalid pagination cursor")
                statement = statement.where(
                    or_(
                        WorkOrder.created_at < cursor_time,
                        and_(WorkOrder.created_at == cursor_time, WorkOrder.id < cursor_pk),
                    )
                )
            rows = session.execute(
                statement.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc()).limit(limit + 1)
            ).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            _, users_by_pk = user_maps(session)
            items = [order_dict(session, order, users_by_pk, issue_no or "") for order, issue_no in rows]
            next_key = None
            if has_more and rows:
                last_order = rows[-1][0]
                next_key = (last_order.created_at.isoformat(), str(last_order.id))
            return items, total, next_key

    def save_all(self, orders: List[dict]) -> None:
        with session_scope() as session:
            users, _ = user_maps(session)
            requested_issue_numbers = {
                str(payload.get("issue_id") or "")
                for payload in orders
                if payload.get("issue_id")
            }
            issue_ids = {
                issue_no: issue_pk
                for issue_pk, issue_no in session.execute(
                    select(Issue.id, Issue.issue_no).where(Issue.issue_no.in_(requested_issue_numbers or [""]))
                ).all()
            }
            existing = {
                order.work_order_no: order
                for order in session.scalars(select(WorkOrder).where(WorkOrder.work_order_no.in_([str(item.get("id")) for item in orders] or [""]))).all()
            }
            for payload in orders:
                order_no = str(payload.get("id") or "")
                if not order_no:
                    continue
                order = existing.get(order_no)
                if order is None:
                    order = WorkOrder(work_order_no=order_no, alarm_code=str(payload.get("alarm_code") or "SYMPTOM"))
                    session.add(order)
                    session.flush()
                    existing[order_no] = order
                expected = int(payload.get("version") or order.version)
                updates = {
                    "issue_id": issue_ids.get(str(payload.get("issue_id") or "")),
                    "alarm_code": str(payload.get("alarm_code") or "SYMPTOM"),
                    "manual": str(payload.get("manual") or "808d"),
                    "machine_id": str(payload.get("machine_id") or ""),
                    "status": str(payload.get("status") or "pending"),
                    "priority": str(payload.get("priority") or "medium"),
                    "assigned_to_ref": str(payload.get("assigned_to") or ""),
                    "created_by_ref": str(payload.get("created_by") or ""),
                    "updated_by_ref": str(payload.get("updated_by") or ""),
                    "accepted_by_ref": str(payload.get("accepted_by") or ""),
                    "completed_by_ref": str(payload.get("completed_by") or ""),
                    "verified_by_ref": str(payload.get("verified_by") or ""),
                    "description": str(payload.get("description") or ""),
                    "resolution": str(payload.get("resolution") or ""),
                    "notes": str(payload.get("notes") or ""),
                    "root_cause": str(payload.get("root_cause") or ""),
                    "repair_action": str(payload.get("repair_action") or ""),
                    "failure_category": str(payload.get("failure_category") or ""),
                    "rag_suggestion": str(payload.get("rag_suggestion") or ""),
                    "rag_answer_id": str(payload.get("rag_answer_id") or ""),
                    "source": str(payload.get("source") or "auto"),
                    "llm_correctness": str(payload.get("llm_correctness") or ""),
                    "llm_coverage": str(payload.get("llm_coverage") or ""),
                    "llm_missing_info": str(payload.get("llm_missing_info") or ""),
                    "llm_expected_fix": str(payload.get("llm_expected_fix") or ""),
                    "llm_answer_used": bool(payload.get("llm_answer_used", False)),
                    "kb_candidate": bool(payload.get("kb_candidate", False)),
                    "kb_review_status": str(payload.get("kb_review_status") or "not_ready"),
                    "kb_review_note": str(payload.get("kb_review_note") or ""),
                    "kb_reviewed_by_ref": str(payload.get("kb_reviewed_by") or ""),
                    "kb_reviewed_at": parse_datetime(payload.get("kb_reviewed_at")),
                    "kb_ingested_at": parse_datetime(payload.get("kb_ingested_at")),
                    "kb_ingest_result": payload.get("kb_ingest_result"),
                    "kb_duplicate_of": str(payload.get("kb_duplicate_of") or ""),
                    "completed_at": parse_datetime(payload.get("completed_at")),
                    "deleted_at": parse_datetime(payload.get("deleted_at")),
                }
                changed = any(getattr(order, field) != value for field, value in updates.items())
                if changed and order.version != expected:
                    raise ConcurrentUpdateError(f"Work order {order_no} was updated by another transaction")
                for field, value in updates.items():
                    setattr(order, field, value)
                order.assigned_to_user_id = users.get(order.assigned_to_ref)
                order.created_by_user_id = users.get(order.created_by_ref)
                order.updated_by_user_id = users.get(order.updated_by_ref)
                if changed:
                    order.version += 1
                add_missing_audits(session, "work_order", order.id, order_no, payload.get("work_order_history") or [], users)

    def save_one(self, payload: dict) -> dict:
        order_id = str(payload.get("id") or "")
        if not order_id:
            raise ValueError("work order id is required")
        self.save_all([payload])
        saved = self.get_one(order_id)
        if saved is None:
            raise LookupError(f"Work order {order_id} not found after save")
        return saved
