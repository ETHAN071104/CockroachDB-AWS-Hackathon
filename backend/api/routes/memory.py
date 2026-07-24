from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.api.errors import ApiError
from backend.api.public_ids import PublicIdInput
from backend.application.dependencies import get_application_dependencies
from backend.api.schemas import (
    ConsolidationApplyRequest,
    ConsolidationApplyResultResponse,
    ConsolidationProposalResponse,
    ConsolidationProposeRequest,
    DeleteResponse,
    MemoryCreate,
    MemoryListResponse,
    MemoryProposalDecisionRequest,
    MemoryProposalDecisionResultResponse,
    MemoryProposalResponse,
    MemoryResponse,
    MemorySearchItemResponse,
    MemorySearchResponse,
    MemoryUpdate,
)
from backend.memory.consolidation_registry import (
    MemoryConsolidationNotFoundError,
    PendingMemoryConsolidation,
    apply_pending_memory_consolidation,
    create_memory_consolidation,
)
from backend.memory.database import StoredMemory
from backend.memory.proposals import (
    MemoryProposalDecisionError,
    MemoryProposalNotFoundError,
    PendingMemoryProposal,
    decide_memory_proposal,
)
from backend.memory.service import (
    add_memory,
    archive_memory,
    delete_memory,
    get_all_memories,
    search_memories,
    update_memory,
)
from backend.repositories.interfaces import RepositoryConflictError


router = APIRouter(prefix="/api", tags=["memory"])


def memory_response(memory: StoredMemory) -> MemoryResponse:
    dependencies = get_application_dependencies()
    linked_signals = [
        signal
        for signal in dependencies.learning_signals.list()
        if signal.memory_id == memory.id
    ]
    evidence = [item for signal in linked_signals for item in signal.evidence]
    latest_event = next(
        (
            event
            for event in dependencies.adaptation_events.list(limit=100)
            if memory.id in event.memory_ids
        ),
        None,
    )
    return MemoryResponse(
        id=memory.id,
        memory_type=memory.memory_type,
        content=memory.content,
        confidence=memory.confidence,
        importance=memory.importance,
        status=memory.status,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        evidence=evidence,
        source_quiz_id=(
            linked_signals[0].source_id if linked_signals else None
        ),
        occurrence_count=sum(signal.occurrence_count for signal in linked_signals),
        improvement_state=(
            linked_signals[0].status if linked_signals else None
        ),
        latest_use=(
            {
                "workflow_type": latest_event.workflow_type,
                "request_id": latest_event.request_id,
                "reason": latest_event.reason,
                "created_at": latest_event.created_at,
            }
            if latest_event is not None
            else None
        ),
    )


def memory_proposal_response(
    pending: PendingMemoryProposal,
) -> MemoryProposalResponse:
    existing = pending.conflict.existing_memory
    return MemoryProposalResponse(
        proposal_id=pending.id,
        memory_type=pending.candidate.memory_type,
        content=pending.candidate.content,
        confidence=pending.candidate.confidence,
        importance=pending.candidate.importance,
        conflict_type=pending.conflict.conflict_type,
        conflict_confidence=pending.conflict.confidence,
        existing_memory_id=pending.existing_memory_id,
        existing_memory_content=(
            existing.content
            if existing is not None
            else None
        ),
        allowed_decisions=list(
            pending.allowed_decisions
        ),
        reason=pending.conflict.reason,
        created_at=pending.created_at,
        evidence=list(pending.evidence),
        learning_signal_ids=list(pending.learning_signal_ids),
        source_type=pending.source_type,
        source_id=pending.source_id,
        occurrence_count=pending.occurrence_count,
        signal_status=pending.signal_status,
    )


