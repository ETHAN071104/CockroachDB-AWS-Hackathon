from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine, make_url

from backend.rag import config


_ACTIVE_CONNECTION: ContextVar[Connection | None] = ContextVar(
    "agentbook_active_cockroach_connection",
    default=None,
)


def cockroach_url():
    """Build a masked SQLAlchemy URL object without copying it into config files."""
    config.validate_persistence_config()
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for CockroachDB.")
    return make_url(config.DATABASE_URL).set(drivername="cockroachdb+psycopg")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        cockroach_url(),
        pool_size=config.DATABASE_POOL_SIZE,
        max_overflow=config.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": config.DATABASE_CONNECT_TIMEOUT},
    )


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()


def active_connection() -> Connection | None:
    return _ACTIVE_CONNECTION.get()


def bind_connection(connection: Connection):
    return _ACTIVE_CONNECTION.set(connection)


def reset_connection(token: object) -> None:
    _ACTIVE_CONNECTION.reset(token)  # type: ignore[arg-type]


@contextmanager
def connection_scope() -> Iterator[Connection]:
    """Join the active Cockroach UnitOfWork or own one short transaction."""
    current = active_connection()
    if current is not None:
        yield current
        return
    with get_engine().begin() as connection:
        yield connection
