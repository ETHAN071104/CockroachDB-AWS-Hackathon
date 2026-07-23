from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDefinition:
    status_code: int
    title: str
    reason: str
    next_action: str
    retryable: bool


ERROR_CATALOG: dict[str, ErrorDefinition] = {
    # Study material and retrieval
    "NO_INDEXED_DOCUMENTS": ErrorDefinition(
        422,
        "No indexed study material",
        "The selected scope does not contain a document that is ready for study.",
        "Upload or index a document, or choose a different scope.",
        False,
    ),
    "SCOPE_NOT_FOUND": ErrorDefinition(
        404,
        "Study scope not found",
        "The selected document, notebook, or topic is no longer available.",
        "Choose the source again and retry the request.",
        False,
    ),
    "SCOPE_EMPTY": ErrorDefinition(
        422,
        "The selected scope is empty",
        "The selected scope does not contain usable indexed study material.",
        "Choose a scope with indexed documents.",
        False,
    ),
    "DOCUMENT_NOT_READY": ErrorDefinition(
        422,
        "The document is not ready",
        "The selected document has not produced usable indexed chunks yet.",
        "Re-index the document or choose another source.",
        False,
    ),
    "NO_RELEVANT_CHUNKS": ErrorDefinition(
        422,
        "No relevant study material found",
        "Indexed documents were available, but no relevant excerpts matched the request.",
        "Change the source scope or ask a more document-specific question.",
        False,
    ),
    "INSUFFICIENT_GROUNDED_EVIDENCE": ErrorDefinition(
        422,
        "Not enough grounded evidence",
        "Agentbook found study material, but it did not contain enough evidence for a safe answer.",
        "Narrow the request or choose a source that directly covers the topic.",
        False,
    ),
    "CITATION_VALIDATION_FAILED": ErrorDefinition(
        502,
        "The generated citations were invalid",
        "The AI response did not cite the available study material correctly.",
        "Retry the request or ask a narrower question.",
        True,
    ),
    # Learning history
    "NO_LEARNING_HISTORY": ErrorDefinition(
        422,
        "No learning history yet",
        "Agentbook does not have completed study or quiz activity to analyze.",
        "Complete a study session or quiz first.",
        False,
    ),
    "NO_WEAKNESS_EVIDENCE": ErrorDefinition(
        422,
        "No weakness evidence found",
        "The available outcomes do not show an unresolved weakness.",
        "Complete or rate more study activities before trying again.",
        False,
    ),
    "NO_COACHING_ITEMS": ErrorDefinition(
        422,
        "No coaching items available",
        "No unresolved evidence could be turned into a grounded coaching activity.",
        "Complete a quiz or study session and record the outcome first.",
        False,
    ),
    "NO_STUDY_PLAN_INPUTS": ErrorDefinition(
        422,
        "No study-plan inputs available",
        "There are no unresolved outcomes or quiz gaps to prioritize.",
        "Complete and rate a study activity before building a plan.",
        False,
    ),
    # AI provider and structured output
    "AI_PROVIDER_UNAVAILABLE": ErrorDefinition(
        503,
        "AI provider temporarily unavailable",
        "The configured AI provider could not complete the request.",
        "Try again shortly. If the problem continues, verify the model settings.",
        True,
    ),
    "AI_PROVIDER_RATE_LIMITED": ErrorDefinition(
        429,
        "AI provider rate limit reached",
        "The configured provider is temporarily refusing additional requests.",
        "Wait briefly and try again.",
        True,
    ),
    "AI_PROVIDER_TIMEOUT": ErrorDefinition(
        504,
        "The AI request timed out",
        "The provider did not finish the request within the allowed time.",
        "Try again. A smaller scope may complete faster.",
        True,
    ),
    "AI_EMPTY_RESPONSE": ErrorDefinition(
        502,
        "The AI returned an empty response",
        "The provider completed the request without usable content.",
        "Retry the request.",
        True,
    ),
    "AI_INVALID_JSON": ErrorDefinition(
        502,
        "The AI response had invalid JSON",
        "The provider returned content that could not be parsed as the required JSON object.",
        "Retry the request.",
        True,
    ),
    "AI_SCHEMA_VALIDATION_FAILED": ErrorDefinition(
        502,
        "The AI response had an invalid format",
        "The returned content did not match the structure required by this feature.",
        "Retry the request. If it keeps failing, verify the configured model.",
        True,
    ),
    "AI_REFUSAL": ErrorDefinition(
        422,
        "The AI declined the request",
        "The provider did not generate the requested study content.",
        "Rephrase the request using only the selected study material.",
        False,
    ),
    "AI_CONTEXT_TOO_LARGE": ErrorDefinition(
        413,
        "The AI context is too large",
        "The selected material exceeds the provider's context limit.",
        "Choose a smaller scope or ask about a narrower topic.",
        False,
    ),
    # Persistence and vector systems
    "DATABASE_UNAVAILABLE": ErrorDefinition(
        503,
        "Study data is temporarily unavailable",
        "Agentbook could not reach its persistence service.",
        "Try again shortly. If the problem continues, check the database service.",
        True,
    ),
    "DATABASE_TRANSACTION_RETRY_EXHAUSTED": ErrorDefinition(
        503,
        "The study-data update could not be completed",
        "Concurrent database activity exhausted the safe transaction retries.",
        "Try the action again.",
        True,
    ),
    "VECTOR_RETRIEVAL_FAILED": ErrorDefinition(
        503,
        "Study-material search is unavailable",
        "Agentbook could not search the document vector index.",
        "Try again shortly or check vector-store health.",
        True,
    ),
    "EMBEDDING_JOB_FAILED": ErrorDefinition(
        502,
        "Document indexing did not finish",
        "Agentbook could not create or synchronize the required embeddings.",
        "Retry indexing or run vector reconciliation.",
        True,
    ),
    "WORKSPACE_ACCESS_DENIED": ErrorDefinition(
        403,
        "Workspace access denied",
        "The requested resource is not available in the active workspace.",
        "Return to the workspace and choose an available resource.",
        False,
    ),
    # Anonymous guest sessions
    "GUEST_SESSION_REQUIRED": ErrorDefinition(
        401,
        "Private study space required",
        "Create or restore a guest study session before accessing this feature.",
        "Continue as Guest.",
        False,
    ),
    "GUEST_SESSION_INVALID": ErrorDefinition(
        401,
        "Unable to restore your study space",
        "The saved anonymous study session is not valid.",
        "Start a new study space.",
        False,
    ),
    "GUEST_SESSION_EXPIRED": ErrorDefinition(
        401,
        "Session expired",
        "Your anonymous study session is no longer active.",
        "Start a new study space.",
        False,
    ),
    "GUEST_SESSION_REVOKED": ErrorDefinition(
        401,
        "Session no longer active",
        "This anonymous study session has been revoked.",
        "Start a new study space.",
        False,
    ),
    "GUEST_SESSION_CREATION_FAILED": ErrorDefinition(
        503,
        "Unable to create a private study space",
        "Agentbook could not create the anonymous study session.",
        "Try again shortly.",
        True,
    ),
    "GUEST_SESSION_CONFLICT": ErrorDefinition(
        409,
        "Study-space creation already completed",
        "This creation attempt was already committed and cannot replay its credential.",
        "Start a new study space with a new creation attempt.",
        False,
    ),
    # Application
    "VALIDATION_ERROR": ErrorDefinition(
        422,
        "Check the submitted information",
        "One or more request fields are invalid.",
        "Correct the highlighted values and submit again.",
        False,
    ),
    "REQUEST_CONFLICT": ErrorDefinition(
        409,
        "The request conflicts with current data",
        "The resource changed or its current state does not allow this action.",
        "Refresh the page and review the current state before trying again.",
        False,
    ),
    "INTERNAL_ERROR": ErrorDefinition(
        500,
        "Agentbook could not complete the request",
        "An unexpected server error occurred.",
        "Use the request ID when asking for support.",
        False,
    ),
    # Additional application-wide compatibility codes
    "RESOURCE_NOT_FOUND": ErrorDefinition(
        404,
        "Resource not found",
        "The requested resource is no longer available.",
        "Return to the previous screen and choose an available resource.",
        False,
    ),
    "EXPORT_FAILED": ErrorDefinition(
        500,
        "Study-data export failed",
        "Agentbook could not create the export archive.",
        "Try again. If it keeps failing, use the request ID for support.",
        True,
    ),
}


