from __future__ import annotations

import tempfile
import unittest

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.application.dependencies import (
    configure_application_dependencies,
    get_application_dependencies,
)
from backend.application.vector_outbox import (
    VectorOutboxService,
    VectorSynchronizationError,
)
from backend.domain import DEFAULT_WORKSPACE_ID
from backend.memory import consolidation_registry, proposals, service as memory_service
from backend.memory.conflict_detector import MemoryConflictResult
from backend.memory.consolidator import MemoryConsolidationProposal
from backend.memory.models import MemoryCandidate, MemoryConsolidationCandidate
from backend.rag import database as rag_database
from backend.rag import document_service, ingestion, vector_store as rag_vector_store
from backend.repositories.chroma import (
    ChromaDocumentVectorRepository,
    ChromaMemoryVectorRepository,
)
from backend.repositories.sqlite import (
    SQLiteDocumentRepository,
    SQLiteIntelligenceRepository,
    SQLiteLearnerMemoryRepository,
    SQLiteNotebookRepository,
    SQLiteStudySessionRepository,
    SQLiteVectorOutboxRepository,
    SQLiteWorkflowStateRepository,
    initialize_foundation_schema,
)
from backend.study import database as study_database
from backend.study import quiz_api
from backend.rag.rag_service import RetrievedSource
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


SECOND_WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"


class _DocumentVectors:
    def __init__(self) -> None:
        self.documents: dict[str, object] = {}
        self.fail_add = False
        self.fail_delete = False

    def add_documents(self, *, documents, ids) -> None:
        if self.fail_add:
            raise RuntimeError("document vector write failed")
        self.documents.update(zip(ids, documents, strict=True))

    def get(self, *, where, include=None):
        del include
        ids = [
            vector_id
            for vector_id, document in self.documents.items()
            if document.metadata.get("document_id") == where.get("document_id")
        ]
        return {"ids": ids}

    def delete(self, *, ids) -> None:
        if self.fail_delete:
            raise RuntimeError("document vector delete failed")
        for vector_id in ids:
            self.documents.pop(vector_id, None)


class _MemoryVectors:
    def __init__(self) -> None:
        self.documents: dict[str, object] = {}
        self.fail_add = False
        self.fail_delete = False

    def add_documents(self, *, documents, ids) -> None:
        if self.fail_add:
            raise RuntimeError("memory vector write failed")
        self.documents.update(zip(ids, documents, strict=True))

    def delete(self, *, ids) -> None:
        if self.fail_delete:
            raise RuntimeError("memory vector delete failed")
        for vector_id in ids:
            self.documents.pop(vector_id, None)

    def similarity_search_with_score(self, *, query, k, filter=None):
        del query, filter
        return [(document, 0.1) for document in self.documents.values()][:k]


class PersistenceFoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.database_path = Path(temporary) / "foundation.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(patch.object(rag_database, "ensure_directories"))
        rag_database.initialize_database()
        from backend.memory.database import initialize_memory_database

        initialize_memory_database()
        study_database.initialize_study_database()
        initialize_foundation_schema()
        configure_application_dependencies(None)
        self.addCleanup(configure_application_dependencies, None)

    def test_repository_contract_crud_and_workspace_isolation(self) -> None:
        with rag_database.get_connection() as connection:
            timestamp = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO workspaces (id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (SECOND_WORKSPACE_ID, "Second", timestamp, timestamp),
            )

        local_documents = SQLiteDocumentRepository(DEFAULT_WORKSPACE_ID)
        other_documents = SQLiteDocumentRepository(SECOND_WORKSPACE_ID)
        local_id = local_documents.insert("local.txt", "text/plain", "local", b"a")
        other_id = other_documents.insert("other.txt", "text/plain", "other", b"b")
        self.assertEqual([item.id for item in local_documents.list()], [local_id])
        self.assertEqual([item.id for item in other_documents.list()], [other_id])
        self.assertIsNone(local_documents.get(other_id))
        self.assertIsNone(other_documents.get(local_id))

        local_notebooks = SQLiteNotebookRepository(DEFAULT_WORKSPACE_ID)
        other_notebooks = SQLiteNotebookRepository(SECOND_WORKSPACE_ID)
        local_notebooks.create("Local notebook")
        other_notebooks.create("Other notebook")
        self.assertEqual(
            [item.name for item in local_notebooks.list()],
            ["Local notebook"],
        )
        self.assertEqual(
            [item.name for item in other_notebooks.list()],
            ["Other notebook"],
        )

        local_memories = SQLiteLearnerMemoryRepository(DEFAULT_WORKSPACE_ID)
        other_memories = SQLiteLearnerMemoryRepository(SECOND_WORKSPACE_ID)
        local_memory_id = local_memories.insert(memory_type="profile", content="local")
        other_memory_id = other_memories.insert(memory_type="profile", content="other")
        self.assertEqual([item.id for item in local_memories.list()], [local_memory_id])
        self.assertEqual([item.id for item in other_memories.list()], [other_memory_id])

        local_workflows = SQLiteWorkflowStateRepository(DEFAULT_WORKSPACE_ID)
        other_workflows = SQLiteWorkflowStateRepository(SECOND_WORKSPACE_ID)
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        local_workflows.put("local-flow", "contract", {"value": 1}, expiry)
        other_workflows.put("other-flow", "contract", {"value": 2}, expiry)
        self.assertIsNone(local_workflows.get("other-flow", "contract"))
        self.assertIsNone(other_workflows.get("local-flow", "contract"))

        with rag_database.get_connection() as connection:
            local_topic_id = str(uuid4())
            other_topic_id = str(uuid4())
            for topic_id, workspace_id, name in (
                (local_topic_id, DEFAULT_WORKSPACE_ID, "Local topic"),
                (other_topic_id, SECOND_WORKSPACE_ID, "Other topic"),
            ):
                connection.execute(
                    """
                    INSERT INTO topics (
                        id, name, description, extraction_scope_kind,
                        extraction_scope_key, generated_at,
                        source_fingerprint, workspace_id
                    ) VALUES (?, ?, '', 'global', 'global', ?, 'fingerprint', ?)
                    """,
                    (topic_id, name, timestamp, workspace_id),
                )
        local_intelligence = SQLiteIntelligenceRepository(DEFAULT_WORKSPACE_ID)
        other_intelligence = SQLiteIntelligenceRepository(SECOND_WORKSPACE_ID)
        self.assertEqual(
            [topic.name for topic in local_intelligence.list_topics()],
            ["Local topic"],
        )
        self.assertEqual(
            [topic.name for topic in other_intelligence.list_topics()],
            ["Other topic"],
        )

        local_study = SQLiteStudySessionRepository(DEFAULT_WORKSPACE_ID)
        other_study = SQLiteStudySessionRepository(SECOND_WORKSPACE_ID)
        local_study.get_or_create_active()
        other_study.get_or_create_active()
        self.assertEqual(len(local_study.list()), 1)
        self.assertEqual(len(other_study.list()), 1)

    def test_unit_of_work_rolls_back_all_relational_changes(self) -> None:
        dependencies = get_application_dependencies()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with dependencies.unit_of_work():
                dependencies.memories.insert(
                    memory_type="profile",
                    content="must not commit",
                )
                dependencies.learning_signals.create(
                    "memory_candidate",
                    "test",
                    "1",
                    {"value": True},
                )
                raise RuntimeError("rollback")
        self.assertEqual(dependencies.memories.list(), [])
        self.assertEqual(dependencies.learning_signals.list(), [])

    def test_expired_workflow_cleanup(self) -> None:
        repository = get_application_dependencies().workflows
        repository.put(
            "expired",
            "contract",
            {"value": True},
            (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        with rag_database.get_connection() as connection:
            connection.execute(
                "UPDATE workflow_states SET expires_at = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    "expired",
                ),
            )
        self.assertGreaterEqual(repository.cleanup_expired(), 1)
        self.assertIsNone(repository.get("expired", "contract"))
        terminal = repository.get("expired", "contract", include_terminal=True)
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal.status, "expired")

    def test_pending_quiz_survives_dependency_restart(self) -> None:
        document_id = rag_database.insert_document(
            "source.txt",
            "text/plain",
            "quiz-source",
            b"source",
        )
        rag_database.update_chunk_count(document_id, 1)
        generated = _generated_quiz(document_id)
        with patch.object(quiz_api, "generate_grounded_quiz", return_value=generated):
            presented = quiz_api.generate_quiz_for_api("topic", 1)
        configure_application_dependencies(None)
        submitted = quiz_api.submit_quiz(
            presented.quiz_id,
            [quiz_api.QuizResponse(question_number=1, selected_option=2)],
        )
        self.assertEqual(submitted.correct_answers, 1)
        self.assertIsNone(
            get_application_dependencies().workflows.get(
                presented.quiz_id,
                quiz_api.PENDING_QUIZ_WORKFLOW,
            )
        )

    def test_memory_and_consolidation_proposals_survive_restart(self) -> None:
        candidate = MemoryCandidate(
            should_store=True,
            memory_type="profile",
            content="The learner prefers concise explanations.",
            confidence=0.95,
            importance=0.8,
            reason="Direct preference.",
        )
        with (
            patch.object(proposals, "ENABLE_MEMORY_PROPOSALS", True),
            patch.object(proposals, "propose_memory_candidate", return_value=candidate),
            patch.object(
                proposals,
                "detect_memory_conflict",
                return_value=MemoryConflictResult("new", None, 0.9, "New."),
            ),
        ):
            pending = proposals.create_memory_proposal(
                user_message="Be concise.",
                assistant_answer="Understood.",
            )
        self.assertIsNotNone(pending)
        configure_application_dependencies(None)
        self.assertIsNotNone(proposals.get_memory_proposal(pending.id))
        rejected = proposals.decide_memory_proposal(pending.id, "reject")
        self.assertTrue(rejected.consumed)

        memory_ids = [
            get_application_dependencies().memories.insert(
                memory_type="procedural",
                content=f"Source {number}",
            )
            for number in (1, 2)
        ]
        source_memories = tuple(
            get_application_dependencies().memories.get_many(memory_ids)
        )
        consolidation = MemoryConsolidationProposal(
            source_memories=source_memories,
            candidate=MemoryConsolidationCandidate(
                should_consolidate=True,
                memory_type="procedural",
                content="Combined source memory.",
                confidence=0.9,
                importance=0.7,
                reason="Compatible.",
            ),
        )
        with patch.object(
            consolidation_registry,
            "propose_memory_consolidation",
            return_value=consolidation,
        ):
            durable = consolidation_registry.create_memory_consolidation(memory_ids)
        configure_application_dependencies(None)
        self.assertIsNotNone(
            consolidation_registry.get_memory_consolidation(durable.id)
        )

    def test_vector_outbox_failure_retry_is_idempotent(self) -> None:
        repository = SQLiteVectorOutboxRepository()
        memory_vectors = _MemoryVectors()
        memory_vectors.fail_add = True
        service = VectorOutboxService(
            repository,
            ChromaDocumentVectorRepository(lambda: _DocumentVectors()),
            ChromaMemoryVectorRepository(lambda: memory_vectors),
        )
        job = repository.enqueue(
            "memory",
            "7",
            "upsert",
            {"text": "durable", "metadata": {"memory_id": 7}},
        )
        with self.assertRaises(VectorSynchronizationError):
            service.process(job.id)
        self.assertEqual(repository.get(job.id).status, "failed")
        self.assertEqual(repository.get(job.id).attempts, 1)
        memory_vectors.fail_add = False
        result = service.reconcile()
        self.assertEqual((result.attempted, result.completed, result.failed), (1, 1, 0))
        self.assertEqual(repository.get(job.id).attempts, 2)
        service.process(job.id)
        self.assertEqual(repository.get(job.id).attempts, 2)

    def test_memory_update_and_delete_recover_from_vector_failure(self) -> None:
        vectors = _MemoryVectors()
        with patch.object(
            memory_service,
            "get_memory_vector_store",
            return_value=vectors,
        ):
            memory = memory_service.add_memory("profile", "Original memory")
            vectors.fail_add = True
            with self.assertRaises(VectorSynchronizationError):
                memory_service.update_memory(
                    memory.id,
                    "profile",
                    "Updated memory",
                    0.9,
                    0.8,
                )
            self.assertEqual(
                get_application_dependencies().memories.get(memory.id).content,
                "Updated memory",
            )
            vectors.fail_add = False
            self._reconcile_memory(vectors)
            self.assertEqual(
                vectors.documents[f"memory-{memory.id}"].page_content,
                "Updated memory",
            )

            vectors.fail_delete = True
            with self.assertRaises(VectorSynchronizationError):
                memory_service.delete_memory(memory.id)
            self.assertIsNone(get_application_dependencies().memories.get(memory.id))
            vectors.fail_delete = False
            self._reconcile_memory(vectors)
            self.assertNotIn(f"memory-{memory.id}", vectors.documents)

    def test_document_ingestion_and_deletion_recover_from_vector_failure(self) -> None:
        vectors = _DocumentVectors()
        vectors.fail_add = True
        with patch.object(ingestion, "get_vector_store", return_value=vectors):
            with self.assertRaises(VectorSynchronizationError):
                ingestion.index_file_bytes("lesson.txt", b"Durable document content.")
        stored = get_application_dependencies().documents.list()
        self.assertEqual(len(stored), 1)
        vectors.fail_add = False
        self._reconcile_documents(vectors)
        self.assertTrue(vectors.documents)

        vectors.fail_delete = True
        with patch.object(rag_vector_store, "get_vector_store", return_value=vectors):
            with self.assertRaises(document_service.DocumentDeletionError):
                document_service.delete_document(stored[0].id)
        self.assertIsNone(get_application_dependencies().documents.get(stored[0].id))
        vectors.fail_delete = False
        self._reconcile_documents(vectors)
        self.assertFalse(vectors.documents)

    @staticmethod
    def _reconcile_memory(vectors: _MemoryVectors) -> None:
        dependencies = get_application_dependencies()
        result = VectorOutboxService(
            dependencies.vector_outbox,
            dependencies.document_vectors,
            ChromaMemoryVectorRepository(lambda: vectors),
        ).reconcile()
        if result.failed:
            raise AssertionError(result)

    @staticmethod
    def _reconcile_documents(vectors: _DocumentVectors) -> None:
        dependencies = get_application_dependencies()
        result = VectorOutboxService(
            dependencies.vector_outbox,
            ChromaDocumentVectorRepository(lambda: vectors),
            dependencies.memory_vectors,
        ).reconcile()
        if result.failed:
            raise AssertionError(result)


def _generated_quiz(document_id: int) -> GeneratedGroundedQuiz:
    source = RetrievedSource(
        index=1,
        filename="source.txt",
        page_number=1,
        chunk_index=0,
        distance=0.1,
        text="The source states the correct option.",
        document_id=document_id,
        mime_type="text/plain",
    )
    return GeneratedGroundedQuiz(
        requested_topic="topic",
        sources=(source,),
        quiz=GroundedQuiz(
            should_generate=True,
            topic="Topic",
            questions=[
                GroundedQuizQuestion(
                    question="Which option is correct?",
                    options=["A", "B", "C", "D"],
                    correct_option=2,
                    explanation="B is supported [1].",
                    source_indexes=[1],
                )
            ],
            confidence=0.9,
            reason="Supported.",
        ),
    )


if __name__ == "__main__":
    unittest.main()