def _consolidation_response(
    pending: PendingMemoryConsolidation,
) -> ConsolidationProposalResponse:
    candidate = pending.proposal.candidate
    return ConsolidationProposalResponse(
        proposal_id=pending.id,
        should_consolidate=candidate.should_consolidate,
        memory_type=candidate.memory_type,
        content=candidate.content,
        confidence=candidate.confidence,
        importance=candidate.importance,
        reason=candidate.reason,
        source_memories=[
            memory_response(memory)
            for memory in pending.proposal.source_memories
        ],
        created_at=pending.created_at,
    )


def _memory_or_404(memory_id: int) -> StoredMemory:
    memory = get_application_dependencies().memories.get(memory_id)

    if memory is None:
        raise ApiError(
            status_code=404,
            code="memory_not_found",
            message="Memory was not found.",
        )

    return memory


@router.get("/memories", response_model=MemoryListResponse)
def list_memories_route(
    include_archived: Annotated[bool, Query()] = False,
) -> MemoryListResponse:
    memories = get_all_memories(
        include_archived=include_archived
    )
    return MemoryListResponse(
        items=[memory_response(memory) for memory in memories],
        total=len(memories),
    )


@router.post(
    "/memories",
    response_model=MemoryResponse,
    status_code=201,
)
def create_memory_route(
    payload: MemoryCreate,
) -> MemoryResponse:
    try:
        memory = add_memory(
            memory_type=payload.memory_type,
            content=payload.content,
            confidence=payload.confidence,
            importance=payload.importance,
        )
    except ValueError as error:
        raise ApiError(
            status_code=400,
            code="invalid_memory",
            message=str(error),
        ) from error

    return memory_response(memory)


@router.get(
    "/memories/search",
    response_model=MemorySearchResponse,
)
def search_memories_route(
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> MemorySearchResponse:
    results = search_memories(
        query=q,
        k=limit,
    )
    return MemorySearchResponse(
        items=[
            MemorySearchItemResponse(
                memory_id=result.memory_id,
                memory_type=result.memory_type,
                content=result.content,
                confidence=result.confidence,
                importance=result.importance,
                distance=result.distance,
            )
            for result in results
        ],
        total=len(results),
    )


@router.post(
    "/memories/proposals/{proposal_id}/decision",
    response_model=MemoryProposalDecisionResultResponse,
)
def decide_memory_proposal_route(
    proposal_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: MemoryProposalDecisionRequest,
) -> MemoryProposalDecisionResultResponse:
    try:
        result = decide_memory_proposal(
            proposal_id=proposal_id,
            decision=payload.decision,
            replace_memory_id=payload.replace_memory_id,
            edited_content=payload.edited_content,
        )
    except MemoryProposalNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="memory_proposal_not_found",
            message="Memory proposal was not found or was already consumed.",
        ) from error
    except MemoryProposalDecisionError as error:
        raise ApiError(
            status_code=409,
            code="invalid_memory_proposal_decision",
            message=str(error),
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=409,
            code="memory_proposal_conflict",
            message="Memory proposal could not be applied to current data.",
        ) from error

    saved_memory = (
        get_application_dependencies().memories.get(result.saved_memory.id)
        if result.saved_memory is not None
        else None
    )
    archived_memory = (
        get_application_dependencies().memories.get(result.archived_memory.id)
        if result.archived_memory is not None
        else None
    )
    return MemoryProposalDecisionResultResponse(
        proposal_id=result.proposal_id,
        decision=result.decision,
        consumed=result.consumed,
        saved_memory=(
            memory_response(saved_memory)
            if saved_memory is not None
            else None
        ),
        archived_memory=(
            memory_response(archived_memory)
            if archived_memory is not None
            else None
        ),
    )


@router.post(
    "/memories/consolidation/propose",
    response_model=ConsolidationProposalResponse,
)
def propose_consolidation_route(
    payload: ConsolidationProposeRequest,
) -> ConsolidationProposalResponse:
    try:
        pending = create_memory_consolidation(
            payload.memory_ids
        )
    except ValueError as error:
        raise ApiError(
            status_code=400,
            code="invalid_consolidation_sources",
            message=str(error),
        ) from error

    return _consolidation_response(pending)


