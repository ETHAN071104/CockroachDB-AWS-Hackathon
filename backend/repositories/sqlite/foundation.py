from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.domain import (
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    LearningSignal,
    VectorOutboxJob,
    WorkflowState,
    Workspace,
)
from backend.repositories.interfaces import RepositoryConflictError


WORKSPACE_TABLES = (
    "documents",
    "notebooks",
    "notebook_documents",
    "cached_intelligence",
    "topics",
    "topic_sources",
    "memories",
    "memory_relationships",
    "study_sessions",
    "study_interactions",
    "study_interaction_sources",
    "quiz_attempts",
    "quiz_question_attempts",
    "quiz_question_sources",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection_scope():
    from backend.rag.database import get_connection

    return get_connection()


def initialize_foundation_schema() -> None:
    """Create ownership/workflow/outbox tables and backfill local data."""
    timestamp = _now()
    with _connection_scope() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO workspaces (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME, timestamp, timestamp),
        )

        table_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table_name in WORKSPACE_TABLES:
            if table_name not in table_names:
                continue
            columns = {
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }
            if "workspace_id" not in columns:
                connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN workspace_id "
                    f"TEXT NOT NULL DEFAULT '{DEFAULT_WORKSPACE_ID}'"
                )
            connection.execute(
                f"UPDATE {table_name} SET workspace_id = ? "
                "WHERE workspace_id IS NULL OR workspace_id = ''",
                (DEFAULT_WORKSPACE_ID,),
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_workspace "
                f"ON {table_name}(workspace_id)"
            )

        if "study_sessions" in table_names:
            connection.execute("DROP INDEX IF EXISTS idx_study_sessions_single_active")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_study_sessions_single_active
                ON study_sessions(workspace_id)
                WHERE status = 'active'
                """
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_states (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                workflow_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                decision_metadata_json TEXT,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_states_lookup
            ON workflow_states(workspace_id, workflow_type, status, expires_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_outbox (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
                CHECK (entity_type IN ('document', 'memory')),
                CHECK (operation IN ('upsert', 'delete')),
                CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_vector_outbox_retry
            ON vector_outbox(workspace_id, status, created_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_signals (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_signals_workspace
            ON learning_signals(workspace_id, status, created_at)
            """
        )


