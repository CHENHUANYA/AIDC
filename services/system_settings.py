from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from repositories.runtime import postgres_store_enabled


DEFAULT_SETTINGS: dict[str, Any] = {
    "default_manual": "808d",
    "session_hours": 12,
    # Reopening a closed issue is a privileged workflow transition.  Missing or
    # unreadable configuration must not silently grant it.
    "allow_operator_reopen": False,
    "updated_by": "",
    "updated_at": "",
    "revision": "",
}


class SettingsReader(Protocol):
    def load_all(self) -> dict[str, Any]: ...


def session_hours_override(value: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = int(normalized)
    except ValueError:
        return None
    return min(max(parsed, 1), 72)


def load_effective_settings(
    settings_path: str | Path,
    *,
    postgres_reader: SettingsReader | None = None,
    use_postgres: bool | None = None,
) -> dict[str, Any]:
    """Load the effective settings from the configured backend.

    Invalid or unavailable JSON falls back to security-conscious defaults.
    PostgreSQL errors are deliberately propagated so callers can fail closed or
    return a service error instead of applying a different backend's policy.
    """
    postgres = postgres_store_enabled() if use_postgres is None else use_postgres
    if postgres:
        if postgres_reader is None:
            raise RuntimeError("PostgreSQL settings reader is required")
        payload = postgres_reader.load_all()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid PostgreSQL settings payload")
        return {**DEFAULT_SETTINGS, **payload}

    try:
        with Path(settings_path).open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(payload, dict):
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **payload}
