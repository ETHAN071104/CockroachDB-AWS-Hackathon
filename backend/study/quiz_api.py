from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from backend.rag.scope import RetrievalScope
from backend.study.quiz_generator import GeneratedGroundedQuiz, generate_grounded_quiz
from backend.study.quiz_runner import QuizQuestionAttempt, QuizRunResult
from backend.application.dependencies import get_application_dependencies
from backend.rag.rag_service import RetrievedSource
from backend.study.quiz_generator import GroundedQuiz


MAX_PENDING_QUIZZES = 128
PENDING_QUIZ_TTL = timedelta(hours=24)
PENDING_QUIZ_WORKFLOW = "pending_quiz"


class PendingQuizNotFoundError(LookupError):
    """Raised when a pending quiz expired, was submitted, or never existed."""


class QuizGenerationRejectedError(ValueError):
    """Raised when scoped evidence cannot support a quiz."""


@dataclass(frozen=True)
class QuizResponse:
    question_number: int
    selected_option: int | None


@dataclass(frozen=True)
class PresentedQuizQuestion:
    question_number: int
    question: str
    options: tuple[str, str, str, str]


@dataclass(frozen=True)
class PresentedQuiz:
    quiz_id: str
    requested_topic: str
    topic: str
    confidence: float
    questions: tuple[PresentedQuizQuestion, ...]


@dataclass(frozen=True)
class QuizFeedbackSource:
    index: int
    document_id: int | None
    notebook_id: int | None
    filename: str
    mime_type: str | None
    page_number: int | None
    slide_number: int | None
    chunk_index: int | None
    distance: float
    excerpt: str


@dataclass(frozen=True)
class QuizQuestionFeedback:
    question_number: int
    question: str
    selected_option: int | None
    correct_option: int
    is_correct: bool
    skipped: bool
    explanation: str
    sources: tuple[QuizFeedbackSource, ...]


@dataclass(frozen=True)
class QuizSubmissionResult:
    attempt_id: int
    status: str
    total_questions: int
    presented_questions: int
    answered_questions: int
    skipped_questions: int
    correct_answers: int
    score_percentage: float
    accuracy_percentage: float | None
    feedback: tuple[QuizQuestionFeedback, ...]


_registry_lock = RLock()


def _workflow_repository():
    return get_application_dependencies().workflows


def clear_quiz_registry() -> None:
    with _registry_lock:
        _workflow_repository().clear_type(PENDING_QUIZ_WORKFLOW)


def pending_quiz_count() -> int:
    with _registry_lock:
        return _workflow_repository().count_pending(PENDING_QUIZ_WORKFLOW)


def generate_quiz_for_api(
    topic: str,
    question_count: int,
    scope: RetrievalScope | None = None,
) -> PresentedQuiz:
    generated = generate_grounded_quiz(
        topic,
        question_count,
        scope=scope,
    )
    if not generated.quiz.should_generate:
        raise QuizGenerationRejectedError(generated.quiz.reason)

    quiz_id = str(uuid4())
    with _registry_lock:
        repository = _workflow_repository()
        repository.put(
            quiz_id,
            PENDING_QUIZ_WORKFLOW,
            _serialize_generated_quiz(generated),
            (datetime.now(timezone.utc) + PENDING_QUIZ_TTL).isoformat(),
        )
        repository.trim_pending(PENDING_QUIZ_WORKFLOW, MAX_PENDING_QUIZZES)

    questions = tuple(
        PresentedQuizQuestion(
            question_number=number,
            question=question.question,
            options=(
                question.options[0],
                question.options[1],
                question.options[2],
                question.options[3],
            ),
        )
        for number, question in enumerate(generated.quiz.questions, start=1)
    )
    return PresentedQuiz(
        quiz_id=quiz_id,
        requested_topic=generated.requested_topic,
        topic=generated.quiz.topic,
        confidence=generated.quiz.confidence,
        questions=questions,
    )


