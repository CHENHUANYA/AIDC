from __future__ import annotations

import argparse
import json

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from db.session import database_status, get_engine


REQUIRED_TABLES = {
    "users",
    "sessions",
    "login_throttles",
    "alarm_events",
    "issues",
    "issue_notes",
    "work_orders",
    "audit_events",
    "feedback",
    "rag_answers",
    "documents",
    "document_versions",
    "system_settings",
}
REQUIRED_INDEXES = {
    "sessions": {"ix_sessions_user_expires"},
    "login_throttles": {
        "ix_login_throttles_updated_at",
        "ix_login_throttles_locked_until",
    },
    "alarm_events": {
        "ix_alarm_events_machine_occurred",
        "ix_alarm_events_code_occurred",
    },
    "issues": {
        "ix_issues_status_created",
        "ix_issues_machine_status",
        "ix_issues_assignee_status",
    },
    "issue_notes": {"ix_issue_notes_issue_created"},
    "work_orders": {
        "ix_work_orders_status_created",
        "ix_work_orders_assignee_status",
        "ix_work_orders_machine_status",
    },
    "audit_events": {
        "ix_audit_events_entity_created",
        "ix_audit_events_actor_created",
    },
    "feedback": {
        "ix_feedback_created",
        "ix_feedback_alarm_created",
    },
    "rag_answers": {
        "ix_rag_answers_created",
        "ix_rag_answers_collection_created",
    },
    "document_versions": {"ix_document_versions_status_imported"},
}


def missing_required_indexes(indexes: dict[str, set[str]]) -> dict[str, list[str]]:
    return {
        table: sorted(required - indexes.get(table, set()))
        for table, required in REQUIRED_INDEXES.items()
        if required - indexes.get(table, set())
    }


def check_database(alembic_ini: str = "alembic.ini") -> dict:
    engine = get_engine()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_revision = context.get_current_revision()
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        indexes = {
            table: {
                str(index["name"])
                for index in inspector.get_indexes(table)
                if index.get("name")
            }
            for table in REQUIRED_INDEXES
            if table in tables
        }

    config = Config(alembic_ini)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    missing_tables = sorted(REQUIRED_TABLES - tables)
    extra_tables = sorted(tables - REQUIRED_TABLES - {"alembic_version"})
    missing_indexes = missing_required_indexes(indexes)
    status = database_status()
    ok = current_revision == head_revision and not missing_tables and not missing_indexes
    return {
        "status": "ok" if ok else "fail",
        "database": status,
        "current_revision": current_revision,
        "head_revision": head_revision,
        "tables": sorted(tables),
        "missing_tables": missing_tables,
        "extra_tables": extra_tables,
        "indexes": {table: sorted(names) for table, names in sorted(indexes.items())},
        "missing_indexes": missing_indexes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Alarm RAG PostgreSQL connectivity and schema revision")
    parser.add_argument("--alembic-ini", default="alembic.ini")
    args = parser.parse_args()
    report = check_database(args.alembic_ini)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
