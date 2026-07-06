from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from secret_values import secret_value


class DatabaseNotConfigured(RuntimeError):
    pass


def load_local_env(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DatabaseSettings:
    enabled: bool
    url: URL | None
    pool_size: int
    max_overflow: int
    pool_timeout: int

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        raw_url = os.getenv("DATABASE_URL", "").strip()
        url: URL | None = make_url(raw_url) if raw_url else None
        if url is None and env_bool("POSTGRES_ENABLED"):
            url = URL.create(
                drivername="postgresql+psycopg",
                username=os.getenv("POSTGRES_USER", "alarm_rag"),
                password=secret_value("POSTGRES_PASSWORD"),
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=env_int("POSTGRES_PORT", 5432),
                database=os.getenv("POSTGRES_DB", "alarm_rag"),
            )
        return cls(
            enabled=env_bool("POSTGRES_ENABLED") or url is not None,
            url=url,
            pool_size=max(1, env_int("POSTGRES_POOL_SIZE", 5)),
            max_overflow=max(0, env_int("POSTGRES_MAX_OVERFLOW", 5)),
            pool_timeout=max(1, env_int("POSTGRES_POOL_TIMEOUT", 30)),
        )


def get_database_url(*, hide_password: bool = False) -> str:
    settings = DatabaseSettings.from_env()
    if not settings.enabled or settings.url is None:
        raise DatabaseNotConfigured(
            "PostgreSQL is not configured. Set POSTGRES_ENABLED=true and the POSTGRES_* variables, or DATABASE_URL."
        )
    return settings.url.render_as_string(hide_password=hide_password)


def create_database_engine(url: str | URL | None = None) -> Engine:
    settings = DatabaseSettings.from_env()
    connect_url = url or settings.url
    if connect_url is None:
        raise DatabaseNotConfigured(
            "PostgreSQL is not configured. Set POSTGRES_ENABLED=true and the POSTGRES_* variables, or DATABASE_URL."
        )
    return create_engine(
        connect_url,
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
    )


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_current_session: ContextVar[Session | None] = ContextVar("alarm_rag_database_session", default=None)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_database_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    current = _current_session.get()
    if current is not None:
        yield current
        return
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def transaction_scope() -> Iterator[Session]:
    current = _current_session.get()
    if current is not None:
        yield current
        return
    session = get_session_factory()()
    token = _current_session.set(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _current_session.reset(token)
        session.close()


def database_status() -> dict[str, str]:
    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT current_database(), current_user, current_setting('server_version')")
        ).one()
    return {"database": str(row[0]), "user": str(row[1]), "server_version": str(row[2])}


def reset_database_state_for_tests() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
