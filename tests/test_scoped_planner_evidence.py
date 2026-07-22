from __future__ import annotations

import sqlite3
import tempfile
import unittest

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import backend.study.database as study_database
import backend.study.planner as planner
import backend.rag.database as rag_database

from backend.application.dependencies import configure_application_dependencies
from backend.memory.database import initialize_memory_database
from backend.rag.scope import RetrievalScope, ResolvedRetrievalScope
from backend.repositories.sqlite import initialize_foundation_schema


class ScopedAdaptivePlanEvidenceTest(unittest.TestCase):
    """Protect scope boundaries before planner grouping and deduplication."""

    TOPIC_ID = "3e8df186-62ef-45b4-a80b-21831ea71cc5"

    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_dir.name) / "scoped_planner.db"
        )

        @contextmanager
        def temporary_connection():
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        self.temporary_connection = temporary_connection
        self.connection_patch = patch.object(
            study_database,
            "get_connection",
            temporary_connection,
        )
        self.connection_patch.start()
        self.database_path_patch = patch.object(
            rag_database,
            "DATABASE_PATH",
            self.database_path,
        )
        self.database_path_patch.start()
        self.directories_patch = patch.object(
            rag_database,
            "ensure_directories",
        )
        self.directories_patch.start()
        rag_database.initialize_database()
        initialize_memory_database()
        study_database.initialize_study_database()
        initialize_foundation_schema()
        configure_application_dependencies(None)

    def tearDown(self) -> None:
        configure_application_dependencies(None)
        self.directories_patch.stop()
        self.database_path_patch.stop()
        self.connection_patch.stop()
        self.temporary_dir.cleanup()

    def test_document_scope_filters_interactions_before_question_grouping(
        self,
    ) -> None:
        inside_interaction_id = self._add_interaction(
            question="What is ATP?",
            outcome="confused",
            document_id=1,
            chunk_index=0,
            filename="inside.txt",
            created_at="2026-01-01T00:00:00+00:00",
        )
        self._add_interaction(
            question="  what IS atp!!!  ",
            outcome="understood",
            document_id=2,
            chunk_index=0,
            filename="outside.txt",
            created_at="2026-01-02T00:00:00+00:00",
        )

        plan = self._build_document_plan(document_id=1)

        self.assertEqual(plan.item_count, 1)
        item = plan.items[0]
        self.assertEqual(item.evidence[0].status, "confused")
        self.assertEqual(
            item.evidence[0].reference_id,
            inside_interaction_id,
        )
        self.assertEqual(item.source_document_ids, (1,))
        self.assertEqual(item.source_filenames, ("inside.txt",))
        self.assertNotIn("outside.txt", item.source_filenames)

    def test_outside_stronger_quiz_gap_cannot_suppress_inside_study_gap(
        self,
    ) -> None:
        inside_interaction_id = self._add_interaction(
            question="Explain cellular respiration.",
            outcome="partial",
            document_id=1,
            chunk_index=0,
            filename="inside-study.txt",
            created_at="2026-02-01T00:00:00+00:00",
        )
        self._add_quiz_gap(
            question=" explain CELLULAR respiration!!! ",
            status="incorrect",
            document_id=2,
            chunk_index=0,
            filename="outside-quiz.txt",
            created_at="2026-02-02T00:00:00+00:00",
        )

        plan = self._build_document_plan(document_id=1)

        self.assertEqual(plan.item_count, 1)
        item = plan.items[0]
        self.assertEqual(item.evidence[0].evidence_type, "study_outcome")
        self.assertEqual(
            item.evidence[0].reference_id,
            inside_interaction_id,
        )
        self.assertEqual(item.source_filenames, ("inside-study.txt",))

    def test_outside_stronger_study_gap_cannot_suppress_inside_quiz_gap(
        self,
    ) -> None:
        inside_attempt_id = self._add_quiz_gap(
            question="How does osmosis work?",
            status="incorrect",
            document_id=1,
            chunk_index=0,
            filename="inside-quiz.txt",
            created_at="2026-03-01T00:00:00+00:00",
        )
        self._add_interaction(
            question="HOW does osmosis work!",
            outcome="confused",
            document_id=2,
            chunk_index=0,
            filename="outside-study-one.txt",
            created_at="2026-03-02T00:00:00+00:00",
        )
        self._add_interaction(
            question="How does osmosis work???",
            outcome="confused",
            document_id=2,
            chunk_index=1,
            filename="outside-study-two.txt",
            created_at="2026-03-03T00:00:00+00:00",
        )

        plan = self._build_document_plan(document_id=1)

        self.assertEqual(plan.item_count, 1)
        item = plan.items[0]
        self.assertEqual(item.evidence[0].evidence_type, "quiz_result")
        self.assertEqual(item.evidence[0].reference_id, inside_attempt_id)
        self.assertEqual(item.source_filenames, ("inside-quiz.txt",))

    def test_topic_scope_filters_exact_pairs_before_quiz_grouping(
        self,
    ) -> None:
        inside_attempt_id = self._add_quiz_gap(
            question="Which molecule carries energy?",
            status="skipped",
            document_id=1,
            chunk_index=0,
            filename="exact-topic-chunk.txt",
            created_at="2026-04-01T00:00:00+00:00",
        )
        self._add_quiz_gap(
            question="which MOLECULE carries energy!!!",
            status="incorrect",
            document_id=1,
            chunk_index=1,
            filename="wrong-topic-chunk.txt",
            created_at="2026-04-02T00:00:00+00:00",
        )

        resolved_scope = ResolvedRetrievalScope(
            kind="topic",
            document_ids=(1,),
            source_pairs=((1, 0),),
            chroma_filter={
                "$and": [
                    {"document_id": {"$eq": 1}},
                    {"chunk_index": {"$eq": 0}},
                ]
            },
        )
        with patch.object(
            planner,
            "resolve_retrieval_scope",
            return_value=resolved_scope,
        ):
            plan = planner.build_adaptive_study_plan(
                scope=RetrievalScope(topic_id=self.TOPIC_ID),
            )

        self.assertEqual(plan.item_count, 1)
        item = plan.items[0]
        self.assertEqual(item.evidence[0].status, "skipped")
        self.assertEqual(item.evidence[0].reference_id, inside_attempt_id)
        self.assertEqual(item.priority_score, 55)
        self.assertEqual(item.source_filenames, ("exact-topic-chunk.txt",))
        self.assertNotIn("wrong-topic-chunk.txt", item.source_filenames)

    def _build_document_plan(self, *, document_id: int):
        resolved_scope = ResolvedRetrievalScope(
            kind="documents",
            document_ids=(document_id,),
            chroma_filter={
                "document_id": {"$in": [document_id]},
            },
        )
        with patch.object(
            planner,
            "resolve_retrieval_scope",
            return_value=resolved_scope,
        ):
            return planner.build_adaptive_study_plan(
                scope=RetrievalScope(document_ids=(document_id,)),
            )

    def _add_interaction(
        self,
        *,
        question: str,
        outcome: str,
        document_id: int,
        chunk_index: int,
        filename: str,
        created_at: str,
    ) -> int:
        session = study_database.create_study_session()
        interaction, _sources = (
            study_database.insert_study_interaction_with_sources(
                session_id=session.id,
                question=question,
                answer="Stored answer.",
                outcome=outcome,
                sources=[
                    study_database.StudySourceInput(
                        source_index=1,
                        filename=filename,
                        page_number=None,
                        chunk_index=chunk_index,
                        distance=0.1,
                        document_id=document_id,
                        mime_type="text/plain",
                        excerpt="Scoped evidence.",
                    )
                ],
            )
        )
        study_database.end_study_session(session.id)
        with self.temporary_connection() as connection:
            connection.execute(
                "UPDATE study_interactions SET created_at = ? WHERE id = ?",
                (created_at, interaction.id),
            )
        return interaction.id

    def _add_quiz_gap(
        self,
        *,
        question: str,
        status: str,
        document_id: int,
        chunk_index: int,
        filename: str,
        created_at: str,
    ) -> int:
        if status not in {"incorrect", "skipped"}:
            raise ValueError("Test quiz status must be incorrect or skipped.")

        skipped = status == "skipped"
        source = study_database.QuizQuestionSourceInput(
            source_index=1,
            filename=filename,
            page_number=None,
            chunk_index=chunk_index,
            distance=0.1,
            document_id=document_id,
            mime_type="text/plain",
            excerpt="Scoped quiz evidence.",
        )
        attempt, _questions = (
            study_database.insert_quiz_attempt_with_questions(
                requested_topic="Scoped regression",
                quiz_topic="Scoped regression",
                confidence=1.0,
                aborted=False,
                questions=[
                    study_database.QuizQuestionAttemptInput(
                        question_number=1,
                        question=question,
                        options=("A", "B", "C", "D"),
                        presented=True,
                        selected_option=None if skipped else 1,
                        correct_option=2,
                        is_correct=False,
                        skipped=skipped,
                        explanation="Grounded explanation [1].",
                        sources=(source,),
                    )
                ],
            )
        )
        with self.temporary_connection() as connection:
            connection.execute(
                "UPDATE quiz_attempts SET created_at = ? WHERE id = ?",
                (created_at, attempt.id),
            )
        return attempt.id


if __name__ == "__main__":
    unittest.main()
