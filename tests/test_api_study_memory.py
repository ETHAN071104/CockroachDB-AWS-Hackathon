from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from langchain_core.documents import Document

import backend.memory.consolidation_registry as consolidation_registry
import backend.memory.database as memory_database
import backend.memory.proposals as memory_proposals
import backend.memory.service as memory_service
import backend.rag.chat_service as chat_service
import backend.rag.database as rag_database
import backend.study.database as study_database
from backend.memory.conflict_detector import MemoryConflictResult
from backend.memory.consolidator import MemoryConsolidationProposal
from backend.memory.models import MemoryCandidate, MemoryConsolidationCandidate
from backend.memory.validator import MemoryValidationResult
from backend.rag.notebooks import NotebookNotFoundError, assign_document_to_notebook, create_notebook
from backend.rag.rag_service import RetrievedSource


app_module = import_module("backend.api.app")


class StudyLineageMigrationTest(unittest.TestCase):
    def test_additive_lineage_migration_keeps_legacy_sources_readable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            with closing(sqlite3.connect(database_path)) as connection:
                self._create_legacy_study_tables(connection)
                self._insert_legacy_rows(connection)
                connection.commit()

            with (
                patch.object(rag_database, "DATABASE_PATH", database_path),
                patch.object(rag_database, "ensure_directories"),
            ):
                study_database.initialize_study_database()
                study_database.initialize_study_database()
                study_sources = study_database.list_interaction_sources(1)
                quiz_sources = study_database.list_quiz_question_sources(1)

            self.assertEqual(len(study_sources), 1)
            self.assertEqual(study_sources[0].filename, "legacy.txt")
            self.assertEqual(study_sources[0].page_number, 4)
            self.assertIsNone(study_sources[0].document_id)
            self.assertIsNone(study_sources[0].notebook_id)
            self.assertIsNone(study_sources[0].mime_type)
            self.assertIsNone(study_sources[0].slide_number)
            self.assertIn(study_sources[0].excerpt, {None, ""})

            self.assertEqual(len(quiz_sources), 1)
            self.assertEqual(quiz_sources[0].filename, "legacy.txt")
            self.assertIsNone(quiz_sources[0].document_id)
            self.assertIsNone(quiz_sources[0].notebook_id)
            self.assertIsNone(quiz_sources[0].mime_type)
            self.assertIsNone(quiz_sources[0].slide_number)
            self.assertIn(quiz_sources[0].excerpt, {None, ""})

            with closing(sqlite3.connect(database_path)) as connection:
                for table_name in (
                    "study_interaction_sources",
                    "quiz_question_sources",
                ):
                    columns = {
                        row[1]
                        for row in connection.execute(
                            f"PRAGMA table_info('{table_name}')"
                        ).fetchall()
                    }
                    self.assertTrue(
                        {
                            "document_id",
                            "notebook_id",
                            "mime_type",
                            "slide_number",
                            "excerpt",
                        }
                        <= columns
                    )

    @staticmethod
    def _create_legacy_study_tables(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );
            CREATE TABLE study_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE study_interaction_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interaction_id INTEGER NOT NULL,
                source_index INTEGER NOT NULL,
                filename TEXT NOT NULL,
                page_number INTEGER,
                chunk_index INTEGER,
                distance REAL NOT NULL
            );
            CREATE TABLE quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_topic TEXT NOT NULL,
                quiz_topic TEXT NOT NULL,
                status TEXT NOT NULL,
                total_questions INTEGER NOT NULL,
                presented_questions INTEGER NOT NULL,
                answered_questions INTEGER NOT NULL,
                skipped_questions INTEGER NOT NULL,
                correct_answers INTEGER NOT NULL,
                score_percentage REAL NOT NULL,
                accuracy_percentage REAL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE quiz_question_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_attempt_id INTEGER NOT NULL,
                question_number INTEGER NOT NULL,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                presented INTEGER NOT NULL,
                selected_option INTEGER,
                correct_option INTEGER NOT NULL,
                is_correct INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                explanation TEXT NOT NULL
            );
            CREATE TABLE quiz_question_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_attempt_id INTEGER NOT NULL,
                source_index INTEGER NOT NULL,
                filename TEXT NOT NULL,
                page_number INTEGER,
                chunk_index INTEGER,
                distance REAL
            );
            """
        )

    @staticmethod
    def _insert_legacy_rows(connection: sqlite3.Connection) -> None:
        timestamp = "2026-07-18T00:00:00+00:00"
        connection.execute(
            "INSERT INTO study_sessions VALUES (1, 'active', ?, NULL)",
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO study_interactions
            VALUES (1, 1, 'Legacy question?', 'Legacy answer.', 'unrated', ?)
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO study_interaction_sources
            VALUES (1, 1, 1, 'legacy.txt', 4, 0, 0.2)
            """
        )
        connection.execute(
            """
            INSERT INTO quiz_attempts VALUES (
                1, 'legacy', 'Legacy quiz', 'completed',
                1, 1, 1, 0, 1, 100.0, 100.0, 0.9, ?
            )
            """,
            (timestamp,),
        )
        connection.execute(
            """
            INSERT INTO quiz_question_attempts VALUES (
                1, 1, 1, 'Legacy quiz question?',
                '["A","B","C","D"]', 1, 1, 1, 1, 0,
                'Legacy explanation.'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO quiz_question_sources
            VALUES (1, 1, 1, 'legacy.txt', 4, 0, 0.2)
            """
        )


