from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.database_check import REQUIRED_TABLES


def test_alembic_has_one_expected_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["20260713_0006"]


def test_revision_chain_is_linear_and_starts_at_base():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == [
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


def test_rag_answer_state_revision_has_default_and_constraint():
    revision = Path("migrations/versions/20260713_0006_rag_answer_state.py").read_text(encoding="utf-8")

    assert 'server_default="complete"' in revision
    assert "answer_state IN ('complete','fallback','unavailable')" in revision
