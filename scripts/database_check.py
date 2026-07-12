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


def check_database(alembic_ini: str = "alembic.ini") -> dict:
    engine = get_engine()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_revision = context.get_current_revision()
        tables = set(inspect(connection).get_table_names())

    config = Config(alembic_ini)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    missing_tables = sorted(REQUIRED_TABLES - tables)
    extra_tables = sorted(tables - REQUIRED_TABLES - {"alembic_version"})
    status = database_status()
    ok = current_revision == head_revision and not missing_tables
    return {
        "status": "ok" if ok else "fail",
        "database": status,
        "current_revision": current_revision,
        "head_revision": head_revision,
        "tables": sorted(tables),
        "missing_tables": missing_tables,
        "extra_tables": extra_tables,
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
