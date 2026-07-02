"""Enable PostgreSQL query performance statistics.

Revision ID: 20260701_0004
Revises: 20260630_0003
Create Date: 2026-07-01
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260701_0004"
down_revision: Union[str, Sequence[str], None] = "20260630_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
