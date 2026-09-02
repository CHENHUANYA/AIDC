from __future__ import annotations

import json
import os
import tempfile
import threading
import asyncio
from contextlib import asynccontextmanager
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_lock_registry_guard = threading.Lock()
_lock_registry: dict[str, threading.RLock] = {}
_async_lock_registry: dict[str, asyncio.Lock] = {}


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _lock_registry_guard:
        return _lock_registry.setdefault(key, threading.RLock())


@contextmanager
def exclusive_file_lock(path: str | Path):
    """Serialize a JSON transaction across threads and application processes."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock(lock_path):
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@asynccontextmanager
async def async_exclusive_file_lock(path: str | Path):
    """Acquire the blocking OS lock off the event-loop thread."""
    lock_path = Path(path)
    key = str(lock_path.resolve())
    with _lock_registry_guard:
        task_lock = _async_lock_registry.setdefault(key, asyncio.Lock())

    def acquire_handle():
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        return handle

    def release_handle(handle) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            handle.close()

    async with task_lock:
        handle = await asyncio.to_thread(acquire_handle)
        try:
            yield
        finally:
            await asyncio.to_thread(release_handle, handle)


def write_json_atomic(path: str | Path, payload: Any) -> None:
    """Serialize JSON beside its destination, fsync it, then atomically publish it."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        with temporary.open("r", encoding="utf-8") as staged:
            json.load(staged)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
