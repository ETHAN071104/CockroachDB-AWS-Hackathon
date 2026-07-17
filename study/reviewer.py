from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from llm.factory import create_chat_model
from rag.config import LLM_PROVIDER
from rag.rag_service import (
    RetrievedSource,
    extract_response_text,
    format_document_context,
    retrieve_sources,
)
from study.recommendations import ReviewRecommendation


# ============================================================
# STRUCTURED REVIEW ACTION
# ============================================================

ReviewMode = Literal[
    "none",
    "simpler_explanation",
    "targeted_recap",
]


class GroundedReviewAction(BaseModel):
    """
    Proposed review activity grounded in indexed documents.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    should_generate: bool = Field(
        description=(
            "Whether the retrieved excerpts contain enough "
            "information to create a grounded review action."
        )
    )

    review_mode: ReviewMode = Field(
        description=(
            "Use simpler_explanation for confused outcomes, "
            "targeted_recap for partial outcomes, and none "
            "when generation is rejected."
        )
    )

    topic: str = Field(
        max_length=200,
        description=(
            "A concise name for the concept being reviewed."
        ),
    )

    explanation: str = Field(
        max_length=2500,
        description=(
            "A grounded explanation or recap using citations "
            "such as [1] and [2]."
        ),
    )

    worked_example: str = Field(
        max_length=2000,
        description=(
            "One short worked example grounded in the supplied "
            "material, with citations where appropriate."
        ),
    )

    check_question: str = Field(
        max_length=500,
        description=(
            "One question that checks the learner's "
            "understanding."
        ),
    )

    expected_answer: str = Field(
        max_length=1200,
        description=(
            "The expected answer to the check question, "
            "grounded in the supplied excerpts."
        ),
    )

    source_indexes: list[int] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "The source citation indexes used in the review "
            "action."
        ),
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that the review action is supported "
            "by the supplied excerpts."
        ),
    )

    reason: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Why the review action could or could not be "
            "generated."
        ),
    )


@dataclass(frozen=True)
class GeneratedReviewAction:
    recommendation: ReviewRecommendation
    sources: tuple[RetrievedSource, ...]
    action: GroundedReviewAction


# ============================================================
# PARSER
# ============================================================

REVIEW_ACTION_PARSER = PydanticOutputParser(
    pydantic_object=GroundedReviewAction
)


# ============================================================
# PROMPT
# ============================================================

REVIEW_ACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You create one short review activity for a learner.

The learner previously answered a study question and reported
either partial understanding or confusion.

Use only the supplied document excerpts for factual content.

Review modes:

- If the outcome is "confused", use "simpler_explanation".
  Explain the idea from the beginning in simpler language.

- If the outcome is "partial", use "targeted_recap".
  Focus on the likely missing distinction or step without
  unnecessarily repeating everything.

Requirements:

- Give one concise explanation.
- Give one short worked example when the excerpts support one.
- Give one check question.
- Give the expected answer.
- Cite supporting document excerpts using [1], [2], and so on.
- source_indexes must contain only indexes actually cited.
- Do not cite learner outcomes or stored memories.
- Do not invent facts or examples unsupported by the excerpts.
- Do not use outside knowledge.
- Do not claim that the learner understands something.
- Treat the previous question and outcome as review context,
  not factual evidence.

When the excerpts are insufficient:

- should_generate must be false;
- review_mode must be "none";
- topic, explanation, worked_example, check_question, and
  expected_answer must be empty strings;
- source_indexes must be empty;
- confidence must be 0;
- reason must explain what information is missing.

Return only one valid JSON object.
Do not use Markdown or code fences.

{format_instructions}
""".strip(),
        ),
        (
            "human",
            """
Previous question:

{question}

Recorded outcome:

{outcome}

Reason this item was recommended:

{recommendation_reason}

Document excerpts:

{document_context}
""".strip(),
        ),
    ]
).partial(
    format_instructions=(
        REVIEW_ACTION_PARSER.get_format_instructions()
    )
)


# ============================================================
# MODEL
# ============================================================

@lru_cache(maxsize=1)
def get_review_action_model():
    model = create_chat_model(
        max_tokens=1200,
        temperature=0,
        max_retries=2,
    )

    # Your current Groq model does not support json_schema.
    if LLM_PROVIDER == "groq":
        model = model.bind(
            response_format={
                "type": "json_object",
            }
        )

    return model


# ============================================================
# SOURCE RETRIEVAL
# ============================================================

def retrieve_review_sources(
    recommendation: ReviewRecommendation,
    retrieval_count: int = 8,
    max_sources: int = 5,
) -> list[RetrievedSource]:
    """
    Retrieve excerpts for the recommended review question.

    When the original interaction recorded source filenames,
    only excerpts from those files are retained.
    """
    if retrieval_count <= 0:
        raise ValueError(
            "Retrieval count must be greater than zero."
        )

    if max_sources <= 0:
        raise ValueError(
            "Maximum source count must be greater than zero."
        )

    retrieved = retrieve_sources(
        question=recommendation.question,
        k=retrieval_count,
    )

    if recommendation.source_filenames:
        allowed_filenames = set(
            recommendation.source_filenames
        )

        retrieved = [
            source
            for source in retrieved
            if source.filename in allowed_filenames
        ]

    return retrieved[:max_sources]


# ============================================================
# JSON HELPERS
# ============================================================

