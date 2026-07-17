from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MemoryCandidateType = Literal[
    "none",
    "profile",
    "learning_state",
    "episodic",
    "procedural",
]


class MemoryCandidate(BaseModel):
    """
    Structured proposal produced by the memory extractor.

    This is only a candidate. It has not yet passed application
    validation and has not been saved.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    should_store: bool = Field(
        description=(
            "Whether this interaction contains one durable "
            "learner memory worth proposing."
        )
    )

    memory_type: MemoryCandidateType = Field(
        description=(
            "The proposed memory category. Use 'none' when "
            "should_store is false."
        )
    )

    content: str = Field(
        max_length=500,
        description=(
            "A concise third-person memory statement. "
            "Use an empty string when should_store is false."
        ),
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that the memory is directly supported "
            "by the user's message."
        ),
    )

    importance: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How useful this memory is likely to be for future "
            "study assistance."
        ),
    )

    reason: str = Field(
        max_length=500,
        description=(
            "A concise explanation of why the memory should or "
            "should not be stored."
        ),
    )


MemoryRelationshipType = Literal[
    "duplicate",
    "new",
    "refinement",
    "contradiction",
]

class MemoryRelationshipAssessment(BaseModel):
    """
    LLM assessment of the relationship between a proposed
    memory and one existing active memory.

    Duplicate detection is handled deterministically before
    this model is called, so the LLM only chooses between:
    new, refinement and contradiction.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    relationship_type: MemoryRelationshipType = Field(
        description=(
            "How the proposed memory relates to the existing "
            "memory: new, refinement, or contradiction."
        )
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in the relationship classification."
        ),
    )

    reason: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "A concise explanation grounded only in the two "
            "memory statements."
        ),
    )