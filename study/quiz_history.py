from __future__ import annotations

from study.database import (
    QuizQuestionAttemptInput,
    QuizQuestionSourceInput,
    StoredQuizAttempt,
    StoredQuizQuestionAttempt,
    insert_quiz_attempt_with_questions,
)
from study.quiz_runner import QuizRunResult


def build_quiz_question_inputs(
    result: QuizRunResult,
) -> list[QuizQuestionAttemptInput]:
    """
    Convert a terminal quiz result into validated database
    inputs.

    Every generated question is included, including questions
    not reached because the learner stopped early.
    """
    generated_quiz = result.generated_quiz
    quiz = generated_quiz.quiz

    if not quiz.should_generate:
        raise ValueError(
            "Cannot store a quiz that was not generated."
        )

    if not quiz.questions:
        raise ValueError(
            "Cannot store a quiz containing no questions."
        )

    attempts_by_number = {}

    for attempt in result.attempts:
        question_number = attempt.question_number

        if question_number in attempts_by_number:
            raise ValueError(
                "Quiz result contains duplicate question "
                f"attempt number {question_number}."
            )

        if not 1 <= question_number <= len(
            quiz.questions
        ):
            raise ValueError(
                "Quiz result contains an invalid question "
                f"number: {question_number}."
            )

        generated_question = quiz.questions[
            question_number - 1
        ]

        if (
            attempt.question.strip()
            != generated_question.question.strip()
        ):
            raise ValueError(
                "Stored quiz attempt question does not match "
                "the generated quiz."
            )

        if (
            attempt.correct_option
            != generated_question.correct_option
        ):
            raise ValueError(
                "Stored correct option does not match the "
                "generated quiz."
            )

        attempts_by_number[
            question_number
        ] = attempt

    if (
        not result.aborted
        and len(attempts_by_number)
        != len(quiz.questions)
    ):
        raise ValueError(
            "A completed quiz must contain an attempt for "
            "every generated question."
        )

    sources_by_index = {}

    for source in generated_quiz.sources:
        if source.index in sources_by_index:
            raise ValueError(
                "Generated quiz contains duplicate source "
                f"index {source.index}."
            )

        sources_by_index[
            source.index
        ] = source

    question_inputs: list[
        QuizQuestionAttemptInput
    ] = []

    for question_number, question in enumerate(
        quiz.questions,
        start=1,
    ):
        attempt = attempts_by_number.get(
            question_number
        )

        seen_source_indexes: set[int] = set()

        source_inputs: list[
            QuizQuestionSourceInput
        ] = []

        for source_index in question.source_indexes:
            if source_index in seen_source_indexes:
                raise ValueError(
                    "Quiz question contains duplicate source "
                    f"index {source_index}."
                )

            seen_source_indexes.add(
                source_index
            )

            source = sources_by_index.get(
                source_index
            )

            if source is None:
                raise ValueError(
                    f"Quiz question {question_number} cites "
                    f"missing source index {source_index}."
                )

            source_inputs.append(
                QuizQuestionSourceInput(
                    source_index=source.index,
                    filename=source.filename,
                    page_number=source.page_number,
                    chunk_index=source.chunk_index,
                    distance=source.distance,
                )
            )

        if len(question.options) != 4:
            raise ValueError(
                "Generated quiz question must contain exactly "
                "four options."
            )

        options = (
            question.options[0],
            question.options[1],
            question.options[2],
            question.options[3],
        )

        question_inputs.append(
            QuizQuestionAttemptInput(
                question_number=question_number,
                question=question.question,
                options=options,
                presented=attempt is not None,
                selected_option=(
                    attempt.selected_option
                    if attempt is not None
                    else None
                ),
                correct_option=question.correct_option,
                is_correct=(
                    attempt.is_correct
                    if attempt is not None
                    else False
                ),
                skipped=(
                    attempt.skipped
                    if attempt is not None
                    else False
                ),
                explanation=question.explanation,
                sources=tuple(source_inputs),
            )
        )

    return question_inputs


def save_quiz_run_result(
    result: QuizRunResult,
) -> tuple[
    StoredQuizAttempt,
    tuple[StoredQuizQuestionAttempt, ...],
]:
    """
    Atomically persist one completed or aborted quiz run.
    """
    generated_quiz = result.generated_quiz
    quiz = generated_quiz.quiz

    question_inputs = build_quiz_question_inputs(
        result
    )

    return insert_quiz_attempt_with_questions(
        requested_topic=(
            generated_quiz.requested_topic
        ),
        quiz_topic=quiz.topic,
        confidence=quiz.confidence,
        aborted=result.aborted,
        questions=question_inputs,
    )