"""Persist immutable RAG answer snapshots and workflow links.

Revision ID: 20260712_0005
Revises: 20260701_0004
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0005"
down_revision: Union[str, Sequence[str], None] = "20260701_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_answers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("answer_id", sa.String(length=255), nullable=False),
        sa.Column("query", sa.Text(), server_default="", nullable=False),
        sa.Column("collection", sa.String(length=128), server_default="", nullable=False),
        sa.Column("answer", sa.Text(), server_default="", nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=64), server_default="", nullable=False),
        sa.Column("model", sa.String(length=255), server_default="", nullable=False),
        sa.Column("tokenizer_version", sa.String(length=128), server_default="", nullable=False),
        sa.Column("retrieval_version", sa.String(length=128), server_default="", nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by_ref", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_id"),
    )
    op.create_index("ix_rag_answers_created", "rag_answers", ["created_at"])
    op.create_index("ix_rag_answers_collection_created", "rag_answers", ["collection", "created_at"])
    op.add_column("issues", sa.Column("rag_answer_id", sa.String(length=255), server_default="", nullable=False))
    op.add_column("work_orders", sa.Column("rag_answer_id", sa.String(length=255), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("work_orders", "rag_answer_id")
    op.drop_column("issues", "rag_answer_id")
    op.drop_index("ix_rag_answers_collection_created", table_name="rag_answers")
    op.drop_index("ix_rag_answers_created", table_name="rag_answers")
    op.drop_table("rag_answers")
