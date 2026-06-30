import asyncio
import inspect
from unittest.mock import MagicMock, patch

from db.session import session_scope, transaction_scope
from services.transactions import postgres_transactional


def test_nested_session_scope_reuses_unit_of_work_session_and_commits_once():
    session = MagicMock()
    factory = MagicMock(return_value=session)

    with patch("db.session.get_session_factory", return_value=factory):
        with transaction_scope() as outer:
            with session_scope() as inner:
                assert inner is outer

    factory.assert_called_once_with()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_transaction_scope_rolls_back_once_on_nested_failure():
    session = MagicMock()
    factory = MagicMock(return_value=session)

    with patch("db.session.get_session_factory", return_value=factory):
        try:
            with transaction_scope():
                with session_scope():
                    raise RuntimeError("rollback")
        except RuntimeError:
            pass

    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()


def test_postgres_transactional_preserves_signature_and_skips_db_for_json():
    async def handler(value: str, count: int = 1) -> str:
        return value * count

    wrapped = postgres_transactional(handler)

    assert inspect.signature(wrapped) == inspect.signature(handler)
    with patch("services.transactions.postgres_store_enabled", return_value=False):
        assert asyncio.run(wrapped("a", count=2)) == "aa"
