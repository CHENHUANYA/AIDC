import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from db.base import Base
from db.session import DatabaseNotConfigured, DatabaseSettings, get_database_url
import db.models  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
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


def test_model_metadata_contains_phase1_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_business_keys_and_issue_work_order_link_are_unique():
    expected = {
        "users": {("user_id",)},
        "issues": {("issue_no",)},
        "work_orders": {("work_order_no",), ("issue_id",)},
        "documents": {("collection", "document_key")},
    }
    for table_name, required in expected.items():
        table = Base.metadata.tables[table_name]
        unique_sets = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert required <= unique_sets


def test_core_foreign_keys_and_checks_are_declared():
    work_orders = Base.metadata.tables["work_orders"]
    foreign_targets = {
        element.target_fullname
        for constraint in work_orders.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        for element in constraint.elements
    }
    checks = {
        constraint.name
        for constraint in work_orders.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "issues.id" in foreign_targets
    assert "users.id" in foreign_targets
    assert {"ck_work_orders_valid_status", "ck_work_orders_valid_priority", "ck_work_orders_positive_version"} <= checks


def test_database_url_uses_components_without_exposing_password():
    env = {
        "POSTGRES_ENABLED": "true",
        "POSTGRES_USER": "alarm_rag",
        "POSTGRES_PASSWORD": "p@ss:/word",
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "alarm_rag",
    }
    with patch.dict(os.environ, env, clear=True):
        settings = DatabaseSettings.from_env()
        assert settings.url is not None
        assert settings.url.password == "p@ss:/word"
        assert "***" in get_database_url(hide_password=True)
        assert "p@ss:/word" not in get_database_url(hide_password=True)


def test_database_configuration_is_opt_in():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(DatabaseNotConfigured):
            get_database_url()


def test_alembic_revision_and_compose_overlay_exist():
    assert (ROOT / "alembic.ini").exists()
    assert (ROOT / "migrations" / "versions" / "20260630_0001_initial_transactional_schema.py").exists()
    compose = (ROOT / "docker-compose.postgresql.yml").read_text(encoding="utf-8")
    assert "postgres:17.10" in compose
    assert "condition: service_healthy" in compose
    assert "127.0.0.1" in compose
