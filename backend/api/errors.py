from __future__ import annotations

import logging
import re
import sqlite3

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.error_catalog import (
    canonical_error_code,
    error_definition,
)
from backend.api.schemas import ErrorBody, ErrorResponse


LOGGER = logging.getLogger("study_companion.api")
REQUEST_ID_HEADER = "X-Request-ID"
MAX_DETAIL_STRING_LENGTH = 500
MAX_DETAIL_ITEMS = 25
_SENSITIVE_DETAIL_KEYS = {
    "api_key",
    "authorization",
    "content",
    "database_url",
    "document",
    "document_content",
    "embedding",
    "excerpt",
    "password",
    "payload",
    "private_key",
    "prompt",
    "query",
    "raw_output",
    "request",
    "response",
    "secret",
    "text",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:postgres|postgresql)://\S+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+\S+"),
)


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str | None = None,
        title: str | None = None,
        reason: str | None = None,
        next_action: str | None = None,
        retryable: bool | None = None,
        details: Any | None = None,
        log_context: Mapping[str, Any] | None = None,
    ) -> None:
        canonical_code, legacy_code = canonical_error_code(code)
        definition = error_definition(
            canonical_code,
            status_code=status_code,
            message=message,
        )
        resolved_title = title or definition.title
        resolved_reason = reason or message or definition.reason
        super().__init__(resolved_title)
        self.status_code = status_code
        self.code = canonical_code
        self.legacy_code = legacy_code
        self.title = resolved_title
        self.reason = resolved_reason
        self.next_action = next_action or definition.next_action
        self.retryable = (
            definition.retryable
            if retryable is None
            else retryable
        )
        # `message` remains a safe compatibility alias for existing clients.
        self.message = message or resolved_reason
        self.details = details
        self.log_context = dict(log_context or {})


def map_exception(
    error: BaseException,
    *,
    fallback_code: str = "INTERNAL_ERROR",
    context: str | None = None,
) -> ApiError:
    """Map an exception chain to a stable, safe application error."""
    chain = _exception_chain(error)
    names = " ".join(
        f"{type(item).__module__}.{type(item).__name__}".lower()
        for item in chain
    )
    messages = " ".join(str(item).lower() for item in chain)
    provider_status = _provider_status(chain)
    log_context: dict[str, Any] = {
        "exception_type": type(chain[-1]).__name__,
    }
    if context:
        log_context["validation_stage"] = context
    if provider_status is not None:
        log_context["provider_status_category"] = str(provider_status)

    code = fallback_code
    overrides: dict[str, Any] = {}

    if (
        provider_status == 429
        or "ratelimit" in names
        or "rate limit" in messages
        or "too many requests" in messages
    ):
        code = "AI_PROVIDER_RATE_LIMITED"
    elif (
        provider_status in {408, 504}
        or "timeout" in names
        or "timed out" in messages
    ):
        code = "AI_PROVIDER_TIMEOUT"
    elif (
        provider_status == 413
        or "context length" in messages
        or "context_length" in messages
        or "maximum context" in messages
        or "too many tokens" in messages
    ):
        code = "AI_CONTEXT_TOO_LARGE"
    elif (
        "refusal" in names
        or "refusal" in messages
        or "refused" in messages
        or "content policy" in messages
        or "content_filter" in messages
    ):
        code = "AI_REFUSAL"
    elif (
        "empty response" in messages
        or "without usable content" in messages
        or "returned no content" in messages
    ):
        code = "AI_EMPTY_RESPONSE"
    elif (
        "jsondecodeerror" in names
        or "invalid json" in messages
        or "did not return json" in messages
        or "did not return a json object" in messages
    ):
        code = "AI_INVALID_JSON"
    elif (
        "citation" in messages
        or "cited unavailable" in messages
        or (
            "source indexes" in messages
            and "visible" in messages
        )
    ):
        code = "CITATION_VALIDATION_FAILED"
    elif _is_schema_validation_failure(names, messages):
        code = "AI_SCHEMA_VALIDATION_FAILED"
    elif (
        "workspaceaccessdenied" in names
        or (
            "workspace" in messages
            and (
                "access denied" in messages
                or "not available" in messages
                or "permission" in messages
            )
        )
    ):
        code = "WORKSPACE_ACCESS_DENIED"
    elif context == "vector_retrieval":
        code = "VECTOR_RETRIEVAL_FAILED"
    elif context == "embedding_job":
        code = "EMBEDDING_JOB_FAILED"
    elif _is_retry_exhaustion(names, messages):
        code = "DATABASE_TRANSACTION_RETRY_EXHAUSTED"
    elif _is_database_failure(chain, names):
        code = "DATABASE_UNAVAILABLE"
    elif (
        provider_status in {400, 401, 403}
        and (
            _is_provider_failure(names)
            or _is_ai_generation_context(context)
        )
    ):
        code = "AI_PROVIDER_UNAVAILABLE"
        overrides = {
            "title": "AI provider settings were rejected",
            "reason": "The configured AI provider rejected the request.",
            "next_action": "Verify the provider key and model settings, then try again.",
            "retryable": False,
        }
    elif (
        provider_status in {500, 502, 503}
        or _is_provider_failure(names)
    ):
        code = "AI_PROVIDER_UNAVAILABLE"

    canonical_code, _legacy = canonical_error_code(code)
    definition = error_definition(canonical_code)
    return ApiError(
        status_code=definition.status_code,
        code=canonical_code,
        log_context=log_context,
        **overrides,
    )