class SQLiteWorkspaceRepository:
    def ensure_default(self) -> Workspace:
        initialize_foundation_schema()
        workspace = self.get(DEFAULT_WORKSPACE_ID)
        assert workspace is not None
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT id, name, created_at, updated_at FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        return Workspace(
            id=str(row["id"]),
            name=str(row["name"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create(self, workspace_id: str, name: str) -> Workspace:
        initialize_foundation_schema()
        normalized_id = workspace_id.strip()
        normalized_name = name.strip()
        if not normalized_id or not normalized_name:
            raise ValueError("Workspace ID and name are required.")
        timestamp = _now()
        with _connection_scope() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO workspaces (id, name, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (normalized_id, normalized_name, timestamp, timestamp),
                )
            except Exception as error:
                raise RepositoryConflictError(str(error)) from error
        workspace = self.get(normalized_id)
        assert workspace is not None
        return workspace


class SQLiteWorkflowStateRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def put(
        self,
        workflow_id: str,
        workflow_type: str,
        payload: dict[str, object],
        expires_at: str,
    ) -> WorkflowState:
        initialize_foundation_schema()
        timestamp = _now()
        with _connection_scope() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO workflow_states (
                        id, workspace_id, workflow_type, payload_json, status,
                        created_at, updated_at, expires_at, version
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, 1)
                    """,
                    (
                        workflow_id,
                        self.workspace_id,
                        workflow_type,
                        json.dumps(payload, separators=(",", ":"), sort_keys=True),
                        timestamp,
                        timestamp,
                        expires_at,
                    ),
                )
            except Exception as error:
                raise RepositoryConflictError(str(error)) from error
        state = self.get(workflow_id, workflow_type)
        if state is None:
            state = self.get(
                workflow_id,
                workflow_type,
                include_terminal=True,
            )
        assert state is not None
        return state

    def get(
        self,
        workflow_id: str,
        workflow_type: str | None = None,
        *,
        include_terminal: bool = False,
    ) -> WorkflowState | None:
        initialize_foundation_schema()
        clauses = ["id = ?", "workspace_id = ?"]
        parameters: list[object] = [workflow_id, self.workspace_id]
        if workflow_type is not None:
            clauses.append("workflow_type = ?")
            parameters.append(workflow_type)
        if not include_terminal:
            clauses.append("status = 'pending'")
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_states WHERE " + " AND ".join(clauses),
                tuple(parameters),
            ).fetchone()
            if row is not None and str(row["expires_at"]) <= _now() and str(row["status"]) == "pending":
                connection.execute(
                    """
                    UPDATE workflow_states
                    SET status = 'expired', updated_at = ?, version = version + 1
                    WHERE id = ? AND workspace_id = ? AND version = ?
                    """,
                    (_now(), workflow_id, self.workspace_id, int(row["version"])),
                )
                return None
        return _workflow_state(row) if row is not None else None

    def decide(
        self,
        workflow_id: str,
        expected_version: int,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkflowState:
        initialize_foundation_schema()
        timestamp = _now()
        with _connection_scope() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_states
                SET status = ?, decision_metadata_json = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ? AND workspace_id = ? AND version = ?
                """,
                (
                    status,
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True)
                    if metadata is not None
                    else None,
                    timestamp,
                    workflow_id,
                    self.workspace_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflictError(
                    "Workflow state changed before the requested decision."
                )
        state = self.get(workflow_id, include_terminal=True)
        assert state is not None
        return state

    def count_pending(self, workflow_type: str) -> int:
        self.cleanup_expired()
        with _connection_scope() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total FROM workflow_states
                WHERE workspace_id = ? AND workflow_type = ? AND status = 'pending'
                """,
                (self.workspace_id, workflow_type),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def clear_type(self, workflow_type: str) -> int:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            cursor = connection.execute(
                "DELETE FROM workflow_states WHERE workspace_id = ? AND workflow_type = ?",
                (self.workspace_id, workflow_type),
            )
        return int(cursor.rowcount)

    def cleanup_expired(self) -> int:
        initialize_foundation_schema()
        timestamp = _now()
        with _connection_scope() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_states
                SET status = 'expired', updated_at = ?, version = version + 1
                WHERE workspace_id = ? AND status = 'pending' AND expires_at <= ?
                """,
                (timestamp, self.workspace_id, timestamp),
            )
        return int(cursor.rowcount)

    def trim_pending(self, workflow_type: str, maximum: int) -> int:
        if maximum <= 0:
            raise ValueError("Maximum pending workflow count must be positive.")
        self.cleanup_expired()
        with _connection_scope() as connection:
            rows = connection.execute(
                """
                SELECT id FROM workflow_states
                WHERE workspace_id = ? AND workflow_type = ? AND status = 'pending'
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (self.workspace_id, workflow_type, maximum),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                connection.executemany(
                    "DELETE FROM workflow_states WHERE id = ? AND workspace_id = ?",
                    [(workflow_id, self.workspace_id) for workflow_id in ids],
                )
        return len(ids)


class SQLiteVectorOutboxRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def enqueue(
        self,
        entity_type: str,
        entity_id: str,
        operation: str,
        payload: dict[str, object],
    ) -> VectorOutboxJob:
        initialize_foundation_schema()
        job_id = str(uuid4())
        timestamp = _now()
        with _connection_scope() as connection:
            # A newer operation for the same entity carries the authoritative
            # desired state. Superseding older retryable jobs prevents a late
            # retry from restoring stale vectors after a newer change.
            connection.execute(
                """
                UPDATE vector_outbox
                SET status = 'completed', updated_at = ?,
                    last_error = 'Superseded by a newer vector operation.'
                WHERE workspace_id = ? AND entity_type = ? AND entity_id = ?
                  AND status IN ('pending', 'failed')
                """,
                (timestamp, self.workspace_id, entity_type, entity_id),
            )
            connection.execute(
                """
                INSERT INTO vector_outbox (
                    id, workspace_id, entity_type, entity_id, operation,
                    payload_json, status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (
                    job_id,
                    self.workspace_id,
                    entity_type,
                    entity_id,
                    operation,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> VectorOutboxJob | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT * FROM vector_outbox WHERE id = ? AND workspace_id = ?",
                (job_id, self.workspace_id),
            ).fetchone()
        return _outbox_job(row) if row is not None else None

    def list_retryable(self, limit: int = 100) -> list[VectorOutboxJob]:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            rows = connection.execute(
                """
                SELECT * FROM vector_outbox
                WHERE workspace_id = ?
                  AND status IN ('pending', 'failed', 'processing')
                ORDER BY created_at ASC, id ASC LIMIT ?
                """,
                (self.workspace_id, int(limit)),
            ).fetchall()
        return [_outbox_job(row) for row in rows]

    def mark_processing(self, job_id: str) -> VectorOutboxJob:
        return self._set_status(job_id, "processing", increment_attempts=True)

    def mark_completed(self, job_id: str) -> VectorOutboxJob:
        return self._set_status(job_id, "completed", last_error=None)

    def mark_failed(self, job_id: str, error: str) -> VectorOutboxJob:
        return self._set_status(job_id, "failed", last_error=error[:2000])

    def _set_status(
        self,
        job_id: str,
        status: str,
        *,
        increment_attempts: bool = False,
        last_error: str | None = None,
    ) -> VectorOutboxJob:
        initialize_foundation_schema()
        attempts_sql = ", attempts = attempts + 1" if increment_attempts else ""
        with _connection_scope() as connection:
            cursor = connection.execute(
                "UPDATE vector_outbox SET status = ?, updated_at = ?, last_error = ?"
                + attempts_sql
                + " WHERE id = ? AND workspace_id = ?",
                (status, _now(), last_error, job_id, self.workspace_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Vector outbox job {job_id} does not exist.")
        job = self.get(job_id)
        assert job is not None
        return job


class SQLiteLearningSignalRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def create(
        self,
        signal_type: str,
        source_type: str,
        source_id: str,
        payload: dict[str, object],
        status: str = "pending",
    ) -> LearningSignal:
        initialize_foundation_schema()
        signal_id = str(uuid4())
        timestamp = _now()
        with _connection_scope() as connection:
            connection.execute(
                """
                INSERT INTO learning_signals (
                    id, workspace_id, signal_type, source_type, source_id,
                    payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    self.workspace_id,
                    signal_type,
                    source_type,
                    source_id,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    status,
                    timestamp,
                    timestamp,
                ),
            )
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT * FROM learning_signals WHERE id = ? AND workspace_id = ?",
                (signal_id, self.workspace_id),
            ).fetchone()
        assert row is not None
        return _learning_signal(row)

    def list(self, status: str | None = None) -> list[LearningSignal]:
        initialize_foundation_schema()
        sql = "SELECT * FROM learning_signals WHERE workspace_id = ?"
        parameters: list[object] = [self.workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status)
        sql += " ORDER BY created_at DESC, id DESC"
        with _connection_scope() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_learning_signal(row) for row in rows]


def _workflow_state(row: Any) -> WorkflowState:
    metadata = row["decision_metadata_json"]
    return WorkflowState(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        workflow_type=str(row["workflow_type"]),
        payload=json.loads(str(row["payload_json"])),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]),
        version=int(row["version"]),
        decision_metadata=json.loads(str(metadata)) if metadata else None,
    )


def _outbox_job(row: Any) -> VectorOutboxJob:
    return VectorOutboxJob(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]),
        operation=str(row["operation"]),
        payload=json.loads(str(row["payload_json"])),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
    )


def _learning_signal(row: Any) -> LearningSignal:
    return LearningSignal(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        signal_type=str(row["signal_type"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        payload=json.loads(str(row["payload_json"])),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
