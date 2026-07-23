from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.domain import GuestSession
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import iso, timestamp
from backend.repositories.interfaces import RepositoryConflictError


class CockroachGuestSessionRepository:
    """Resolve opaque credential hashes without exposing stored hash values."""

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
        created = timestamp(created_at)
        try:
            with connection_scope() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO guest_sessions (
                            id, workspace_id, token_hash, creation_key_hash,
                            status, created_at, updated_at, last_seen_at,
                            expires_at, revoked_at, version, session_label
                        ) VALUES (
                            :id, :workspace_id, :token_hash, :creation_key_hash,
                            'active', :created_at, :created_at, NULL,
                            :expires_at, NULL, 1, :session_label
                        )
                        """
                    ),
                    {
                        "id": UUID(session_id),
                        "workspace_id": UUID(workspace_id),
                        "token_hash": token_hash,
                        "creation_key_hash": creation_key_hash,
                        "created_at": created,
                        "expires_at": (
                            timestamp(expires_at)
                            if expires_at is not None
                            else None
                        ),
                        "session_label": session_label,
                    },
                )
        except IntegrityError as error:
            raise RepositoryConflictError(
                "Guest session already exists."
            ) from error
        session = self.get(session_id)
        assert session is not None
        return session

    def resolve_by_token_hash(self, token_hash: str) -> GuestSession | None:
        return self._one("token_hash=:token_hash", {"token_hash": token_hash})

    def find_by_creation_key_hash(
        self,
        creation_key_hash: str,
    ) -> GuestSession | None:
        return self._one(
            "creation_key_hash=:creation_key_hash",
            {"creation_key_hash": creation_key_hash},
        )

    def get(self, session_id: str) -> GuestSession | None:
        return self._one("id=:id", {"id": UUID(session_id)})

    def update_last_seen(
        self,
        session_id: str,
        *,
        seen_at: str,
        update_before: str,
    ) -> GuestSession:
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    UPDATE guest_sessions
                    SET last_seen_at=:seen_at, updated_at=:seen_at,
                        version=version+1
                    WHERE id=:id AND status='active'
                      AND (last_seen_at IS NULL OR last_seen_at <= :update_before)
                    """
                ),
                {
                    "id": UUID(session_id),
                    "seen_at": timestamp(seen_at),
                    "update_before": timestamp(update_before),
                },
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
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE guest_sessions
                    SET status='revoked', revoked_at=:revoked_at,
                        updated_at=:revoked_at, version=version+1
                    WHERE id=:id AND status='active' AND version=:version
                    """
                ),
                {
                    "id": UUID(session_id),
                    "version": int(expected_version),
                    "revoked_at": timestamp(revoked_at),
                },
            )
            if result.rowcount != 1:
                raise RepositoryConflictError(
                    "Guest session changed or is no longer active."
                )
        session = self.get(session_id)
        assert session is not None
        return session

    def expire(
        self,
        session_id: str,
        *,
        expected_version: int,
        expired_at: str,
    ) -> GuestSession:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE guest_sessions
                    SET status='expired', updated_at=:expired_at,
                        version=version+1
                    WHERE id=:id AND status='active' AND version=:version
                    """
                ),
                {
                    "id": UUID(session_id),
                    "version": int(expected_version),
                    "expired_at": timestamp(expired_at),
                },
            )
            if result.rowcount != 1:
                raise RepositoryConflictError(
                    "Guest session changed or is no longer active."
                )
        session = self.get(session_id)
        assert session is not None
        return session

    def list_internal(self) -> list[GuestSession]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, workspace_id, status, created_at, updated_at,
                           last_seen_at, expires_at, revoked_at, version,
                           session_label
                    FROM guest_sessions
                    ORDER BY created_at DESC, id DESC
                    """
                )
            ).mappings().all()
        return [_guest_session(row) for row in rows]

    def _one(
        self,
        clause: str,
        parameters: dict[str, object],
    ) -> GuestSession | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, workspace_id, status, created_at, updated_at,
                           last_seen_at, expires_at, revoked_at, version,
                           session_label
                    FROM guest_sessions
                    WHERE """ + clause
                ),
                parameters,
            ).mappings().one_or_none()
        return _guest_session(row) if row is not None else None


def _guest_session(row: Any) -> GuestSession:
    return GuestSession(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        status=str(row["status"]),
        created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]),
        last_seen_at=(
            iso(row["last_seen_at"])
            if row["last_seen_at"] is not None
            else None
        ),
        expires_at=(
            iso(row["expires_at"])
            if row["expires_at"] is not None
            else None
        ),
        revoked_at=(
            iso(row["revoked_at"])
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
