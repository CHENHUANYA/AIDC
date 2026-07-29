from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from db.session import get_database_url
from scripts.database_check import REQUIRED_INDEXES, REQUIRED_TABLES, missing_required_indexes


SCRATCH_PREFIX = "alarm_rag_migration_drill_"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validated_scratch_name(value: str) -> str:
    if not value.startswith(SCRATCH_PREFIX) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Scratch database name is outside the migration-drill namespace")
    return value


def scratch_database_name() -> str:
    return f"{SCRATCH_PREFIX}{uuid.uuid4().hex[:12]}"


def postgresql_url(value: str) -> URL:
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Migration drill requires a PostgreSQL database URL")
    if not url.database:
        raise ValueError("Migration drill database URL must include a database")
    return url


def execute_admin_statement(url: URL, statement: str) -> None:
    admin_url = url.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(statement)
    finally:
        engine.dispose()


def create_scratch_database(url: URL, scratch: str) -> None:
    name = validated_scratch_name(scratch)
    execute_admin_statement(url, f'CREATE DATABASE "{name}" TEMPLATE template0')


def drop_scratch_database(url: URL, scratch: str) -> None:
    name = validated_scratch_name(scratch)
    execute_admin_statement(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def run_alembic(url: URL, alembic_ini: str, command: str, revision: str) -> None:
    if command not in {"upgrade", "downgrade"}:
        raise ValueError(f"Unsupported Alembic command: {command}")
    environment = dict(os.environ)
    environment["DATABASE_URL"] = url.render_as_string(hide_password=False)
    environment["POSTGRES_ENABLED"] = "true"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", alembic_ini, command, revision],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )


def schema_snapshot(url: URL) -> dict:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
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
            revision = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    return {
        "revision": revision,
        "tables": sorted(tables),
        "missing_tables": sorted(REQUIRED_TABLES - tables),
        "missing_indexes": missing_required_indexes(indexes),
    }


def migration_drill(
    *,
    database_url: str,
    alembic_ini: str = "alembic.ini",
    apply: bool,
    scratch: str | None = None,
) -> dict:
    config = Config(alembic_ini)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    if not apply:
        return {
            "status": "dry-run",
            "head_revision": head_revision,
            "steps": [
                "create isolated scratch database",
                "upgrade head",
                "downgrade base",
                "upgrade head",
                "validate revision, tables, and indexes",
                "drop scratch database",
            ],
        }

    source_url = postgresql_url(database_url)
    scratch_name = validated_scratch_name(scratch or scratch_database_name())
    scratch_url = source_url.set(database=scratch_name)
    created = False
    removed = False
    started = time.monotonic()
    report: dict = {}
    try:
        create_scratch_database(source_url, scratch_name)
        created = True

        run_alembic(scratch_url, alembic_ini, "upgrade", "head")
        first_upgrade = schema_snapshot(scratch_url)

        run_alembic(scratch_url, alembic_ini, "downgrade", "base")
        downgrade = schema_snapshot(scratch_url)

        run_alembic(scratch_url, alembic_ini, "upgrade", "head")
        second_upgrade = schema_snapshot(scratch_url)

        checks = {
            "first_upgrade": (
                first_upgrade["revision"] == head_revision
                and not first_upgrade["missing_tables"]
                and not first_upgrade["missing_indexes"]
            ),
            "downgrade_base": (
                downgrade["revision"] is None
                and not (set(downgrade["tables"]) & REQUIRED_TABLES)
            ),
            "second_upgrade": (
                second_upgrade["revision"] == head_revision
                and not second_upgrade["missing_tables"]
                and not second_upgrade["missing_indexes"]
            ),
        }
        report = {
            "status": "ok" if all(checks.values()) else "fail",
            "scratch_database": scratch_name,
            "head_revision": head_revision,
            "checks": checks,
            "first_upgrade": first_upgrade,
            "downgrade": downgrade,
            "second_upgrade": second_upgrade,
        }
    finally:
        if created:
            drop_scratch_database(source_url, scratch_name)
            removed = True

    report["scratch_removed"] = removed
    report["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return report


def redacted_error(exc: Exception, database_url: str) -> str:
    message = str(exc)
    try:
        password = make_url(database_url).password
    except Exception:
        password = None
    if password:
        message = message.replace(password, "***")
    return message[:500]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse Alembic upgrade/downgrade only in an isolated scratch database"
    )
    parser.add_argument("--alembic-ini", default="alembic.ini")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create and remove a scratch database; without this flag only print the plan",
    )
    args = parser.parse_args()
    database_url = get_database_url() if args.apply else ""
    try:
        report = migration_drill(
            database_url=database_url,
            alembic_ini=str(Path(args.alembic_ini)),
            apply=args.apply,
        )
    except Exception as exc:
        report = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": redacted_error(exc, database_url),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"ok", "dry-run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
