import os
from unittest.mock import patch

from repositories.postgres_auth import token_digest
from repositories.runtime import configured_data_store, postgres_store_enabled, require_known_data_store


def test_json_is_default_store():
    with patch.dict(os.environ, {}, clear=True):
        assert configured_data_store() == "json"
        assert postgres_store_enabled() is False


def test_postgresql_aliases_enable_repository_store():
    for value in ("postgres", "postgresql", " PostgreSQL "):
        with patch.dict(os.environ, {"DATA_STORE": value}, clear=True):
            assert postgres_store_enabled() is True
            assert require_known_data_store() == value.strip().lower()


def test_unknown_store_is_rejected():
    with patch.dict(os.environ, {"DATA_STORE": "mystery"}, clear=True):
        try:
            require_known_data_store()
        except RuntimeError as exc:
            assert "Unsupported DATA_STORE" in str(exc)
        else:
            raise AssertionError("unknown store was accepted")


def test_session_token_digest_is_deterministic_and_non_reversible_value():
    token = "raw-secret-token"

    assert token_digest(token) == token_digest(token)
    assert token_digest(token) != token
    assert len(token_digest(token)) == 64
