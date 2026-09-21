"""Add persisted RAG snapshot accounting.

Revision ID: 20260902_0009
Revises: 20260902_0008
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260902_0009"
down_revision: Union[str, Sequence[str], None] = "20260902_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rag_answers", sa.Column("payload_bytes", sa.Integer(), server_default="0", nullable=False))
    op.execute(
        """
        UPDATE rag_answers
        SET payload_bytes =
            octet_length(query) + octet_length(collection) + octet_length(answer) +
            octet_length(citations::text) + octet_length(provider) + octet_length(model) +
            octet_length(tokenizer_version) + octet_length(retrieval_version) +
            octet_length(created_by_ref)
        """
    )
    op.create_index(
        "ix_rag_answers_creator_created",
        "rag_answers",
        ["created_by_ref", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rag_answers_creator_created", table_name="rag_answers")
    op.drop_column("rag_answers", "payload_bytes")
