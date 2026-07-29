from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.database_check import REQUIRED_INDEXES, REQUIRED_TABLES, missing_required_indexes


def test_alembic_has_one_expected_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["20260729_0007"]


def test_revision_chain_is_linear_and_starts_at_base():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == [
        "20260729_0007",
        "20260713_0006",
        "20260712_0005",
        "20260701_0004",
        "20260630_0003",
        "20260630_0002",
        "20260630_0001",
    ]
    assert revisions[-1].down_revision is None


def test_database_check_requires_all_model_tables():
    assert REQUIRED_TABLES == {
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


def test_database_check_requires_operational_indexes() -> None:
    assert REQUIRED_INDEXES["login_throttles"] == {
        "ix_login_throttles_updated_at",
        "ix_login_throttles_locked_until",
    }
    assert REQUIRED_INDEXES["issues"] == {
        "ix_issues_status_created",
        "ix_issues_machine_status",
        "ix_issues_assignee_status",
    }
    assert missing_required_indexes(REQUIRED_INDEXES) == {}

    incomplete = {table: set(indexes) for table, indexes in REQUIRED_INDEXES.items()}
    incomplete["login_throttles"].remove("ix_login_throttles_updated_at")
    assert missing_required_indexes(incomplete) == {
        "login_throttles": ["ix_login_throttles_updated_at"],
    }


def test_rag_answer_state_revision_has_default_and_constraint():
    revision = Path("migrations/versions/20260713_0006_rag_answer_state.py").read_text(encoding="utf-8")

    assert 'server_default="complete"' in revision
    assert "answer_state IN ('complete','fallback','unavailable')" in revision


def test_login_throttle_revision_hashes_keys_and_supports_cleanup_indexes():
    revision = Path("migrations/versions/20260729_0007_login_throttles.py").read_text(encoding="utf-8")

    assert 'sa.Column("key_hash", sa.String(length=64), nullable=False)' in revision
    assert "failure_count >= 0" in revision
    assert "ix_login_throttles_updated_at" in revision
    assert "ix_login_throttles_locked_until" in revision
