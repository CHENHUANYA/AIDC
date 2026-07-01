from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from db.models import AlarmEvent, Document, DocumentVersion, Feedback, Issue, SystemSetting, User, WorkOrder
from db.session import session_scope
from repositories.postgres_workflow import parse_datetime


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def alarm_dict(record: AlarmEvent) -> dict:
    payload = dict(record.raw_payload or {})
    payload.update({
        "alarm_code": record.alarm_code,
        "manual": record.manual,
        "machine_id": record.machine_id,
        "line_id": record.line_id,
        "source": record.source,
        "severity": record.severity,
        "description": record.description,
        "time": iso(record.occurred_at),
        "date": record.occurred_at.date().isoformat(),
    })
    return payload


class PostgresAlarmRepository:
    def add(self, payload: dict, event_key: str | None = None):
        occurred_at = parse_datetime(payload.get("time") or payload.get("date")) or datetime.now(timezone.utc)
        with session_scope() as session:
            record = AlarmEvent(
                event_key=event_key or f"runtime:alarm:{uuid.uuid4().hex}",
                manual=str(payload.get("manual") or "808d"),
                alarm_code=str(payload.get("alarm_code") or ""),
                machine_id=str(payload.get("machine_id") or ""),
                line_id=str(payload.get("line_id") or ""),
                severity=str(payload.get("severity") or "info"),
                source=str(payload.get("source") or ""),
                description=str(payload.get("description") or ""),
                occurred_at=occurred_at,
                raw_payload=payload,
            )
            session.add(record)
            session.flush()
            return record.id

    def load_all(self, limit: int | None = None) -> list[dict]:
        with session_scope() as session:
            statement = select(AlarmEvent).order_by(AlarmEvent.occurred_at, AlarmEvent.id)
            if limit:
                statement = statement.limit(limit)
            return [alarm_dict(record) for record in session.scalars(statement).all()]

    def clear(self) -> int:
        with session_scope() as session:
            result = session.execute(delete(AlarmEvent))
            return int(result.rowcount or 0)


def feedback_dict(record: Feedback, issue_no: str = "", order_no: str = "") -> dict:
    return {
        "time": iso(record.created_at),
        "query": record.query,
        "collection": record.collection,
        "alarm_code": record.alarm_code,
        "feedback": record.feedback,
        "answer_id": record.answer_id,
        "issue_id": issue_no,
        "work_order_id": order_no,
        "user_id": record.user_ref,
        "role": record.role,
        "correctness": record.correctness,
        "coverage": record.coverage,
        "missing_info": record.missing_info,
        "expected_fix": record.expected_fix,
        "kb_candidate": record.kb_candidate,
    }


class PostgresFeedbackRepository:
    def add(self, payload: dict) -> None:
        with session_scope() as session:
            user_ref = str(payload.get("user_id") or "")
            issue_ref = str(payload.get("issue_id") or "")
            order_ref = str(payload.get("work_order_id") or "")
            record = Feedback(
                legacy_key=f"runtime:feedback:{uuid.uuid4().hex}",
                answer_id=str(payload.get("answer_id") or ""),
                issue_id=session.scalar(select(Issue.id).where(Issue.issue_no == issue_ref)) if issue_ref else None,
                work_order_id=session.scalar(select(WorkOrder.id).where(WorkOrder.work_order_no == order_ref)) if order_ref else None,
                user_id=session.scalar(select(User.id).where(User.user_id == user_ref)) if user_ref else None,
                user_ref=user_ref,
                role=str(payload.get("role") or ""),
                query=str(payload.get("query") or ""),
                collection=str(payload.get("collection") or ""),
                alarm_code=str(payload.get("alarm_code") or ""),
                feedback=str(payload.get("feedback") or ""),
                correctness=str(payload.get("correctness") or ""),
                coverage=str(payload.get("coverage") or ""),
                missing_info=str(payload.get("missing_info") or ""),
                expected_fix=str(payload.get("expected_fix") or ""),
                kb_candidate=bool(payload.get("kb_candidate", False)),
                created_at=parse_datetime(payload.get("time")) or datetime.now(timezone.utc),
            )
            session.add(record)

    def load_all(self) -> list[dict]:
        with session_scope() as session:
            records = session.scalars(select(Feedback).order_by(Feedback.created_at, Feedback.id)).all()
            issue_numbers = dict(session.execute(select(Issue.id, Issue.issue_no)).all())
            order_numbers = dict(session.execute(select(WorkOrder.id, WorkOrder.work_order_no)).all())
            return [
                feedback_dict(
                    record,
                    issue_numbers.get(record.issue_id, ""),
                    order_numbers.get(record.work_order_id, ""),
                )
                for record in records
            ]


