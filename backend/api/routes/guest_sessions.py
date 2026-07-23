from __future__ import annotations

import threading
import time

from collections import deque
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from backend.api.errors import ApiError
from backend.api.guest_auth import (
    guest_session_service,
    require_guest_principal,
)
from backend.api.schemas import (
    GuestSessionCreateResponse,
    GuestSessionInspectResponse,
    GuestSessionMetadataResponse,
    GuestWorkspaceResponse,
)
from backend.application.guest_sessions import (
    AuthenticatedGuest,
    GuestSessionConflict,
    GuestSessionCreationFailed,
    GuestSessionInvalid,
)
from backend.rag import config


router = APIRouter(prefix="/api/guest-session", tags=["guest-session"])


class CreationRateLimiter:
    """Instance-wide demo limiter; distributed deployments need an upstream limit."""

    def __init__(self) -> None:
        self._attempts: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, maximum: int) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            while self._attempts and self._attempts[0] <= cutoff:
                self._attempts.popleft()
            if len(self._attempts) >= maximum:
                return False
            self._attempts.append(now)
            return True

    def clear_for_test(self) -> None:
        with self._lock:
            self._attempts.clear()


CREATION_LIMITER = CreationRateLimiter()


@router.post(
    "",
    response_model=GuestSessionCreateResponse,
    status_code=201,
)
def create_guest_session(
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=200),
    ] = None,
) -> GuestSessionCreateResponse:
    if idempotency_key is None:
        raise ApiError(
            status_code=422,
            code="VALIDATION_ERROR",
            reason="A session creation key is required.",
            next_action="Retry the guest-session bootstrap.",
            retryable=False,
        )
    if not CREATION_LIMITER.check(
        config.GUEST_SESSION_CREATION_LIMIT_PER_MINUTE
    ):
        raise ApiError(
            status_code=429,
            code="GUEST_SESSION_CREATION_FAILED",
            reason="Guest-session creation is temporarily limited.",
            next_action="Wait briefly and try again.",
            retryable=True,
        )
    try:
        created = guest_session_service().create(idempotency_key)
    except GuestSessionConflict as error:
        raise ApiError(
            status_code=409,
            code="GUEST_SESSION_CONFLICT",
        ) from error
    except GuestSessionInvalid as error:
        raise ApiError(
            status_code=422,
            code="VALIDATION_ERROR",
            reason="The session creation key is invalid.",
            next_action="Retry the guest-session bootstrap.",
            retryable=False,
        ) from error
    except GuestSessionCreationFailed as error:
        raise ApiError(
            status_code=503,
            code="GUEST_SESSION_CREATION_FAILED",
        ) from error
    return GuestSessionCreateResponse(
        token=created.token,
        session=GuestSessionMetadataResponse(
            status=created.session.status,
            created_at=created.session.created_at,
            last_seen_at=created.session.last_seen_at,
            expires_at=created.session.expires_at,
        ),
        workspace=GuestWorkspaceResponse(name=created.workspace.name),
    )


@router.get("", response_model=GuestSessionInspectResponse)
def inspect_guest_session(
    principal: Annotated[
        AuthenticatedGuest,
        Depends(require_guest_principal),
    ],
) -> GuestSessionInspectResponse:
    return GuestSessionInspectResponse(
        status=principal.session.status,
        workspace=GuestWorkspaceResponse(name=principal.workspace.name),
        created_at=principal.session.created_at,
        last_seen_at=principal.session.last_seen_at,
        expires_at=principal.session.expires_at,
    )
