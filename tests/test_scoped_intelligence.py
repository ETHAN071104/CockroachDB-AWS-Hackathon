from __future__ import annotations

import sqlite3
import tempfile
import unittest

from contextlib import ExitStack, closing
from importlib import import_module
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

import backend.rag.database as rag_database
import backend.rag.intelligence as intelligence
import backend.rag.intelligence_store as intelligence_store
import backend.rag.rag_service as rag_service
from backend.rag.notebooks import assign_document_to_notebook, create_notebook
from backend.rag.scope import (
    RetrievalScope,
    TopicNotFoundError,
    resolve_retrieval_scope,
)


app_module = import_module("backend.api.app")


class FilteringVectorStore:
    """Small Chroma stand-in that applies metadata filters before k."""

    def __init__(
        self,
        results: list[tuple[Document, float]],
    ) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def similarity_search_with_score(
        self,
        *,
        query: str,
        k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[Document, float]]:
        self.calls.append(
            {
                "query": query,
                "k": k,
                "filter": filter,
            }
        )
        filtered = [
            result
            for result in self.results
            if filter is None
            or self._matches(result[0].metadata, filter)
        ]
        return filtered[:k]

    @classmethod
    def _matches(
        cls,
        metadata: dict[str, Any],
        expression: dict[str, Any],
    ) -> bool:
        if "$and" in expression:
            return all(
                cls._matches(metadata, item)
                for item in expression["$and"]
            )
        if "$or" in expression:
            return any(
                cls._matches(metadata, item)
                for item in expression["$or"]
            )

        for field, condition in expression.items():
            actual = metadata.get(field)
            if isinstance(condition, dict):
                if "$eq" in condition and actual != condition["$eq"]:
                    return False
                if "$in" in condition and actual not in condition["$in"]:
                    return False
            elif actual != condition:
                return False
        return True