LEGACY_CODE_ALIASES: dict[str, str] = {
    "internal_error": "INTERNAL_ERROR",
    "validation_error": "VALIDATION_ERROR",
    "invalid_scope": "VALIDATION_ERROR",
    "invalid_chat_request": "VALIDATION_ERROR",
    "invalid_quiz_request": "VALIDATION_ERROR",
    "invalid_quiz_submission": "VALIDATION_ERROR",
    "invalid_study_plan": "VALIDATION_ERROR",
    "scope_not_found": "SCOPE_NOT_FOUND",
    "retrieval_scope_not_found": "SCOPE_NOT_FOUND",
    "scope_empty": "SCOPE_EMPTY",
    "notebook_has_no_indexed_material": "SCOPE_EMPTY",
    "topic_has_no_indexed_material": "SCOPE_EMPTY",
    "no_study_material": "NO_INDEXED_DOCUMENTS",
    "document_not_ready": "DOCUMENT_NOT_READY",
    "insufficient_evidence": "INSUFFICIENT_GROUNDED_EVIDENCE",
    "export_failed": "EXPORT_FAILED",
    "not_found": "RESOURCE_NOT_FOUND",
}


def canonical_error_code(code: str) -> tuple[str, str | None]:
    cleaned = code.strip()
    canonical = LEGACY_CODE_ALIASES.get(cleaned, cleaned.upper())
    legacy_code = cleaned if cleaned != canonical else None
    return canonical, legacy_code


def error_definition(
    code: str,
    *,
    status_code: int | None = None,
    message: str | None = None,
) -> ErrorDefinition:
    definition = ERROR_CATALOG.get(code)
    if definition is not None:
        return definition
    resolved_status = status_code or 500
    title = code.replace("_", " ").title()
    reason = message or "The request could not be completed."
    return ErrorDefinition(
        status_code=resolved_status,
        title=title,
        reason=reason,
        next_action=(
            "Try again."
            if resolved_status >= 500
            else "Review the request and try again."
        ),
        retryable=resolved_status >= 500,
    )
