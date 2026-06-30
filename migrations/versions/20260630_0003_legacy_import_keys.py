"""Add stable legacy import keys for documents and feedback.

Revision ID: 20260630_0003
Revises: 20260630_0002
Create Date: 2026-06-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260630_0003"
down_revision: Union[str, Sequence[str], None] = "20260630_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("feedback", sa.Column("legacy_key", sa.String(length=96), nullable=True))
    op.create_unique_constraint("uq_feedback_legacy_key", "feedback", ["legacy_key"])

    op.add_column("documents", sa.Column("document_key", sa.String(length=255), nullable=True))
    op.execute("UPDATE documents SET document_key = id::text WHERE document_key IS NULL")
    op.alter_column("documents", "document_key", nullable=False)
    op.drop_constraint("uq_documents_collection_filename", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_collection_document_key",
        "documents",
        ["collection", "document_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_documents_collection_document_key", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_collection_filename",
        "documents",
        ["collection", "filename"],
    )
    op.drop_column("documents", "document_key")
    op.drop_constraint("uq_feedback_legacy_key", "feedback", type_="unique")
    op.drop_column("feedback", "legacy_key")