class ScopedRetrievalTest(unittest.TestCase):
    TOPIC_ID = "5f1ed350-4a86-4e2b-b7e8-1f605c652d11"
    MISSING_TOPIC_ID = "7a569e43-a7aa-4ee7-b72b-82f0eef7b354"

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "app.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()

        self.document_one = self._insert_document(
            "biology.pdf",
            "application/pdf",
            "hash-biology",
        )
        self.document_two = self._insert_document(
            "chemistry.pptx",
            (
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            "hash-chemistry",
        )
        self.notebook = create_notebook("Biology")
        self.empty_notebook = create_notebook("Empty")
        assign_document_to_notebook(
            self.document_one,
            self.notebook.id,
        )

    def test_scope_allows_global_or_exactly_one_selector(self) -> None:
        global_scope = resolve_retrieval_scope(None)
        self.assertTrue(global_scope.is_global)
        with self.assertRaises(ValueError):
            RetrievalScope()
        normalized = RetrievalScope(
            document_ids=(self.document_one, self.document_one)
        )
        self.assertEqual(normalized.document_ids, (self.document_one,))

        invalid_scopes = (
            {
                "notebook_id": self.notebook.id,
                "document_ids": (self.document_one,),
            },
            {
                "notebook_id": self.notebook.id,
                "topic_id": self.TOPIC_ID,
            },
            {
                "document_ids": (self.document_one,),
                "topic_id": self.TOPIC_ID,
            },
            {"notebook_id": 0},
            {"document_ids": (-1,)},
            {"topic_id": "not-a-uuid"},
        )
        for values in invalid_scopes:
            with self.subTest(values=values), self.assertRaises(ValueError):
                RetrievalScope(**values)

    def test_notebook_document_and_topic_scopes_resolve(self) -> None:
        notebook_scope = resolve_retrieval_scope(
            RetrievalScope(notebook_id=self.notebook.id)
        )
        self.assertEqual(notebook_scope.kind, "notebook")
        self.assertEqual(notebook_scope.document_ids, (self.document_one,))
        self.assertEqual(
            notebook_scope.chroma_filter,
            {"document_id": {"$in": [self.document_one]}},
        )

        document_scope = resolve_retrieval_scope(
            RetrievalScope(
                document_ids=(self.document_two, self.document_one)
            )
        )
        self.assertEqual(document_scope.kind, "documents")
        self.assertEqual(
            document_scope.document_ids,
            (self.document_two, self.document_one),
        )

        topic_scope = resolve_retrieval_scope(
            RetrievalScope(topic_id=self.TOPIC_ID),
            topic_source_repository=lambda _topic_id: (
                {
                    "document_id": self.document_one,
                    "chunk_index": 2,
                },
                (self.document_two, 4),
                (self.document_one, 2),
            ),
        )
        self.assertEqual(topic_scope.kind, "topic")
        self.assertEqual(
            topic_scope.source_pairs,
            ((self.document_one, 2), (self.document_two, 4)),
        )
        self.assertIn("$or", topic_scope.chroma_filter or {})

        with self.assertRaises(TopicNotFoundError):
            resolve_retrieval_scope(
                RetrievalScope(topic_id=self.MISSING_TOPIC_ID),
                topic_source_repository=lambda _topic_id: None,
            )

    def test_filter_is_given_to_chroma_before_top_k(self) -> None:
        vector_store = FilteringVectorStore(
            [
                (
                    self._vector_document(
                        document_id=self.document_two,
                        chunk_index=0,
                        text="Globally nearest but outside requested scope.",
                    ),
                    0.01,
                ),
                (
                    self._vector_document(
                        document_id=self.document_one,
                        chunk_index=1,
                        text="Scoped biology evidence.",
                    ),
                    0.20,
                ),
            ]
        )

        with patch.object(
            rag_service,
            "get_vector_store",
            return_value=vector_store,
        ):
            sources = rag_service.retrieve_sources(
                "energy",
                k=1,
                scope=RetrievalScope(
                    document_ids=(self.document_one,)
                ),
            )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].document_id, self.document_one)
        self.assertEqual(sources[0].text, "Scoped biology evidence.")
        self.assertEqual(
            vector_store.calls[0]["filter"],
            {"document_id": {"$in": [self.document_one]}},
        )
        self.assertEqual(vector_store.calls[0]["k"], 1)

    def test_requested_empty_scope_never_queries_or_falls_back_global(
        self,
    ) -> None:
        vector_store = FilteringVectorStore(
            [
                (
                    self._vector_document(
                        document_id=self.document_two,
                        chunk_index=0,
                        text="Global evidence must not leak.",
                    ),
                    0.01,
                )
            ]
        )

        with patch.object(
            rag_service,
            "get_vector_store",
            return_value=vector_store,
        ):
            notebook_sources = rag_service.retrieve_sources(
                "anything",
                scope=RetrievalScope(
                    notebook_id=self.empty_notebook.id
                ),
            )
            document_sources = rag_service.retrieve_sources(
                "anything",
                scope=RetrievalScope(document_ids=()),
            )
            topic_sources = rag_service.retrieve_sources(
                "anything",
                scope=RetrievalScope(topic_id=self.TOPIC_ID),
                topic_source_repository=lambda _topic_id: (),
            )

        self.assertEqual(notebook_sources, [])
        self.assertEqual(document_sources, [])
        self.assertEqual(topic_sources, [])
        self.assertEqual(vector_store.calls, [])

    def test_topic_scope_filters_exact_document_chunk_pairs(self) -> None:
        vector_store = FilteringVectorStore(
            [
                (
                    self._vector_document(
                        document_id=self.document_one,
                        chunk_index=0,
                        text="Same document, wrong chunk.",
                    ),
                    0.01,
                ),
                (
                    self._vector_document(
                        document_id=self.document_one,
                        chunk_index=3,
                        text="Exact cited topic chunk.",
                    ),
                    0.40,
                ),
            ]
        )

        with patch.object(
            rag_service,
            "get_vector_store",
            return_value=vector_store,
        ):
            sources = rag_service.retrieve_sources(
                "topic",
                k=1,
                scope=RetrievalScope(topic_id=self.TOPIC_ID),
                topic_source_repository=lambda _topic_id: (
                    (self.document_one, 3),
                ),
            )

        self.assertEqual([source.chunk_index for source in sources], [3])
        self.assertEqual(sources[0].text, "Exact cited topic chunk.")
        self.assertEqual(
            vector_store.calls[0]["filter"],
            {
                "$and": [
                    {
                        "document_id": {
                            "$eq": self.document_one,
                        }
                    },
                    {"chunk_index": {"$eq": 3}},
                ]
            },
        )

    def test_retrieved_lineage_keeps_document_mime_location_and_chunk(
        self,
    ) -> None:
        pdf_document = self._vector_document(
            document_id=self.document_one,
            chunk_index=2,
            text="PDF excerpt",
            filename="biology.pdf",
            mime_type="application/pdf",
            page_number=5,
        )
        slide_document = self._vector_document(
            document_id=self.document_two,
            chunk_index=6,
            text="Slide excerpt",
            filename="chemistry.pptx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            slide_number=9,
        )
        vector_store = FilteringVectorStore(
            [(pdf_document, 0.2), (slide_document, 0.3)]
        )

        with patch.object(
            rag_service,
            "get_vector_store",
            return_value=vector_store,
        ):
            sources = rag_service.retrieve_sources("lineage", k=2)

        self.assertEqual(sources[0].document_id, self.document_one)
        self.assertEqual(sources[0].mime_type, "application/pdf")
        self.assertEqual(sources[0].page_number, 5)
        self.assertIsNone(sources[0].slide_number)
        self.assertEqual(sources[0].chunk_index, 2)
        self.assertEqual(sources[0].distance, 0.2)
        self.assertEqual(sources[0].text, "PDF excerpt")

        self.assertEqual(sources[1].document_id, self.document_two)
        self.assertEqual(sources[1].slide_number, 9)
        self.assertIsNone(sources[1].page_number)
        self.assertEqual(sources[1].chunk_index, 6)

    def _insert_document(
        self,
        filename: str,
        mime_type: str,
        file_hash: str,
    ) -> int:
        return rag_database.insert_document(
            filename=filename,
            mime_type=mime_type,
            file_hash=file_hash,
            file_data=filename.encode("utf-8"),
        )

    @staticmethod
    def _vector_document(
        *,
        document_id: int,
        chunk_index: int,
        text: str,
        filename: str = "source.txt",
        mime_type: str = "text/plain",
        page_number: int | None = None,
        slide_number: int | None = None,
    ) -> Document:
        metadata: dict[str, Any] = {
            "document_id": document_id,
            "filename": filename,
            "mime_type": mime_type,
            "chunk_index": chunk_index,
        }
        if page_number is not None:
            metadata["page_number"] = page_number
        if slide_number is not None:
            metadata["slide_number"] = slide_number
        return Document(page_content=text, metadata=metadata)


