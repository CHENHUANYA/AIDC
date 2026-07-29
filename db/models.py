from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('operator','maintenance','supervisor','admin')", name="valid_role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    line_scope: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class LoginSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_sessions_user_expires", "user_id", "expires_at"),)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        CheckConstraint("failure_count >= 0", name="non_negative_failure_count"),
        Index("ix_login_throttles_updated_at", "updated_at"),
        Index("ix_login_throttles_locked_until", "locked_until"),
    )

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AlarmEvent(Base):
    __tablename__ = "alarm_events"
    __table_args__ = (
        CheckConstraint("severity IN ('info','low','medium','high','critical')", name="valid_severity"),
        Index("ix_alarm_events_machine_occurred", "machine_id", "occurred_at"),
        Index("ix_alarm_events_code_occurred", "alarm_code", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    manual: Mapped[str] = mapped_column(String(64), nullable=False, server_default="808d")
    alarm_code: Mapped[str] = mapped_column(String(128), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    line_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    severity: Mapped[str] = mapped_column(String(32), nullable=False, server_default="medium")
    source: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint("status IN ('open','assigned','in_progress','completed','verified','cancelled')", name="valid_status"),
        CheckConstraint("severity IN ('info','low','medium','high','critical')", name="valid_severity"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_issues_status_created", "status", "created_at"),
        Index("ix_issues_machine_status", "machine_id", "status"),
        Index("ix_issues_assignee_status", "assigned_to_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    issue_no: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    alarm_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alarm_events.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(128), nullable=False, server_default="operator")
    manual: Mapped[str] = mapped_column(String(64), nullable=False, server_default="808d")
    machine_id: Mapped[str] = mapped_column(String(255), nullable=False)
    line_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    alarm_code: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    original_description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    severity: Mapped[str] = mapped_column(String(32), nullable=False, server_default="medium")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="open")
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    rag_suggestion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rag_answer_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    resolution_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IssueNote(Base):
    __tablename__ = "issue_notes"
    __table_args__ = (Index("ix_issue_notes_issue_created", "issue_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_at: Mapped[datetime] = created_at_column()


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        CheckConstraint("status IN ('pending','assigned','in_progress','completed','verified','cancelled')", name="valid_status"),
        CheckConstraint("priority IN ('low','medium','high','critical')", name="valid_priority"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_work_orders_status_created", "status", "created_at"),
        Index("ix_work_orders_assignee_status", "assigned_to_user_id", "status"),
        Index("ix_work_orders_machine_status", "machine_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    work_order_no: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), unique=True)
    alarm_code: Mapped[str] = mapped_column(String(128), nullable=False)
    manual: Mapped[str] = mapped_column(String(64), nullable=False, server_default="808d")
    machine_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, server_default="medium")
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    accepted_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    completed_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    verified_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    resolution: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    repair_action: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    failure_category: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    rag_suggestion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rag_answer_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    source: Mapped[str] = mapped_column(String(128), nullable=False, server_default="auto")
    llm_correctness: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    llm_coverage: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    llm_missing_info: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    llm_expected_fix: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    llm_answer_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    kb_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    kb_review_status: Mapped[str] = mapped_column(String(64), nullable=False, server_default="not_ready")
    kb_review_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    kb_reviewed_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    kb_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kb_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kb_ingest_result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    kb_duplicate_of: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user','system','automation','legacy')", name="valid_actor_type"),
        Index("ix_audit_events_entity_created", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="legacy")
    from_status: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    to_status: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    changed_fields: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    created_at: Mapped[datetime] = created_at_column()


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        Index("ix_feedback_created", "created_at"),
        Index("ix_feedback_alarm_created", "alarm_code", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    legacy_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    answer_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_orders.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    query: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    collection: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    alarm_code: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    feedback: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    correctness: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    coverage: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    missing_info: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    expected_fix: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    kb_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = created_at_column()


class RagAnswer(Base):
    __tablename__ = "rag_answers"
    __table_args__ = (
        Index("ix_rag_answers_created", "created_at"),
        Index("ix_rag_answers_collection_created", "collection", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    answer_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    query: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    collection: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    answer: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    answer_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="complete")
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    model: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    tokenizer_version: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    retrieval_version: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    created_at: Mapped[datetime] = created_at_column()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("collection", "document_key", name="uq_documents_collection_document_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    document_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", name="fk_documents_current_version", use_alter=True, ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "source_hash", name="uq_document_versions_document_hash"),
        Index("ix_document_versions_status_imported", "status", "imported_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(64), nullable=False, server_default="ready")
    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    imported_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_TYPE, nullable=False, default=dict)
    imported_at: Mapped[datetime] = created_at_column()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON_TYPE, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
