from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class LoginThrottleLimits:
    failure_limit: int
    failure_window_seconds: int
    lockout_seconds: int
    max_keys: int
    prune_interval_seconds: int


@dataclass
class LoginThrottleState:
    failures: dict[str, deque[float]]
    lockouts: dict[str, float]
    last_seen: dict[str, float]
    last_pruned_at: float = 0.0


def normalize_login_key(username: str) -> str:
    return username.strip().casefold() or "<empty>"


def discard_key(state: LoginThrottleState, key: str) -> None:
    state.failures.pop(key, None)
    state.lockouts.pop(key, None)
    state.last_seen.pop(key, None)


def prune_state(
    state: LoginThrottleState,
    limits: LoginThrottleLimits,
    current: float,
    *,
    incoming_key: str | None = None,
) -> None:
    known_keys = set(state.failures) | set(state.lockouts)
    should_prune = (
        current - state.last_pruned_at >= limits.prune_interval_seconds
        or (incoming_key not in known_keys and len(known_keys) >= limits.max_keys)
    )
    if not should_prune:
        return

    cutoff = current - limits.failure_window_seconds
    for key, failures in list(state.failures.items()):
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            state.failures.pop(key, None)
    for key, locked_until in list(state.lockouts.items()):
        if locked_until <= current:
            state.lockouts.pop(key, None)

    active_keys = set(state.failures) | set(state.lockouts)
    for key in list(state.last_seen):
        if key not in active_keys:
            state.last_seen.pop(key, None)

    if incoming_key not in active_keys and len(active_keys) >= limits.max_keys:
        oldest_key = min(active_keys, key=lambda key: state.last_seen.get(key, 0.0))
        discard_key(state, oldest_key)
    state.last_pruned_at = current


def retry_after(
    state: LoginThrottleState,
    limits: LoginThrottleLimits,
    key: str,
    current: float,
) -> int:
    prune_state(state, limits, current)
    locked_until = state.lockouts.get(key, 0.0)
    if locked_until > current:
        state.last_seen[key] = current
        return max(ceil(locked_until - current), 1)
    state.lockouts.pop(key, None)
    failures = state.failures.get(key)
    if failures is None:
        state.last_seen.pop(key, None)
        return 0
    cutoff = current - limits.failure_window_seconds
    while failures and failures[0] <= cutoff:
        failures.popleft()
    if not failures:
        discard_key(state, key)
    else:
        state.last_seen[key] = current
    return 0


def record_failure(
    state: LoginThrottleState,
    limits: LoginThrottleLimits,
    key: str,
    current: float,
) -> int:
    prune_state(state, limits, current, incoming_key=key)
    failures = state.failures.setdefault(key, deque())
    state.last_seen[key] = current
    cutoff = current - limits.failure_window_seconds
    while failures and failures[0] <= cutoff:
        failures.popleft()
    failures.append(current)
    if len(failures) < limits.failure_limit:
        return 0
    state.lockouts[key] = current + limits.lockout_seconds
    failures.clear()
    return limits.lockout_seconds
