"""Bind sessions to a credential generation.

Revision ID: 20260902_0008
Revises: 20260729_0007
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260902_0008"
down_revision: Union[str, Sequence[str], None] = "20260729_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("credential_epoch", sa.Integer(), server_default="1", nullable=False))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("sessions", sa.Column("credential_epoch", sa.Integer(), server_default="1", nullable=False))


def downgrade() -> None:
    op.drop_column("sessions", "credential_epoch")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "credential_epoch")
