from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from memory.models import MemoryCandidate
from memory.service import (
    MemorySearchResult,
    search_memories,
)
from rag.config import MEMORY_DUPLICATE_MAX_DISTANCE


@dataclass(frozen=True)
class DuplicateMemoryResult:
    """
    Result of comparing a proposed memory against existing
    active learner memories.
    """

    is_duplicate: bool
    existing_memory: MemorySearchResult | None
    reason: str


def normalize_memory_text(text: str) -> str:
    """
    Normalize text for exact duplicate comparison.

    Examples that become equivalent:

    "The learner prefers examples."
    "the learner prefers examples"
    """

    normalized = unicodedata.normalize(
        "NFKC",
        text,
    )

    normalized = normalized.casefold()

    # Replace punctuation with spaces.
    normalized = re.sub(
        r"[^\w\s]",
        " ",
        normalized,
    )

    # Collapse repeated whitespace.
    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip()


def find_duplicate_memory(
    candidate: MemoryCandidate,
    search_count: int = 5,
) -> DuplicateMemoryResult:
    """
    Check whether a proposed memory duplicates an existing
    active memory.

    Duplicate detection has two levels:

    1. Normalized exact-text comparison.
    2. Semantic-distance comparison.

    Only memories with the same memory type are considered
    duplicates in Milestone 4A.
    """

    if not candidate.should_store:
        return DuplicateMemoryResult(
            is_duplicate=False,
            existing_memory=None,
            reason="Candidate was not marked for storage.",
        )

    if candidate.memory_type == "none":
        return DuplicateMemoryResult(
            is_duplicate=False,
            existing_memory=None,
            reason="Candidate has no valid memory type.",
        )

    candidate_content = candidate.content.strip()

    if not candidate_content:
        return DuplicateMemoryResult(
            is_duplicate=False,
            existing_memory=None,
            reason="Candidate content is empty.",
        )

    results = search_memories(
        query=candidate_content,
        k=search_count,
    )

    # In Milestone 4A, only compare memories of the same type.
    same_type_results = [
        result
        for result in results
        if result.memory_type == candidate.memory_type
    ]

    if not same_type_results:
        return DuplicateMemoryResult(
            is_duplicate=False,
            existing_memory=None,
            reason=(
                "No active memories of the same type were found."
            ),
        )

    normalized_candidate = normalize_memory_text(
        candidate_content
    )

    # First check normalized exact matches.
    for result in same_type_results:
        normalized_existing = normalize_memory_text(
            result.content
        )

        if normalized_candidate == normalized_existing:
            return DuplicateMemoryResult(
                is_duplicate=True,
                existing_memory=result,
                reason=(
                    "The proposed memory exactly matches an "
                    "existing memory after text normalization."
                ),
            )

    # Then check the closest semantic match.
    closest_result = min(
        same_type_results,
        key=lambda item: item.distance,
    )

    if (
        closest_result.distance
        <= MEMORY_DUPLICATE_MAX_DISTANCE
    ):
        return DuplicateMemoryResult(
            is_duplicate=True,
            existing_memory=closest_result,
            reason=(
                "The proposed memory is semantically very close "
                "to an existing active memory."
            ),
        )

    return DuplicateMemoryResult(
        is_duplicate=False,
        existing_memory=closest_result,
        reason=(
            "The closest same-type memory is outside the "
            "duplicate-distance threshold."
        ),
    )