class PostgresSettingsRepository:
    def load_all(self) -> dict[str, Any]:
        with session_scope() as session:
            return {key: value for key, value in session.execute(select(SystemSetting.key, SystemSetting.value)).all()}

    def save_all(self, settings: dict[str, Any], updated_by: str) -> None:
        with session_scope() as session:
            user_pk = session.scalar(select(User.id).where(User.user_id == updated_by)) if updated_by else None
            existing = {
                record.key: record
                for record in session.scalars(
                    select(SystemSetting).where(SystemSetting.key.in_(list(settings) or [""]))
                ).all()
            }
            for key, value in settings.items():
                record = existing.get(key)
                if record is None:
                    record = SystemSetting(key=key)
                    session.add(record)
                record.value = value
                record.updated_by_ref = updated_by
                record.updated_by_user_id = user_pk


def document_dict(document: Document, version: DocumentVersion | None) -> dict:
    payload = dict(version.metadata_json or {}) if version else {}
    payload.update({
        "doc_id": document.document_key,
        "filename": document.filename,
        "source_hash": version.source_hash if version else "",
        "imported_at": iso(version.imported_at) if version else iso(document.created_at),
        "sections": version.section_count if version else 0,
        "version": payload.get("version", 1),
    })
    return payload


class PostgresDocumentRepository:
    def load_collection(self, collection: str) -> list[dict]:
        with session_scope() as session:
            records = session.execute(
                select(Document, DocumentVersion)
                .outerjoin(DocumentVersion, DocumentVersion.id == Document.current_version_id)
                .where(Document.collection == collection)
                .order_by(Document.created_at.desc(), Document.id)
            ).all()
            return [document_dict(document, version) for document, version in records]

    def list_collections(self) -> list[dict]:
        with session_scope() as session:
            records = session.execute(
                select(Document, DocumentVersion)
                .outerjoin(DocumentVersion, DocumentVersion.id == Document.current_version_id)
                .order_by(Document.collection, Document.created_at)
            ).all()
            grouped: dict[str, dict] = {}
            for document, version in records:
                item = grouped.setdefault(document.collection, {
                    "name": document.collection,
                    "documents": 0,
                    "sections": 0,
                    "updated_at": "",
                })
                item["documents"] += 1
                item["sections"] += version.section_count if version else 0
                imported_at = iso(version.imported_at) if version else iso(document.created_at)
                item["updated_at"] = max(item["updated_at"], imported_at)
            return list(grouped.values())

    def find_by_hash(self, collection: str, source_hash: str) -> dict | None:
        if not source_hash:
            return None
        with session_scope() as session:
            row = session.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(Document.collection == collection, DocumentVersion.source_hash == source_hash)
                .order_by(DocumentVersion.imported_at.desc())
            ).first()
            return document_dict(*row) if row else None

    def upsert(self, collection: str, payload: dict) -> None:
        document_key = str(payload.get("doc_id") or "")
        if not document_key:
            raise ValueError("Document doc_id is required")
        source_hash = str(payload.get("source_hash") or "")
        imported_at = parse_datetime(payload.get("imported_at")) or datetime.now(timezone.utc)
        with session_scope() as session:
            document = session.scalar(
                select(Document).where(
                    Document.collection == collection,
                    Document.document_key == document_key,
                )
            )
            if document is None:
                document = Document(
                    collection=collection,
                    document_key=document_key,
                    filename=str(payload.get("filename") or document_key),
                    created_at=imported_at,
                )
                session.add(document)
                session.flush()
            else:
                document.filename = str(payload.get("filename") or document.filename)

            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.source_hash == source_hash,
                )
            )
            if version is None:
                version = DocumentVersion(
                    document_id=document.id,
                    source_hash=source_hash,
                    storage_path=str(payload.get("filename") or ""),
                    imported_at=imported_at,
                )
                session.add(version)
                session.flush()
            version.section_count = int(payload.get("sections") or 0)
            version.status = "ready"
            version.imported_by_ref = str(payload.get("imported_by") or "runtime")
            version.metadata_json = dict(payload)
            document.current_version_id = version.id

    def remove(self, collection: str, document_key: str) -> bool:
        with session_scope() as session:
            document = session.scalar(
                select(Document).where(
                    Document.collection == collection,
                    Document.document_key == document_key,
                )
            )
            if document is None:
                return False
            document.current_version_id = None
            session.flush()
            session.delete(document)
            return True
