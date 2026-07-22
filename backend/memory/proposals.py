from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal
from uuid import UUID, uuid4

from backend.memory.conflict_detector import (
    MemoryConflictResult,
    detect_memory_conflict,
)
from backend.memory.database import StoredMemory
from backend.memory.extractor import propose_memory_candidate
from backend.memory.models import MemoryCandidate
from backend.memory.service import (
    add_memory,
    replace_memory_with_candidate,
)
from backend.memory.validator import validate_memory_candidate
from backend.rag.config import ENABLE_MEMORY_PROPOSALS
from backend.application.dependencies import get_application_dependencies
from backend.memory.service import MemorySearchResult


MemoryProposalDecision = Literal[
    "accept",
    "replace",
    "keep_both",
    "reject",
    "cancel",
]
MAX_PENDING_MEMORY_PROPOSALS = 128
MEMORY_PROPOSAL_TTL = timedelta(days=7)
MEMORY_PROPOSAL_WORKFLOW = "memory_proposal"


class MemoryProposalNotFoundError(LookupError):
    """Raised when a pending proposal is absent or already consumed."""


class MemoryProposalDecisionError(ValueError):
    """Raised when a decision is incompatible with a proposal."""


@dataclass(frozen=True)
class PendingMemoryProposal:
    id: str
    candidate: MemoryCandidate
    conflict: MemoryConflictResult
    created_at: str

    @property
    def existing_memory_id(self) -> int | None:
        existing = self.conflict.existing_memory
        return (
            existing.memory_id
            if existing is not None
            else None
        )

    @property
    def allowed_decisions(self) -> tuple[MemoryProposalDecision, ...]:
        if self.conflict.conflict_type == "new":
            return (
                "accept",
                "reject",
                "cancel",
            )

        return (
            "replace",
            "keep_both",
            "reject",
            "cancel",
        )


@dataclass(frozen=True)
class MemoryProposalDecisionResult:
    proposal_id: str
    decision: MemoryProposalDecision
    consumed: bool
    saved_memory: StoredMemory | None = None
    archived_memory: StoredMemory | None = None


_proposal_lock = RLock()


def _workflow_repository():
    return get_application_dependencies().workflows


