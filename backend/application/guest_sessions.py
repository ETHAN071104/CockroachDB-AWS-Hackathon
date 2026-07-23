from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.application.dependencies import ApplicationDependencies
from backend.domain import GuestSession, Workspace
from backend.repositories.interfaces import RepositoryConflictError


TOKEN_PREFIX = "agentbook_guest_v1_"
TOKEN_RANDOM_BYTES = 32
TOKEN_PATTERN = re.compile(
    rf"^{TOKEN_PREFIX}[A-Za-z0-9_-]{{43}}$"
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,200}$")
DEFAULT_WORKSPACE_NAME = "My Study Space"
DEFAULT_SESSION_LABEL = "Private to this browser"


class GuestSessionFailure(RuntimeError):
    """Base class for safe anonymous-session failures."""


class GuestSessionRequired(GuestSessionFailure):
    pass


class GuestSessionInvalid(GuestSessionFailure):
    pass


class GuestSessionExpired(GuestSessionFailure):
    pass


class GuestSessionRevoked(GuestSessionFailure):
    pass


class GuestSessionConflict(GuestSessionFailure):
    pass


class GuestSessionCreationFailed(GuestSessionFailure):
    pass


@dataclass(frozen=True)
class CreatedGuestSession:
    token: str
    session: GuestSession
    workspace: Workspace


@dataclass(frozen=True)
class AuthenticatedGuest:
    session: GuestSession
    workspace: Workspace


class GuestSessionService:
    def __init__(
        self,
        dependencies: ApplicationDependencies,
        *,
        pepper: str,
        ttl_days: int | None = None,
        last_seen_minutes: int = 5,
    ) -> None:
        if len(pepper.encode("utf-8")) < 32:
            raise ValueError(
                "Guest-session pepper must contain at least 32 bytes."
            )
        if ttl_days is not None and not 1 <= ttl_days <= 3650:
            raise ValueError("Guest-session TTL is outside the safe range.")
        if last_seen_minutes < 1:
            raise ValueError("Last-seen update interval must be positive.")
        self.dependencies = dependencies
        self._pepper = pepper.encode("utf-8")
        self.ttl_days = ttl_days
        self.last_seen_minutes = last_seen_minutes

    def create(self, idempotency_key: str) -> CreatedGuestSession:
        cleaned_key = idempotency_key.strip()
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(cleaned_key):
            raise GuestSessionInvalid(
                "The session creation key is invalid."
            )

        token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)
        token_hash = self._hash("agentbook-guest-token-v1", token)
        creation_hash = self._hash(
            "agentbook-guest-creation-v1",
            cleaned_key,
        )
        session_id = str(uuid4())
        workspace_id = str(uuid4())
        now = _utc_now()
        expires_at = (
            now + timedelta(days=self.ttl_days)
            if self.ttl_days is not None
            else None
        )

        def persist(_unit_of_work):
            existing = (
                self.dependencies.guest_sessions.find_by_creation_key_hash(
                    creation_hash
                )
            )
            if existing is not None:
                raise GuestSessionConflict(
                    "This guest-session creation was already committed."
                )
            workspace = self.dependencies.workspaces.create(
                workspace_id,
                DEFAULT_WORKSPACE_NAME,
            )
            session = self.dependencies.guest_sessions.create(
                session_id=session_id,
                workspace_id=workspace_id,
                token_hash=token_hash,
                creation_key_hash=creation_hash,
                created_at=now.isoformat(),
                expires_at=(
                    expires_at.isoformat()
                    if expires_at is not None
                    else None
                ),
                session_label=DEFAULT_SESSION_LABEL,
            )
            return session, workspace

        try:
            session, workspace = self.dependencies.unit_of_work().run(persist)
        except GuestSessionConflict:
            raise
        except RepositoryConflictError as error:
            if (
                self.dependencies.guest_sessions.find_by_creation_key_hash(
                    creation_hash
                )
                is not None
            ):
                raise GuestSessionConflict(
                    "This guest-session creation was already committed."
                ) from error
            raise GuestSessionCreationFailed(
                "The guest session could not be created."
            ) from error
        except Exception as error:
            raise GuestSessionCreationFailed(
                "The guest session could not be created."
            ) from error

        return CreatedGuestSession(
            token=token,
            session=session,
            workspace=workspace,
        )

    def authenticate(self, token: str) -> AuthenticatedGuest:
        cleaned = token.strip()
        if not TOKEN_PATTERN.fullmatch(cleaned):
            raise GuestSessionInvalid("The guest credential is invalid.")
        token_hash = self._hash("agentbook-guest-token-v1", cleaned)
        session = self.dependencies.guest_sessions.resolve_by_token_hash(
            token_hash
        )
        if session is None:
            raise GuestSessionInvalid("The guest credential is invalid.")
        if session.status == "revoked":
            raise GuestSessionRevoked("The guest session was revoked.")
        if session.status == "expired":
            raise GuestSessionExpired("The guest session expired.")
        if session.status != "active":
            raise GuestSessionInvalid("The guest credential is invalid.")

        now = _utc_now()
        if (
            session.expires_at is not None
            and _parse_timestamp(session.expires_at) <= now
        ):
            try:
                self.dependencies.guest_sessions.expire(
                    session.id,
                    expected_version=session.version,
                    expired_at=now.isoformat(),
                )
            except RepositoryConflictError:
                refreshed = self.dependencies.guest_sessions.get(session.id)
                if refreshed is not None and refreshed.status == "revoked":
                    raise GuestSessionRevoked(
                        "The guest session was revoked."
                    )
            raise GuestSessionExpired("The guest session expired.")

        workspace = self.dependencies.workspaces.get(session.workspace_id)
        if workspace is None:
            raise GuestSessionInvalid(
                "The guest session has no available study space."
            )

        update_before = now - timedelta(minutes=self.last_seen_minutes)
        try:
            session = self.dependencies.guest_sessions.update_last_seen(
                session.id,
                seen_at=now.isoformat(),
                update_before=update_before.isoformat(),
            )
        except (KeyError, RepositoryConflictError):
            refreshed = self.dependencies.guest_sessions.get(session.id)
            if refreshed is None or refreshed.status != "active":
                raise GuestSessionInvalid(
                    "The guest session is no longer active."
                )
            session = refreshed
        return AuthenticatedGuest(session=session, workspace=workspace)

    def hash_token_for_test(self, token: str) -> str:
        """Return the lookup digest for security tests, never for API output."""
        return self._hash("agentbook-guest-token-v1", token)

    def _hash(self, domain: str, value: str) -> str:
        return hmac.new(
            self._pepper,
            f"{domain}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
