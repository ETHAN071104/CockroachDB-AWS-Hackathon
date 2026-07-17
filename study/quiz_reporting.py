from __future__ import annotations

from dataclasses import dataclass

from study.database import (
    StoredQuizAttempt,
    StoredQuizQuestionAttempt,
    StoredQuizQuestionSource,
    get_quiz_attempt,
    list_quiz_attempts,
    list_quiz_question_attempts,
    list_quiz_question_sources,
)


# ============================================================
# ATTEMPT REPORT MODELS
# ============================================================

@dataclass(frozen=True)
class QuizQuestionReport:
    question_attempt: StoredQuizQuestionAttempt
    sources: tuple[StoredQuizQuestionSource, ...]

    @property
    def status(self) -> str:
        question = self.question_attempt

        if not question.presented:
            return "not_presented"

        if question.skipped:
            return "skipped"

        if question.is_correct:
            return "correct"

        return "incorrect"


@dataclass(frozen=True)
class QuizAttemptReport:
    attempt: StoredQuizAttempt
    questions: tuple[QuizQuestionReport, ...]

    @property
    def source_filenames(self) -> tuple[str, ...]:
        filenames = {
            source.filename
            for question in self.questions
            for source in question.sources
        }

        return tuple(sorted(filenames))

    @property
    def incorrect_count(self) -> int:
        return sum(
            1
            for question in self.questions
            if question.status == "incorrect"
        )

    @property
    def skipped_count(self) -> int:
        return sum(
            1
            for question in self.questions
            if question.status == "skipped"
        )

    @property
    def not_presented_count(self) -> int:
        return sum(
            1
            for question in self.questions
            if question.status == "not_presented"
        )


# ============================================================
# PERFORMANCE MODELS
# ============================================================

@dataclass(frozen=True)
class QuizTopicPerformance:
    topic: str
    attempt_count: int
    total_questions: int
    answered_questions: int
    correct_answers: int

    @property
    def score_percentage(self) -> float:
        if self.total_questions == 0:
            return 0.0

        return (
            self.correct_answers
            / self.total_questions
            * 100
        )

    @property
    def accuracy_percentage(self) -> float | None:
        if self.answered_questions == 0:
            return None

        return (
            self.correct_answers
            / self.answered_questions
            * 100
        )


@dataclass(frozen=True)
class QuizReviewItem:
    quiz_attempt_id: int
    quiz_topic: str
    attempt_created_at: str
    question: QuizQuestionReport


@dataclass(frozen=True)
class QuizPerformanceReport:
    attempts: tuple[QuizAttemptReport, ...]
    topic_performance: tuple[QuizTopicPerformance, ...]
    review_items: tuple[QuizReviewItem, ...]
    source_filenames: tuple[str, ...]

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def completed_attempt_count(self) -> int:
        return sum(
            1
            for report in self.attempts
            if report.attempt.status == "completed"
        )

    @property
    def aborted_attempt_count(self) -> int:
        return sum(
            1
            for report in self.attempts
            if report.attempt.status == "aborted"
        )

    @property
    def total_questions(self) -> int:
        return sum(
            report.attempt.total_questions
            for report in self.attempts
        )

    @property
    def presented_questions(self) -> int:
        return sum(
            report.attempt.presented_questions
            for report in self.attempts
        )

    @property
    def answered_questions(self) -> int:
        return sum(
            report.attempt.answered_questions
            for report in self.attempts
        )

    @property
    def correct_answers(self) -> int:
        return sum(
            report.attempt.correct_answers
            for report in self.attempts
        )

    @property
    def overall_score_percentage(self) -> float:
        """
        Correct answers divided by all generated questions.

        Skipped and unreached questions count as incorrect.
        """
        if self.total_questions == 0:
            return 0.0

        return (
            self.correct_answers
            / self.total_questions
            * 100
        )

    @property
    def answered_accuracy_percentage(self) -> float | None:
        """
        Correct answers divided by questions actually answered.
        """
        if self.answered_questions == 0:
            return None

        return (
            self.correct_answers
            / self.answered_questions
            * 100
        )


# ============================================================
# ATTEMPT REPORT
# ============================================================

