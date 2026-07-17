from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from study.quiz_generator import (
    GeneratedGroundedQuiz,
)


# ============================================================
# RESULT MODELS
# ============================================================

@dataclass(frozen=True)
class QuizQuestionAttempt:
    """
    Learner response to one presented quiz question.
    """

    question_number: int
    question: str
    selected_option: int | None
    correct_option: int
    is_correct: bool
    skipped: bool


@dataclass(frozen=True)
class QuizRunResult:
    """
    Complete or partially completed interactive quiz run.
    """

    generated_quiz: GeneratedGroundedQuiz
    attempts: tuple[QuizQuestionAttempt, ...]
    aborted: bool

    @property
    def total_question_count(self) -> int:
        return len(
            self.generated_quiz.quiz.questions
        )

    @property
    def presented_question_count(self) -> int:
        return len(self.attempts)

    @property
    def answered_question_count(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.selected_option is not None
        )

    @property
    def skipped_question_count(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.skipped
        )

    @property
    def correct_answer_count(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.is_correct
        )

    @property
    def completed(self) -> bool:
        return (
            not self.aborted
            and self.presented_question_count
            == self.total_question_count
        )

    @property
    def score_percentage(self) -> float:
        """
        Score across every question in the quiz.

        Skipped and unattempted questions count as incorrect.
        """
        if self.total_question_count == 0:
            return 0.0

        return (
            self.correct_answer_count
            / self.total_question_count
            * 100
        )

    @property
    def accuracy_percentage(self) -> float | None:
        """
        Accuracy across questions the learner answered.

        Skipped and unattempted questions are excluded.
        """
        if self.answered_question_count == 0:
            return None

        return (
            self.correct_answer_count
            / self.answered_question_count
            * 100
        )


# ============================================================
# INPUT PARSING
# ============================================================

QuizInputAction = Literal[
    "answer",
    "skip",
    "back",
]


@dataclass(frozen=True)
class ParsedQuizInput:
    action: QuizInputAction
    selected_option: int | None = None


def parse_quiz_input(
    raw_value: str,
) -> ParsedQuizInput | None:
    """
    Parse terminal quiz input.

    Returns None when the input is invalid.
    """
    cleaned = raw_value.strip().lower()

    option_map = {
        "a": 1,
        "1": 1,
        "b": 2,
        "2": 2,
        "c": 3,
        "3": 3,
        "d": 4,
        "4": 4,
    }

    selected_option = option_map.get(
        cleaned
    )

    if selected_option is not None:
        return ParsedQuizInput(
            action="answer",
            selected_option=selected_option,
        )

    if cleaned in {
        "s",
        "skip",
    }:
        return ParsedQuizInput(
            action="skip"
        )

    if cleaned in {
        "/back",
        "/quit",
        "quit",
    }:
        return ParsedQuizInput(
            action="back"
        )

    return None


# ============================================================
# INTERACTIVE RUNNER
# ============================================================

def run_quiz_interactively(
    generated_quiz: GeneratedGroundedQuiz,
) -> QuizRunResult:
    """
    Present a generated quiz in the terminal.

    Correct answers are not displayed until the returned result
    is formatted after the quiz run.

    No database records are created or modified.
    """
    quiz = generated_quiz.quiz

    if not quiz.should_generate:
        raise ValueError(
            "Cannot run a quiz that was not generated. "
            f"Reason: {quiz.reason}"
        )

    if not quiz.questions:
        raise ValueError(
            "Cannot run a quiz containing no questions."
        )

    option_labels = (
        "A",
        "B",
        "C",
        "D",
    )

    attempts: list[
        QuizQuestionAttempt
    ] = []

    aborted = False

    print("\n" + "=" * 60)
    print("INTERACTIVE STUDY QUIZ")
    print("=" * 60)
    print(f"Topic: {quiz.topic}")
    print(
        f"Questions: {len(quiz.questions)}"
    )
    print(
        "Enter A-D or 1-4. "
        "Enter S to skip or /back to stop."
    )

    for question_number, question in enumerate(
        quiz.questions,
        start=1,
    ):
        print("\n" + "-" * 60)
        print(
            f"Question {question_number} of "
            f"{len(quiz.questions)}"
        )
        print(question.question)

        for label, option in zip(
            option_labels,
            question.options,
        ):
            print(f"  {label}. {option}")

        while True:
            try:
                raw_answer = input(
                    "\nYour answer: "
                )
            except (EOFError, KeyboardInterrupt):
                print(
                    "\nQuiz stopped."
                )
                aborted = True
                break

            parsed_input = parse_quiz_input(
                raw_answer
            )

            if parsed_input is None:
                print(
                    "Invalid answer. Enter A-D, 1-4, "
                    "S, or /back."
                )
                continue

            if parsed_input.action == "back":
                aborted = True
                print("Quiz stopped.")
                break

            if parsed_input.action == "skip":
                attempts.append(
                    QuizQuestionAttempt(
                        question_number=question_number,
                        question=question.question,
                        selected_option=None,
                        correct_option=(
                            question.correct_option
                        ),
                        is_correct=False,
                        skipped=True,
                    )
                )

                print("Question skipped.")
                break

            selected_option = (
                parsed_input.selected_option
            )

            if selected_option is None:
                raise RuntimeError(
                    "Parsed answer did not contain an option."
                )

            attempts.append(
                QuizQuestionAttempt(
                    question_number=question_number,
                    question=question.question,
                    selected_option=selected_option,
                    correct_option=(
                        question.correct_option
                    ),
                    is_correct=(
                        selected_option
                        == question.correct_option
                    ),
                    skipped=False,
                )
            )

            print("Answer recorded.")
            break

        if aborted:
            break

    return QuizRunResult(
        generated_quiz=generated_quiz,
        attempts=tuple(attempts),
        aborted=aborted,
    )


