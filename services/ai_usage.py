from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import AsyncIterator

from config_values import env_int


class AIUsageLimitExceeded(Exception):
    def __init__(self, message: str, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class AIUsageLease:
    guard: "AIUsageGuard"
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.guard._concurrency.release()


class AIUsageGuard:
    """Process-local per-actor budgets plus a global generation concurrency cap."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._configured_concurrency = 0
        self._concurrency = asyncio.Semaphore(1)

    def _refresh_concurrency(self) -> None:
        configured = env_int("ALARM_RAG_LLM_GLOBAL_CONCURRENCY", 4, minimum=1, maximum=128)
        if configured != self._configured_concurrency:
            self._configured_concurrency = configured
            self._concurrency = asyncio.Semaphore(configured)

    async def acquire(self, actor_id: str, reserved_tokens: int) -> AIUsageLease:
        now = time.monotonic()
        window = env_int("ALARM_RAG_LLM_BUDGET_WINDOW_SECONDS", 60, minimum=1, maximum=3600)
        request_limit = env_int("ALARM_RAG_LLM_REQUESTS_PER_WINDOW", 20, minimum=1)
        token_limit = env_int("ALARM_RAG_LLM_TOKENS_PER_WINDOW", 32768, minimum=1)
        reserved_tokens = max(int(reserved_tokens), 1)
        with self._lock:
            events = self._events[actor_id]
            while events and events[0][0] <= now - window:
                events.popleft()
            if len(events) >= request_limit:
                raise AIUsageLimitExceeded("AI request rate limit exceeded", retry_after=window)
            if sum(tokens for _, tokens in events) + reserved_tokens > token_limit:
                raise AIUsageLimitExceeded("AI token budget exceeded", retry_after=window)
            events.append((now, reserved_tokens))

        self._refresh_concurrency()
        try:
            await asyncio.wait_for(self._concurrency.acquire(), timeout=0.05)
        except TimeoutError as exc:
            with self._lock:
                stored_events = self._events.get(actor_id)
                if stored_events:
                    try:
                        stored_events.remove((now, reserved_tokens))
                    except ValueError:
                        pass
            raise AIUsageLimitExceeded("AI service is at concurrency capacity", retry_after=1) from exc
        return AIUsageLease(self)


async def release_after_stream(iterator: AsyncIterator, lease: AIUsageLease) -> AsyncIterator:
    try:
        async for item in iterator:
            yield item
    finally:
        lease.release()


ai_usage_guard = AIUsageGuard()


def estimate_reserved_tokens(messages: list, max_output_tokens: int) -> int:
    input_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
    input_tokens = max((input_chars + 3) // 4, 1)
    # Reserve two output generations because the grounding guard can retry once.
    return input_tokens + max(int(max_output_tokens), 1) * 2