def build_quiz_attempt_report(
    quiz_attempt_id: int,
) -> QuizAttemptReport:
    attempt = get_quiz_attempt(
        quiz_attempt_id
    )

    if attempt is None:
        raise ValueError(
            f"Quiz attempt ID {quiz_attempt_id} does not exist."
        )

    stored_questions = list_quiz_question_attempts(
        quiz_attempt_id
    )

    questions = tuple(
        QuizQuestionReport(
            question_attempt=question,
            sources=tuple(
                list_quiz_question_sources(
                    question.id
                )
            ),
        )
        for question in stored_questions
    )

    if len(questions) != attempt.total_questions:
        raise RuntimeError(
            "Stored quiz question count does not match the "
            "quiz-attempt total."
        )

    presented_count = sum(
        1
        for question in stored_questions
        if question.presented
    )

    answered_count = sum(
        1
        for question in stored_questions
        if (
            question.presented
            and not question.skipped
            and question.selected_option is not None
        )
    )

    skipped_count = sum(
        1
        for question in stored_questions
        if question.skipped
    )

    correct_count = sum(
        1
        for question in stored_questions
        if question.is_correct
    )

    if presented_count != attempt.presented_questions:
        raise RuntimeError(
            "Stored presented-question count is inconsistent."
        )

    if answered_count != attempt.answered_questions:
        raise RuntimeError(
            "Stored answered-question count is inconsistent."
        )

    if skipped_count != attempt.skipped_questions:
        raise RuntimeError(
            "Stored skipped-question count is inconsistent."
        )

    if correct_count != attempt.correct_answers:
        raise RuntimeError(
            "Stored correct-answer count is inconsistent."
        )

    return QuizAttemptReport(
        attempt=attempt,
        questions=questions,
    )


# ============================================================
# OVERALL PERFORMANCE
# ============================================================

def build_quiz_performance_report(
    attempt_limit: int | None = None,
) -> QuizPerformanceReport:
    if (
        attempt_limit is not None
        and attempt_limit <= 0
    ):
        raise ValueError(
            "Quiz-attempt limit must be greater than zero."
        )

    attempts = list_quiz_attempts(
        limit=attempt_limit
    )

    attempt_reports = tuple(
        build_quiz_attempt_report(
            attempt.id
        )
        for attempt in attempts
    )

    topic_groups: dict[
        str,
        dict[str, object],
    ] = {}

    review_items: list[QuizReviewItem] = []
    source_filenames: set[str] = set()

    for report in attempt_reports:
        attempt = report.attempt

        normalized_topic = (
            attempt.quiz_topic
            .strip()
            .casefold()
        )

        if normalized_topic not in topic_groups:
            topic_groups[normalized_topic] = {
                "topic": attempt.quiz_topic,
                "attempt_count": 0,
                "total_questions": 0,
                "answered_questions": 0,
                "correct_answers": 0,
            }

        group = topic_groups[
            normalized_topic
        ]

        group["attempt_count"] = (
            int(group["attempt_count"])
            + 1
        )

        group["total_questions"] = (
            int(group["total_questions"])
            + attempt.total_questions
        )

        group["answered_questions"] = (
            int(group["answered_questions"])
            + attempt.answered_questions
        )

        group["correct_answers"] = (
            int(group["correct_answers"])
            + attempt.correct_answers
        )

        source_filenames.update(
            report.source_filenames
        )

        for question in report.questions:
            if question.status not in {
                "incorrect",
                "skipped",
            }:
                continue

            review_items.append(
                QuizReviewItem(
                    quiz_attempt_id=attempt.id,
                    quiz_topic=attempt.quiz_topic,
                    attempt_created_at=(
                        attempt.created_at
                    ),
                    question=question,
                )
            )

    topic_performance = tuple(
        sorted(
            (
                QuizTopicPerformance(
                    topic=str(group["topic"]),
                    attempt_count=int(
                        group["attempt_count"]
                    ),
                    total_questions=int(
                        group["total_questions"]
                    ),
                    answered_questions=int(
                        group["answered_questions"]
                    ),
                    correct_answers=int(
                        group["correct_answers"]
                    ),
                )
                for group in topic_groups.values()
            ),
            key=lambda item: (
                item.attempt_count,
                item.total_questions,
                item.topic.casefold(),
            ),
            reverse=True,
        )
    )

    return QuizPerformanceReport(
        attempts=attempt_reports,
        topic_performance=topic_performance,
        review_items=tuple(review_items),
        source_filenames=tuple(
            sorted(source_filenames)
        ),
    )


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_quiz_attempt_report(
    report: QuizAttemptReport,
) -> str:
    attempt = report.attempt

    accuracy_text = (
        f"{attempt.accuracy_percentage:.1f}%"
        if attempt.accuracy_percentage is not None
        else "N/A"
    )

    lines = [
        "=" * 60,
        f"QUIZ ATTEMPT {attempt.id}",
        "=" * 60,
        f"Status: {attempt.status}",
        f"Requested topic: {attempt.requested_topic}",
        f"Quiz topic: {attempt.quiz_topic}",
        f"Created: {attempt.created_at}",
        f"Questions: {attempt.total_questions}",
        f"Presented: {attempt.presented_questions}",
        f"Answered: {attempt.answered_questions}",
        f"Skipped: {attempt.skipped_questions}",
        f"Correct: {attempt.correct_answers}",
        f"Overall score: {attempt.score_percentage:.1f}%",
        f"Answered accuracy: {accuracy_text}",
        f"Generation confidence: {attempt.confidence:.2f}",
        "",
        "QUESTION REVIEW",
    ]

    option_labels = (
        "A",
        "B",
        "C",
        "D",
    )

    for question_report in report.questions:
        question = question_report.question_attempt

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
                    f"{question.question_number}. "
                    f"{question.question}"
                ),
                (
                    "Status: "
                    f"{question_report.status}"
                ),
            ]
        )

        if question.selected_option is not None:
            selected_label = option_labels[
                question.selected_option - 1
            ]

            selected_text = question.options[
                question.selected_option - 1
            ]

            lines.append(
                "Selected answer: "
                f"{selected_label}. {selected_text}"
            )

        elif question.skipped:
            lines.append(
                "Selected answer: Skipped"
            )

        else:
            lines.append(
                "Selected answer: Not presented"
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
                "Sources:",
            ]
        )

        if question_report.sources:
            for source in question_report.sources:
                label = (
                    f"[{source.source_index}] "
                    f"{source.filename}"
                )

                if source.page_number is not None:
                    label += (
                        f", page {source.page_number}"
                    )

                lines.append(f"- {label}")
        else:
            lines.append(
                "- No source lineage recorded"
            )

    return "\n".join(lines)