def extract_json_object(
    raw_text: str,
) -> str:
    cleaned = raw_text.strip()

    if not cleaned:
        raise RuntimeError(
            "The review model returned an empty response."
        )

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise RuntimeError(
            "The review model did not return a JSON object.\n"
            f"Raw output:\n{cleaned}"
        )

    return cleaned[start:end + 1]


# ============================================================
# VALIDATION
# ============================================================

def validate_review_action(
    recommendation: ReviewRecommendation,
    sources: list[RetrievedSource],
    action: GroundedReviewAction,
) -> None:
    """
    Apply deterministic checks after model generation.
    """
    if not action.should_generate:
        if action.review_mode != "none":
            raise RuntimeError(
                "A rejected review action must use mode 'none'."
            )

        if action.source_indexes:
            raise RuntimeError(
                "A rejected review action cannot cite sources."
            )

        return

    expected_mode = (
        "simpler_explanation"
        if recommendation.outcome == "confused"
        else "targeted_recap"
    )

    if action.review_mode != expected_mode:
        raise RuntimeError(
            "The review model returned the wrong review mode."
        )

    required_text_fields = {
        "topic": action.topic,
        "explanation": action.explanation,
        "check_question": action.check_question,
        "expected_answer": action.expected_answer,
    }

    empty_fields = [
        name
        for name, value in required_text_fields.items()
        if not value.strip()
    ]

    if empty_fields:
        raise RuntimeError(
            "The generated review action contains empty "
            "required fields: "
            + ", ".join(empty_fields)
        )

    valid_source_indexes = {
        source.index
        for source in sources
    }

    invalid_indexes = [
        index
        for index in action.source_indexes
        if index not in valid_source_indexes
    ]

    if invalid_indexes:
        raise RuntimeError(
            "The review action cited unavailable source "
            f"indexes: {invalid_indexes}"
        )

    if not action.source_indexes:
        raise RuntimeError(
            "A generated review action must cite at least "
            "one document source."
        )

    combined_text = "\n".join(
        [
            action.explanation,
            action.worked_example,
            action.expected_answer,
        ]
    )

    has_visible_citation = any(
        f"[{index}]" in combined_text
        for index in action.source_indexes
    )

    if not has_visible_citation:
        raise RuntimeError(
            "The review action listed source indexes but did "
            "not include visible citations in its content."
        )


# ============================================================
# GENERATION
# ============================================================

def generate_review_action(
    recommendation: ReviewRecommendation,
) -> GeneratedReviewAction:
    """
    Generate one grounded review activity.

    No database records are created or modified.
    """
    if recommendation.outcome not in {
        "partial",
        "confused",
    }:
        raise ValueError(
            "Review actions require a partial or confused "
            "outcome."
        )

    sources = retrieve_review_sources(
        recommendation
    )

    if not sources:
        return GeneratedReviewAction(
            recommendation=recommendation,
            sources=(),
            action=GroundedReviewAction(
                should_generate=False,
                review_mode="none",
                topic="",
                explanation="",
                worked_example="",
                check_question="",
                expected_answer="",
                source_indexes=[],
                confidence=0.0,
                reason=(
                    "No matching indexed excerpts were found "
                    "from the interaction's recorded sources."
                ),
            ),
        )

    document_context = format_document_context(
        sources
    )

    messages = REVIEW_ACTION_PROMPT.format_messages(
        question=recommendation.question,
        outcome=recommendation.outcome,
        recommendation_reason=recommendation.reason,
        document_context=document_context,
    )

    response = get_review_action_model().invoke(
        messages
    )

    raw_text = extract_response_text(
        response
    )

    json_text = extract_json_object(
        raw_text
    )

    try:
        action = REVIEW_ACTION_PARSER.parse(
            json_text
        )

    except Exception as error:
        raise RuntimeError(
            "The review model returned invalid JSON.\n"
            f"Raw output:\n{raw_text}"
        ) from error

    validate_review_action(
        recommendation=recommendation,
        sources=sources,
        action=action,
    )

    return GeneratedReviewAction(
        recommendation=recommendation,
        sources=tuple(sources),
        action=action,
    )


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_review_action(
    result: GeneratedReviewAction,
) -> str:
    action = result.action

    lines = [
        "=" * 60,
        "GROUNDED REVIEW ACTIVITY",
        "=" * 60,
        f"Original outcome: {result.recommendation.outcome}",
        (
            "Priority score: "
            f"{result.recommendation.priority_score}"
        ),
    ]

    if not action.should_generate:
        lines.extend(
            [
                "",
                "A grounded review activity could not be "
                "generated.",
                f"Reason: {action.reason}",
            ]
        )

        return "\n".join(lines)

    lines.extend(
        [
            f"Review mode: {action.review_mode}",
            f"Confidence: {action.confidence:.2f}",
            "",
            f"Topic: {action.topic}",
            "",
            "Explanation:",
            action.explanation,
        ]
    )

    if action.worked_example:
        lines.extend(
            [
                "",
                "Worked example:",
                action.worked_example,
            ]
        )

    lines.extend(
        [
            "",
            "Check question:",
            action.check_question,
            "",
            "Expected answer:",
            action.expected_answer,
            "",
            "Document sources:",
        ]
    )

    cited_indexes = set(
        action.source_indexes
    )

    for source in result.sources:
        if source.index not in cited_indexes:
            continue

        source_label = (
            f"[{source.index}] {source.filename}"
        )

        if source.page_number is not None:
            source_label += (
                f", page {source.page_number}"
            )

        lines.append(
            f"- {source_label}"
        )

    return "\n".join(lines)