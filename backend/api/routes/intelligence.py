from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.api.errors import ApiError, map_exception
from backend.api.public_ids import PublicIdInput
from backend.api.schemas import (
    SourceLineageResponse,
    SummaryContentResponse,
    SummaryKeyPointResponse,
    SummaryResponse,
    TopicExtractionRequest,
    TopicListResponse,
    TopicResponse,
)
from backend.rag.intelligence import (
    InsufficientEvidenceError,
    IntelligenceGenerationError,
    SummaryView,
    TopicView,
    extract_topics,
    generate_summary,
    get_cached_summary,
    get_topic_view,
    list_topic_views,
)
from backend.rag.intelligence_store import FingerprintMismatchError
from backend.rag.scope import RetrievalScope, resolve_retrieval_scope

router = APIRouter(prefix="/api", tags=["intelligence"])


def _source_response(source: dict) -> SourceLineageResponse:
    return SourceLineageResponse.model_validate(source)


def _summary_response(view: SummaryView) -> SummaryResponse:
    return SummaryResponse(
        kind=view.kind,
        scope_id=view.scope_id,
        summary=SummaryContentResponse(
            title=view.summary.title,
            overview=view.summary.overview,
            key_points=[
                SummaryKeyPointResponse(
                    text=point.text,
                    source_indexes=point.source_indexes,
                )
                for point in view.summary.key_points
            ],
            confidence=view.summary.confidence,
        ),
        sources=[_source_response(source) for source in view.sources],
        generated_at=view.generated_at,
        stale=view.stale,
    )


def _topic_response(view: TopicView) -> TopicResponse:
    return TopicResponse(
        id=view.id,
        name=view.name,
        description=view.description,
        sources=[_source_response(source) for source in view.sources],
        generated_at=view.generated_at,
        stale=view.stale,
    )


def _scope_from_request(payload: TopicExtractionRequest) -> RetrievalScope:
    scope = payload.scope
    return RetrievalScope(
        notebook_id=scope.notebook_id,
        document_ids=(
            tuple(scope.document_ids)
            if scope.document_ids is not None
            else None
        ),
        topic_id=scope.topic_id,
    )


def _get_summary_or_404(kind: str, scope_id: int | str) -> SummaryResponse:
    try:
        view = get_cached_summary(kind, scope_id)  # type: ignore[arg-type]
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code=f"{kind}_not_found",
            message=f"Requested {kind} was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Summary scope is invalid.",
        ) from error
    if view is None:
        raise ApiError(
            status_code=404,
            code="summary_not_generated",
            message="No cached summary exists. Generate it with POST first.",
        )
    return _summary_response(view)


def _post_summary(kind: str, scope_id: int | str) -> SummaryResponse:
    try:
        view = generate_summary(kind, scope_id)  # type: ignore[arg-type]
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code=f"{kind}_not_found",
            message=f"Requested {kind} was not found.",
        ) from error
    except InsufficientEvidenceError as error:
        raise ApiError(
            status_code=422,
            code="insufficient_evidence",
            message=str(error),
        ) from error
    except FingerprintMismatchError as error:
        raise ApiError(
            status_code=409,
            code="sources_changed",
            message="Sources changed during generation. Previous cache was preserved.",
        ) from error
    except IntelligenceGenerationError as error:
        raise map_exception(
            error,
            fallback_code="INTERNAL_ERROR",
            context="structured_summary",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Summary scope is invalid.",
        ) from error
    return _summary_response(view)


@router.get(
    "/documents/{document_id}/summary",
    response_model=SummaryResponse,
)
def get_document_summary(
    document_id: Annotated[PublicIdInput, Path()],
) -> SummaryResponse:
    return _get_summary_or_404("document", document_id)


@router.post(
    "/documents/{document_id}/summary",
    response_model=SummaryResponse,
)
def post_document_summary(
    document_id: Annotated[PublicIdInput, Path()],
) -> SummaryResponse:
    return _post_summary("document", document_id)