def error_response(
    error: ApiError,
    *,
    request_id: str,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(
            code=error.code,
            title=error.title,
            reason=error.reason,
            next_action=error.next_action,
            retryable=error.retryable,
            request_id=request_id,
            details=_sanitize_details(error.details),
            message=error.message,
            legacy_code=error.legacy_code,
        )
    ).model_dump(exclude_none=False)
    if error.details is None:
        payload["error"]["details"] = None
    if error.legacy_code is None:
        payload["error"].pop("legacy_code", None)
    return JSONResponse(
        status_code=error.status_code,
        content=payload,
        headers={REQUEST_ID_HEADER: request_id},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(
        request: Request,
        error: ApiError,
    ) -> JSONResponse:
        _log_error(request, error)
        return error_response(
            error,
            request_id=_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        error_types = {
            str(item.get("type", ""))
            for item in error.errors()
        }
        if "public_id_string_required" in error_types:
            code = "PUBLIC_ID_STRING_REQUIRED"
            reason = (
                "Large public IDs cannot be represented safely as "
                "JavaScript numbers."
            )
        elif "invalid_public_id" in error_types:
            code = "INVALID_PUBLIC_ID"
            reason = "The supplied public ID is not a positive decimal string."
        else:
            code = "VALIDATION_ERROR"
            reason = "One or more request fields are invalid."
        details = [
            {
                "field": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
        api_error = ApiError(
            status_code=422,
            code=code,
            message="Request validation failed.",
            reason=reason,
            details=details,
            log_context={"validation_stage": "request"},
        )
        _log_error(request, api_error)
        return error_response(
            api_error,
            request_id=_request_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        api_error = ApiError(
            status_code=error.status_code,
            code=(
                "RESOURCE_NOT_FOUND"
                if error.status_code == 404
                else f"HTTP_{error.status_code}"
            ),
            message=(
                "The requested resource was not found."
                if error.status_code == 404
                else "The request could not be completed."
            ),
        )
        _log_error(request, api_error)
        return error_response(
            api_error,
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        api_error = map_exception(error)
        _log_error(request, api_error)
        return error_response(
            api_error,
            request_id=_request_id(request),
        )


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"[a-f0-9]{32}", request_id):
        return request_id
    request_id = uuid4().hex
    request.state.request_id = request_id
    return request_id


def _log_error(request: Request, error: ApiError) -> None:
    cause = error.__cause__
    exception_type = str(
        error.log_context.get(
            "exception_type",
            type(cause or error).__name__,
        )
    )
    provider_category = str(
        error.log_context.get("provider_status_category", "none")
    )
    validation_stage = str(
        error.log_context.get("validation_stage", "none")
    )
    log = LOGGER.error if error.status_code >= 500 else LOGGER.warning
    log(
        (
            "API request failed request_id=%s code=%s status=%s method=%s "
            "route=%s error_type=%s provider_category=%s validation_stage=%s"
        ),
        _request_id(request),
        error.code,
        error.status_code,
        request.method,
        request.url.path,
        exception_type,
        provider_category,
        validation_stage,
    )


def _sanitize_details(value: Any, *, key: str | None = None) -> Any:
    if key and key.casefold() in _SENSITIVE_DETAIL_KEYS:
        return "[redacted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cleaned = value[:MAX_DETAIL_STRING_LENGTH]
        for pattern in _SECRET_VALUE_PATTERNS:
            cleaned = pattern.sub("[redacted]", cleaned)
        return cleaned
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_details(item_value, key=str(item_key))
            for item_key, item_value in list(value.items())[:MAX_DETAIL_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_details(item)
            for item in value[:MAX_DETAIL_ITEMS]
        ]
    return type(value).__name__


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 10:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _provider_status(chain: list[BaseException]) -> int | None:
    for item in chain:
        for candidate in (
            getattr(item, "status_code", None),
            getattr(item, "status", None),
            getattr(getattr(item, "response", None), "status_code", None),
        ):
            if isinstance(candidate, int):
                return candidate
    return None


def _is_provider_failure(names: str) -> bool:
    return any(
        marker in names
        for marker in (
            "groq",
            "openai",
            "openrouter",
            "apiconnection",
            "apierror",
            "serviceunavailable",
            "providererror",
        )
    )


def _is_ai_generation_context(context: str | None) -> bool:
    return bool(
        context
        and any(
            marker in context
            for marker in (
                "chat",
                "coaching",
                "quiz",
                "review",
                "summary",
                "topic",
            )
        )
    )


def _is_schema_validation_failure(names: str, messages: str) -> bool:
    return (
        "pydantic" in names
        or "validationerror" in names
        or "invalid structured data" in messages
        or "wrong coaching mode" in messages
        or "wrong review mode" in messages
        or "wrong number of questions" in messages
        or "empty required fields" in messages
        or "returned the wrong" in messages
        or "must use mode" in messages
    )


def _is_retry_exhaustion(names: str, messages: str) -> bool:
    return (
        "retryexhaust" in names
        or (
            "serialization" in names
            and "retry" in messages
        )
        or (
            "transaction" in messages
            and "retries" in messages
            and "exhaust" in messages
        )
    )


def _is_database_failure(
    chain: list[BaseException],
    names: str,
) -> bool:
    if any(isinstance(item, sqlite3.Error) for item in chain):
        return True
    return any(
        marker in names
        for marker in (
            "sqlalchemy.exc.operationalerror",
            "sqlalchemy.exc.interfaceerror",
            "psycopg.operationalerror",
            "psycopg.interfaceerror",
            "databaseerror",
            "disconnectionerror",
        )
    )
