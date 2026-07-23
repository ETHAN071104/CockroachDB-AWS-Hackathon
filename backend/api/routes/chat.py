from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

import backend.rag.chat_service as chat_service
from backend.api.errors import ApiError, map_exception
from backend.application.dependencies import get_application_dependencies
from backend.api.routes.memory import memory_proposal_response
from backend.api.schemas import (
    ChatRequest,
    ChatResponse,
    FeatureRedirectResponse,
    InteractionOutcomeUpdate,
    SessionDetailResponse,
    StudyInteractionResponse,
    StudySessionListResponse,
    StudySessionResponse,
    StudySourceResponse,
)
from backend.rag.notebooks import DocumentNotFoundError, NotebookNotFoundError
from backend.rag.scope import RetrievalScope, TopicNotFoundError
from backend.study.database import (
    StoredInteractionSource,
    StoredStudyInteraction,
    StoredStudySession,
)


router = APIRouter(prefix="/api", tags=["chat", "study"])


def study_source_response(
    source: StoredInteractionSource,
) -> StudySourceResponse:
    return StudySourceResponse(
        index=source.source_index,
        document_id=source.document_id,
        notebook_id=source.notebook_id,
        filename=source.filename,
        mime_type=source.mime_type,
        page_number=source.page_number,
        slide_number=source.slide_number,
        chunk_index=source.chunk_index,
        distance=source.distance,
        excerpt=source.excerpt or "",
    )


def study_session_response(
    session: StoredStudySession,
) -> StudySessionResponse:
    return StudySessionResponse(
        id=session.id,
        status=session.status,
        started_at=session.started_at,
        ended_at=session.ended_at,
    )


def study_interaction_response(
    interaction: StoredStudyInteraction,
    sources: list[StoredInteractionSource],
) -> StudyInteractionResponse:
    return StudyInteractionResponse(
        id=interaction.id,
        session_id=interaction.session_id,
        question=interaction.question,
        answer=interaction.answer,
        outcome=interaction.outcome,
        created_at=interaction.created_at,
        sources=[
            study_source_response(source)
            for source in sources
        ],
    )


def _scope_from_chat_request(
    payload: ChatRequest,
) -> RetrievalScope | None:
    if payload.notebook_id is not None:
        return RetrievalScope(
            notebook_id=payload.notebook_id,
        )

    if payload.document_ids is not None:
        return RetrievalScope(
            document_ids=tuple(payload.document_ids),
        )

    if payload.topic_id is not None:
        return RetrievalScope(
            topic_id=payload.topic_id,
        )

    return None


def _session_or_404(
    session_id: int,
) -> StoredStudySession:
    session = get_application_dependencies().study_sessions.get(session_id)

    if session is None:
        raise ApiError(
            status_code=404,
            code="study_session_not_found",
            message="Study session was not found.",
        )

    return session


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    try:
        scope = _scope_from_chat_request(payload)
        result = chat_service.run_chat(
            payload.question,
            scope=scope,
        )
    except (
        NotebookNotFoundError,
        DocumentNotFoundError,
        TopicNotFoundError,
    ) as error:
        raise ApiError(
            status_code=404,
            code="retrieval_scope_not_found",
            message="The requested chat scope was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_chat_request",
            message=str(error),
        ) from error
    except Exception as error:
        raise map_exception(
            error,
            fallback_code="INTERNAL_ERROR",
            context="chat",
        ) from error

    return ChatResponse(
        session_id=result.session.id,
        interaction_id=result.interaction.id,
        answer=result.interaction.answer,
        sources=[
            study_source_response(source)
            for source in result.sources
        ],
        memory_proposal=(
            memory_proposal_response(result.memory_proposal)
            if result.memory_proposal is not None
            else None
        ),
        type=result.type,
        intent=result.intent,
        evidence_status=result.evidence_status,
        redirect=(
            FeatureRedirectResponse(
                target=result.redirect.target,
                title=result.redirect.title,
                message=result.redirect.message,
                action_label=result.redirect.action_label,
                original_prompt=result.redirect.original_prompt,
                suggested_prompt=result.redirect.suggested_prompt,
            )
            if result.redirect is not None
            else None
        ),
        suggested_question=result.suggested_question,
    )


@router.patch(
    "/study/interactions/{interaction_id}/outcome",
    response_model=StudyInteractionResponse,
)
def update_interaction_outcome(
    interaction_id: Annotated[int, Path(ge=1)],
    payload: InteractionOutcomeUpdate,
) -> StudyInteractionResponse:
    repository = get_application_dependencies().study_sessions
    if repository.get_interaction(interaction_id) is None:
        raise ApiError(
            status_code=404,
            code="study_interaction_not_found",
            message="Study interaction was not found.",
        )

    interaction = repository.update_outcome(
        interaction_id,
        payload.outcome,
    )
    sources = repository.list_sources(
        interaction_id
    )
    return study_interaction_response(
        interaction,
        sources,
    )


@router.get(
    "/study/sessions",
    response_model=StudySessionListResponse,
)
def list_study_sessions() -> StudySessionListResponse:
    sessions = get_application_dependencies().study_sessions.list()
    return StudySessionListResponse(
        items=[
            study_session_response(session)
            for session in sessions
        ],
        total=len(sessions),
    )


@router.get(
    "/study/sessions/{session_id}",
    response_model=SessionDetailResponse,
)
def get_study_session(
    session_id: Annotated[int, Path(ge=1)],
) -> SessionDetailResponse:
    session = _session_or_404(session_id)
    repository = get_application_dependencies().study_sessions
    interactions = repository.list_interactions(
        session_id
    )
    return SessionDetailResponse(
        session=study_session_response(session),
        interactions=[
            study_interaction_response(
                interaction,
                repository.list_sources(
                    interaction.id
                ),
            )
            for interaction in interactions
        ],
    )


@router.post(
    "/study/sessions/active/end",
    response_model=StudySessionResponse,
)
def end_active_study_session() -> StudySessionResponse:
    repository = get_application_dependencies().study_sessions
    active_session = repository.get_active()

    if active_session is None:
        raise ApiError(
            status_code=404,
            code="active_study_session_not_found",
            message="There is no active study session to end.",
        )

    completed = repository.end(
        active_session.id
    )
    return study_session_response(completed)
