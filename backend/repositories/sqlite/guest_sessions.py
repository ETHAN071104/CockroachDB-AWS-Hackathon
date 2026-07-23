from __future__ import annotations

from typing import Any

from backend.domain import GuestSession
from backend.repositories.interfaces import RepositoryConflictError
from backend.repositories.sqlite.foundation import (
    _connection_scope,
    initialize_foundation_schema,
)


class SQLiteGuestSessionRepository:
    """SQLite compatibility adapter for isolated tests and legacy development."""

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        token_hash: str,
        creation_key_hash: str,
        created_at: str,
        expires_at: str | None,
        session_label: str | None,
    ) -> GuestSession:
        initialize_foundation_schema()
        try:
            with _connection_scope() as connection:
                connection.execute(
                    """
                    INSERT INTO guest_sessions (
                        id, workspace_id, token_hash, creation_key_hash,
                        status, created_at, updated_at, last_seen_at,
                        expires_at, revoked_at, version, session_label
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, ?, NULL, 1, ?)
                    """,
                    (
                        session_id,
                        workspace_id,
                        token_hash,
                        creation_key_hash,
                        created_at,
                        created_at,
                        expires_at,
                        session_label,
                    ),
                )
        except Exception as error:
            raise RepositoryConflictError(
                "Guest session already exists."
            ) from error
        session = self.get(session_id)
        assert session is not None
        return session

    def resolve_by_token_hash(self, token_hash: str) -> GuestSession | None:
        return self._one("token_hash = ?", (token_hash,))

    def find_by_creation_key_hash(
        self,
        creation_key_hash: str,
    ) -> GuestSession | None:
        return self._one("creation_key_hash = ?", (creation_key_hash,))

    def get(self, session_id: str) -> GuestSession | None:
        return self._one("id = ?", (session_id,))

    def update_last_seen(
        self,
        session_id: str,
        *,
        seen_at: str,
        update_before: str,
    ) -> GuestSession:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            connection.execute(
                """
                UPDATE guest_sessions
                SET last_seen_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND status = 'active'
                  AND (last_seen_at IS NULL OR last_seen_at <= ?)
                """,
                (seen_at, seen_at, session_id, update_before),
            )
        session = self.get(session_id)
        if session is None:
            raise KeyError("Guest session does not exist.")
        return session

    def revoke(
        self,
        session_id: str,
        *,
        expected_version: int,
        revoked_at: str,
    ) -> GuestSession:
        return self._transition(
            session_id,
            expected_version=expected_version,
            status="revoked",
            changed_at=revoked_at,
        )

    def expire(
        self,
        session_id: str,
        *,
        expected_version: int,
        expired_at: str,
    ) -> GuestSession:
        return self._transition(
            session_id,
            expected_version=expected_version,
            status="expired",
            changed_at=expired_at,
        )

    def list_internal(self) -> list[GuestSession]:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            rows = connection.execute(
                """
                SELECT id, workspace_id, status, created_at, updated_at,
                       last_seen_at, expires_at, revoked_at, version,
                       session_label
                FROM guest_sessions
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return [_guest_session(row) for row in rows]

    def _transition(
        self,
        session_id: str,
        *,
        expected_version: int,
        status: str,
        changed_at: str,
    ) -> GuestSession:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            result = connection.execute(
                """
                UPDATE guest_sessions
                SET status = ?, revoked_at = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ? AND status = 'active' AND version = ?
                """,
                (
                    status,
                    changed_at if status == "revoked" else None,
                    changed_at,
                    session_id,
                    int(expected_version),
                ),
            )
            if result.rowcount != 1:
                raise RepositoryConflictError(
                    "Guest session changed or is no longer active."
                )
        session = self.get(session_id)
        assert session is not None
        return session

    def _one(
        self,
        clause: str,
        parameters: tuple[object, ...],
    ) -> GuestSession | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                """
                SELECT id, workspace_id, status, created_at, updated_at,
                       last_seen_at, expires_at, revoked_at, version,
                       session_label
                FROM guest_sessions
                WHERE """ + clause,
                parameters,
            ).fetchone()
        return _guest_session(row) if row is not None else None


def _guest_session(row: Any) -> GuestSession:
    return GuestSession(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_seen_at=(
            str(row["last_seen_at"])
            if row["last_seen_at"] is not None
            else None
        ),
        expires_at=(
            str(row["expires_at"])
            if row["expires_at"] is not None
            else None
        ),
        revoked_at=(
            str(row["revoked_at"])
            if row["revoked_at"] is not None
            else None
        ),
        version=int(row["version"]),
        session_label=(
            str(row["session_label"])
            if row["session_label"] is not None
            else None
        ),
    )
