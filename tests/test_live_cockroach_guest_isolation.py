from __future__ import annotations

import os
import unittest

from hashlib import sha256
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.app import create_app
from backend.application.dependencies import (
    build_application_dependencies,
    configure_application_dependencies,
)
from backend.application.guest_sessions import GuestSessionService
from backend.domain import DEFAULT_WORKSPACE_ID
from backend.rag import config
from backend.repositories.cockroach.connection import (
    dispose_engine,
    get_engine,
)
from backend.repositories.cockroach.helpers import (
    content_sha256,
    uuid_for_public,
)


RUN_LIVE = os.getenv("RUN_LIVE_GUEST_ISOLATION") == "1"
EXPECTED_LEGACY_COUNTS = {
    "adaptation_events": 31,
    "cached_intelligence": 0,
    "document_blobs": 2,
    "document_chunks": 23,
    "documents": 2,
    "embedding_jobs": 8,
    "learner_memories": 7,
    "learner_memory_embeddings": 7,
    "learning_signals": 7,
    "memory_relationships": 0,
    "notebook_documents": 1,
    "notebooks": 1,
    "quiz_attempts": 5,
    "quiz_question_attempts": 9,
    "quiz_question_sources": 9,
    "study_interaction_sources": 5,
    "study_interactions": 2,
    "study_sessions": 2,
    "topic_sources": 0,
    "topics": 0,
    "workflow_states": 20,
}


