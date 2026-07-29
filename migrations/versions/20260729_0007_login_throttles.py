"""Add shared login throttling state.

Revision ID: 20260729_0007
Revises: 20260713_0006
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260729_0007"
down_revision: Union[str, Sequence[str], None] = "20260713_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_login_throttles_non_negative_failure_count",
        ),
        sa.PrimaryKeyConstraint("key_hash", name="pk_login_throttles"),
    )
    op.create_index(
        "ix_login_throttles_updated_at",
        "login_throttles",
        ["updated_at"],
    )
    op.create_index(
        "ix_login_throttles_locked_until",
        "login_throttles",
        ["locked_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_login_throttles_locked_until", table_name="login_throttles")
    op.drop_index("ix_login_throttles_updated_at", table_name="login_throttles")
    op.drop_table("login_throttles")