class ActiveSessionConcurrencyTest(unittest.TestCase):
    def test_concurrent_get_or_create_returns_one_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "sessions.db"
            with (
                patch.object(rag_database, "DATABASE_PATH", database_path),
                patch.object(rag_database, "ensure_directories"),
            ):
                study_database.initialize_study_database()
                workers = 8
                barrier = threading.Barrier(workers)

                def get_session_id() -> int:
                    barrier.wait(timeout=5)
                    return study_database.get_or_create_active_study_session().id

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    session_ids = list(
                        executor.map(lambda _index: get_session_id(), range(workers))
                    )

                self.assertEqual(len(set(session_ids)), 1)
                with study_database.get_connection() as connection:
                    active_count = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM study_sessions
                        WHERE status = 'active'
                        """
                    ).fetchone()[0]
                    unique_indexes = connection.execute(
                        "PRAGMA index_list(study_sessions)"
                    ).fetchall()

            self.assertEqual(active_count, 1)
            self.assertTrue(
                any(bool(row[2]) and bool(row[4]) for row in unique_indexes),
                "Active-session invariant needs a partial unique index.",
            )


class FakeMemoryVectorStore:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}

    def add_documents(
        self,
        *,
        documents: list[Document],
        ids: list[str],
    ) -> None:
        self.documents.update(zip(ids, documents, strict=True))

    def delete(self, *, ids: list[str]) -> None:
        for vector_id in ids:
            self.documents.pop(vector_id, None)

    def similarity_search_with_score(
        self,
        *,
        query: str,
        k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[Document, float]]:
        del query, filter
        return [(document, 0.1) for document in self.documents.values()][:k]


class MemoryRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        database_path = Path(temporary_directory) / "backend.memory.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()
        memory_database.initialize_memory_database()
        self.vector_store = FakeMemoryVectorStore()
        self.stack.enter_context(
            patch.object(
                memory_service,
                "get_memory_vector_store",
                return_value=self.vector_store,
            )
        )
        self.stack.enter_context(
            patch.object(
                memory_service,
                "delete_memory_vector",
                side_effect=lambda memory_id: self.vector_store.delete(
                    ids=[f"memory-{memory_id}"]
                ),
            )
        )
        memory_proposals.clear_memory_proposals()
        consolidation_registry.clear_memory_consolidations()
        self.addCleanup(memory_proposals.clear_memory_proposals)
        self.addCleanup(consolidation_registry.clear_memory_consolidations)

    def test_accept_cancel_reject_and_keep_both_decisions(self) -> None:
        new_candidate = self._candidate(
            "The learner prefers concise worked examples."
        )
        accepted_proposal = self._proposal(
            new_candidate,
            self._conflict("new"),
        )
        accepted = memory_proposals.decide_memory_proposal(
            accepted_proposal.id,
            "accept",
        )
        self.assertTrue(accepted.consumed)
        self.assertIsNotNone(accepted.saved_memory)
        self.assertIsNone(
            memory_proposals.get_memory_proposal(accepted_proposal.id)
        )

        cancelled_proposal = self._proposal(
            self._candidate("The learner studies best before lunch."),
            self._conflict("new"),
        )
        cancelled = memory_proposals.decide_memory_proposal(
            cancelled_proposal.id,
            "cancel",
        )
        self.assertFalse(cancelled.consumed)
        self.assertIsNotNone(
            memory_proposals.get_memory_proposal(cancelled_proposal.id)
        )
        rejected = memory_proposals.decide_memory_proposal(
            cancelled_proposal.id,
            "reject",
        )
        self.assertTrue(rejected.consumed)
        self.assertIsNone(
            memory_proposals.get_memory_proposal(cancelled_proposal.id)
        )

        existing = memory_service.add_memory(
            "profile",
            "The learner prefers diagrams.",
            0.9,
            0.7,
        )
        keep_both_proposal = self._proposal(
            self._candidate("The learner also prefers worked examples."),
            self._conflict("refinement", existing),
        )
        kept = memory_proposals.decide_memory_proposal(
            keep_both_proposal.id,
            "keep_both",
        )
        self.assertTrue(kept.consumed)
        self.assertNotEqual(kept.saved_memory.id, existing.id)
        self.assertEqual(memory_database.get_memory(existing.id).status, "active")

    def test_replace_uses_server_conflict_target_and_archives_existing(self) -> None:
        existing = memory_service.add_memory(
            "profile",
            "The learner prefers long explanations.",
            0.9,
            0.6,
        )
        unrelated = memory_service.add_memory(
            "profile",
            "The learner likes diagrams.",
            0.8,
            0.6,
        )
        proposal = self._proposal(
            self._candidate("The learner now prefers concise explanations."),
            self._conflict("contradiction", existing),
        )

        with self.assertRaises(memory_proposals.MemoryProposalDecisionError):
            memory_proposals.decide_memory_proposal(
                proposal.id,
                "replace",
                replace_memory_id=unrelated.id,
            )
        self.assertIsNotNone(memory_proposals.get_memory_proposal(proposal.id))

        result = memory_proposals.decide_memory_proposal(
            proposal.id,
            "replace",
            replace_memory_id=existing.id,
        )
        self.assertTrue(result.consumed)
        self.assertEqual(
            memory_database.get_memory(existing.id).status,
            "archived",
        )
        self.assertEqual(result.saved_memory.status, "active")
        self.assertEqual(
            result.saved_memory.content,
            "The learner now prefers concise explanations.",
        )
        self.assertEqual(memory_database.get_memory(unrelated.id).status, "active")

    def test_consolidation_is_two_step_registry_backed_and_stale_safe(
        self,
    ) -> None:
        first = memory_service.add_memory(
            "procedural",
            "The learner reviews notes after class.",
            0.85,
            0.6,
        )
        second = memory_service.add_memory(
            "procedural",
            "The learner makes flashcards after class.",
            0.85,
            0.6,
        )
        candidate = MemoryConsolidationCandidate(
            should_consolidate=True,
            memory_type="procedural",
            content=(
                "The learner reviews notes and makes flashcards after class."
            ),
            confidence=0.9,
            importance=0.75,
            reason="Compatible after-class routines.",
        )
        proposal_snapshot = MemoryConsolidationProposal(
            source_memories=(first, second),
            candidate=candidate,
        )
        with patch.object(
            consolidation_registry,
            "propose_memory_consolidation",
            return_value=proposal_snapshot,
        ):
            pending = consolidation_registry.create_memory_consolidation(
                [first.id, second.id]
            )

        memory_database.update_memory_record(
            memory_id=first.id,
            memory_type=first.memory_type,
            content="The learner changed this routine.",
            confidence=first.confidence,
            importance=first.importance,
        )
        with self.assertRaises(RuntimeError):
            consolidation_registry.apply_pending_memory_consolidation(
                pending.id
            )
        self.assertIsNotNone(
            consolidation_registry.get_memory_consolidation(pending.id)
        )

        current_first = memory_database.get_memory(first.id)
        current_second = memory_database.get_memory(second.id)
        fresh_snapshot = MemoryConsolidationProposal(
            source_memories=(current_first, current_second),
            candidate=candidate,
        )
        with patch.object(
            consolidation_registry,
            "propose_memory_consolidation",
            return_value=fresh_snapshot,
        ):
            fresh = consolidation_registry.create_memory_consolidation(
                [first.id, second.id]
            )
        applied = consolidation_registry.apply_pending_memory_consolidation(
            fresh.id
        )
        self.assertEqual(applied.consolidated_memory.status, "active")
        self.assertEqual(memory_database.get_memory(first.id).status, "archived")
        self.assertEqual(memory_database.get_memory(second.id).status, "archived")
        self.assertIsNone(
            consolidation_registry.get_memory_consolidation(fresh.id)
        )

    @staticmethod
    def _candidate(content: str) -> MemoryCandidate:
        return MemoryCandidate(
            should_store=True,
            memory_type="profile",
            content=content,
            confidence=0.92,
            importance=0.7,
            reason="Direct learner preference.",
        )

    @staticmethod
    def _conflict(
        conflict_type: str,
        existing: memory_database.StoredMemory | None = None,
    ) -> MemoryConflictResult:
        search_result = (
            memory_service.MemorySearchResult(
                memory_id=existing.id,
                memory_type=existing.memory_type,
                content=existing.content,
                confidence=existing.confidence,
                importance=existing.importance,
                distance=0.1,
            )
            if existing is not None
            else None
        )
        return MemoryConflictResult(
            conflict_type=conflict_type,
            existing_memory=search_result,
            confidence=0.9,
            reason="Test conflict.",
        )

    @staticmethod
    def _proposal(
        candidate: MemoryCandidate,
        conflict: MemoryConflictResult,
    ):
        with (
            patch.object(
                memory_proposals,
                "ENABLE_MEMORY_PROPOSALS",
                True,
            ),
            patch.object(
                memory_proposals,
                "propose_memory_candidate",
                return_value=candidate,
            ),
            patch.object(
                memory_proposals,
                "validate_memory_candidate",
                return_value=MemoryValidationResult(True, "accepted"),
            ),
            patch.object(
                memory_proposals,
                "detect_memory_conflict",
                return_value=conflict,
            ),
        ):
            return memory_proposals.create_memory_proposal(
                user_message="remember this",
                assistant_answer="acknowledged",
            )


class StudyMemoryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "backend.api.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()
        memory_database.initialize_memory_database()
        study_database.initialize_study_database()
        self.vector_store = FakeMemoryVectorStore()
        self.stack.enter_context(
            patch.object(
                memory_service,
                "get_memory_vector_store",
                return_value=self.vector_store,
            )
        )
        self.stack.enter_context(
            patch.object(
                memory_service,
                "delete_memory_vector",
                side_effect=lambda memory_id: self.vector_store.delete(
                    ids=[f"memory-{memory_id}"]
                ),
            )
        )
        memory_proposals.clear_memory_proposals()
        consolidation_registry.clear_memory_consolidations()
        self.addCleanup(memory_proposals.clear_memory_proposals)
        self.addCleanup(consolidation_registry.clear_memory_consolidations)
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value={"status": "ok", "collection_present": True},
            )
        )
        self.stack.enter_context(patch("backend.api.errors.LOGGER.error"))
        self.client = self.stack.enter_context(
            TestClient(
                app_module.create_app(allow_legacy_default_workspace=True),
                raise_server_exceptions=False,
            )
        )

    def test_memory_crud_search_archive_and_delete(self) -> None:
        created_response = self.client.post(
            "/api/memories",
            json={
                "memory_type": "profile",
                "content": "The learner prefers visual examples.",
                "confidence": 0.9,
                "importance": 0.7,
            },
        )
        self.assertEqual(created_response.status_code, 201, created_response.text)
        created = created_response.json()
        memory_id = created["id"]
        self.assertIn(f"memory-{memory_id}", self.vector_store.documents)

        listed = self.client.get("/api/memories")
        detail = self.client.get(f"/api/memories/{memory_id}")
        searched = self.client.get(
            "/api/memories/search",
            params={"q": "visual", "limit": 5},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["items"][0]["memory_id"], memory_id)

        updated = self.client.patch(
            f"/api/memories/{memory_id}",
            json={
                "content": "The learner prefers diagrams and visual examples.",
                "importance": 0.8,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["importance"], 0.8)

        archived = self.client.post(f"/api/memories/{memory_id}/archive")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["status"], "archived")
        self.assertEqual(self.client.get("/api/memories").json()["total"], 0)
        self.assertEqual(
            self.client.get(
                "/api/memories",
                params={"include_archived": True},
            ).json()["total"],
            1,
        )

        deleted = self.client.delete(f"/api/memories/{memory_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(
            self.client.get(f"/api/memories/{memory_id}").status_code,
            404,
        )

    def test_memory_proposal_decisions_use_uuid_registry_not_client_candidate(
        self,
    ) -> None:
        pending = self._proposal(
            self._candidate("The learner prefers concise examples."),
            self._conflict("new"),
        )
        UUID(pending.id)

        injected = self.client.post(
            f"/api/memories/proposals/{pending.id}/decision",
            json={
                "decision": "accept",
                "candidate": {
                    "memory_type": "profile",
                    "content": "Injected client candidate",
                },
            },
        )
        self.assertEqual(injected.status_code, 422)
        self.assertIsNotNone(memory_proposals.get_memory_proposal(pending.id))

        accepted = self.client.post(
            f"/api/memories/proposals/{pending.id}/decision",
            json={"decision": "accept"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["consumed"])
        self.assertEqual(
            accepted.json()["saved_memory"]["content"],
            "The learner prefers concise examples.",
        )

        cancelled_pending = self._proposal(
            self._candidate("The learner studies in the morning."),
            self._conflict("new"),
        )
        cancelled = self.client.post(
            f"/api/memories/proposals/{cancelled_pending.id}/decision",
            json={"decision": "cancel"},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(cancelled.json()["consumed"])
        rejected = self.client.post(
            f"/api/memories/proposals/{cancelled_pending.id}/decision",
            json={"decision": "reject"},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertTrue(rejected.json()["consumed"])

        existing = memory_service.add_memory(
            "profile",
            "The learner prefers lengthy explanations.",
            0.9,
            0.7,
        )
        replacement_pending = self._proposal(
            self._candidate("The learner now prefers concise explanations."),
            self._conflict("contradiction", existing),
        )
        keep_both_pending = self._proposal(
            self._candidate("The learner also prefers diagrams."),
            self._conflict("refinement", existing),
        )
        kept = self.client.post(
            f"/api/memories/proposals/{keep_both_pending.id}/decision",
            json={"decision": "keep_both"},
        )
        self.assertEqual(kept.status_code, 200)
        self.assertEqual(memory_database.get_memory(existing.id).status, "active")

        wrong_target = self.client.post(
            f"/api/memories/proposals/{replacement_pending.id}/decision",
            json={
                "decision": "replace",
                "replace_memory_id": kept.json()["saved_memory"]["id"],
            },
        )
        self.assertEqual(wrong_target.status_code, 409)
        replaced = self.client.post(
            f"/api/memories/proposals/{replacement_pending.id}/decision",
            json={
                "decision": "replace",
                "replace_memory_id": existing.id,
            },
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json()["archived_memory"]["status"], "archived")
        self.assertEqual(memory_database.get_memory(existing.id).status, "archived")

    def test_consolidation_api_requires_propose_then_apply(self) -> None:
        first = self._create_memory(
            "procedural",
            "The learner reviews notes after class.",
        )
        second = self._create_memory(
            "procedural",
            "The learner makes flashcards after class.",
        )
        source_memories = (
            memory_database.get_memory(first["id"]),
            memory_database.get_memory(second["id"]),
        )
        proposal = MemoryConsolidationProposal(
            source_memories=source_memories,
            candidate=MemoryConsolidationCandidate(
                should_consolidate=True,
                memory_type="procedural",
                content=(
                    "The learner reviews notes and makes flashcards after class."
                ),
                confidence=0.9,
                importance=0.75,
                reason="Compatible routines.",
            ),
        )
        with patch.object(
            consolidation_registry,
            "propose_memory_consolidation",
            return_value=proposal,
        ):
            proposed = self.client.post(
                "/api/memories/consolidation/propose",
                json={"memory_ids": [first["id"], second["id"]]},
            )
        self.assertEqual(proposed.status_code, 200, proposed.text)
        proposal_id = proposed.json()["proposal_id"]
        UUID(proposal_id)
        self.assertEqual(memory_database.get_memory(first["id"]).status, "active")
        self.assertEqual(memory_database.get_memory(second["id"]).status, "active")

        applied = self.client.post(
            "/api/memories/consolidation/apply",
            json={"proposal_id": proposal_id},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["consolidated_memory"]["status"], "active")
        self.assertTrue(
            all(
                item["status"] == "archived"
                for item in applied.json()["archived_source_memories"]
            )
        )
        repeated = self.client.post(
            f"/api/memories/consolidation/{proposal_id}/apply"
        )
        self.assertEqual(repeated.status_code, 404)

    def _create_memory(self, memory_type: str, content: str) -> dict[str, Any]:
        response = self.client.post(
            "/api/memories",
            json={
                "memory_type": memory_type,
                "content": content,
                "confidence": 0.9,
                "importance": 0.7,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def _candidate(content: str) -> MemoryCandidate:
        return MemoryRegistryTest._candidate(content)

    @staticmethod
    def _conflict(
        conflict_type: str,
        existing: memory_database.StoredMemory | None = None,
    ) -> MemoryConflictResult:
        return MemoryRegistryTest._conflict(conflict_type, existing)

    @staticmethod
    def _proposal(
        candidate: MemoryCandidate,
        conflict: MemoryConflictResult,
    ):
        return MemoryRegistryTest._proposal(candidate, conflict)


class DeterministicGetApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        database_path = Path(temporary_directory) / "reports.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        model_targets = (
            "backend.rag.intelligence.get_intelligence_model",
            "backend.study.quiz_generator.get_quiz_model",
            "backend.study.reviewer.get_review_action_model",
            "backend.study.coach.get_coaching_model",
            "backend.study.summarizer.get_summary_model",
            "backend.memory.consolidator.get_consolidation_model",
        )
        for target in model_targets:
            self.stack.enter_context(
                patch(target, side_effect=AssertionError("GET invoked an LLM"))
            )
        self.client = self.stack.enter_context(
            TestClient(
                app_module.create_app(allow_legacy_default_workspace=True),
                raise_server_exceptions=False,
            )
        )

    def test_reports_dashboard_and_integrity_gets_never_invoke_llm(self) -> None:
        paths = (
            "/api/reports/study/sessions",
            "/api/reports/study/progress",
            "/api/reports/quizzes/performance",
            "/api/study/actions/review-queue",
            "/api/dashboard",
            "/api/system/integrity",
        )
        payloads: dict[str, Any] = {}
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                payloads[path] = response.json()

        dashboard = payloads["/api/dashboard"]
        self.assertEqual(dashboard["counts"]["documents"], 0)
        self.assertEqual(dashboard["counts"]["study_sessions"], 0)
        self.assertEqual(dashboard["quiz"]["total"], 0)
        self.assertIsNone(dashboard["active_session"])
        integrity = payloads["/api/system/integrity"]
        self.assertTrue(integrity["passed"])
        self.assertEqual(integrity["error_count"], 0)


class ChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "chat.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()
        memory_database.initialize_memory_database()
        study_database.initialize_study_database()
        self.document_id = rag_database.insert_document(
            filename="lesson.pdf",
            mime_type="application/pdf",
            file_hash="chat-source-hash",
            file_data=b"lesson",
        )
        rag_database.update_chunk_count(self.document_id, 1)
        self.notebook = create_notebook("Lessons")
        assign_document_to_notebook(self.document_id, self.notebook.id)

        self.source = RetrievedSource(
            index=1,
            filename="lesson.pdf",
            page_number=6,
            chunk_index=0,
            distance=0.12,
            text="Mitochondria convert stored energy for cells.",
            document_id=self.document_id,
            mime_type="application/pdf",
        )
        self.answer_error: Exception | None = None
        self.last_scope = None
        self.pending_proposal = None

        def answer_question(question: str, scope=None, **_kwargs):
            self.last_scope = scope
            if self.answer_error is not None:
                raise self.answer_error
            return (
                f"Grounded answer for: {question} [1]",
                [self.source],
            )

        self.answer_mock = self.stack.enter_context(
            patch.object(
                chat_service.rag_service,
                "answer_question",
                side_effect=answer_question,
            )
        )
        self.stack.enter_context(
            patch.object(
                chat_service.memory_proposals,
                "create_memory_proposal",
                side_effect=lambda **_kwargs: self.pending_proposal,
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_vector_store",
                return_value={"status": "ok", "collection_present": True},
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        self.stack.enter_context(patch("backend.api.errors.LOGGER.error"))
        self.client = self.stack.enter_context(
            TestClient(
                app_module.create_app(allow_legacy_default_workspace=True),
                raise_server_exceptions=False,
            )
        )

    def test_chat_scopes_then_atomically_stores_unrated_lineage_and_proposal(
        self,
    ) -> None:
        self.pending_proposal = memory_proposals.PendingMemoryProposal(
            id=str(uuid4()),
            candidate=MemoryRegistryTest._candidate(
                "The learner prefers concise explanations."
            ),
            conflict=MemoryRegistryTest._conflict("new"),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        response = self.client.post(
            "/api/chat",
            json={
                "question": "What do mitochondria do?",
                "notebook_id": self.notebook.id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(self.last_scope.notebook_id, self.notebook.id)
        self.assertIsNone(self.last_scope.document_ids)
        self.assertEqual(
            payload["answer"],
            "Grounded answer for: What do mitochondria do? [1]",
        )
        self.assertEqual(payload["type"], "answer")
        self.assertEqual(payload["intent"], "document_question")
        self.assertEqual(payload["evidence_status"], "grounded")
        self.assertIsNone(payload["redirect"])
        self.assertIsInstance(payload["session_id"], int)
        self.assertIsInstance(payload["interaction_id"], int)
        self.assertEqual(payload["memory_proposal"]["proposal_id"], self.pending_proposal.id)

        interaction = study_database.get_study_interaction(
            payload["interaction_id"]
        )
        self.assertEqual(interaction.outcome, "unrated")
        sources = study_database.list_interaction_sources(interaction.id)
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.document_id, self.document_id)
        self.assertEqual(source.notebook_id, self.notebook.id)
        self.assertEqual(source.mime_type, "application/pdf")
        self.assertEqual(source.page_number, 6)
        self.assertIsNone(source.slide_number)
        self.assertEqual(source.chunk_index, 0)
        self.assertEqual(source.excerpt, self.source.text)
        self.assertNotIn("file_hash", response.text)
        self.assertNotIn(str(self.database_path), response.text)

    def test_chat_distinguishes_an_unindexed_scope_without_calling_rag(self) -> None:
        rag_database.update_chunk_count(self.document_id, 0)
        response = self.client.post(
            "/api/chat",
            json={
                "question": "What does this lesson explain?",
                "document_ids": [self.document_id],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["type"], "answer")
        self.assertEqual(payload["evidence_status"], "no_documents_indexed")
        self.assertEqual(payload["sources"], [])
        self.assertIn("No indexed study material", payload["answer"])
        self.answer_mock.assert_not_called()

    def test_performance_and_planning_questions_return_feature_redirects(self) -> None:
        cases = (
            (
                "What are my weaknesses?",
                "weakness_analysis",
                "coaching",
                "Open Coaching",
            ),
            (
                "What should I learn first based on my mistakes?",
                "coaching_request",
                "coaching",
                "Open Coaching",
            ),
            (
                "Create a study plan based on my history.",
                "study_plan_request",
                "study-plan",
                "Create Study Plan",
            ),
        )
        for prompt, intent, target, action_label in cases:
            with self.subTest(prompt=prompt):
                self.answer_mock.reset_mock()
                response = self.client.post("/api/chat", json={"question": prompt})
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["type"], "feature_redirect")
                self.assertEqual(payload["intent"], intent)
                self.assertEqual(payload["redirect"]["target"], target)
                self.assertEqual(payload["redirect"]["action_label"], action_label)
                self.assertEqual(payload["redirect"]["original_prompt"], prompt)
                self.assertEqual(payload["sources"], [])
                self.assertIsNone(payload["memory_proposal"])
                self.answer_mock.assert_not_called()

    def test_redirect_preserves_prompt_as_data_and_ambiguous_text_stays_in_chat(self) -> None:
        prompt = 'What are my weaknesses? <script>alert("x")</script> & notes'
        redirected = self.client.post("/api/chat", json={"question": prompt})
        self.assertEqual(redirected.status_code, 200, redirected.text)
        self.assertEqual(
            redirected.json()["redirect"]["original_prompt"],
            prompt,
        )
        self.assertNotIn("<script>", redirected.json()["redirect"]["message"])

        self.answer_mock.reset_mock()
        ambiguous = self.client.post("/api/chat", json={"question": "Help me"})
        self.assertEqual(ambiguous.status_code, 200, ambiguous.text)
        self.assertEqual(ambiguous.json()["type"], "answer")
        self.assertEqual(
            ambiguous.json()["intent"],
            "unsupported_or_ambiguous",
        )
        self.assertIsNone(ambiguous.json()["redirect"])
        self.answer_mock.assert_called_once()

    def test_study_plan_and_coaching_endpoints_remain_compatible(self) -> None:
        for path in (
            "/api/study/actions/plan",
            "/api/study/actions/coaching-plan",
        ):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    json={"total_minutes": 45, "max_items": 4},
                )
                self.assertEqual(response.status_code, 200, response.text)
                if path == "/api/study/actions/plan":
                    self.assertIn("items", response.json())
                else:
                    self.assertIn("plan", response.json())
                    self.assertIn("items", response.json())

    def test_coaching_provider_failure_uses_actionable_error_envelope(self) -> None:
        class ProviderRateLimitError(RuntimeError):
            status_code = 429

        with patch(
            "backend.api.routes.reports_study.generate_coaching_plan",
            side_effect=ProviderRateLimitError("private provider payload"),
        ):
            response = self.client.post(
                "/api/study/actions/coaching-plan",
                json={"total_minutes": 45, "max_items": 4},
            )

        self.assertEqual(response.status_code, 429, response.text)
        payload = response.json()["error"]
        self.assertEqual(payload["code"], "AI_PROVIDER_RATE_LIMITED")
        self.assertEqual(
            payload["title"],
            "AI provider rate limit reached",
        )
        self.assertTrue(payload["retryable"])
        self.assertRegex(payload["request_id"], r"^[a-f0-9]{32}$")
        self.assertNotIn("private provider payload", response.text)

    def test_answer_or_atomic_source_failure_writes_no_interaction(self) -> None:
        self.answer_error = RuntimeError("model unavailable")
        failed_answer = self.client.post(
            "/api/chat",
            json={
                "question": "First failure",
                "document_ids": [self.document_id],
            },
        )
        self.assertEqual(failed_answer.status_code, 500)
        with study_database.get_connection() as connection:
            interaction_count = connection.execute(
                "SELECT COUNT(*) FROM study_interactions"
            ).fetchone()[0]
            source_count = connection.execute(
                "SELECT COUNT(*) FROM study_interaction_sources"
            ).fetchone()[0]
        self.assertEqual(interaction_count, 0)
        self.assertEqual(source_count, 0)

        self.answer_error = None
        self.source = RetrievedSource(
            index=1,
            filename="lesson.pdf",
            page_number=6,
            chunk_index=0,
            distance=-0.1,
            text="Invalid source distance.",
            document_id=self.document_id,
            mime_type="application/pdf",
        )
        invalid_source = self.client.post(
            "/api/chat",
            json={"question": "Second failure"},
        )
        self.assertEqual(invalid_source.status_code, 422)
        with study_database.get_connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM study_interactions"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM study_interaction_sources"
                ).fetchone()[0],
                0,
            )

    def test_invalid_scope_is_rejected_before_session_creation(self) -> None:
        self.answer_error = NotebookNotFoundError("missing notebook")
        response = self.client.post(
            "/api/chat",
            json={"question": "Invalid scope", "notebook_id": 999},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(study_database.list_study_sessions(), [])

        multiple = self.client.post(
            "/api/chat",
            json={
                "question": "Too many scopes",
                "notebook_id": self.notebook.id,
                "document_ids": [self.document_id],
            },
        )
        self.assertEqual(multiple.status_code, 422)

    def test_session_outcome_and_end_lifecycle(self) -> None:
        chat = self.client.post(
            "/api/chat",
            json={"question": "Lifecycle question"},
        )
        self.assertEqual(chat.status_code, 200)
        first = chat.json()

        outcome = self.client.patch(
            f"/api/study/interactions/{first['interaction_id']}/outcome",
            json={"outcome": "understood"},
        )
        self.assertEqual(outcome.status_code, 200)
        self.assertEqual(outcome.json()["outcome"], "understood")

        sessions = self.client.get("/api/study/sessions")
        detail = self.client.get(
            f"/api/study/sessions/{first['session_id']}"
        )
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(sessions.json()["total"], 1)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["interactions"]), 1)

        ended = self.client.post("/api/study/sessions/active/end")
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.json()["status"], "completed")
        self.assertIsNotNone(ended.json()["ended_at"])

        second = self.client.post(
            "/api/chat",
            json={"question": "New session question"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second.json()["session_id"], first["session_id"])



if __name__ == "__main__":
    unittest.main()