@unittest.skipUnless(RUN_LIVE, "live CockroachDB guest proof is opt-in")
class LiveCockroachGuestIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if config.PERSISTENCE_BACKEND != "cockroach":
            raise unittest.SkipTest("CockroachDB runtime is not selected")
        config.validate_persistence_config()
        config.validate_guest_session_config()

    def test_two_guest_sessions_restart_and_vector_isolation(self) -> None:
        before = self._legacy_counts()
        self.assertEqual(before, EXPECTED_LEGACY_COUNTS)

        base = build_application_dependencies()
        service = GuestSessionService(
            base,
            pepper=config.GUEST_SESSION_TOKEN_PEPPER,
            ttl_days=config.GUEST_SESSION_TTL_DAYS,
        )
        created_a = service.create(uuid4().hex)
        created_b = service.create(uuid4().hex)
        token_a = created_a.token
        token_b = created_b.token
        self.assertNotEqual(created_a.workspace.id, created_b.workspace.id)
        self.assertNotEqual(token_a, token_b)
        self.assertNotEqual(created_a.workspace.id, DEFAULT_WORKSPACE_ID)
        self.assertNotEqual(created_b.workspace.id, DEFAULT_WORKSPACE_ID)

        deps_a = build_application_dependencies(created_a.workspace.id)
        deps_b = build_application_dependencies(created_b.workspace.id)
        suffix = uuid4().hex
        content_a = "Public test fact A: amber triangles represent review."
        content_b = "Public test fact B: cobalt circles represent recall."
        document_a = deps_a.documents.insert(
            f"guest-a-{suffix}.txt",
            "text/plain",
            sha256(content_a.encode()).hexdigest(),
            content_a.encode(),
        )
        document_b = deps_b.documents.insert(
            f"guest-b-{suffix}.txt",
            "text/plain",
            sha256(content_b.encode()).hexdigest(),
            content_b.encode(),
        )
        deps_a.documents.update_chunk_count(document_a, 1)
        deps_b.documents.update_chunk_count(document_b, 1)
        memory_a = deps_a.memories.insert(
            memory_type="learning_state",
            content="Public test memory A: review amber triangles.",
            confidence=0.9,
            importance=0.7,
            status="active",
        )
        memory_b = deps_b.memories.insert(
            memory_type="learning_state",
            content="Public test memory B: review cobalt circles.",
            confidence=0.9,
            importance=0.7,
            status="active",
        )
        self._insert_test_vectors(
            workspace_id=created_a.workspace.id,
            document_id=document_a,
            document_content=content_a,
            memory_id=memory_a,
            memory_content="Public test memory A: review amber triangles.",
            first_value=1.0,
        )
        self._insert_test_vectors(
            workspace_id=created_b.workspace.id,
            document_id=document_b,
            document_content=content_b,
            memory_id=memory_b,
            memory_content="Public test memory B: review cobalt circles.",
            first_value=-1.0,
        )

        with patch(
            "backend.repositories.cockroach.vectors.encode_query",
            return_value=[1.0] + [0.0] * 383,
        ):
            document_results_a = deps_a.document_vectors.search("test", 5)
            document_results_b = deps_b.document_vectors.search("test", 5)
            memory_results_a = deps_a.memory_vectors.search("test", 5)
            memory_results_b = deps_b.memory_vectors.search("test", 5)
        self.assertEqual(
            [item.page_content for item, _ in document_results_a],
            [content_a],
        )
        self.assertEqual(
            [item.page_content for item, _ in document_results_b],
            [content_b],
        )
        self.assertEqual(
            [item.page_content for item, _ in memory_results_a],
            ["Public test memory A: review amber triangles."],
        )
        self.assertEqual(
            [item.page_content for item, _ in memory_results_b],
            ["Public test memory B: review cobalt circles."],
        )

        self.assertIsNone(deps_a.documents.get(document_b))
        self.assertIsNone(deps_b.documents.get(document_a))
        self.assertIsNone(deps_a.memories.get(memory_b))
        self.assertIsNone(deps_b.memories.get(memory_a))
        self.assertIsNone(deps_a.documents.get(1))
        self.assertIsNone(deps_b.documents.get(1))

        application = create_app(allow_legacy_default_workspace=False)
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(client.close)
        auth_a = {"Authorization": f"Bearer {token_a}"}
        auth_b = {"Authorization": f"Bearer {token_b}"}
        self.assertEqual(client.get("/api/documents", headers=auth_a).status_code, 200)
        self.assertEqual(client.get("/api/documents", headers=auth_b).status_code, 200)
        self.assertEqual(
            client.get(f"/api/documents/{document_b}", headers=auth_a).status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/api/documents/{document_a}", headers=auth_b).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                f"/api/documents?workspace_id={created_b.workspace.id}",
                headers=auth_a,
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/api/notebooks",
                json={
                    "name": "Denied override",
                    "workspace_id": created_b.workspace.id,
                },
                headers=auth_a,
            ).status_code,
            422,
        )
        self.assertEqual(client.get("/api/documents").status_code, 401)
        self.assertEqual(
            client.get("/api/documents/1", headers=auth_a).status_code,
            404,
        )

        with get_engine().connect() as connection:
            stored_hashes = connection.execute(
                text(
                    "SELECT token_hash FROM guest_sessions "
                    "WHERE id = ANY(:ids)"
                ),
                {
                    "ids": [
                        UUID(created_a.session.id),
                        UUID(created_b.session.id),
                    ]
                },
            ).scalars().all()
        self.assertEqual(len(stored_hashes), 2)
        self.assertNotIn(token_a, stored_hashes)
        self.assertNotIn(token_b, stored_hashes)

        dispose_engine()
        configure_application_dependencies(None)
        restarted = GuestSessionService(
            build_application_dependencies(),
            pepper=config.GUEST_SESSION_TOKEN_PEPPER,
            ttl_days=config.GUEST_SESSION_TTL_DAYS,
        )
        self.assertEqual(
            restarted.authenticate(token_a).workspace.id,
            created_a.workspace.id,
        )
        self.assertEqual(
            restarted.authenticate(token_b).workspace.id,
            created_b.workspace.id,
        )
        self.assertEqual(self._legacy_counts(), before)

    @staticmethod
    def _legacy_counts() -> dict[str, int]:
        workspace = UUID(DEFAULT_WORKSPACE_ID)
        with get_engine().connect() as connection:
            return {
                table_name: int(
                    connection.execute(
                        text(
                            f"SELECT count(*) FROM {table_name} "
                            "WHERE workspace_id=:workspace_id"
                        ),
                        {"workspace_id": workspace},
                    ).scalar_one()
                )
                for table_name in EXPECTED_LEGACY_COUNTS
            }

    @staticmethod
    def _insert_test_vectors(
        *,
        workspace_id: str,
        document_id: int,
        document_content: str,
        memory_id: int,
        memory_content: str,
        first_value: float,
    ) -> None:
        workspace = UUID(workspace_id)
        document_uuid = uuid_for_public("documents", workspace_id, document_id)
        memory_uuid = uuid_for_public(
            "learner_memories",
            workspace_id,
            memory_id,
        )
        assert document_uuid is not None
        assert memory_uuid is not None
        vector = "[" + ",".join(
            [str(first_value)] + ["0"] * 383
        ) + "]"
        now_id = uuid4()
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id,workspace_id,document_id,chunk_index,content,
                        filename_snapshot,mime_type,metadata,embedding,
                        embedding_model,embedding_version,content_hash,
                        created_at,updated_at
                    ) VALUES (
                        :id,:workspace_id,:document_id,0,:content,
                        :filename,'text/plain','{}'::JSONB,
                        CAST(:embedding AS VECTOR(384)),
                        'guest-isolation-proof','guest-isolation-proof',
                        :content_hash,now(),now()
                    )
                    """
                ),
                {
                    "id": now_id,
                    "workspace_id": workspace,
                    "document_id": document_uuid,
                    "content": document_content,
                    "filename": "guest-isolation-proof.txt",
                    "embedding": vector,
                    "content_hash": content_sha256(document_content),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO learner_memory_embeddings (
                        memory_id,workspace_id,embedding,embedding_model,
                        embedding_version,content_hash,retrieval_count,
                        created_at,updated_at
                    ) VALUES (
                        :memory_id,:workspace_id,
                        CAST(:embedding AS VECTOR(384)),
                        'guest-isolation-proof','guest-isolation-proof',
                        :content_hash,0,now(),now()
                    )
                    """
                ),
                {
                    "memory_id": memory_uuid,
                    "workspace_id": workspace,
                    "embedding": vector,
                    "content_hash": content_sha256(memory_content),
                },
            )


if __name__ == "__main__":
    unittest.main()
