from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TypeVar

from sqlalchemy.engine import Connection, Transaction

from backend.rag import config
from backend.repositories.cockroach.connection import (
    bind_connection,
    get_engine,
    reset_connection,
)


T = TypeVar("T")
LOGGER = logging.getLogger(__name__)
_ACTIVE_UOW: ContextVar[CockroachUnitOfWork | None] = ContextVar(
    "agentbook_active_cockroach_uow",
    default=None,
)


def sqlstate_from_exception(error: BaseException) -> str | None:
    """Find a DBAPI SQLSTATE without formatting a possibly sensitive error."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None)
        if isinstance(sqlstate, str):
            return sqlstate
        original = getattr(current, "orig", None)
        if isinstance(original, BaseException) and id(original) not in seen:
            current = original
            continue
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) else None
    return None


class CockroachUnitOfWork:
    """Serializable transaction boundary with callback-based safe retries."""

    def __init__(
        self,
        *,
        maximum_retries: int | None = None,
        base_delay_ms: int | None = None,
    ) -> None:
        self.maximum_retries = (
            config.DATABASE_MAX_TRANSACTION_RETRIES
            if maximum_retries is None
            else maximum_retries
        )
        self.base_delay_ms = (
            config.DATABASE_RETRY_BASE_DELAY_MS
            if base_delay_ms is None
            else base_delay_ms
        )
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self._connection_token: object | None = None
        self._uow_token: Token[CockroachUnitOfWork | None] | None = None
        self._root: CockroachUnitOfWork = self
        self._owner = False
        self._finished = False
        self._after_commit: list[Callable[[], None]] = []
        self._retry_count = 0

    @property
    def retry_count(self) -> int:
        return self._retry_count

    def __enter__(self) -> CockroachUnitOfWork:
        current = _ACTIVE_UOW.get()
        if current is not None:
            self._root = current._root
            self._connection = current._root._connection
            return self
        self._connection = get_engine().connect()
        self._transaction = self._connection.begin()
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
        committed = False
        try:
            if exc_type is None:
                self.commit()
                committed = True
            else:
                self.rollback()
        finally:
            if self._uow_token is not None:
                _ACTIVE_UOW.reset(self._uow_token)
            if self._connection_token is not None:
                reset_connection(self._connection_token)
            if self._connection is not None:
                self._connection.close()
        if committed:
            callbacks = tuple(self._after_commit)
            self._after_commit.clear()
            for callback in callbacks:
                callback()
        return None

    def commit(self) -> None:
        if not self._owner or self._finished:
            return
        assert self._transaction is not None
        self._transaction.commit()
        self._finished = True

    def rollback(self) -> None:
        if not self._owner or self._finished:
            return
        assert self._transaction is not None
        self._transaction.rollback()
        self._finished = True
        self._after_commit.clear()

    def after_commit(self, callback: Callable[[], None]) -> None:
        self._root._after_commit.append(callback)

    def run(self, work: Callable[[CockroachUnitOfWork], T]) -> T:
        """Replay an idempotent database callback on SQLSTATE 40001."""
        current = _ACTIVE_UOW.get()
        if current is not None:
            return work(current)
        original_error: BaseException | None = None
        for attempt in range(self.maximum_retries + 1):
            candidate = CockroachUnitOfWork(
                maximum_retries=self.maximum_retries,
                base_delay_ms=self.base_delay_ms,
            )
            try:
                with candidate:
                    result = work(candidate)
                self._retry_count = attempt
                return result
            except BaseException as error:
                if sqlstate_from_exception(error) != "40001":
                    raise
                if original_error is None:
                    original_error = error
                if attempt >= self.maximum_retries:
                    raise original_error
                self._retry_count = attempt + 1
                LOGGER.warning(
                    "Retrying CockroachDB transaction sqlstate=40001 attempt=%s",
                    attempt + 1,
                )
                base_seconds = self.base_delay_ms / 1000.0
                delay = base_seconds * (2**attempt)
                jitter = random.uniform(0.0, max(base_seconds, 0.001))
                time.sleep(delay + jitter)
        assert original_error is not None
        raise original_error
