from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from memory.models import MemoryCandidate
from memory.service import (
    MemorySearchResult,
    search_memories,
)


@dataclass(frozen=True)
class DuplicateMemoryResult:
    """
    Result of checking a proposed memory against existing
    active learner memories.
    """

    is_duplicate: bool
    existing_memory: MemorySearchResult | None
    reason: str


def normalize_memory_text(text: str) -> str:
    """
    Normalize text for deterministic exact comparison.

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
    Check for normalized exact duplicates.

    Semantic similarity is used only to locate the closest
    same-type memory. It does not decide whether the candidate
    is a duplicate.

    Non-exact matches are passed to the relationship classifier,
    which decides between:

    - duplicate
    - new
    - refinement
    - contradiction
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

    # Only normalized exact matches are blocked here.
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

    # Find the nearest related memory, but do not declare it
    # a duplicate based only on vector distance.
    closest_result = min(
        same_type_results,
        key=lambda item: item.distance,
    )

    return DuplicateMemoryResult(
        is_duplicate=False,
        existing_memory=closest_result,
        reason=(
            "No normalized exact duplicate was found. "
            "The closest same-type memory will be passed to "
            "relationship classification."
        ),
    )