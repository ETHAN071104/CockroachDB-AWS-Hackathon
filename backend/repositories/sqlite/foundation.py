from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.domain import (
    AdaptationEvent,
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
            CREATE TABLE IF NOT EXISTS guest_sessions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                creation_key_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL
                    CHECK (status IN ('active', 'revoked', 'expired')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                expires_at TEXT,
                revoked_at TEXT,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                session_label TEXT,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
                CHECK (length(token_hash) = 64),
                CHECK (length(creation_key_hash) = 64),
                CHECK (
                    (status = 'revoked' AND revoked_at IS NOT NULL)
                    OR
                    (status IN ('active', 'expired') AND revoked_at IS NULL)
                )
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_guest_sessions_workspace_status
            ON guest_sessions(workspace_id, status, created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_guest_sessions_active_expiry
            ON guest_sessions(expires_at)
            WHERE status = 'active' AND expires_at IS NOT NULL
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
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_question_id TEXT,
                topic TEXT NOT NULL DEFAULT '',
                signal_type TEXT NOT NULL,
                statement TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.5,
                importance REAL NOT NULL DEFAULT 0.5,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                signal_key TEXT,
                memory_id INTEGER,
                proposal_id TEXT,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            )
            """
        )
        signal_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(learning_signals)"
            ).fetchall()
        }
        signal_migrations = {
            "source_question_id": "TEXT",
            "topic": "TEXT NOT NULL DEFAULT ''",
            "statement": "TEXT NOT NULL DEFAULT ''",
            "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "confidence": "REAL NOT NULL DEFAULT 0.5",
            "importance": "REAL NOT NULL DEFAULT 0.5",
            "occurrence_count": "INTEGER NOT NULL DEFAULT 1",
            "first_observed_at": "TEXT NOT NULL DEFAULT ''",
            "last_observed_at": "TEXT NOT NULL DEFAULT ''",
            "signal_key": "TEXT",
            "memory_id": "INTEGER",
            "proposal_id": "TEXT",
        }
        for column_name, definition in signal_migrations.items():
            if column_name not in signal_columns:
                connection.execute(
                    "ALTER TABLE learning_signals "
                    f"ADD COLUMN {column_name} {definition}"
                )
        connection.execute(
            """
            UPDATE learning_signals
            SET first_observed_at = created_at
            WHERE first_observed_at IS NULL OR first_observed_at = ''
            """
        )
        connection.execute(
            """
            UPDATE learning_signals
            SET last_observed_at = updated_at
            WHERE last_observed_at IS NULL OR last_observed_at = ''
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_signals_workspace
            ON learning_signals(workspace_id, status, created_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_signals_key
            ON learning_signals(workspace_id, signal_key)
            WHERE signal_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_events (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                workflow_type TEXT NOT NULL,
                request_id TEXT NOT NULL,
                memory_ids_json TEXT NOT NULL,
                learning_signal_ids_json TEXT NOT NULL,
                applied_changes_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adaptation_events_workspace
            ON adaptation_events(workspace_id, workflow_type, created_at)
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

    def replace_payload(
        self,
        workflow_id: str,
        expected_version: int,
        payload: dict[str, object],
        expires_at: str,
    ) -> WorkflowState:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_states
                SET payload_json = ?, expires_at = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ? AND workspace_id = ? AND status = 'pending'
                    AND version = ?
                """,
                (
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    expires_at,
                    _now(),
                    workflow_id,
                    self.workspace_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflictError(
                    "Workflow state changed before its payload was updated."
                )
        state = self.get(workflow_id, include_terminal=True)
        assert state is not None
        return state

    def list_pending(self, workflow_type: str) -> list[WorkflowState]:
        self.cleanup_expired()
        with _connection_scope() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_states
                WHERE workspace_id = ? AND workflow_type = ?
                    AND status = 'pending'
                ORDER BY created_at DESC, id DESC
                """,
                (self.workspace_id, workflow_type),
            ).fetchall()
        return [_workflow_state(row) for row in rows]

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
        *,
        source_question_id: str | None = None,
        topic: str = "",
        statement: str = "",
        evidence: tuple[dict[str, object], ...] = (),
        confidence: float = 0.5,
        importance: float = 0.5,
        occurrence_count: int = 1,
        first_observed_at: str | None = None,
        last_observed_at: str | None = None,
        signal_key: str | None = None,
        memory_id: int | None = None,
        proposal_id: str | None = None,
    ) -> LearningSignal:
        initialize_foundation_schema()
        signal_id = str(uuid4())
        timestamp = _now()
        observed_at = first_observed_at or timestamp
        last_seen_at = last_observed_at or observed_at
        with _connection_scope() as connection:
            connection.execute(
                """
                INSERT INTO learning_signals (
                    id, workspace_id, source_type, source_id,
                    source_question_id, topic, signal_type, statement,
                    evidence_json, confidence, importance, occurrence_count,
                    payload_json, status, first_observed_at, last_observed_at,
                    created_at, updated_at, signal_key, memory_id, proposal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    self.workspace_id,
                    source_type,
                    source_id,
                    source_question_id,
                    topic.strip(),
                    signal_type,
                    statement.strip(),
                    json.dumps(evidence, separators=(",", ":"), sort_keys=True),
                    confidence,
                    importance,
                    occurrence_count,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    status,
                    observed_at,
                    last_seen_at,
                    timestamp,
                    timestamp,
                    signal_key,
                    memory_id,
                    proposal_id,
                ),
            )
        signal = self.get(signal_id)
        assert signal is not None
        return signal

    def get(self, signal_id: str) -> LearningSignal | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT * FROM learning_signals WHERE id = ? AND workspace_id = ?",
                (signal_id, self.workspace_id),
            ).fetchone()
        return _learning_signal(row) if row is not None else None

    def find_by_key(self, signal_key: str) -> LearningSignal | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                """
                SELECT * FROM learning_signals
                WHERE workspace_id = ? AND signal_key = ?
                """,
                (self.workspace_id, signal_key),
            ).fetchone()
        return _learning_signal(row) if row is not None else None

    def update(self, signal_id: str, **values: Any) -> LearningSignal:
        initialize_foundation_schema()
        columns = {
            "source_type": "source_type",
            "source_id": "source_id",
            "source_question_id": "source_question_id",
            "topic": "topic",
            "signal_type": "signal_type",
            "statement": "statement",
            "evidence": "evidence_json",
            "confidence": "confidence",
            "importance": "importance",
            "occurrence_count": "occurrence_count",
            "payload": "payload_json",
            "status": "status",
            "first_observed_at": "first_observed_at",
            "last_observed_at": "last_observed_at",
            "signal_key": "signal_key",
            "memory_id": "memory_id",
            "proposal_id": "proposal_id",
        }
        unknown = set(values) - set(columns)
        if unknown:
            raise ValueError("Unsupported learning signal fields: " + ", ".join(sorted(unknown)))
        if not values:
            signal = self.get(signal_id)
            if signal is None:
                raise KeyError(f"Learning signal {signal_id} does not exist.")
            return signal
        assignments: list[str] = []
        parameters: list[object] = []
        for name, value in values.items():
            assignments.append(f"{columns[name]} = ?")
            if name in {"evidence", "payload"}:
                value = json.dumps(value, separators=(",", ":"), sort_keys=True)
            parameters.append(value)
        assignments.append("updated_at = ?")
        parameters.extend((_now(), signal_id, self.workspace_id))
        with _connection_scope() as connection:
            cursor = connection.execute(
                "UPDATE learning_signals SET " + ", ".join(assignments)
                + " WHERE id = ? AND workspace_id = ?",
                tuple(parameters),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Learning signal {signal_id} does not exist.")
        signal = self.get(signal_id)
        assert signal is not None
        return signal

    def list(
        self,
        status: str | None = None,
        *,
        topic: str | None = None,
        signal_types: tuple[str, ...] | None = None,
    ) -> list[LearningSignal]:
        initialize_foundation_schema()
        sql = "SELECT * FROM learning_signals WHERE workspace_id = ?"
        parameters: list[object] = [self.workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status)
        if topic is not None:
            sql += " AND topic = ? COLLATE NOCASE"
            parameters.append(topic)
        if signal_types:
            sql += " AND signal_type IN (" + ",".join("?" for _ in signal_types) + ")"
            parameters.extend(signal_types)
        sql += " ORDER BY created_at DESC, id DESC"
        with _connection_scope() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_learning_signal(row) for row in rows]

    def link_memory(
        self,
        signal_ids: tuple[str, ...],
        memory_id: int,
        proposal_id: str | None = None,
    ) -> None:
        if not signal_ids:
            return
        initialize_foundation_schema()
        with _connection_scope() as connection:
            connection.executemany(
                """
                UPDATE learning_signals
                SET memory_id = ?, proposal_id = COALESCE(?, proposal_id),
                    updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                [
                    (memory_id, proposal_id, _now(), signal_id, self.workspace_id)
                    for signal_id in signal_ids
                ],
            )


class SQLiteAdaptationEventRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def create(
        self,
        workflow_type: str,
        request_id: str,
        memory_ids: tuple[int, ...],
        learning_signal_ids: tuple[str, ...],
        applied_changes: dict[str, object],
        reason: str,
    ) -> AdaptationEvent:
        initialize_foundation_schema()
        event_id = str(uuid4())
        with _connection_scope() as connection:
            connection.execute(
                """
                INSERT INTO adaptation_events (
                    id, workspace_id, workflow_type, request_id,
                    memory_ids_json, learning_signal_ids_json,
                    applied_changes_json, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    self.workspace_id,
                    workflow_type,
                    request_id,
                    json.dumps(memory_ids),
                    json.dumps(learning_signal_ids),
                    json.dumps(applied_changes, separators=(",", ":"), sort_keys=True),
                    reason.strip(),
                    _now(),
                ),
            )
        event = self.get(event_id)
        assert event is not None
        return event

    def get(self, event_id: str) -> AdaptationEvent | None:
        initialize_foundation_schema()
        with _connection_scope() as connection:
            row = connection.execute(
                "SELECT * FROM adaptation_events WHERE id = ? AND workspace_id = ?",
                (event_id, self.workspace_id),
            ).fetchone()
        return _adaptation_event(row) if row is not None else None

    def list(
        self,
        workflow_type: str | None = None,
        limit: int | None = None,
    ) -> list[AdaptationEvent]:
        initialize_foundation_schema()
        sql = "SELECT * FROM adaptation_events WHERE workspace_id = ?"
        parameters: list[object] = [self.workspace_id]
        if workflow_type is not None:
            sql += " AND workflow_type = ?"
            parameters.append(workflow_type)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with _connection_scope() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [_adaptation_event(row) for row in rows]


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
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        source_question_id=(
            str(row["source_question_id"])
            if row["source_question_id"] is not None
            else None
        ),
        topic=str(row["topic"]),
        signal_type=str(row["signal_type"]),
        statement=str(row["statement"]),
        evidence=tuple(json.loads(str(row["evidence_json"]))),
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        occurrence_count=int(row["occurrence_count"]),
        payload=json.loads(str(row["payload_json"])),
        status=str(row["status"]),
        first_observed_at=str(row["first_observed_at"]),
        last_observed_at=str(row["last_observed_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        signal_key=str(row["signal_key"]) if row["signal_key"] is not None else None,
        memory_id=int(row["memory_id"]) if row["memory_id"] is not None else None,
        proposal_id=str(row["proposal_id"]) if row["proposal_id"] is not None else None,
    )


def _adaptation_event(row: Any) -> AdaptationEvent:
    return AdaptationEvent(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        workflow_type=str(row["workflow_type"]),
        request_id=str(row["request_id"]),
        memory_ids=tuple(int(value) for value in json.loads(str(row["memory_ids_json"]))),
        learning_signal_ids=tuple(
            str(value) for value in json.loads(str(row["learning_signal_ids_json"]))
        ),
        applied_changes=json.loads(str(row["applied_changes_json"])),
        reason=str(row["reason"]),
        created_at=str(row["created_at"]),
    )
