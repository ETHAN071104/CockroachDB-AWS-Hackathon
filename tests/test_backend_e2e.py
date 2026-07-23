from __future__ import annotations

import sqlite3
import tempfile
import unittest

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import backend.study.database as study_database
import backend.study.integrity as study_integrity
import backend.rag.database as rag_database

from backend.application.dependencies import configure_application_dependencies
from backend.memory.database import initialize_memory_database
from backend.repositories.sqlite import initialize_foundation_schema

from backend.study.planner import (
    build_adaptive_study_plan,
)
from backend.study.progress import (
    build_progress_report,
)
from backend.study.quiz_reporting import (
    build_quiz_performance_report,
)
from backend.study.recommendations import (
    build_review_queue,
)
from backend.study.reporting import (
    build_session_report,
)


class BackendEndToEndTest(unittest.TestCase):
    """
    Exercise the complete deterministic study backend using
    an isolated temporary SQLite database.
    """

    def test_complete_backend_workflow(self) -> None:
        self.addCleanup(configure_application_dependencies, None)
        with tempfile.TemporaryDirectory() as temporary_dir:
            database_path = (
                Path(temporary_dir)
                / "test_app.db"
            )

            @contextmanager
            def temporary_connection():
                connection = sqlite3.connect(
                    database_path
                )

                connection.row_factory = sqlite3.Row

                connection.execute(
                    "PRAGMA foreign_keys = ON"
                )

                try:
                    yield connection
                    connection.commit()

                except Exception:
                    connection.rollback()
                    raise

                finally:
                    connection.close()

            with (
                patch.object(
                    rag_database,
                    "DATABASE_PATH",
                    database_path,
                ),
                patch.object(
                    rag_database,
                    "ensure_directories",
                ),
                patch.object(
                    study_database,
                    "get_connection",
                    temporary_connection,
                ),
                patch.object(
                    study_integrity,
                    "get_connection",
                    temporary_connection,
                ),
            ):
                rag_database.initialize_database()
                initialize_memory_database()
                initialize_foundation_schema()
                configure_application_dependencies(None)
                self._run_workflow(
                    temporary_connection
                )

    def _run_workflow(
        self,
        temporary_connection,
    ) -> None:
        # ----------------------------------------------------
        # INITIALIZE DATABASE
        # ----------------------------------------------------

        study_database.initialize_study_database()

        # ----------------------------------------------------
        # CREATE STUDY SESSION
        # ----------------------------------------------------

        session = (
            study_database.create_study_session()
        )

        self.assertEqual(
            session.status,
            "active",
        )

        study_source = (
            study_database.StudySourceInput(
                source_index=1,
                filename="memory_rag_test.txt",
                page_number=None,
                chunk_index=0,
                distance=0.25,
            )
        )

        interaction, stored_sources = (
            study_database
            .insert_study_interaction_with_sources(
                session_id=session.id,
                question=(
                    "Explain how Chroma distance relates "
                    "to vector similarity."
                ),
                answer=(
                    "A lower distance generally indicates "
                    "closer vector similarity."
                ),
                sources=[study_source],
                outcome="partial",
            )
        )

        self.assertEqual(
            interaction.session_id,
            session.id,
        )

        self.assertEqual(
            interaction.outcome,
            "partial",
        )

        self.assertEqual(
            len(stored_sources),
            1,
        )

        completed_session = (
            study_database.end_study_session(
                session.id
            )
        )

        self.assertEqual(
            completed_session.status,
            "completed",
        )

        self.assertIsNotNone(
            completed_session.ended_at
        )

        # ----------------------------------------------------
        # CREATE QUIZ ATTEMPT
        # ----------------------------------------------------

        quiz_source = (
            study_database
            .QuizQuestionSourceInput(
                source_index=1,
                filename="memory_rag_test.txt",
                page_number=None,
                chunk_index=0,
                distance=0.25,
            )
        )

        quiz_questions = [
            study_database
            .QuizQuestionAttemptInput(
                question_number=1,
                question=(
                    "What does a lower vector distance "
                    "usually indicate?"
                ),
                options=(
                    "Lower similarity",
                    "Closer similarity",
                    "A larger document",
                    "A missing embedding",
                ),
                presented=True,
                selected_option=1,
                correct_option=2,
                is_correct=False,
                skipped=False,
                explanation=(
                    "A lower distance generally indicates "
                    "closer vector similarity. [1]"
                ),
                sources=(quiz_source,),
            ),
            study_database
            .QuizQuestionAttemptInput(
                question_number=2,
                question=(
                    "Which component stores vector "
                    "embeddings in this test?"
                ),
                options=(
                    "SQLite",
                    "Chroma",
                    "The terminal",
                    "The PDF loader",
                ),
                presented=True,
                selected_option=2,
                correct_option=2,
                is_correct=True,
                skipped=False,
                explanation=(
                    "Chroma is used as the vector index "
                    "in this test material. [1]"
                ),
                sources=(quiz_source,),
            ),
        ]

        quiz_attempt, stored_questions = (
            study_database
            .insert_quiz_attempt_with_questions(
                requested_topic=(
                    "Chroma vector distance"
                ),
                quiz_topic=(
                    "Chroma Vector Similarity"
                ),
                confidence=0.9,
                aborted=False,
                questions=quiz_questions,
            )
        )

        self.assertEqual(
            quiz_attempt.total_questions,
            2,
)

        self.assertEqual(
            quiz_attempt.presented_questions,
            2,
        )

        self.assertEqual(
            quiz_attempt.answered_questions,
            2,
        )

        self.assertEqual(
            quiz_attempt.correct_answers,
            1,
        )

        self.assertAlmostEqual(
            quiz_attempt.score_percentage,
            50.0,
        )

        self.assertAlmostEqual(
            quiz_attempt.accuracy_percentage or 0,
            50.0,
        )

        self.assertEqual(
            len(stored_questions),
            2,
        )

        # ----------------------------------------------------
        # SESSION REPORT
        # ----------------------------------------------------

        session_report = build_session_report(
            session.id
        )

        self.assertEqual(
            session_report.interaction_count,
            1,
        )

        self.assertEqual(
            session_report
            .outcome_counts
            .partial,
            1,
        )

        self.assertEqual(
            session_report.source_filenames,
            ("memory_rag_test.txt",),
        )

        # ----------------------------------------------------
        # PROGRESS REPORT
        # ----------------------------------------------------

        progress_report = build_progress_report()

        self.assertEqual(
            progress_report.session_count,
            1,
        )

        self.assertEqual(
            progress_report.total_questions,
            1,
        )

        self.assertEqual(
            progress_report
            .outcome_counts
            .partial,
            1,
        )

        # ----------------------------------------------------
        # REVIEW QUEUE
        # ----------------------------------------------------

        review_queue = build_review_queue()

        self.assertEqual(
            review_queue.recommendation_count,
            1,
        )

        recommendation = (
            review_queue.recommendations[0]
        )

        self.assertEqual(
            recommendation.outcome,
            "partial",
        )

        self.assertEqual(
            recommendation.interaction_id,
            interaction.id,
        )

        # ----------------------------------------------------
        # QUIZ PERFORMANCE
        # ----------------------------------------------------

        quiz_performance = (
            build_quiz_performance_report()
        )

        self.assertEqual(
            quiz_performance.attempt_count,
            1,
        )

        self.assertEqual(
            quiz_performance.total_questions,
            2,
        )

        self.assertEqual(
            quiz_performance.correct_answers,
            1,
        )

        self.assertEqual(
            len(quiz_performance.review_items),
            1,
        )

        # ----------------------------------------------------
        # ADAPTIVE PLAN
        # ----------------------------------------------------

        adaptive_plan = (
            build_adaptive_study_plan(
                total_minutes=45,
                max_items=5,
            )
        )

        self.assertGreaterEqual(
            adaptive_plan.item_count,
            1,
        )

        self.assertLessEqual(
            adaptive_plan.allocated_minutes,
            adaptive_plan.requested_minutes,
        )

        evidence_types = {
            evidence.evidence_type
            for item in adaptive_plan.items
            for evidence in item.evidence
        }

        self.assertIn(
            "study_outcome",
            evidence_types,
        )

        self.assertIn(
            "quiz_result",
            evidence_types,
        )

        # ----------------------------------------------------
        # INTEGRITY CHECK
        # ----------------------------------------------------

        integrity_report = (
            study_integrity
            .run_study_integrity_check()
        )

        self.assertTrue(
            integrity_report.passed,
            msg=(
                "Integrity issues were detected: "
                f"{integrity_report.issues}"
            ),
        )

        self.assertEqual(
            integrity_report.error_count,
            0,
        )

        # ----------------------------------------------------
        # FINAL DATABASE COUNTS
        # ----------------------------------------------------

        with temporary_connection() as connection:
            expected_counts = {
                "study_sessions": 1,
                "study_interactions": 1,
                "study_interaction_sources": 1,
                "quiz_attempts": 1,
                "quiz_question_attempts": 2,
                "quiz_question_sources": 2,
            }

            for table_name, expected_count in (
                expected_counts.items()
            ):
                actual_count = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {table_name}
                    """
                ).fetchone()[0]

                self.assertEqual(
                    actual_count,
                    expected_count,
                    msg=(
                        f"Unexpected record count for "
                        f"{table_name}."
                    ),
                )


if __name__ == "__main__":
    unittest.main()