def _apply_consolidation(
    proposal_id: str,
) -> ConsolidationApplyResultResponse:
    try:
        result = apply_pending_memory_consolidation(
            proposal_id
        )
    except MemoryConsolidationNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="consolidation_proposal_not_found",
            message=(
                "Consolidation proposal was not found or was already consumed."
            ),
        ) from error
    except (ValueError, RuntimeError) as error:
        raise ApiError(
            status_code=409,
            code="consolidation_conflict",
            message=(
                "Consolidation could not be applied because the proposal "
                "is not actionable or its source memories changed."
            ),
        ) from error

    consolidated = get_application_dependencies().memories.get(
        result.consolidated_memory.id
    ) or result.consolidated_memory
    archived_sources = [
        get_application_dependencies().memories.get(memory.id) or memory
        for memory in result.source_memories
    ]
    return ConsolidationApplyResultResponse(
        proposal_id=proposal_id,
        consolidated_memory=memory_response(
            consolidated
        ),
        archived_source_memories=[
            memory_response(memory)
            for memory in archived_sources
        ],
    )


@router.post(
    "/memories/consolidation/apply",
    response_model=ConsolidationApplyResultResponse,
)
def apply_consolidation_route(
    payload: ConsolidationApplyRequest,
) -> ConsolidationApplyResultResponse:
    return _apply_consolidation(payload.proposal_id)


@router.post(
    "/memories/consolidation/{proposal_id}/apply",
    response_model=ConsolidationApplyResultResponse,
)
def apply_consolidation_alias_route(
    proposal_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> ConsolidationApplyResultResponse:
    return _apply_consolidation(proposal_id)


@router.get(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
)
def get_memory_route(
    memory_id: Annotated[PublicIdInput, Path()],
) -> MemoryResponse:
    return memory_response(
        _memory_or_404(memory_id)
    )


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
)
def update_memory_route(
    memory_id: Annotated[PublicIdInput, Path()],
    payload: MemoryUpdate,
) -> MemoryResponse:
    existing = _memory_or_404(memory_id)

    try:
        updated = update_memory(
            memory_id=memory_id,
            memory_type=(
                payload.memory_type
                if payload.memory_type is not None
                else existing.memory_type
            ),
            content=(
                payload.content
                if payload.content is not None
                else existing.content
            ),
            confidence=(
                payload.confidence
                if payload.confidence is not None
                else existing.confidence
            ),
            importance=(
                payload.importance
                if payload.importance is not None
                else existing.importance
            ),
        )
    except ValueError as error:
        raise ApiError(
            status_code=400,
            code="invalid_memory",
            message=str(error),
        ) from error

    return memory_response(updated)


@router.post(
    "/memories/{memory_id}/archive",
    response_model=MemoryResponse,
)
def archive_memory_route(
    memory_id: Annotated[PublicIdInput, Path()],
) -> MemoryResponse:
    _memory_or_404(memory_id)
    archived = archive_memory(memory_id)

    if not archived:
        raise ApiError(
            status_code=404,
            code="memory_not_found",
            message="Memory was not found.",
        )

    return memory_response(
        _memory_or_404(memory_id)
    )


@router.delete(
    "/memories/{memory_id}",
    response_model=DeleteResponse,
)
def delete_memory_route(
    memory_id: Annotated[PublicIdInput, Path()],
) -> DeleteResponse:
    _memory_or_404(memory_id)

    try:
        deleted = delete_memory(memory_id)
    except RepositoryConflictError as error:
        raise ApiError(
            status_code=409,
            code="memory_in_use",
            message="Memory cannot be deleted while lineage references it.",
        ) from error

    if not deleted:
        raise ApiError(
            status_code=404,
            code="memory_not_found",
            message="Memory was not found.",
        )

    return DeleteResponse(deleted=True)
