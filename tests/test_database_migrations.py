from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.database_check import REQUIRED_TABLES


def test_alembic_has_one_expected_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["20260701_0004"]


def test_revision_chain_is_linear_and_starts_at_base():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == [
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
        "alarm_events",
        "issues",
        "issue_notes",
        "work_orders",
        "audit_events",
        "feedback",
        "documents",
        "document_versions",
        "system_settings",
    }