# ============================================================
# RESULT FORMATTING
# ============================================================

def format_quiz_run_result(
    result: QuizRunResult,
) -> str:
    """
    Display the quiz score and answer explanations.
    """
    quiz = result.generated_quiz.quiz

    accuracy_text = (
        f"{result.accuracy_percentage:.1f}%"
        if result.accuracy_percentage is not None
        else "N/A"
    )

    lines = [
        "=" * 60,
        "QUIZ RESULT",
        "=" * 60,
        (
            "Status: "
            + (
                "Completed"
                if result.completed
                else "Stopped early"
            )
        ),
        (
            "Questions in quiz: "
            f"{result.total_question_count}"
        ),
        (
            "Questions presented: "
            f"{result.presented_question_count}"
        ),
        (
            "Questions answered: "
            f"{result.answered_question_count}"
        ),
        (
            "Questions skipped: "
            f"{result.skipped_question_count}"
        ),
        (
            "Correct answers: "
            f"{result.correct_answer_count}"
        ),
        (
            "Overall score: "
            f"{result.score_percentage:.1f}%"
        ),
        (
            "Answered-question accuracy: "
            f"{accuracy_text}"
        ),
    ]

    option_labels = (
        "A",
        "B",
        "C",
        "D",
    )

    attempts_by_number = {
        attempt.question_number: attempt
        for attempt in result.attempts
    }

    used_source_indexes: set[int] = set()

    lines.extend(
        [
            "",
            "ANSWER REVIEW",
        ]
    )

    for question_number, question in enumerate(
        quiz.questions,
        start=1,
    ):
        attempt = attempts_by_number.get(
            question_number
        )

        correct_label = option_labels[
            question.correct_option - 1
        ]

        correct_text = question.options[
            question.correct_option - 1
        ]

        lines.extend(
            [
                "",
                "-" * 60,
                (
                    f"{question_number}. "
                    f"{question.question}"
                ),
            ]
        )

        if attempt is None:
            lines.append(
                "Your answer: Not attempted"
            )
            lines.append("Result: Incorrect")

        elif attempt.skipped:
            lines.append(
                "Your answer: Skipped"
            )
            lines.append("Result: Incorrect")

        else:
            selected_option = (
                attempt.selected_option
            )

            if selected_option is None:
                raise RuntimeError(
                    "Answered attempt is missing its "
                    "selected option."
                )

            selected_label = option_labels[
                selected_option - 1
            ]

            selected_text = question.options[
                selected_option - 1
            ]

            lines.append(
                "Your answer: "
                f"{selected_label}. {selected_text}"
            )

            lines.append(
                "Result: "
                + (
                    "Correct"
                    if attempt.is_correct
                    else "Incorrect"
                )
            )

        lines.extend(
            [
                (
                    "Correct answer: "
                    f"{correct_label}. {correct_text}"
                ),
                (
                    "Explanation: "
                    f"{question.explanation}"
                ),
            ]
        )

        used_source_indexes.update(
            question.source_indexes
        )

    lines.extend(
        [
            "",
            "DOCUMENT SOURCES",
        ]
    )

    source_found = False

    for source in result.generated_quiz.sources:
        if source.index not in used_source_indexes:
            continue

        source_found = True

        source_label = (
            f"[{source.index}] "
            f"{source.filename}"
        )

        if source.page_number is not None:
            source_label += (
                f", page {source.page_number}"
            )

        lines.append(f"- {source_label}")

    if not source_found:
        lines.append(
            "- No cited document sources"
        )

    return "\n".join(lines)