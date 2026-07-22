from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


_ACTIVE_CONNECTION: ContextVar[sqlite3.Connection | None] = ContextVar(
    "agentbook_active_sqlite_connection",
    default=None,
)


def active_connection() -> sqlite3.Connection | None:
    return _ACTIVE_CONNECTION.get()


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def open_connection(database_path: Path) -> sqlite3.Connection:
    return configure_connection(sqlite3.connect(database_path, timeout=5.0))


@contextmanager
def connection_scope(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Join an active UnitOfWork or own one short SQLite transaction."""
    current = active_connection()
    if current is not None:
        yield current
        return

    connection = open_connection(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def bind_connection(connection: sqlite3.Connection):
    return _ACTIVE_CONNECTION.set(connection)


def reset_connection(token: object) -> None:
    _ACTIVE_CONNECTION.reset(token)  # type: ignore[arg-type]
