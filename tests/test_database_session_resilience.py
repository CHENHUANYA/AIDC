from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import URL

from db import session as database_session


def test_load_local_env_handles_missing_comments_quotes_and_existing_values(tmp_path, monkeypatch):
    monkeypatch.setenv("EXISTING_SETTING", "keep")
    database_session.load_local_env(tmp_path / "missing.env")
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\nINVALID\nQUOTED_SETTING="value"\nSINGLE_SETTING=\'other\'\nEXISTING_SETTING=replace\n',
        encoding="utf-8",
    )

    database_session.load_local_env(env_file)

    assert database_session.os.environ["QUOTED_SETTING"] == "value"
    assert database_session.os.environ["SINGLE_SETTING"] == "other"
    assert database_session.os.environ["EXISTING_SETTING"] == "keep"


def test_database_environment_helpers_use_defaults_for_missing_and_invalid_values(monkeypatch):
    monkeypatch.delenv("BOOL_SETTING", raising=False)
    monkeypatch.setenv("BAD_INTEGER", "not-an-int")
    assert database_session.env_bool("BOOL_SETTING", True) is True
    monkeypatch.setenv("BOOL_SETTING", "YES")
    assert database_session.env_bool("BOOL_SETTING") is True
    monkeypatch.setenv("BOOL_SETTING", "no")
    assert database_session.env_bool("BOOL_SETTING", True) is False
    assert database_session.env_int("BAD_INTEGER", 12) == 12


def test_database_settings_accept_url_and_bound_pool_values(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@db/example")
    monkeypatch.setenv("POSTGRES_POOL_SIZE", "0")
    monkeypatch.setenv("POSTGRES_MAX_OVERFLOW", "-1")
    monkeypatch.setenv("POSTGRES_POOL_TIMEOUT", "0")

    settings = database_session.DatabaseSettings.from_env()

    assert settings.enabled is True
    assert settings.url is not None
    assert settings.url.database == "example"
    assert settings.pool_size == 1
    assert settings.max_overflow == 0
    assert settings.pool_timeout == 1


def test_create_database_engine_validates_configuration_and_forwards_pool_settings(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_ENABLED", raising=False)
    with pytest.raises(database_session.DatabaseNotConfigured):
        database_session.create_database_engine()

    engine = MagicMock()
    url = URL.create("postgresql+psycopg", username="user", password="secret", host="db", database="example")
    with patch.object(database_session, "create_engine", return_value=engine) as create_engine:
        assert database_session.create_database_engine(url) is engine
    create_engine.assert_called_once_with(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
    )


def test_engine_and_session_factory_are_cached(monkeypatch):
    engine = MagicMock()
    factory = MagicMock()
    monkeypatch.setattr(database_session, "_engine", None)
    monkeypatch.setattr(database_session, "_session_factory", None)
    with (
        patch.object(database_session, "create_database_engine", return_value=engine) as create_engine,
        patch.object(database_session, "sessionmaker", return_value=factory) as sessionmaker,
    ):
        assert database_session.get_engine() is engine
        assert database_session.get_engine() is engine
        assert database_session.get_session_factory() is factory
        assert database_session.get_session_factory() is factory
    create_engine.assert_called_once_with()
    sessionmaker.assert_called_once_with(bind=engine, expire_on_commit=False, autoflush=False)


def test_session_scope_commits_and_rolls_back_independent_sessions():
    successful = MagicMock()
    failing = MagicMock()
    factory = MagicMock(side_effect=[successful, failing])
    with patch.object(database_session, "get_session_factory", return_value=factory):
        with database_session.session_scope() as yielded:
            assert yielded is successful
        with pytest.raises(RuntimeError, match="fail"):
            with database_session.session_scope() as yielded:
                assert yielded is failing
                raise RuntimeError("fail")

    successful.commit.assert_called_once_with()
    successful.rollback.assert_not_called()
    successful.close.assert_called_once_with()
    failing.commit.assert_not_called()
    failing.rollback.assert_called_once_with()
    failing.close.assert_called_once_with()


def test_database_status_and_reset_hide_no_state(monkeypatch):
    row = ("alarm_rag", "service_user", "17.1")
    connection = MagicMock()
    connection.execute.return_value.one.return_value = row
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(database_session, "_engine", engine)
    monkeypatch.setattr(database_session, "_session_factory", MagicMock())

    assert database_session.database_status() == {
        "database": "alarm_rag",
        "user": "service_user",
        "server_version": "17.1",
    }
    database_session.reset_database_state_for_tests()

    engine.dispose.assert_called_once_with()
    assert database_session._engine is None
    assert database_session._session_factory is None