def create_memory_proposal(
    *,
    user_message: str,
    assistant_answer: str,
) -> PendingMemoryProposal | None:
    """Run the noninteractive proposal pipeline and store safe candidates."""
    if not ENABLE_MEMORY_PROPOSALS:
        return None

    candidate = propose_memory_candidate(
        user_message=user_message,
        assistant_answer=assistant_answer,
    )
    validation = validate_memory_candidate(candidate)

    if not validation.accepted:
        return None

    conflict = detect_memory_conflict(candidate)

    # Equivalent active memory already exists; no user decision can
    # safely create additional value.
    if conflict.conflict_type == "duplicate":
        return None

    pending = PendingMemoryProposal(
        id=str(uuid4()),
        candidate=candidate,
        conflict=conflict,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    with _proposal_lock:
        repository = _workflow_repository()
        repository.put(
            pending.id,
            MEMORY_PROPOSAL_WORKFLOW,
            _serialize_pending_proposal(pending),
            (datetime.now(timezone.utc) + MEMORY_PROPOSAL_TTL).isoformat(),
        )
        repository.trim_pending(
            MEMORY_PROPOSAL_WORKFLOW,
            MAX_PENDING_MEMORY_PROPOSALS,
        )

    return pending


def get_memory_proposal(
    proposal_id: str,
) -> PendingMemoryProposal | None:
    normalized_id = _normalize_proposal_id(proposal_id)

    with _proposal_lock:
        state = _workflow_repository().get(
            normalized_id,
            MEMORY_PROPOSAL_WORKFLOW,
        )
        return _deserialize_pending_proposal(state.payload) if state else None


def decide_memory_proposal(
    proposal_id: str,
    decision: MemoryProposalDecision,
    *,
    replace_memory_id: int | None = None,
) -> MemoryProposalDecisionResult:
    """Apply a decision using only the registry-held candidate."""
    normalized_id = _normalize_proposal_id(proposal_id)

    with _proposal_lock:
        dependencies = get_application_dependencies()
        state = dependencies.workflows.get(
            normalized_id,
            MEMORY_PROPOSAL_WORKFLOW,
        )
        pending = (
            _deserialize_pending_proposal(state.payload)
            if state is not None
            else None
        )

        if pending is None:
            raise MemoryProposalNotFoundError(
                "The memory proposal does not exist or was already "
                "consumed."
            )

        if decision not in pending.allowed_decisions:
            raise MemoryProposalDecisionError(
                f"Decision '{decision}' is not valid for this proposal."
            )

        if decision != "replace" and replace_memory_id is not None:
            raise MemoryProposalDecisionError(
                "replace_memory_id is only valid for replacement."
            )

        if decision == "cancel":
            dependencies.workflows.decide(
                state.id,
                state.version,
                "pending",
                {"decision": "cancel"},
            )
            return MemoryProposalDecisionResult(
                proposal_id=pending.id,
                decision=decision,
                consumed=False,
            )

        if decision == "reject":
            dependencies.workflows.decide(
                state.id,
                state.version,
                "rejected",
                {"decision": "reject"},
            )
            return MemoryProposalDecisionResult(
                proposal_id=pending.id,
                decision=decision,
                consumed=True,
            )

        candidate = pending.candidate

        if candidate.memory_type == "none":
            raise MemoryProposalDecisionError(
                "The pending proposal has no durable memory type."
            )

        with dependencies.unit_of_work():
            if decision == "replace":
                existing_memory_id = pending.existing_memory_id

                if existing_memory_id is None:
                    raise MemoryProposalDecisionError(
                        "The pending proposal has no memory to replace."
                    )

                if (
                    replace_memory_id is not None
                    and replace_memory_id != existing_memory_id
                ):
                    raise MemoryProposalDecisionError(
                        "Replacement memory ID does not match the "
                        "server-held proposal."
                    )

                _validate_replacement_snapshot(pending)

                replacement = replace_memory_with_candidate(
                    existing_memory_id=existing_memory_id,
                    memory_type=candidate.memory_type,
                    content=candidate.content,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                )
                result = MemoryProposalDecisionResult(
                    proposal_id=pending.id,
                    decision=decision,
                    consumed=True,
                    saved_memory=replacement.new_memory,
                    archived_memory=replacement.archived_memory,
                )

            else:
                saved_memory = add_memory(
                    memory_type=candidate.memory_type,
                    content=candidate.content,
                    confidence=candidate.confidence,
                    importance=candidate.importance,
                )
                result = MemoryProposalDecisionResult(
                    proposal_id=pending.id,
                    decision=decision,
                    consumed=True,
                    saved_memory=saved_memory,
                )

            dependencies.workflows.decide(
                state.id,
                state.version,
                "completed",
                {"decision": decision},
            )

        return result


def clear_memory_proposals() -> None:
    """Clear durable proposal state, primarily for isolated tests."""
    with _proposal_lock:
        _workflow_repository().clear_type(MEMORY_PROPOSAL_WORKFLOW)


def _validate_replacement_snapshot(
    pending: PendingMemoryProposal,
) -> None:
    snapshot = pending.conflict.existing_memory

    if snapshot is None:
        raise MemoryProposalDecisionError(
            "The pending proposal has no memory to replace."
        )

    current = get_application_dependencies().memories.get(snapshot.memory_id)

    if current is None or current.status != "active":
        raise MemoryProposalDecisionError(
            "The proposed replacement target is no longer active."
        )

    if (
        current.memory_type != snapshot.memory_type
        or current.content != snapshot.content
        or current.confidence != snapshot.confidence
        or current.importance != snapshot.importance
    ):
        raise MemoryProposalDecisionError(
            "The proposed replacement target changed after the "
            "proposal was generated."
        )


def _normalize_proposal_id(proposal_id: str) -> str:
    try:
        normalized = str(UUID(proposal_id))
    except (ValueError, AttributeError, TypeError) as error:
        raise MemoryProposalNotFoundError(
            "The memory proposal does not exist or was already "
            "consumed."
        ) from error

    return normalized


def _serialize_pending_proposal(pending: PendingMemoryProposal) -> dict[str, object]:
    return {
        "id": pending.id,
        "candidate": pending.candidate.model_dump(mode="json"),
        "conflict": {
            "conflict_type": pending.conflict.conflict_type,
            "existing_memory": (
                asdict(pending.conflict.existing_memory)
                if pending.conflict.existing_memory is not None
                else None
            ),
            "confidence": pending.conflict.confidence,
            "reason": pending.conflict.reason,
        },
        "created_at": pending.created_at,
    }


def _deserialize_pending_proposal(payload: dict[str, object]) -> PendingMemoryProposal:
    raw_candidate = payload.get("candidate")
    raw_conflict = payload.get("conflict")
    raw_id = payload.get("id")
    raw_created_at = payload.get("created_at")
    if (
        not isinstance(raw_candidate, dict)
        or not isinstance(raw_conflict, dict)
        or not isinstance(raw_id, str)
        or not isinstance(raw_created_at, str)
    ):
        raise MemoryProposalNotFoundError("The stored memory proposal is invalid.")
    raw_existing = raw_conflict.get("existing_memory")
    existing = (
        MemorySearchResult(**raw_existing)
        if isinstance(raw_existing, dict)
        else None
    )
    return PendingMemoryProposal(
        id=raw_id,
        candidate=MemoryCandidate.model_validate(raw_candidate),
        conflict=MemoryConflictResult(
            conflict_type=str(raw_conflict.get("conflict_type")),  # type: ignore[arg-type]
            existing_memory=existing,
            confidence=float(raw_conflict.get("confidence", 0.0)),
            reason=str(raw_conflict.get("reason", "")),
        ),
        created_at=raw_created_at,
    )
