from __future__ import annotations

import os
from pathlib import Path


class SecretConfigError(RuntimeError):
    """Raised when a secret environment/file contract is unsafe or invalid."""


def secret_value(name: str, default: str = "") -> str:
    """Read NAME or NAME_FILE without silently choosing between both sources."""
    value = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE", "").strip()

    if value and file_name:
        raise SecretConfigError(f"Both {name} and {name}_FILE are set; configure exactly one")
    if not file_name:
        return default if value is None else value

    path = Path(file_name)
    try:
        file_value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SecretConfigError(f"Unable to read {name}_FILE") from exc

    if file_value.endswith("\r\n"):
        file_value = file_value[:-2]
    elif file_value.endswith("\n"):
        file_value = file_value[:-1]
    if not file_value:
        raise SecretConfigError(f"{name}_FILE is empty")
    if "\n" in file_value or "\r" in file_value or "\x00" in file_value:
        raise SecretConfigError(f"{name}_FILE must contain exactly one non-empty text line")
    return file_value
