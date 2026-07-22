from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.application.dependencies import get_application_dependencies
from backend.domain import AdaptationEvent, LearningSignal
from backend.memory.proposals import (
    PendingMemoryProposal,
    create_or_update_signal_memory_proposal,
)
from backend.memory.service import search_memories, update_memory
from backend.rag import config


SUPPORTED_SIGNAL_TYPES = frozenset(
    {
        "knowledge_gap",
        "misconception",
        "repeated_error",
        "skipped_topic",
        "low_confidence",
        "mastery",
        "preference",
        "learning_behavior",
    }
)
WEAKNESS_SIGNAL_TYPES = frozenset(
    {
        "knowledge_gap",
        "misconception",
        "repeated_error",
        "skipped_topic",
        "low_confidence",
    }
)
ENRICHMENT_WORKFLOW = "learning_signal_enrichment"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if len(token) > 2}


def _signal_key(topic: str, question: str) -> str:
    return "quiz-gap:" + _normalize(topic) + ":" + _normalize(question)


@dataclass(frozen=True)
class QuizLearningAnalysis:
    signals: tuple[LearningSignal, ...]
    proposals: tuple[PendingMemoryProposal, ...]
    weaknesses: tuple[str, ...]
    enrichment_workflow_id: str | None


def analyze_quiz_outcomes(
    *,
    generated: Any,
    run_result: Any,
    stored_attempt: Any,
    stored_questions: list[Any] | tuple[Any, ...],
) -> QuizLearningAnalysis:
    """Persist deterministic signals from server-scored quiz outcomes.

    Call this inside the same UnitOfWork as quiz persistence. It performs no
    provider, embedding, or vector calls.
    """
    dependencies = get_application_dependencies()
    questions_by_number = {
        question.question_number: question for question in stored_questions
    }
    sources_by_index = {source.index: source for source in generated.sources}
    signals: list[LearningSignal] = []
    proposals: list[PendingMemoryProposal] = []
    observed_at = stored_attempt.created_at
    topic = generated.quiz.topic.strip() or generated.requested_topic.strip()

    for outcome in run_result.attempts:
        stored_question = questions_by_number[outcome.question_number]
        generated_question = generated.quiz.questions[outcome.question_number - 1]
        key = _signal_key(topic, outcome.question)
        existing = dependencies.learning_signals.find_by_key(key)
        evidence_key = f"quiz:{stored_attempt.id}:question:{stored_question.id}"
        evidence = {
            "evidence_key": evidence_key,
            "quiz_attempt_id": stored_attempt.id,
            "source_question_id": str(stored_question.id),
            "question_number": outcome.question_number,
            "question": outcome.question,
            "selected_option": outcome.selected_option,
            "selected_answer": (
                generated_question.options[outcome.selected_option - 1]
                if outcome.selected_option is not None
                else None
            ),
            "correct_option": outcome.correct_option,
            "correct_answer": generated_question.options[outcome.correct_option - 1],
            "outcome": (
                "skipped" if outcome.skipped else "correct" if outcome.is_correct else "incorrect"
            ),
            "explanation": generated_question.explanation,
            "citations": [
                {
                    "index": index,
                    "document_id": sources_by_index[index].document_id,
                    "filename": sources_by_index[index].filename,
                    "page_number": sources_by_index[index].page_number,
                    "slide_number": sources_by_index[index].slide_number,
                    "chunk_index": sources_by_index[index].chunk_index,
                }
                for index in generated_question.source_indexes
                if index in sources_by_index
            ],
            "observed_at": observed_at,
        }

        if outcome.is_correct:
            if existing is None or existing.signal_type not in WEAKNESS_SIGNAL_TYPES:
                continue
            if any(item.get("evidence_key") == evidence_key for item in existing.evidence):
                signals.append(existing)
                continue
            confidence = max(0.0, round(existing.confidence - 0.2, 3))
            status = "resolved" if confidence <= 0.3 else "improving"
            updated = dependencies.learning_signals.update(
                existing.id,
                evidence=existing.evidence + (evidence,),
                confidence=confidence,
                occurrence_count=existing.occurrence_count + 1,
                status=status,
                last_observed_at=observed_at,
                payload={
                    **existing.payload,
                    "latest_outcome": "correct",
                    "resolution_reason": "Later trusted quiz performance was correct.",
                },
            )
            if updated.memory_id is not None:
                memory = dependencies.memories.get(updated.memory_id)
                if memory is not None and memory.status == "active":
                    update_memory(
                        memory_id=memory.id,
                        memory_type=memory.memory_type,
                        content=memory.content,
                        confidence=confidence,
                        importance=memory.importance,
                    )
            signals.append(updated)
            continue

        signal_type = "skipped_topic" if outcome.skipped else "knowledge_gap"
        statement = (
            f"The learner may need more practice with {topic}: {outcome.question}"
        )[:500]
        if existing is None:
            signal = dependencies.learning_signals.create(
                signal_type,
                "quiz_attempt",
                str(stored_attempt.id),
                {
                    "latest_outcome": "skipped" if outcome.skipped else "incorrect",
                    "deterministic": True,
                },
                status="active",
                source_question_id=str(stored_question.id),
                topic=topic,
                statement=statement,
                evidence=(evidence,),
                confidence=0.45 if outcome.skipped else 0.55,
                importance=0.5 if outcome.skipped else 0.55,
                occurrence_count=1,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                signal_key=key,
            )
        elif any(item.get("evidence_key") == evidence_key for item in existing.evidence):
            signal = existing
        else:
            occurrence_count = existing.occurrence_count + 1
            signal = dependencies.learning_signals.update(
                existing.id,
                source_type="quiz_attempt",
                source_question_id=str(stored_question.id),
                source_id=str(stored_attempt.id),
                signal_type=(
                    "repeated_error" if occurrence_count >= 2 else signal_type
                ),
                statement=statement,
                evidence=existing.evidence + (evidence,),
                confidence=min(0.95, round(existing.confidence + 0.12, 3)),
                importance=min(0.95, round(existing.importance + 0.08, 3)),
                occurrence_count=occurrence_count,
                status="active",
                last_observed_at=observed_at,
                payload={
                    **existing.payload,
                    "latest_outcome": "skipped" if outcome.skipped else "incorrect",
                },
            )
        signal = dependencies.learning_signals.get(signal.id) or signal
        if signal.memory_id is not None:
            memory = dependencies.memories.get(signal.memory_id)
            if memory is not None and memory.status == "active":
                update_memory(
                    memory_id=memory.id,
                    memory_type=memory.memory_type,
                    content=memory.content,
                    confidence=signal.confidence,
                    importance=max(memory.importance, signal.importance),
                )
        proposal = create_or_update_signal_memory_proposal(signal)
        signal = dependencies.learning_signals.get(signal.id) or signal
        if proposal is not None:
            proposals.append(proposal)
        signals.append(signal)

    enrichment_id: str | None = None
    if signals:
        enrichment_id = str(uuid4())
        dependencies.workflows.put(
            enrichment_id,
            ENRICHMENT_WORKFLOW,
            {
                "quiz_attempt_id": stored_attempt.id,
                "learning_signal_ids": [signal.id for signal in signals],
                "operation": "optional_llm_enrichment",
                "provider_call_started": False,
            },
            (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        )

    return QuizLearningAnalysis(
        signals=tuple(signals),
        proposals=tuple(proposals),
        weaknesses=tuple(
            signal.statement
            for signal in signals
            if signal.signal_type in WEAKNESS_SIGNAL_TYPES
            and signal.status in {"active", "improving"}
        ),
        enrichment_workflow_id=enrichment_id,
    )


@dataclass(frozen=True)
class AdaptationContext:
    workflow_type: str
    topic: str
    memory_ids: tuple[int, ...]
    learning_signal_ids: tuple[str, ...]
    memory_summaries: tuple[str, ...]
    signal_summaries: tuple[str, ...]
    applied_changes: dict[str, object]
    reason: str

    @property
    def adapted(self) -> bool:
        return bool(self.memory_ids or self.learning_signal_ids)

    @property
    def prompt_instructions(self) -> str:
        if not self.adapted:
            return "No learner-specific adaptation is available."
        lines = [self.reason, "Observable adaptation rules:"]
        lines.extend(f"- {key}: {value}" for key, value in self.applied_changes.items())
        lines.extend(f"- Learner memory: {item}" for item in self.memory_summaries)
        lines.extend(f"- Learning signal: {item}" for item in self.signal_summaries)
        return "\n".join(lines)


def build_adaptation_context(workflow_type: str, topic: str = "") -> AdaptationContext:
    dependencies = get_application_dependencies()
    topic_tokens = _tokens(topic)
    signals = [
        signal
        for signal in dependencies.learning_signals.list()
        if signal.status in {"active", "improving"}
    ]
    active_memories = [
        memory
        for memory in dependencies.memories.list()
        if memory.status == "active"
    ]

    def relevant(text: str) -> bool:
        return not topic_tokens or bool(topic_tokens & _tokens(text))

    selected_signals = [
        signal
        for signal in signals
        if relevant(signal.topic + " " + signal.statement)
    ][:5]
    if (
        config.PERSISTENCE_BACKEND == "cockroach"
        and topic.strip()
        and active_memories
    ):
        semantic_matches = search_memories(topic, k=5)
        selected_memories = [
            memory
            for match in semantic_matches
            if (memory := dependencies.memories.get(match.memory_id)) is not None
            and memory.status == "active"
            and relevant(memory.content)
        ]
    else:
        selected_memories = [
            memory for memory in active_memories if relevant(memory.content)
        ][:5]
    has_weakness = any(
        signal.signal_type in WEAKNESS_SIGNAL_TYPES for signal in selected_signals
    )
    has_mastery = any(signal.signal_type == "mastery" for signal in selected_signals)

    changes_by_workflow: dict[str, dict[str, object]] = {
        "quiz": {
            "targeted_topic": topic or (selected_signals[0].topic if selected_signals else "requested topic"),
            "difficulty": "supportive" if has_weakness else "challenge" if has_mastery else "standard",
            "distractors": "include a plausible misconception check" if has_weakness else "standard grounded distractors",
            "question_type": "concept check with explanation" if has_weakness else "mixed understanding checks",
            "targeted_questions": 1 if has_weakness else 0,
            "misconception_checks": has_weakness,
        },
        "review": {
            "review_priority": "boost active or improving weaknesses",
            "order": "weakness evidence first",
            "chosen_examples": "simpler worked examples" if has_weakness else "standard examples",
            "repeated_misconception_coverage": has_weakness,
            "mastered_topic_repetition": "reduced" if has_mastery else "unchanged",
        },
        "study_plan": {
            "topic_priority": "boost evidence-backed weaknesses",
            "review_frequency": "repeat within the current plan" if has_weakness else "standard",
            "session_duration": "add focused practice time" if has_weakness else "standard",
            "sequencing": "weakness before adjacent practice",
            "estimated_effort": "increased for repeated evidence" if has_weakness else "standard",
        },
        "coaching": {
            "explanation_style": "simple and stepwise" if has_weakness else "concise",
            "depth": "rebuild the missing distinction" if has_weakness else "standard",
            "example_style": "worked example followed by retrieval practice",
            "strategy_recommendations": "reassess the evidenced gap",
            "encouragement": "acknowledge improvement without claiming mastery",
        },
    }
    changes = changes_by_workflow.get(workflow_type, {})
    if selected_memories or selected_signals:
        reason = (
            f"Used {len(selected_memories)} active learner memory record(s) and "
            f"{len(selected_signals)} current learning signal(s) relevant to "
            f"{topic or 'this workflow'}."
        )
    else:
        reason = "No relevant active learner memory or learning signal was available."
    return AdaptationContext(
        workflow_type=workflow_type,
        topic=topic,
        memory_ids=tuple(memory.id for memory in selected_memories),
        learning_signal_ids=tuple(signal.id for signal in selected_signals),
        memory_summaries=tuple(memory.content for memory in selected_memories),
        signal_summaries=tuple(signal.statement for signal in selected_signals),
        applied_changes=changes if (selected_memories or selected_signals) else {},
        reason=reason,
    )


def record_adaptation_event(
    context: AdaptationContext,
    *,
    request_id: str | None = None,
    applied_changes: dict[str, object] | None = None,
) -> AdaptationEvent:
    return get_application_dependencies().adaptation_events.create(
        context.workflow_type,
        request_id or str(uuid4()),
        context.memory_ids,
        context.learning_signal_ids,
        applied_changes if applied_changes is not None else context.applied_changes,
        context.reason,
    )
