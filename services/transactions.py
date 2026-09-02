from __future__ import annotations

from functools import wraps
import os
from typing import Any, Awaitable, Callable, TypeVar

from db.session import transaction_scope
from repositories.runtime import postgres_store_enabled
from services.json_file_store import async_exclusive_file_lock


T = TypeVar("T")


def postgres_transactional(function: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> T:
        if not postgres_store_enabled():
            return await function(*args, **kwargs)
        with transaction_scope():
            return await function(*args, **kwargs)

    return wrapped


def json_transactional(db_dir: Callable[[], str]):
    """Hold one cross-process lock for a complete JSON read/modify/write route."""
    def decorate(function: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(function)
        async def wrapped(*args: Any, **kwargs: Any) -> T:
            if postgres_store_enabled():
                return await function(*args, **kwargs)
            async with async_exclusive_file_lock(os.path.join(db_dir(), ".runtime-json.lock")):
                return await function(*args, **kwargs)

        return wrapped

    return decorate
