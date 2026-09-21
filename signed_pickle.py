"""Authenticated persistence for trusted local pickle indexes.

Pickle remains an internal storage format for the BM25 object, but no bytes are
deserialized until an HMAC made with the deployment-only signing key has been
verified.  This turns an arbitrary file-write primitive into an availability
failure instead of code execution.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

from config_values import env_int
from secret_values import secret_value


SIGNATURE_SUFFIX = ".hmac"
SIGNATURE_PREFIX = "hmac-sha256:"
SIGNING_KEY_ENV = "ALARM_RAG_INDEX_SIGNING_KEY"
SIGNING_KEY_PLACEHOLDER = "replace-with-a-long-random-index-signing-key"


class SignedPickleError(RuntimeError):
    """Raised when an index cannot be authenticated safely."""


def _signing_key() -> bytes:
    value = secret_value(SIGNING_KEY_ENV).strip()
    if value == SIGNING_KEY_PLACEHOLDER or len(value.encode("utf-8")) < 32:
        raise SignedPickleError(f"{SIGNING_KEY_ENV} must contain at least 32 non-placeholder bytes")
    return value.encode("utf-8")


def signature_path(path: str | os.PathLike[str]) -> Path:
    return Path(f"{Path(path)}{SIGNATURE_SUFFIX}")


def _max_bytes() -> int:
    return env_int("ALARM_RAG_INDEX_MAX_MB", 128, minimum=1) * 1024 * 1024


def _signature(data: bytes) -> str:
    digest = hmac.new(_signing_key(), data, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def _write_temp(parent: Path, prefix: str, data: bytes) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=parent)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def dump_signed_pickle(path: str | os.PathLike[str], payload: Any) -> None:
    target = Path(path)
    data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    if len(data) > _max_bytes():
        raise SignedPickleError("BM25 index exceeds ALARM_RAG_INDEX_MAX_MB")
    signature = f"{_signature(data)}\n".encode("ascii")
    data_temp = _write_temp(target.parent, f".{target.name}.", data)
    signature_target = signature_path(target)
    signature_temp = _write_temp(target.parent, f".{signature_target.name}.", signature)
    try:
        os.replace(data_temp, target)
        os.replace(signature_temp, signature_target)
    finally:
        data_temp.unlink(missing_ok=True)
        signature_temp.unlink(missing_ok=True)


def sign_existing_pickle(path: str | os.PathLike[str]) -> Path:
    """Authenticate an existing trusted index without deserializing it."""
    target = Path(path)
    data = target.read_bytes()
    if len(data) > _max_bytes():
        raise SignedPickleError("BM25 index exceeds ALARM_RAG_INDEX_MAX_MB")
    signature_target = signature_path(target)
    temporary = _write_temp(
        target.parent,
        f".{signature_target.name}.",
        f"{_signature(data)}\n".encode("ascii"),
    )
    try:
        os.replace(temporary, signature_target)
    finally:
        temporary.unlink(missing_ok=True)
    return signature_target


def _authenticated_bytes(path: str | os.PathLike[str]) -> bytes:
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise SignedPickleError("Unable to read BM25 index") from exc
    if len(data) > _max_bytes():
        raise SignedPickleError("BM25 index exceeds ALARM_RAG_INDEX_MAX_MB")
    try:
        stored_signature = signature_path(target).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise SignedPickleError("BM25 index signature is missing or unreadable") from exc
    expected_signature = _signature(data)
    if not hmac.compare_digest(stored_signature, expected_signature):
        raise SignedPickleError("BM25 index signature verification failed")
    return data


def verify_signed_pickle(path: str | os.PathLike[str]) -> None:
    """Verify size and HMAC without executing pickle deserialization."""
    _authenticated_bytes(path)


def load_signed_pickle(path: str | os.PathLike[str]) -> Any:
    data = _authenticated_bytes(path)
    try:
        return pickle.loads(data)
    except Exception as exc:
        raise SignedPickleError("Authenticated BM25 index could not be decoded") from exc
