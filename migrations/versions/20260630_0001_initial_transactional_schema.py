"""Create the initial Alarm RAG transactional schema.

Revision ID: 20260630_0001
Revises:
Create Date: 2026-06-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260630_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def id_column() -> sa.Column:
    return sa.Column("id", UUID, nullable=False)


def created_at_column(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "users",
        id_column(),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("team", sa.String(length=128), server_default="", nullable=False),
        sa.Column("line_scope", JSONB, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        created_at_column(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('operator','maintenance','supervisor','admin')", name="ck_users_valid_role"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("user_id", name="uq_users_user_id"),
    )

    op.create_table(
        "sessions",
        id_column(),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        created_at_column(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_expires", "sessions", ["user_id", "expires_at"])

    op.create_table(
        "alarm_events",
        id_column(),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("manual", sa.String(length=64), server_default="808d", nullable=False),
        sa.Column("alarm_code", sa.String(length=128), nullable=False),
        sa.Column("machine_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("line_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("severity", sa.String(length=32), server_default="medium", nullable=False),
        sa.Column("source", sa.String(length=128), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB, nullable=False),
        created_at_column(),
        sa.CheckConstraint("severity IN ('info','low','medium','high','critical')", name="ck_alarm_events_valid_severity"),
        sa.PrimaryKeyConstraint("id", name="pk_alarm_events"),
        sa.UniqueConstraint("event_key", name="uq_alarm_events_event_key"),
    )
    op.create_index("ix_alarm_events_machine_occurred", "alarm_events", ["machine_id", "occurred_at"])
    op.create_index("ix_alarm_events_code_occurred", "alarm_events", ["alarm_code", "occurred_at"])

    op.create_table(
        "issues",
        id_column(),
        sa.Column("issue_no", sa.String(length=128), nullable=False),
        sa.Column("alarm_event_id", UUID, nullable=True),
        sa.Column("source", sa.String(length=128), server_default="operator", nullable=False),
        sa.Column("manual", sa.String(length=64), server_default="808d", nullable=False),
        sa.Column("machine_id", sa.String(length=255), nullable=False),
        sa.Column("line_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("alarm_code", sa.String(length=128), server_default="", nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("original_description", sa.Text(), server_default="", nullable=False),
        sa.Column("severity", sa.String(length=32), server_default="medium", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="open", nullable=False),
        sa.Column("assigned_to_user_id", UUID, nullable=True),
        sa.Column("assigned_to_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=True),
        sa.Column("created_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_by_user_id", UUID, nullable=True),
        sa.Column("updated_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("rag_suggestion", sa.Text(), server_default="", nullable=False),
        sa.Column("resolution_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        created_at_column(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open','assigned','in_progress','completed','verified','cancelled')", name="ck_issues_valid_status"),
        sa.CheckConstraint("severity IN ('info','low','medium','high','critical')", name="ck_issues_valid_severity"),
        sa.CheckConstraint("version > 0", name="ck_issues_positive_version"),
        sa.ForeignKeyConstraint(["alarm_event_id"], ["alarm_events.id"], name="fk_issues_alarm_event_id_alarm_events", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], name="fk_issues_assigned_to_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_issues_created_by_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], name="fk_issues_updated_by_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_issues"),
        sa.UniqueConstraint("issue_no", name="uq_issues_issue_no"),
    )
    op.create_index("ix_issues_status_created", "issues", ["status", "created_at"])
    op.create_index("ix_issues_machine_status", "issues", ["machine_id", "status"])
    op.create_index("ix_issues_assignee_status", "issues", ["assigned_to_user_id", "status"])

    op.create_table(
        "issue_notes",
        id_column(),
        sa.Column("issue_id", UUID, nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=True),
        sa.Column("created_by_ref", sa.String(length=255), server_default="", nullable=False),
        created_at_column(),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], name="fk_issue_notes_issue_id_issues", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_issue_notes_created_by_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_issue_notes"),
    )
    op.create_index("ix_issue_notes_issue_created", "issue_notes", ["issue_id", "created_at"])

    op.create_table(
        "work_orders",
        id_column(),
        sa.Column("work_order_no", sa.String(length=128), nullable=False),
        sa.Column("issue_id", UUID, nullable=True),
        sa.Column("alarm_code", sa.String(length=128), nullable=False),
        sa.Column("manual", sa.String(length=64), server_default="808d", nullable=False),
        sa.Column("machine_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("priority", sa.String(length=32), server_default="medium", nullable=False),
        sa.Column("assigned_to_user_id", UUID, nullable=True),
        sa.Column("assigned_to_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_by_user_id", UUID, nullable=True),
        sa.Column("created_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_by_user_id", UUID, nullable=True),
        sa.Column("updated_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("accepted_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("completed_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("verified_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("resolution", sa.Text(), server_default="", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("root_cause", sa.Text(), server_default="", nullable=False),
        sa.Column("repair_action", sa.Text(), server_default="", nullable=False),
        sa.Column("failure_category", sa.String(length=255), server_default="", nullable=False),
        sa.Column("rag_suggestion", sa.Text(), server_default="", nullable=False),
        sa.Column("source", sa.String(length=128), server_default="auto", nullable=False),
        sa.Column("llm_correctness", sa.String(length=64), server_default="", nullable=False),
        sa.Column("llm_coverage", sa.String(length=64), server_default="", nullable=False),
        sa.Column("llm_missing_info", sa.Text(), server_default="", nullable=False),
        sa.Column("llm_expected_fix", sa.Text(), server_default="", nullable=False),
        sa.Column("llm_answer_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kb_candidate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kb_review_status", sa.String(length=64), server_default="not_ready", nullable=False),
        sa.Column("kb_review_note", sa.Text(), server_default="", nullable=False),
        sa.Column("kb_reviewed_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("kb_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kb_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kb_ingest_result", JSONB, nullable=True),
        sa.Column("kb_duplicate_of", sa.String(length=128), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        created_at_column(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','assigned','in_progress','completed','verified','cancelled')", name="ck_work_orders_valid_status"),
        sa.CheckConstraint("priority IN ('low','medium','high','critical')", name="ck_work_orders_valid_priority"),
        sa.CheckConstraint("version > 0", name="ck_work_orders_positive_version"),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], name="fk_work_orders_issue_id_issues", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], name="fk_work_orders_assigned_to_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_work_orders_created_by_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], name="fk_work_orders_updated_by_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_work_orders"),
        sa.UniqueConstraint("issue_id", name="uq_work_orders_issue_id"),
        sa.UniqueConstraint("work_order_no", name="uq_work_orders_work_order_no"),
    )
    op.create_index("ix_work_orders_status_created", "work_orders", ["status", "created_at"])
    op.create_index("ix_work_orders_assignee_status", "work_orders", ["assigned_to_user_id", "status"])
    op.create_index("ix_work_orders_machine_status", "work_orders", ["machine_id", "status"])

    op.create_table(
        "audit_events",
        id_column(),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", UUID, nullable=True),
        sa.Column("actor_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("actor_type", sa.String(length=32), server_default="legacy", nullable=False),
        sa.Column("from_status", sa.String(length=64), server_default="", nullable=False),
        sa.Column("to_status", sa.String(length=64), server_default="", nullable=False),
        sa.Column("changed_fields", JSONB, nullable=False),
        sa.Column("changes", JSONB, nullable=False),
        sa.Column("request_id", sa.String(length=128), server_default="", nullable=False),
        created_at_column(),
        sa.CheckConstraint("actor_type IN ('user','system','automation','legacy')", name="ck_audit_events_valid_actor_type"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_audit_events_actor_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_entity_created", "audit_events", ["entity_type", "entity_id", "created_at"])
    op.create_index("ix_audit_events_actor_created", "audit_events", ["actor_user_id", "created_at"])

    op.create_table(
        "feedback",
        id_column(),
        sa.Column("answer_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("issue_id", UUID, nullable=True),
        sa.Column("work_order_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("user_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("role", sa.String(length=32), server_default="", nullable=False),
        sa.Column("query", sa.Text(), server_default="", nullable=False),
        sa.Column("collection", sa.String(length=128), server_default="", nullable=False),
        sa.Column("alarm_code", sa.String(length=128), server_default="", nullable=False),
        sa.Column("feedback", sa.String(length=32), server_default="", nullable=False),
        sa.Column("correctness", sa.String(length=64), server_default="", nullable=False),
        sa.Column("coverage", sa.String(length=64), server_default="", nullable=False),
        sa.Column("missing_info", sa.Text(), server_default="", nullable=False),
        sa.Column("expected_fix", sa.Text(), server_default="", nullable=False),
        sa.Column("kb_candidate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        created_at_column(),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], name="fk_feedback_issue_id_issues", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], name="fk_feedback_work_order_id_work_orders", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_feedback_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
    )
    op.create_index("ix_feedback_created", "feedback", ["created_at"])
    op.create_index("ix_feedback_alarm_created", "feedback", ["alarm_code", "created_at"])

    op.create_table(
        "documents",
        id_column(),
        sa.Column("collection", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("current_version_id", UUID, nullable=True),
        created_at_column(),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("collection", "filename", name="uq_documents_collection_filename"),
    )

    op.create_table(
        "document_versions",
        id_column(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("section_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=64), server_default="ready", nullable=False),
        sa.Column("imported_by_user_id", UUID, nullable=True),
        sa.Column("imported_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        created_at_column("imported_at"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_versions_document_id_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["imported_by_user_id"], ["users.id"], name="fk_document_versions_imported_by_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("document_id", "source_hash", name="uq_document_versions_document_hash"),
    )
    op.create_index("ix_document_versions_status_imported", "document_versions", ["status", "imported_at"])
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by_user_id", UUID, nullable=True),
        sa.Column("updated_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], name="fk_system_settings_updated_by_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key", name="pk_system_settings"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_index("ix_document_versions_status_imported", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_index("ix_feedback_alarm_created", table_name="feedback")
    op.drop_index("ix_feedback_created", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("ix_audit_events_actor_created", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_work_orders_machine_status", table_name="work_orders")
    op.drop_index("ix_work_orders_assignee_status", table_name="work_orders")
    op.drop_index("ix_work_orders_status_created", table_name="work_orders")
    op.drop_table("work_orders")
    op.drop_index("ix_issue_notes_issue_created", table_name="issue_notes")
    op.drop_table("issue_notes")
    op.drop_index("ix_issues_assignee_status", table_name="issues")
    op.drop_index("ix_issues_machine_status", table_name="issues")
    op.drop_index("ix_issues_status_created", table_name="issues")
    op.drop_table("issues")
    op.drop_index("ix_alarm_events_code_occurred", table_name="alarm_events")
    op.drop_index("ix_alarm_events_machine_occurred", table_name="alarm_events")
    op.drop_table("alarm_events")
    op.drop_index("ix_sessions_user_expires", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
