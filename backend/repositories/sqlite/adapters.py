from __future__ import annotations

import sqlite3
from typing import Any

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.repositories.interfaces import RepositoryConflictError


class SQLiteDocumentRepository:
    """Compatibility adapter over the existing document persistence module."""

    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    @staticmethod
    def _database():
        from backend.rag import database

        return database

    def find_by_hash(self, file_hash: str) -> Any | None:
        return self._database().find_document_by_hash(
            file_hash,
            workspace_id=self.workspace_id,
        )

    def insert(
        self,
        filename: str,
        mime_type: str,
        file_hash: str,
        file_data: bytes,
    ) -> int:
        try:
            return self._database().insert_document(
                filename,
                mime_type,
                file_hash,
                file_data,
                workspace_id=self.workspace_id,
            )
        except sqlite3.IntegrityError as error:
            raise RepositoryConflictError(str(error)) from error

    def get(self, document_id: int) -> Any | None:
        return self._database().get_document(
            document_id,
            workspace_id=self.workspace_id,
        )

    def get_file_data(self, document_id: int) -> tuple[str, bytes]:
        return self._database().get_document_file_data(
            document_id,
            workspace_id=self.workspace_id,
        )

    def update_chunk_count(self, document_id: int, chunk_count: int) -> None:
        self._database().update_chunk_count(
            document_id,
            chunk_count,
            workspace_id=self.workspace_id,
        )

    def delete(self, document_id: int) -> bool:
        return self._database().delete_document_record(
            document_id,
            workspace_id=self.workspace_id,
        )

    def list(self) -> list[Any]:
        return self._database().list_documents(workspace_id=self.workspace_id)


class SQLiteNotebookRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    @staticmethod
    def _module():
        from backend.rag import notebooks

        return notebooks

    def create(self, name: str, description: str = "") -> Any:
        return self._module().create_notebook(
            name,
            description,
            workspace_id=self.workspace_id,
        )

    def get(self, notebook_id: int) -> Any | None:
        notebook = self._module().get_notebook(
            notebook_id,
            workspace_id=self.workspace_id,
        )
        if notebook is None:
            return None
        return notebook

    def list(self, search: str | None = None) -> list[Any]:
        return self._module().list_notebooks(
            search=search,
            workspace_id=self.workspace_id,
        )

    def update(
        self,
        notebook_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Any:
        return self._module().update_notebook(
            notebook_id,
            name=name,
            description=description,
            workspace_id=self.workspace_id,
        )

    def delete(self, notebook_id: int) -> bool:
        return self._module().delete_notebook(
            notebook_id,
            workspace_id=self.workspace_id,
        )

    def assign_document(self, document_id: int, notebook_id: int) -> Any:
        return self._module().assign_document_to_notebook(
            document_id,
            notebook_id,
            workspace_id=self.workspace_id,
        )

    def remove_document(self, document_id: int) -> bool:
        return self._module().remove_document_from_notebook(
            document_id,
            workspace_id=self.workspace_id,
        )

    def count_documents(self, notebook_id: int | None) -> int:
        return self._module().count_notebook_documents(
            notebook_id,
            workspace_id=self.workspace_id,
        )

    def get_document(self, document_id: int) -> Any | None:
        return self._module().get_document_record(
            document_id,
            workspace_id=self.workspace_id,
        )

    def list_documents(
        self,
        *,
        notebook_id: int | None = None,
        unsorted_only: bool = False,
        search: str | None = None,
    ) -> list[Any]:
        return self._module().list_document_records(
            notebook_id=notebook_id,
            unsorted_only=unsorted_only,
            search=search,
            workspace_id=self.workspace_id,
        )

    def get_document_notebook_id(self, document_id: int) -> int | None:
        return self._module().get_document_notebook_id(
            document_id,
            workspace_id=self.workspace_id,
        )


class SQLiteIntelligenceRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    @staticmethod
    def _module():
        from backend.rag import intelligence_store

        return intelligence_store

    def get_cached(self, kind: str, scope_kind: str, scope_key: str) -> Any | None:
        return self._module().get_cached_intelligence(
            kind,
            scope_kind,
            scope_key,
            workspace_id=self.workspace_id,
        )

    def replace_cached(self, **values: Any) -> Any:
        return self._module().replace_cached_intelligence(
            **values,
            workspace_id=self.workspace_id,
        )

    def replace_topics(self, **values: Any) -> list[Any]:
        return self._module().replace_topics_for_scope(
            **values,
            workspace_id=self.workspace_id,
        )

    def get_topic(self, topic_id: str) -> Any | None:
        return self._module().get_topic(
            topic_id,
            workspace_id=self.workspace_id,
        )

    def list_topics(self, **filters: Any) -> list[Any]:
        return self._module().list_topics(
            **filters,
            workspace_id=self.workspace_id,
        )

    def fingerprint_for_scope(self, scope_kind: str, scope_key: object = None) -> str:
        # The legacy helper currently derives fingerprints from the selected
        # local store; repository ownership has already constrained callers.
        return self._module().fingerprint_for_scope(scope_kind, scope_key)


class SQLiteStudySessionRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    @staticmethod
    def _module():
        from backend.study import database

        return database

    def get_or_create_active(self) -> Any:
        return self._module().get_or_create_active_study_session(
            workspace_id=self.workspace_id
        )

    def get_active(self) -> Any | None:
        return self._module().get_active_study_session(workspace_id=self.workspace_id)

    def insert_interaction_with_sources(self, **values: Any) -> tuple[Any, list[Any]]:
        return self._module().insert_study_interaction_with_sources(
            **values,
            workspace_id=self.workspace_id,
        )

    def get(self, session_id: int) -> Any | None:
        return self._module().get_study_session(
            session_id,
            workspace_id=self.workspace_id,
        )

    def get_interaction(self, interaction_id: int) -> Any | None:
        return self._module().get_study_interaction(interaction_id)

    def list(self) -> list[Any]:
        return self._module().list_study_sessions(workspace_id=self.workspace_id)

    def list_interactions(self, session_id: int) -> list[Any]:
        return self._module().list_session_interactions(
            session_id,
            workspace_id=self.workspace_id,
        )

    def list_sources(self, interaction_id: int) -> list[Any]:
        return self._module().list_interaction_sources(
            interaction_id,
            workspace_id=self.workspace_id,
        )

    def update_outcome(self, interaction_id: int, outcome: str) -> Any:
        return self._module().update_interaction_outcome(interaction_id, outcome)

    def end(self, session_id: int) -> Any:
        return self._module().end_study_session(session_id)


class SQLiteQuizRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def save_run_result(self, result: Any) -> tuple[Any, list[Any]]:
        from backend.study.quiz_history import save_quiz_run_result

        return save_quiz_run_result(result, workspace_id=self.workspace_id)

    @staticmethod
    def _module():
        from backend.study import database

        return database

    def get_attempt(self, attempt_id: int) -> Any | None:
        return self._module().get_quiz_attempt(
            attempt_id,
            workspace_id=self.workspace_id,
        )

    def list_attempts(self, limit: int | None = None) -> list[Any]:
        return self._module().list_quiz_attempts(
            limit=limit,
            workspace_id=self.workspace_id,
        )

    def list_questions(self, attempt_id: int) -> list[Any]:
        return self._module().list_quiz_question_attempts(
            attempt_id,
            workspace_id=self.workspace_id,
        )

    def list_sources(self, question_attempt_id: int) -> list[Any]:
        return self._module().list_quiz_question_sources(
            question_attempt_id,
            workspace_id=self.workspace_id,
        )


class SQLiteLearnerMemoryRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    @staticmethod
    def _module():
        from backend.memory import database

        return database

    def insert(self, **values: Any) -> int:
        values.setdefault("confidence", 1.0)
        values.setdefault("importance", 0.5)
        return self._module().insert_memory(
            **values,
            workspace_id=self.workspace_id,
        )

    def get(self, memory_id: int) -> Any | None:
        return self._module().get_memory(
            memory_id,
            workspace_id=self.workspace_id,
        )

    def get_many(self, memory_ids: list[int]) -> list[Any]:
        return self._module().get_memories_by_ids(
            memory_ids,
            workspace_id=self.workspace_id,
        )

    def list(self, include_archived: bool = False) -> list[Any]:
        return self._module().list_memories(
            include_archived=include_archived,
            workspace_id=self.workspace_id,
        )

    def update(self, **values: Any) -> bool:
        return self._module().update_memory_record(
            **values,
            workspace_id=self.workspace_id,
        )

    def archive(self, memory_id: int) -> bool:
        return self._module().archive_memory_record(
            memory_id,
            workspace_id=self.workspace_id,
        )

    def activate(self, memory_id: int) -> bool:
        return self._module().activate_memory_record(
            memory_id,
            workspace_id=self.workspace_id,
        )

    def delete(self, memory_id: int) -> bool:
        try:
            return self._module().delete_memory_record(
                memory_id,
                workspace_id=self.workspace_id,
            )
        except sqlite3.IntegrityError as error:
            raise RepositoryConflictError(str(error)) from error

    def insert_relationships(self, **values: Any) -> None:
        self._module().insert_memory_relationships(
            **values,
            workspace_id=self.workspace_id,
        )

    def delete_relationships_for_target(self, memory_id: int) -> int:
        return self._module().delete_relationships_for_target(
            memory_id,
            workspace_id=self.workspace_id,
        )
