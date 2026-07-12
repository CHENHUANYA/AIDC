"""Add an explicit lifecycle state to immutable RAG answer snapshots.

Revision ID: 20260713_0006
Revises: 20260712_0005
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260713_0006"
down_revision: Union[str, Sequence[str], None] = "20260712_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rag_answers",
        sa.Column("answer_state", sa.String(length=32), server_default="complete", nullable=False),
    )
    op.create_check_constraint(
        "ck_rag_answers_answer_state",
        "rag_answers",
        "answer_state IN ('complete','fallback','unavailable')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rag_answers_answer_state", "rag_answers", type_="check")
    op.drop_column("rag_answers", "answer_state")