class IntelligenceMigrationTest(unittest.TestCase):
    def test_additive_cache_and_topic_migrations_preserve_legacy_document(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        file_hash TEXT NOT NULL UNIQUE,
                        file_data BLOB NOT NULL,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO documents (
                        filename, mime_type, file_hash, file_data,
                        chunk_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy.txt",
                        "text/plain",
                        "legacy-fingerprint-input",
                        sqlite3.Binary(b"legacy content"),
                        1,
                        "2026-07-18T00:00:00+00:00",
                    ),
                )
                connection.commit()

            with (
                patch.object(rag_database, "DATABASE_PATH", database_path),
                patch.object(rag_database, "ensure_directories"),
            ):
                rag_database.initialize_database()
                rag_database.initialize_database()

            with closing(sqlite3.connect(database_path)) as connection:
                connection.row_factory = sqlite3.Row
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertTrue(
                    {
                        "documents",
                        "cached_intelligence",
                        "topics",
                        "topic_sources",
                    }
                    <= tables
                )
                legacy = connection.execute(
                    "SELECT filename, file_hash, file_data FROM documents WHERE id = 1"
                ).fetchone()
                self.assertEqual(legacy["filename"], "legacy.txt")
                self.assertEqual(
                    legacy["file_hash"],
                    "legacy-fingerprint-input",
                )
                self.assertEqual(bytes(legacy["file_data"]), b"legacy content")

                self.assertTrue(
                    {
                        "kind",
                        "scope_kind",
                        "scope_key",
                        "result_json",
                        "source_snapshot_json",
                        "generated_at",
                        "fingerprint",
                    }
                    <= self._columns(connection, "cached_intelligence")
                )
                self.assertTrue(
                    {
                        "id",
                        "name",
                        "description",
                        "extraction_scope_kind",
                        "extraction_scope_key",
                        "generated_at",
                        "source_fingerprint",
                    }
                    <= self._columns(connection, "topics")
                )
                self.assertTrue(
                    {
                        "topic_id",
                        "document_id",
                        "chunk_index",
                        "source_index",
                        "filename",
                        "mime_type",
                        "page_number",
                        "slide_number",
                        "excerpt",
                        "distance",
                    }
                    <= self._columns(connection, "topic_sources")
                )

    @staticmethod
    def _columns(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info('{table_name}')"
            ).fetchall()
        }


class IntelligenceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "app.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()
        self.document_id = rag_database.insert_document(
            filename="source.txt",
            mime_type="text/plain",
            file_hash="original-document-hash",
            file_data=b"source content",
        )
        rag_database.update_chunk_count(self.document_id, 3)

    def test_stale_fingerprint_and_failed_replace_preserve_old_cache(
        self,
    ) -> None:
        original_fingerprint = intelligence_store.fingerprint_for_document(
            self.document_id
        )
        original = intelligence_store.replace_cached_intelligence(
            "summary",
            "documents",
            [self.document_id],
            result={
                "title": "Original summary",
                "overview": "Grounded overview",
                "key_points": [],
                "confidence": 0.8,
            },
            source_snapshot=[
                {
                    "document_id": self.document_id,
                    "chunk_index": 0,
                }
            ],
            fingerprint=original_fingerprint,
        )

        with rag_database.get_connection() as connection:
            connection.execute(
                "UPDATE documents SET file_hash = ? WHERE id = ?",
                ("changed-document-hash", self.document_id),
            )

        current_fingerprint = intelligence_store.fingerprint_for_document(
            self.document_id
        )
        self.assertNotEqual(current_fingerprint, original_fingerprint)
        self.assertTrue(
            intelligence_store.cache_is_stale(
                original,
                current_fingerprint,
            )
        )

        with self.assertRaises(
            intelligence_store.FingerprintMismatchError
        ):
            intelligence_store.replace_cached_intelligence(
                "summary",
                "documents",
                [self.document_id],
                result={"title": "Must not replace old cache"},
                source_snapshot=[],
                fingerprint=original_fingerprint,
            )

        preserved = intelligence_store.get_cached_intelligence(
            "summary",
            "documents",
            [self.document_id],
        )
        self.assertIsNotNone(preserved)
        assert preserved is not None
        self.assertEqual(preserved.generated_at, original.generated_at)
        self.assertEqual(preserved.result, original.result)

    def test_topics_persist_exact_pairs_and_failed_replace_preserves_old(
        self,
    ) -> None:
        fingerprint = intelligence_store.fingerprint_for_documents(
            [self.document_id]
        )
        topics = intelligence_store.replace_topics_for_scope(
            "documents",
            [self.document_id],
            [
                intelligence_store.TopicInput(
                    name="Core concept",
                    description="Two exact cited chunks.",
                    sources=(
                        self._topic_source(1, 0),
                        self._topic_source(2, 2),
                    ),
                )
            ],
            fingerprint=fingerprint,
        )
        self.assertEqual(len(topics), 1)
        topic = topics[0]
        source_pairs = intelligence_store.get_topic_source_pairs(topic.id)
        self.assertEqual(
            [
                (source.document_id, source.chunk_index)
                for source in source_pairs
            ],
            [(self.document_id, 0), (self.document_id, 2)],
        )

        with self.assertRaises(intelligence_store.IntelligenceStoreError):
            intelligence_store.replace_topics_for_scope(
                "documents",
                [self.document_id],
                [
                    intelligence_store.TopicInput(
                        name="Invalid replacement",
                        sources=(self._topic_source(1, 99),),
                    )
                ],
                fingerprint=fingerprint,
            )

        preserved = intelligence_store.list_topics(
            scope_kind="documents",
            scope_key=[self.document_id],
        )
        self.assertEqual([item.id for item in preserved], [topic.id])
        self.assertEqual(preserved[0].name, "Core concept")

    def _topic_source(
        self,
        source_index: int,
        chunk_index: int,
    ) -> intelligence_store.TopicSourcePair:
        return intelligence_store.TopicSourcePair(
            document_id=self.document_id,
            chunk_index=chunk_index,
            source_index=source_index,
            filename="source.txt",
            mime_type="text/plain",
            excerpt=f"Evidence chunk {chunk_index}",
            distance=0.2 + chunk_index,
        )


class FakeIntelligenceModel:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []
        self.fail = False
        self.invalid = False

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("simulated generation failure")
        if self.invalid:
            return _ModelResponse("not structured JSON")

        prompt = "\n".join(
            str(getattr(message, "content", message))
            for message in messages
        )
        if "identify major study topics" in prompt:
            payload = {
                "should_generate": True,
                "topics": [
                    {
                        "name": "Photosynthesis",
                        "description": "How plants use light energy.",
                        "source_indexes": [1],
                    }
                ],
                "confidence": 0.91,
                "reason": "Grounded topic found.",
            }
        else:
            payload = {
                "title": "Grounded study summary",
                "overview": "Source material explains energy conversion.",
                "key_points": [
                    {
                        "text": "Plants capture light energy [1].",
                        "source_indexes": [1],
                    }
                ],
                "confidence": 0.88,
            }
        return _ModelResponse(json.dumps(payload))


