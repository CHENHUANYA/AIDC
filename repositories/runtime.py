from __future__ import annotations

import os


POSTGRES_STORE_NAMES = {"postgres", "postgresql"}


def configured_data_store() -> str:
    return os.getenv("DATA_STORE", "json").strip().lower() or "json"


def postgres_store_enabled() -> bool:
    return configured_data_store() in POSTGRES_STORE_NAMES


def require_known_data_store() -> str:
    store = configured_data_store()
    if store not in {"json", *POSTGRES_STORE_NAMES}:
        raise RuntimeError(f"Unsupported DATA_STORE: {store}")
    return store