def score_quiz(
    generated: GeneratedGroundedQuiz,
    responses: list[QuizResponse],
) -> QuizRunResult:
    """Purely derive trusted correctness from a generated server quiz."""
    questions = generated.quiz.questions
    if len(responses) > len(questions):
        raise ValueError("Response count exceeds quiz question count.")

    expected_numbers = list(range(1, len(responses) + 1))
    actual_numbers = [response.question_number for response in responses]
    if actual_numbers != expected_numbers:
        raise ValueError(
            "Responses must be a contiguous presented-question prefix starting at 1."
        )

    attempts: list[QuizQuestionAttempt] = []
    for response in responses:
        selected_option = response.selected_option
        if selected_option is not None and (
            isinstance(selected_option, bool)
            or not 1 <= selected_option <= 4
        ):
            raise ValueError("Selected option must be null or between 1 and 4.")

        question = questions[response.question_number - 1]
        skipped = selected_option is None
        attempts.append(
            QuizQuestionAttempt(
                question_number=response.question_number,
                question=question.question,
                selected_option=selected_option,
                correct_option=question.correct_option,
                is_correct=(
                    selected_option is not None
                    and selected_option == question.correct_option
                ),
                skipped=skipped,
            )
        )

    return QuizRunResult(
        generated_quiz=generated,
        attempts=tuple(attempts),
        aborted=len(responses) < len(questions),
    )


def submit_quiz(
    quiz_id: str,
    responses: list[QuizResponse],
) -> QuizSubmissionResult:
    with _registry_lock:
        dependencies = get_application_dependencies()
        state = dependencies.workflows.get(quiz_id, PENDING_QUIZ_WORKFLOW)
        if state is None:
            raise PendingQuizNotFoundError(
                "Pending quiz was not found. It may have expired or been submitted."
            )

        generated = _deserialize_generated_quiz(state.payload)
        run_result = score_quiz(generated, responses)
        with dependencies.unit_of_work():
            stored_attempt, _stored_questions = dependencies.quizzes.save_run_result(
                run_result
            )
            dependencies.workflows.decide(
                state.id,
                state.version,
                "completed",
                {"attempt_id": stored_attempt.id, "action": "submitted"},
            )

    sources_by_index = {source.index: source for source in generated.sources}
    feedback: list[QuizQuestionFeedback] = []
    for attempt in run_result.attempts:
        question = generated.quiz.questions[attempt.question_number - 1]
        question_sources: list[QuizFeedbackSource] = []
        for source_index in question.source_indexes:
            source = sources_by_index[source_index]
            notebook_id: int | None = None
            if source.document_id is not None:
                try:
                    notebook_id = dependencies.notebooks.get_document_notebook_id(
                        source.document_id
                    )
                except LookupError:
                    notebook_id = None
            question_sources.append(
                QuizFeedbackSource(
                    index=source.index,
                    document_id=source.document_id,
                    notebook_id=notebook_id,
                    filename=source.filename,
                    mime_type=source.mime_type,
                    page_number=source.page_number,
                    slide_number=source.slide_number,
                    chunk_index=source.chunk_index,
                    distance=source.distance,
                    excerpt=source.text[:800],
                )
            )
        feedback.append(
            QuizQuestionFeedback(
                question_number=attempt.question_number,
                question=attempt.question,
                selected_option=attempt.selected_option,
                correct_option=attempt.correct_option,
                is_correct=attempt.is_correct,
                skipped=attempt.skipped,
                explanation=question.explanation,
                sources=tuple(question_sources),
            )
        )

    return QuizSubmissionResult(
        attempt_id=stored_attempt.id,
        status=stored_attempt.status,
        total_questions=stored_attempt.total_questions,
        presented_questions=stored_attempt.presented_questions,
        answered_questions=stored_attempt.answered_questions,
        skipped_questions=stored_attempt.skipped_questions,
        correct_answers=stored_attempt.correct_answers,
        score_percentage=stored_attempt.score_percentage,
        accuracy_percentage=stored_attempt.accuracy_percentage,
        feedback=tuple(feedback),
    )


def _serialize_generated_quiz(generated: GeneratedGroundedQuiz) -> dict[str, object]:
    return {
        "requested_topic": generated.requested_topic,
        "sources": [asdict(source) for source in generated.sources],
        "quiz": generated.quiz.model_dump(mode="json"),
    }


def _deserialize_generated_quiz(payload: dict[str, object]) -> GeneratedGroundedQuiz:
    raw_sources = payload.get("sources")
    raw_quiz = payload.get("quiz")
    requested_topic = payload.get("requested_topic")
    if (
        not isinstance(raw_sources, list)
        or not isinstance(raw_quiz, dict)
        or not isinstance(requested_topic, str)
    ):
        raise PendingQuizNotFoundError("The stored pending quiz is invalid.")
    return GeneratedGroundedQuiz(
        requested_topic=requested_topic,
        sources=tuple(
            RetrievedSource(**source)
            for source in raw_sources
            if isinstance(source, dict)
        ),
        quiz=GroundedQuiz.model_validate(raw_quiz),
    )
