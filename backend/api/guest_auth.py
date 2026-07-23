from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Header, Request

from backend.api.errors import ApiError
from backend.application.dependencies import (
    bind_application_dependencies,
    build_application_dependencies,
    get_application_dependencies,
    reset_application_dependencies,
)
from backend.application.guest_sessions import (
    AuthenticatedGuest,
    GuestSessionExpired,
    GuestSessionInvalid,
    GuestSessionRevoked,
    GuestSessionService,
)
from backend.rag import config


TOKEN_QUERY_KEYS = frozenset(
    {"token", "guest_token", "access_token", "authorization"}
)
WORKSPACE_HEADER_KEYS = frozenset(
    {"x-workspace-id", "x-agentbook-workspace"}
)


def guest_session_service() -> GuestSessionService:
    try:
        config.validate_guest_session_config()
        return GuestSessionService(
            get_application_dependencies(),
            pepper=config.GUEST_SESSION_TOKEN_PEPPER,
            ttl_days=config.GUEST_SESSION_TTL_DAYS,
            last_seen_minutes=config.GUEST_SESSION_LAST_SEEN_MINUTES,
        )
    except (RuntimeError, ValueError) as error:
        raise ApiError(
            status_code=503,
            code="GUEST_SESSION_CREATION_FAILED",
            reason="Anonymous study sessions are not configured.",
            next_action="Configure the guest-session subsystem and try again.",
            retryable=False,
        ) from error


def authenticate_guest_request(
    request: Request,
    authorization: str | None,
) -> AuthenticatedGuest:
    _reject_unsafe_identity_inputs(request)
    if not authorization:
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_REQUIRED",
        )
    scheme, separator, credential = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or len(credential) > 200
    ):
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_INVALID",
        )
    try:
        return guest_session_service().authenticate(credential)
    except GuestSessionExpired as error:
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_EXPIRED",
        ) from error
    except GuestSessionRevoked as error:
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_REVOKED",
        ) from error
    except GuestSessionInvalid as error:
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_INVALID",
        ) from error


async def bind_protected_workspace(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AsyncIterator[None]:
    _reject_unsafe_identity_inputs(request)
    allow_legacy = bool(
        getattr(
            request.app.state,
            "allow_legacy_default_workspace",
            config.ALLOW_LEGACY_DEFAULT_WORKSPACE,
        )
    )
    if not authorization and allow_legacy:
        token = bind_application_dependencies(
            get_application_dependencies()
        )
        request.state.workspace_mode = "legacy"
        try:
            yield
        finally:
            reset_application_dependencies(token)
        return

    principal = authenticate_guest_request(request, authorization)
    dependencies = build_application_dependencies(principal.workspace.id)
    token = bind_application_dependencies(dependencies)
    request.state.guest_principal = principal
    request.state.workspace_mode = "guest"
    try:
        yield
    finally:
        reset_application_dependencies(token)


def require_guest_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedGuest:
    return authenticate_guest_request(request, authorization)


def _reject_unsafe_identity_inputs(request: Request) -> None:
    query_keys = {key.casefold() for key in request.query_params.keys()}
    if query_keys & TOKEN_QUERY_KEYS:
        raise ApiError(
            status_code=401,
            code="GUEST_SESSION_INVALID",
            reason="Guest credentials are accepted only in the Authorization header.",
            next_action="Remove the credential from the URL and try again.",
            retryable=False,
        )
    if "workspace_id" in query_keys or any(
        key in request.headers for key in WORKSPACE_HEADER_KEYS
    ):
        raise ApiError(
            status_code=403,
            code="WORKSPACE_ACCESS_DENIED",
            reason="Workspace identity is derived from the authenticated session.",
            next_action="Remove the workspace override and try again.",
            retryable=False,
        )