def format_quiz_performance_report(
    report: QuizPerformanceReport,
) -> str:
    accuracy_text = (
        f"{report.answered_accuracy_percentage:.1f}%"
        if report.answered_accuracy_percentage is not None
        else "N/A"
    )

    lines = [
        "=" * 60,
        "QUIZ PERFORMANCE",
        "=" * 60,
        f"Attempts: {report.attempt_count}",
        (
            "Completed attempts: "
            f"{report.completed_attempt_count}"
        ),
        (
            "Aborted attempts: "
            f"{report.aborted_attempt_count}"
        ),
        (
            "Generated questions: "
            f"{report.total_questions}"
        ),
        (
            "Presented questions: "
            f"{report.presented_questions}"
        ),
        (
            "Answered questions: "
            f"{report.answered_questions}"
        ),
        (
            "Correct answers: "
            f"{report.correct_answers}"
        ),
        (
            "Overall score: "
            f"{report.overall_score_percentage:.1f}%"
        ),
        (
            "Answered accuracy: "
            f"{accuracy_text}"
        ),
    ]

    if not report.attempts:
        lines.extend(
            [
                "",
                "No stored quiz attempts were found.",
            ]
        )

        return "\n".join(lines)

    lines.extend(
        [
            "",
            "PERFORMANCE BY TOPIC",
        ]
    )

    for topic in report.topic_performance:
        topic_accuracy = (
            f"{topic.accuracy_percentage:.1f}%"
            if topic.accuracy_percentage is not None
            else "N/A"
        )

        lines.extend(
            [
                "",
                f"- {topic.topic}",
                (
                    "  Attempts: "
                    f"{topic.attempt_count}"
                ),
                (
                    "  Questions: "
                    f"{topic.total_questions}"
                ),
                (
                    "  Score: "
                    f"{topic.score_percentage:.1f}%"
                ),
                (
                    "  Answered accuracy: "
                    f"{topic_accuracy}"
                ),
            ]
        )

    lines.extend(
        [
            "",
            "RECENT ATTEMPTS",
        ]
    )

    for attempt_report in report.attempts[:10]:
        attempt = attempt_report.attempt

        lines.append(
            f"- Attempt {attempt.id}: "
            f"{attempt.quiz_topic} — "
            f"{attempt.status}, "
            f"{attempt.score_percentage:.1f}%"
        )

    lines.extend(
        [
            "",
            "QUESTIONS REQUIRING REVIEW",
        ]
    )

    if report.review_items:
        for item in report.review_items[:20]:
            question = item.question.question_attempt

            lines.append(
                f"- Attempt {item.quiz_attempt_id} "
                f"[{item.question.status}] "
                f"{question.question}"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "DOCUMENTS USED",
        ]
    )

    if report.source_filenames:
        lines.extend(
            f"- {filename}"
            for filename in report.source_filenames
        )
    else:
        lines.append(
            "- No source lineage recorded"
        )

    return "\n".join(lines)