@router.get(
    "/notebooks/{notebook_id}/summary",
    response_model=SummaryResponse,
)
def get_notebook_summary(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> SummaryResponse:
    return _get_summary_or_404("notebook", notebook_id)


@router.post(
    "/notebooks/{notebook_id}/summary",
    response_model=SummaryResponse,
)
def post_notebook_summary(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> SummaryResponse:
    return _post_summary("notebook", notebook_id)


@router.post(
    "/topics/extract",
    response_model=TopicListResponse,
)
def post_topic_extraction(payload: TopicExtractionRequest) -> TopicListResponse:
    return _post_topic_extraction(_scope_from_request(payload))


def _post_topic_extraction(scope: RetrievalScope) -> TopicListResponse:
    try:
        topics = extract_topics(scope)
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Topic extraction scope was not found.",
        ) from error
    except InsufficientEvidenceError as error:
        raise ApiError(
            status_code=422,
            code="insufficient_evidence",
            message=str(error),
        ) from error
    except FingerprintMismatchError as error:
        raise ApiError(
            status_code=409,
            code="sources_changed",
            message="Sources changed during extraction. Previous topics were preserved.",
        ) from error
    except IntelligenceGenerationError as error:
        raise map_exception(
            error,
            fallback_code="INTERNAL_ERROR",
            context="topic_extraction",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Topic extraction scope is invalid.",
        ) from error
    items = [_topic_response(topic) for topic in topics]
    return TopicListResponse(items=items, total=len(items))


def _scoped_topics(
    *,
    scope_kind: str,
    scope_key: int,
) -> TopicListResponse:
    try:
        scope = (
            RetrievalScope(document_ids=(scope_key,))
            if scope_kind == "documents"
            else RetrievalScope(notebook_id=scope_key)
        )
        resolve_retrieval_scope(scope)
        topics = list_topic_views(
            scope_kind=scope_kind,
            scope_key=scope_key if scope_kind == "notebook" else (scope_key,),
        )
    except LookupError as error:
        label = "document" if scope_kind == "documents" else "notebook"
        raise ApiError(
            status_code=404,
            code=f"{label}_not_found",
            message=f"Requested {label} was not found.",
        ) from error
    items = [_topic_response(topic) for topic in topics]
    return TopicListResponse(items=items, total=len(items))


@router.get(
    "/documents/{document_id}/topics",
    response_model=TopicListResponse,
)
def get_document_topics(
    document_id: Annotated[PublicIdInput, Path()],
) -> TopicListResponse:
    return _scoped_topics(scope_kind="documents", scope_key=document_id)


@router.post(
    "/documents/{document_id}/topics",
    response_model=TopicListResponse,
)
def post_document_topics(
    document_id: Annotated[PublicIdInput, Path()],
) -> TopicListResponse:
    return _post_topic_extraction(
        RetrievalScope(document_ids=(document_id,)),
    )


@router.get(
    "/notebooks/{notebook_id}/topics",
    response_model=TopicListResponse,
)
def get_notebook_topics(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> TopicListResponse:
    return _scoped_topics(scope_kind="notebook", scope_key=notebook_id)


@router.post(
    "/notebooks/{notebook_id}/topics",
    response_model=TopicListResponse,
)
def post_notebook_topics(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> TopicListResponse:
    return _post_topic_extraction(
        RetrievalScope(notebook_id=notebook_id),
    )


@router.get(
    "/topics",
    response_model=TopicListResponse,
)
def get_topics(
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> TopicListResponse:
    topics = list_topic_views(search=q)
    items = [_topic_response(topic) for topic in topics]
    return TopicListResponse(items=items, total=len(items))


@router.get(
    "/topics/{topic_id}",
    response_model=TopicResponse,
)
def get_topic_route(topic_id: str) -> TopicResponse:
    try:
        topic = get_topic_view(topic_id)
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_topic_id",
            message="Topic ID must be a canonical UUID.",
        ) from error
    if topic is None:
        raise ApiError(
            status_code=404,
            code="topic_not_found",
            message="Topic was not found.",
        )
    return _topic_response(topic)


@router.get(
    "/topics/{topic_id}/summary",
    response_model=SummaryResponse,
)
def get_topic_summary(topic_id: str) -> SummaryResponse:
    return _get_summary_or_404("topic", topic_id)


@router.post(
    "/topics/{topic_id}/summary",
    response_model=SummaryResponse,
)
def post_topic_summary(topic_id: str) -> SummaryResponse:
    return _post_summary("topic", topic_id)
