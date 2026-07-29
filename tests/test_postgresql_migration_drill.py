from subprocess import CalledProcessError
from unittest.mock import call, patch

import pytest

from scripts import postgresql_migration_drill as drill


DATABASE_URL = "postgresql+psycopg://alarm_rag:secret-value@localhost:5432/alarm_rag"
SCRATCH = "alarm_rag_migration_drill_test123"


def snapshot(revision, *, tables=True, indexes=True) -> dict:
    return {
        "revision": revision,
        "tables": sorted(drill.REQUIRED_TABLES) if tables else ["alembic_version"],
        "missing_tables": [] if tables else sorted(drill.REQUIRED_TABLES),
        "missing_indexes": {} if indexes else {"issues": ["ix_issues_status_created"]},
    }


def test_migration_drill_dry_run_does_not_connect() -> None:
    with patch.object(drill, "create_scratch_database") as create:
        report = drill.migration_drill(database_url="", apply=False)

    assert report["status"] == "dry-run"
    assert report["head_revision"] == "20260729_0007"
    create.assert_not_called()


def test_migration_drill_rehearses_both_directions_and_removes_scratch() -> None:
    head = "20260729_0007"
    with (
        patch.object(drill, "create_scratch_database") as create,
        patch.object(drill, "drop_scratch_database") as drop,
        patch.object(drill, "run_alembic") as run,
        patch.object(
            drill,
            "schema_snapshot",
            side_effect=[
                snapshot(head),
                snapshot(None, tables=False),
                snapshot(head),
            ],
        ),
    ):
        report = drill.migration_drill(
            database_url=DATABASE_URL,
            apply=True,
            scratch=SCRATCH,
        )

    scratch_url = drill.make_url(DATABASE_URL).set(database=SCRATCH)
    assert report["status"] == "ok"
    assert report["checks"] == {
        "first_upgrade": True,
        "downgrade_base": True,
        "second_upgrade": True,
    }
    assert report["scratch_removed"] is True
    create.assert_called_once()
    assert run.call_args_list == [
        call(scratch_url, "alembic.ini", "upgrade", "head"),
        call(scratch_url, "alembic.ini", "downgrade", "base"),
        call(scratch_url, "alembic.ini", "upgrade", "head"),
    ]
    drop.assert_called_once()


def test_migration_drill_removes_scratch_after_alembic_failure() -> None:
    with (
        patch.object(drill, "create_scratch_database"),
        patch.object(
            drill,
            "run_alembic",
            side_effect=CalledProcessError(1, ["alembic"]),
        ),
        patch.object(drill, "drop_scratch_database") as drop,
    ):
        with pytest.raises(CalledProcessError):
            drill.migration_drill(
                database_url=DATABASE_URL,
                apply=True,
                scratch=SCRATCH,
            )

    drop.assert_called_once()


def test_migration_drill_rejects_unsafe_database_targets_and_redacts_password() -> None:
    with pytest.raises(ValueError, match="namespace"):
        drill.validated_scratch_name("alarm_rag")
    with pytest.raises(ValueError, match="PostgreSQL"):
        drill.postgresql_url("sqlite:///alarm.db")

    message = drill.redacted_error(
        RuntimeError("could not connect with secret-value"),
        DATABASE_URL,
    )
    assert message == "could not connect with ***"
