from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from db.session import transaction_scope
from repositories.runtime import postgres_store_enabled


T = TypeVar("T")


def postgres_transactional(function: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> T:
        if not postgres_store_enabled():
            return await function(*args, **kwargs)
        with transaction_scope():
            return await function(*args, **kwargs)

    return wrapped
