from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from types import TracebackType

from backend.repositories.sqlite.connection import (
    active_connection,
    bind_connection,
    open_connection,
    reset_connection,
)


_ACTIVE_UOW: ContextVar[SQLiteUnitOfWork | None] = ContextVar(
    "agentbook_active_sqlite_uow",
    default=None,
)


class SQLiteUnitOfWork:
    """SQLite transaction boundary with deferred post-commit work."""

    def __init__(
        self,
        database_path: Callable[[], Path],
        ensure_parent: Callable[[], None] | None = None,
    ) -> None:
        self._database_path = database_path
        self._ensure_parent = ensure_parent
        self._connection = None
        self._connection_token: object | None = None
        self._uow_token: Token[SQLiteUnitOfWork | None] | None = None
        self._root: SQLiteUnitOfWork = self
        self._owner = False
        self._finished = False
        self._after_commit: list[Callable[[], None]] = []

    def __enter__(self) -> SQLiteUnitOfWork:
        current = _ACTIVE_UOW.get()
        if current is not None:
            self._root = current._root
            self._connection = active_connection()
            return self

        if self._ensure_parent is not None:
            self._ensure_parent()
        self._connection = open_connection(self._database_path())
        self._connection.execute("BEGIN IMMEDIATE")
        self._connection_token = bind_connection(self._connection)
        self._uow_token = _ACTIVE_UOW.set(self)
        self._owner = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if not self._owner:
            return None

        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            if self._uow_token is not None:
                _ACTIVE_UOW.reset(self._uow_token)
            if self._connection_token is not None:
                reset_connection(self._connection_token)
            if self._connection is not None:
                self._connection.close()

        if exc_type is None:
            callbacks = tuple(self._after_commit)
            self._after_commit.clear()
            for callback in callbacks:
                callback()
        return None

    def commit(self) -> None:
        if not self._owner or self._finished:
            return
        assert self._connection is not None
        self._connection.commit()
        self._finished = True

    def rollback(self) -> None:
        if not self._owner or self._finished:
            return
        assert self._connection is not None
        self._connection.rollback()
        self._finished = True
        self._after_commit.clear()

    def after_commit(self, callback: Callable[[], None]) -> None:
        self._root._after_commit.append(callback)
