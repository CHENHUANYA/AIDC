from __future__ import annotations

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
    actor_id: str
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.guard._release(self.actor_id)


class AIUsageGuard:
    """Process-local per-actor budgets plus a global generation concurrency cap."""

    def __init__(self, env_prefix: str = "ALARM_RAG_LLM") -> None:
        self._env_prefix = env_prefix.rstrip("_")
        self._lock = threading.Lock()
        self._events: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._active_total = 0
        self._active_by_actor: dict[str, int] = defaultdict(int)

    def _release(self, actor_id: str) -> None:
        with self._lock:
            self._active_total = max(self._active_total - 1, 0)
            remaining = self._active_by_actor.get(actor_id, 0) - 1
            if remaining > 0:
                self._active_by_actor[actor_id] = remaining
            else:
                self._active_by_actor.pop(actor_id, None)

    async def acquire(self, actor_id: str, reserved_tokens: int) -> AIUsageLease:
        now = time.monotonic()
        window = env_int(f"{self._env_prefix}_BUDGET_WINDOW_SECONDS", 60, minimum=1, maximum=3600)
        request_limit = env_int(f"{self._env_prefix}_REQUESTS_PER_WINDOW", 20, minimum=1)
        token_limit = env_int(f"{self._env_prefix}_TOKENS_PER_WINDOW", 524288, minimum=1)
        global_concurrency = env_int(f"{self._env_prefix}_GLOBAL_CONCURRENCY", 4, minimum=1, maximum=128)
        per_actor_concurrency = env_int(
            f"{self._env_prefix}_MAX_ACTIVE_PER_ACTOR",
            max((global_concurrency + 1) // 2, 1),
            minimum=1,
            maximum=global_concurrency,
        )
        reserved_tokens = max(int(reserved_tokens), 1)
        with self._lock:
            events = self._events[actor_id]
            while events and events[0][0] <= now - window:
                events.popleft()
            if len(events) >= request_limit:
                raise AIUsageLimitExceeded("AI request rate limit exceeded", retry_after=window)
            if sum(tokens for _, tokens in events) + reserved_tokens > token_limit:
                raise AIUsageLimitExceeded("AI token budget exceeded", retry_after=window)
            if self._active_by_actor[actor_id] >= per_actor_concurrency:
                raise AIUsageLimitExceeded("AI per-user concurrency limit exceeded", retry_after=1)
            if self._active_total >= global_concurrency:
                raise AIUsageLimitExceeded("AI service is at concurrency capacity", retry_after=1)
            events.append((now, reserved_tokens))
            self._active_total += 1
            self._active_by_actor[actor_id] += 1
        return AIUsageLease(self, actor_id)


async def release_after_stream(iterator: AsyncIterator, lease: AIUsageLease) -> AsyncIterator:
    try:
        async for item in iterator:
            yield item
    finally:
        lease.release()


ai_usage_guard = AIUsageGuard()


def estimate_reserved_tokens(
    messages: list,
    max_output_tokens: int,
    *,
    additional_utf8_bytes: int = 0,
) -> int:
    # One token per UTF-8 byte is intentionally conservative for CJK, emoji,
    # system prompts and retrieval context that character/4 estimates miss.
    input_bytes = sum(
        len(str(getattr(message, "content", "") or "").encode("utf-8"))
        for message in messages
    ) + max(int(additional_utf8_bytes), 0)
    input_tokens = max(input_bytes, 1)
    # Reserve two output generations because the grounding guard can retry once.
    return input_tokens + max(int(max_output_tokens), 1) * 2
