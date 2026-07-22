from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import UUID, uuid4

from backend.application.dependencies import get_application_dependencies
from backend.memory.consolidator import (
    MemoryConsolidationProposal,
    propose_memory_consolidation,
)
from backend.memory.database import StoredMemory
from backend.memory.models import MemoryConsolidationCandidate
from backend.memory.service import (
    MemoryConsolidationResult,
    apply_memory_consolidation,
)


MAX_PENDING_MEMORY_CONSOLIDATIONS = 128
MEMORY_CONSOLIDATION_TTL = timedelta(days=7)
MEMORY_CONSOLIDATION_WORKFLOW = "memory_consolidation"


class MemoryConsolidationNotFoundError(LookupError):
    """Raised when a durable consolidation proposal is absent."""


@dataclass(frozen=True)
class PendingMemoryConsolidation:
    id: str
    proposal: MemoryConsolidationProposal
    created_at: str


_consolidation_lock = RLock()


def create_memory_consolidation(
    memory_ids: list[int],
) -> PendingMemoryConsolidation:
    """Generate and persist a server-authoritative proposal snapshot."""
    proposal = propose_memory_consolidation(memory_ids)
    pending = PendingMemoryConsolidation(
        id=str(uuid4()),
        proposal=proposal,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    with _consolidation_lock:
        repository = get_application_dependencies().workflows
        repository.put(
            pending.id,
            MEMORY_CONSOLIDATION_WORKFLOW,
            _serialize_pending_consolidation(pending),
            (datetime.now(timezone.utc) + MEMORY_CONSOLIDATION_TTL).isoformat(),
        )
        repository.trim_pending(
            MEMORY_CONSOLIDATION_WORKFLOW,
            MAX_PENDING_MEMORY_CONSOLIDATIONS,
        )
    return pending


def get_memory_consolidation(
    proposal_id: str,
) -> PendingMemoryConsolidation | None:
    normalized_id = _normalize_proposal_id(proposal_id)
    with _consolidation_lock:
        state = get_application_dependencies().workflows.get(
            normalized_id,
            MEMORY_CONSOLIDATION_WORKFLOW,
        )
        return (
            _deserialize_pending_consolidation(state.payload)
            if state is not None
            else None
        )


def apply_pending_memory_consolidation(
    proposal_id: str,
) -> MemoryConsolidationResult:
    """Apply the stored snapshot and consume it only on success."""
    normalized_id = _normalize_proposal_id(proposal_id)
    with _consolidation_lock:
        dependencies = get_application_dependencies()
        state = dependencies.workflows.get(
            normalized_id,
            MEMORY_CONSOLIDATION_WORKFLOW,
        )
        if state is None:
            raise MemoryConsolidationNotFoundError(
                "The consolidation proposal does not exist or was "
                "already consumed."
            )
        pending = _deserialize_pending_consolidation(state.payload)
        with dependencies.unit_of_work():
            result = apply_memory_consolidation(pending.proposal)
            dependencies.workflows.decide(
                state.id,
                state.version,
                "completed",
                {"decision": "apply"},
            )
        return result


def clear_memory_consolidations() -> None:
    """Clear durable consolidation state, primarily for isolated tests."""
    with _consolidation_lock:
        get_application_dependencies().workflows.clear_type(
            MEMORY_CONSOLIDATION_WORKFLOW
        )


def _normalize_proposal_id(proposal_id: str) -> str:
    try:
        return str(UUID(proposal_id))
    except (ValueError, AttributeError, TypeError) as error:
        raise MemoryConsolidationNotFoundError(
            "The consolidation proposal does not exist or was already consumed."
        ) from error


def _serialize_pending_consolidation(
    pending: PendingMemoryConsolidation,
) -> dict[str, object]:
    return {
        "id": pending.id,
        "source_memories": [
            asdict(memory) for memory in pending.proposal.source_memories
        ],
        "candidate": pending.proposal.candidate.model_dump(mode="json"),
        "created_at": pending.created_at,
    }


def _deserialize_pending_consolidation(
    payload: dict[str, object],
) -> PendingMemoryConsolidation:
    raw_id = payload.get("id")
    raw_sources = payload.get("source_memories")
    raw_candidate = payload.get("candidate")
    raw_created_at = payload.get("created_at")
    if (
        not isinstance(raw_id, str)
        or not isinstance(raw_sources, list)
        or not isinstance(raw_candidate, dict)
        or not isinstance(raw_created_at, str)
    ):
        raise MemoryConsolidationNotFoundError(
            "The stored consolidation proposal is invalid."
        )
    try:
        sources = tuple(
            StoredMemory(**raw_source)
            for raw_source in raw_sources
            if isinstance(raw_source, dict)
        )
        if len(sources) != len(raw_sources):
            raise ValueError("Invalid source memory payload.")
        candidate = MemoryConsolidationCandidate.model_validate(raw_candidate)
    except (TypeError, ValueError) as error:
        raise MemoryConsolidationNotFoundError(
            "The stored consolidation proposal is invalid."
        ) from error
    return PendingMemoryConsolidation(
        id=raw_id,
        proposal=MemoryConsolidationProposal(
            source_memories=sources,
            candidate=candidate,
        ),
        created_at=raw_created_at,
    )
