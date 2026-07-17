from __future__ import annotations

from dataclasses import dataclass

from memory.models import MemoryCandidate
from rag.config import (
    MEMORY_PROPOSAL_MIN_CONFIDENCE,
    MEMORY_PROPOSAL_MIN_IMPORTANCE,
)


@dataclass(frozen=True)
class MemoryValidationResult:
    accepted: bool
    reason: str


def validate_memory_candidate(
    candidate: MemoryCandidate,
) -> MemoryValidationResult:
    if not candidate.should_store:
        return MemoryValidationResult(
            accepted=False,
            reason="No durable learner memory was detected.",
        )

    if candidate.memory_type == "none":
        return MemoryValidationResult(
            accepted=False,
            reason=(
                "The candidate requested storage without "
                "providing a valid memory type."
            ),
        )

    content = candidate.content.strip()

    if len(content) < 12:
        return MemoryValidationResult(
            accepted=False,
            reason="The proposed memory is too short.",
        )

    if len(content) > 500:
        return MemoryValidationResult(
            accepted=False,
            reason="The proposed memory is too long.",
        )

    if (
        candidate.confidence
        < MEMORY_PROPOSAL_MIN_CONFIDENCE
    ):
        return MemoryValidationResult(
            accepted=False,
            reason=(
                "Candidate confidence is below the configured "
                f"threshold of "
                f"{MEMORY_PROPOSAL_MIN_CONFIDENCE:.2f}."
            ),
        )

    if (
        candidate.importance
        < MEMORY_PROPOSAL_MIN_IMPORTANCE
    ):
        return MemoryValidationResult(
            accepted=False,
            reason=(
                "Candidate importance is below the configured "
                f"threshold of "
                f"{MEMORY_PROPOSAL_MIN_IMPORTANCE:.2f}."
            ),
        )

    return MemoryValidationResult(
        accepted=True,
        reason="Candidate passed deterministic validation.",
    )