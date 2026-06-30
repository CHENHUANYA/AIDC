"""Normalize check constraint names to the model naming convention.

Revision ID: 20260630_0002
Revises: 20260630_0001
Create Date: 2026-06-30
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260630_0002"
down_revision: Union[str, Sequence[str], None] = "20260630_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RENAMES = {
    "users": [("ck_users_ck_users_valid_role", "ck_users_valid_role")],
    "alarm_events": [("ck_alarm_events_ck_alarm_events_valid_severity", "ck_alarm_events_valid_severity")],
    "issues": [
        ("ck_issues_ck_issues_valid_status", "ck_issues_valid_status"),
        ("ck_issues_ck_issues_valid_severity", "ck_issues_valid_severity"),
        ("ck_issues_ck_issues_positive_version", "ck_issues_positive_version"),
    ],
    "work_orders": [
        ("ck_work_orders_ck_work_orders_valid_status", "ck_work_orders_valid_status"),
        ("ck_work_orders_ck_work_orders_valid_priority", "ck_work_orders_valid_priority"),
        ("ck_work_orders_ck_work_orders_positive_version", "ck_work_orders_positive_version"),
    ],
    "audit_events": [("ck_audit_events_ck_audit_events_valid_actor_type", "ck_audit_events_valid_actor_type")],
}


def rename(table: str, old: str, new: str) -> None:
    op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    for table, renames in RENAMES.items():
        for old, new in renames:
            rename(table, old, new)


def downgrade() -> None:
    for table, renames in reversed(list(RENAMES.items())):
        for old, new in reversed(renames):
            rename(table, new, old)