class _ModelResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class EvidenceVectorStore:
    def __init__(
        self,
        records: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self.records = records
        self.get_calls: list[dict[str, Any]] = []

    def get(
        self,
        *,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        self.get_calls.append({"where": where, "include": include})
        records = [
            record
            for record in self.records
            if where is None
            or FilteringVectorStore._matches(record[1], where)
        ]
        return {
            "documents": [record[0] for record in records],
            "metadatas": [record[1] for record in records],
        }


class IntelligenceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "app.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()

        self.document_id = rag_database.insert_document(
            filename="biology.pdf",
            mime_type="application/pdf",
            file_hash="biology-hash-v1",
            file_data=b"biology",
        )
        rag_database.update_chunk_count(self.document_id, 2)
        self.slide_document_id = rag_database.insert_document(
            filename="chemistry.pptx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            file_hash="chemistry-hash-v1",
            file_data=b"chemistry",
        )
        rag_database.update_chunk_count(self.slide_document_id, 1)
        self.notebook = create_notebook("Science")
        assign_document_to_notebook(self.document_id, self.notebook.id)

        self.vector_store = EvidenceVectorStore(
            [
                (
                    "Plants capture light energy using chlorophyll.",
                    {
                        "document_id": self.document_id,
                        "filename": "biology.pdf",
                        "mime_type": "application/pdf",
                        "page_number": 2,
                        "chunk_index": 0,
                    },
                ),
                (
                    "Sugars store converted chemical energy.",
                    {
                        "document_id": self.document_id,
                        "filename": "biology.pdf",
                        "mime_type": "application/pdf",
                        "page_number": 3,
                        "chunk_index": 1,
                    },
                ),
                (
                    "Catalysts lower activation energy.",
                    {
                        "document_id": self.slide_document_id,
                        "filename": "chemistry.pptx",
                        "mime_type": (
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                        "slide_number": 4,
                        "chunk_index": 0,
                    },
                ),
            ]
        )
        self.model = FakeIntelligenceModel()
        self.stack.enter_context(
            patch.object(
                intelligence,
                "get_vector_store",
                return_value=self.vector_store,
            )
        )
        self.stack.enter_context(
            patch.object(
                intelligence,
                "get_intelligence_model",
                return_value=self.model,
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

    def test_summary_get_is_cached_only_and_returns_safe_lineage(self) -> None:
        missing = self.client.get(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.model.calls, [])

        generated = self.client.post(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        payload = generated.json()
        self.assertEqual(payload["kind"], "document")
        self.assertEqual(payload["scope_id"], str(self.document_id))
        self.assertFalse(payload["stale"])
        self.assertEqual(
            payload["summary"]["title"],
            "Grounded study summary",
        )
        self.assertTrue(payload["sources"])
        source = payload["sources"][0]
        self.assertEqual(source["document_id"], self.document_id)
        self.assertEqual(source["notebook_id"], self.notebook.id)
        self.assertEqual(source["mime_type"], "application/pdf")
        self.assertEqual(source["page_number"], 2)
        self.assertIsNone(source["slide_number"])
        self.assertEqual(source["chunk_index"], 0)
        self.assertIn("capture light", source["excerpt"])
        self.assertNotIn("file_hash", generated.text)
        self.assertNotIn("file_data", generated.text)
        self.assertNotIn(str(self.database_path), generated.text)

        generation_calls = len(self.model.calls)
        cached = self.client.get(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.json(), payload)
        self.assertEqual(len(self.model.calls), generation_calls)

    def test_stale_cache_and_failed_regeneration_preserve_old_summary(
        self,
    ) -> None:
        first = self.client.post(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_payload = first.json()

        with rag_database.get_connection() as connection:
            connection.execute(
                "UPDATE documents SET file_hash = ? WHERE id = ?",
                ("biology-hash-v2", self.document_id),
            )

        stale = self.client.get(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(stale.status_code, 200)
        self.assertTrue(stale.json()["stale"])

        self.model.fail = True
        failed = self.client.post(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertIn(failed.status_code, {500, 502})
        self.assertIn("error", failed.json())

        preserved = self.client.get(
            f"/api/documents/{self.document_id}/summary"
        )
        self.assertEqual(preserved.status_code, 200)
        self.assertTrue(preserved.json()["stale"])
        self.assertEqual(
            preserved.json()["generated_at"],
            first_payload["generated_at"],
        )
        self.assertEqual(
            preserved.json()["summary"],
            first_payload["summary"],
        )

    def test_notebook_summary_uses_notebook_scope(self) -> None:
        response = self.client.post(
            f"/api/notebooks/{self.notebook.id}/summary"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "notebook")
        self.assertEqual(
            self.vector_store.get_calls[-1]["where"],
            {"document_id": {"$in": [self.document_id]}},
        )

    def test_topic_extraction_views_and_summary_use_exact_source_pairs(
        self,
    ) -> None:
        extraction = self.client.post(
            f"/api/documents/{self.document_id}/topics",
        )
        self.assertIn(extraction.status_code, {200, 201}, extraction.text)
        extracted_topics = self._items(extraction.json())
        self.assertEqual(len(extracted_topics), 1)
        topic = extracted_topics[0]
        topic_id = topic["id"]
        self.assertEqual(topic["name"], "Photosynthesis")
        self.assertFalse(topic["stale"])
        self.assertEqual(
            [
                (source["document_id"], source["chunk_index"])
                for source in topic["sources"]
            ],
            [(self.document_id, 0)],
        )

        model_calls_after_generation = len(self.model.calls)
        listed = self.client.get("/api/topics")
        document_topics = self.client.get(
            f"/api/documents/{self.document_id}/topics"
        )
        detail = self.client.get(f"/api/topics/{topic_id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(document_topics.status_code, 200)
        self.assertEqual(
            self._items(document_topics.json())[0]["id"],
            topic_id,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["id"], topic_id)
        self.assertEqual(len(self.model.calls), model_calls_after_generation)

        summary = self.client.post(f"/api/topics/{topic_id}/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["kind"], "topic")
        self.assertEqual(
            self.vector_store.get_calls[-1]["where"],
            {
                "$and": [
                    {
                        "document_id": {
                            "$eq": self.document_id,
                        }
                    },
                    {"chunk_index": {"$eq": 0}},
                ]
            },
        )
        cached = self.client.get(f"/api/topics/{topic_id}/summary")
        self.assertEqual(cached.status_code, 200)

    def test_notebook_topic_generation_and_cached_get_are_scoped(self) -> None:
        generated = self.client.post(
            f"/api/notebooks/{self.notebook.id}/topics"
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(
            self.vector_store.get_calls[-1]["where"],
            {"document_id": {"$in": [self.document_id]}},
        )

        model_calls = len(self.model.calls)
        cached = self.client.get(
            f"/api/notebooks/{self.notebook.id}/topics"
        )
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertEqual(len(self._items(cached.json())), 1)
        self.assertEqual(len(self.model.calls), model_calls)

    def test_failed_topic_regeneration_preserves_existing_topics(self) -> None:
        first = self.client.post(
            "/api/topics/extract",
            json={"scope": {"document_ids": [self.document_id]}},
        )
        self.assertIn(first.status_code, {200, 201}, first.text)
        original_topics = self._items(first.json())

        self.model.fail = True
        failed = self.client.post(
            "/api/topics/extract",
            json={"scope": {"document_ids": [self.document_id]}},
        )
        self.assertIn(failed.status_code, {500, 502})

        listed = self.client.get("/api/topics")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [topic["id"] for topic in self._items(listed.json())],
            [topic["id"] for topic in original_topics],
        )

    def test_topic_extraction_scope_validation(self) -> None:
        invalid_scopes = (
            {},
            {
                "notebook_id": self.notebook.id,
                "document_ids": [self.document_id],
            },
            {"document_ids": []},
            {"topic_id": ScopedRetrievalTest.TOPIC_ID},
        )
        for scope in invalid_scopes:
            with self.subTest(scope=scope):
                response = self.client.post(
                    "/api/topics/extract",
                    json={"scope": scope},
                )
                self.assertIn(response.status_code, {400, 404, 422})
                self.assertIn("error", response.json())
        self.assertEqual(self.model.calls, [])

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        for key in ("items", "topics"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        raise AssertionError(f"Topic response has no item list: {payload!r}")


# API contracts exercised above; persistence and prompts stay replaceable.


if __name__ == "__main__":
    unittest.main()
