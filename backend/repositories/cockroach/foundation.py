from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.domain import (
    AdaptationEvent,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    LearningSignal,
    VectorOutboxJob,
    WorkflowState,
    Workspace,
    new_record_id,
)
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import (
    iso,
    json_text,
    json_value,
    timestamp,
    utc_now,
    uuid_for_public,
)
from backend.repositories.interfaces import RepositoryConflictError


class CockroachWorkspaceRepository:
    def ensure_default(self) -> Workspace:
        now = utc_now()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workspaces (id, name, created_at, updated_at)
                    VALUES (:id, :name, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": UUID(DEFAULT_WORKSPACE_ID),
                    "name": DEFAULT_WORKSPACE_NAME,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        workspace = self.get(DEFAULT_WORKSPACE_ID)
        assert workspace is not None
        return workspace

    def create(self, workspace_id: str, name: str) -> Workspace:
        now = utc_now()
        try:
            with connection_scope() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO workspaces (id, name, created_at, updated_at)
                        VALUES (:id, :name, :created_at, :updated_at)
                        """
                    ),
                    {
                        "id": UUID(workspace_id),
                        "name": name.strip(),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        except IntegrityError as error:
            raise RepositoryConflictError("Workspace already exists.") from error
        workspace = self.get(workspace_id)
        assert workspace is not None
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        with connection_scope() as connection:
            row = connection.execute(
                text("SELECT * FROM workspaces WHERE id = :id"),
                {"id": UUID(workspace_id)},
            ).mappings().one_or_none()
        if row is None:
            return None
        return Workspace(
            id=str(row["id"]),
            name=str(row["name"]),
            created_at=iso(row["created_at"]),
            updated_at=iso(row["updated_at"]),
        )


class CockroachWorkflowStateRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def put(
        self,
        workflow_id: str,
        workflow_type: str,
        payload: dict[str, object],
        expires_at: str,
    ) -> WorkflowState:
        now = utc_now()
        try:
            with connection_scope() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO workflow_states (
                            id, workspace_id, workflow_type, payload, status,
                            created_at, updated_at, expires_at, version
                        ) VALUES (
                            :id, :workspace_id, :workflow_type,
                            CAST(:payload AS JSONB), 'pending',
                            :created_at, :updated_at, :expires_at, 1
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            payload = excluded.payload,
                            status = 'pending',
                            updated_at = excluded.updated_at,
                            expires_at = excluded.expires_at,
                            version = workflow_states.version + 1,
                            decision_metadata = NULL
                        WHERE workflow_states.workspace_id = excluded.workspace_id
                        """
                    ),
                    {
                        "id": UUID(workflow_id),
                        "workspace_id": UUID(self.workspace_id),
                        "workflow_type": workflow_type,
                        "payload": json_text(payload),
                        "created_at": now,
                        "updated_at": now,
                        "expires_at": timestamp(expires_at),
                    },
                )
        except IntegrityError as error:
            raise RepositoryConflictError("Workflow state conflict.") from error
        state = self.get(workflow_id, include_terminal=True)
        assert state is not None
        return state

    def get(
        self,
        workflow_id: str,
        workflow_type: str | None = None,
        *,
        include_terminal: bool = False,
    ) -> WorkflowState | None:
        clauses = ["id = :id", "workspace_id = :workspace_id"]
        parameters: dict[str, object] = {
            "id": UUID(workflow_id),
            "workspace_id": UUID(self.workspace_id),
        }
        if workflow_type is not None:
            clauses.append("workflow_type = :workflow_type")
            parameters["workflow_type"] = workflow_type
        if not include_terminal:
            clauses.append("status = 'pending'")
            clauses.append("expires_at > now()")
        with connection_scope() as connection:
            row = connection.execute(
                text("SELECT * FROM workflow_states WHERE " + " AND ".join(clauses)),
                parameters,
            ).mappings().one_or_none()
        return _workflow_state(row) if row is not None else None

    def decide(
        self,
        workflow_id: str,
        expected_version: int,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkflowState:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE workflow_states
                    SET status = :status, updated_at = :updated_at,
                        version = version + 1,
                        decision_metadata = CAST(:metadata AS JSONB)
                    WHERE id = :id AND workspace_id = :workspace_id
                      AND status = 'pending' AND version = :expected_version
                      AND expires_at > now()
                    """
                ),
                {
                    "status": status,
                    "updated_at": utc_now(),
                    "metadata": json_text(metadata) if metadata is not None else None,
                    "id": UUID(workflow_id),
                    "workspace_id": UUID(self.workspace_id),
                    "expected_version": expected_version,
                },
            )
            if result.rowcount != 1:
                raise RepositoryConflictError(
                    "Workflow state changed, expired, or was already decided."
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
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE workflow_states
                    SET payload = CAST(:payload AS JSONB), updated_at = :updated_at,
                        expires_at = :expires_at, version = version + 1
                    WHERE id = :id AND workspace_id = :workspace_id
                      AND status = 'pending' AND version = :expected_version
                      AND expires_at > now()
                    """
                ),
                {
                    "payload": json_text(payload),
                    "updated_at": utc_now(),
                    "expires_at": timestamp(expires_at),
                    "id": UUID(workflow_id),
                    "workspace_id": UUID(self.workspace_id),
                    "expected_version": expected_version,
                },
            )
            if result.rowcount != 1:
                raise RepositoryConflictError(
                    "Workflow state changed, expired, or was already decided."
                )
        state = self.get(workflow_id, include_terminal=True)
        assert state is not None
        return state

    def list_pending(self, workflow_type: str) -> list[WorkflowState]:
        self.cleanup_expired()
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM workflow_states
                    WHERE workspace_id = :workspace_id
                      AND workflow_type = :workflow_type AND status = 'pending'
                      AND expires_at > now()
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "workflow_type": workflow_type,
                },
            ).mappings().all()
        return [_workflow_state(row) for row in rows]

    def count_pending(self, workflow_type: str) -> int:
        self.cleanup_expired()
        with connection_scope() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT count(*) FROM workflow_states
                    WHERE workspace_id = :workspace_id
                      AND workflow_type = :workflow_type AND status = 'pending'
                      AND expires_at > now()
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "workflow_type": workflow_type,
                },
            ).scalar_one()
        return int(value)

    def clear_type(self, workflow_type: str) -> int:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "DELETE FROM workflow_states "
                    "WHERE workspace_id=:workspace_id AND workflow_type=:workflow_type"
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "workflow_type": workflow_type,
                },
            )
        return int(result.rowcount)

    def cleanup_expired(self) -> int:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE workflow_states
                    SET status='expired', updated_at=now(), version=version+1
                    WHERE workspace_id=:workspace_id AND status='pending'
                      AND expires_at <= now()
                    """
                ),
                {"workspace_id": UUID(self.workspace_id)},
            )
        return int(result.rowcount)

    def trim_pending(self, workflow_type: str, maximum: int) -> int:
        if maximum <= 0:
            raise ValueError("Maximum pending workflow count must be positive.")
        self.cleanup_expired()
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id FROM workflow_states
                    WHERE workspace_id=:workspace_id
                      AND workflow_type=:workflow_type AND status='pending'
                    ORDER BY created_at DESC, id DESC OFFSET :maximum
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "workflow_type": workflow_type,
                    "maximum": maximum,
                },
            ).scalars().all()
            if rows:
                connection.execute(
                    text(
                        "DELETE FROM workflow_states "
                        "WHERE workspace_id=:workspace_id AND id = ANY(:ids)"
                    ),
                    {"workspace_id": UUID(self.workspace_id), "ids": list(rows)},
                )
        return len(rows)


class CockroachVectorOutboxRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def enqueue(
        self,
        entity_type: str,
        entity_id: str,
        operation: str,
        payload: dict[str, object],
    ) -> VectorOutboxJob:
        job_id = new_record_id()
        now = utc_now()
        idempotency_key = f"{entity_type}:{entity_id}:{operation}:{job_id}"
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    UPDATE embedding_jobs SET status='completed', updated_at=:now,
                        last_error='Superseded by a newer vector operation.'
                    WHERE workspace_id=:workspace_id AND entity_type=:entity_type
                      AND entity_id=:entity_id AND status IN ('pending','failed')
                    """
                ),
                {
                    "now": now,
                    "workspace_id": UUID(self.workspace_id),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO embedding_jobs (
                        id, workspace_id, entity_type, entity_id, operation,
                        payload, status, attempts, idempotency_key,
                        created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, :entity_type, :entity_id, :operation,
                        CAST(:payload AS JSONB), 'pending', 0, :idempotency_key,
                        :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": job_id,
                    "workspace_id": UUID(self.workspace_id),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "operation": operation,
                    "payload": json_text(payload),
                    "idempotency_key": idempotency_key,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        job = self.get(str(job_id))
        assert job is not None
        return job

    def get(self, job_id: str) -> VectorOutboxJob | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM embedding_jobs "
                    "WHERE id=:id AND workspace_id=:workspace_id"
                ),
                {"id": UUID(job_id), "workspace_id": UUID(self.workspace_id)},
            ).mappings().one_or_none()
        return _outbox_job(row) if row is not None else None

    def list_retryable(self, limit: int = 100) -> list[VectorOutboxJob]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM embedding_jobs
                    WHERE workspace_id=:workspace_id
                      AND status IN ('pending','failed','processing')
                    ORDER BY created_at ASC, id ASC LIMIT :limit
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "limit": int(limit)},
            ).mappings().all()
        return [_outbox_job(row) for row in rows]

    def mark_processing(self, job_id: str) -> VectorOutboxJob:
        return self._set_status(job_id, "processing", increment=True)

    def mark_completed(self, job_id: str) -> VectorOutboxJob:
        return self._set_status(job_id, "completed", last_error=None)

    def mark_failed(self, job_id: str, error: str) -> VectorOutboxJob:
        return self._set_status(job_id, "failed", last_error=error[:2000])

    def _set_status(
        self,
        job_id: str,
        status: str,
        *,
        increment: bool = False,
        last_error: str | None = None,
    ) -> VectorOutboxJob:
        attempts = ", attempts=attempts+1, claimed_at=now()" if increment else ""
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "UPDATE embedding_jobs SET status=:status, updated_at=now(), "
                    "last_error=:last_error" + attempts
                    + " WHERE id=:id AND workspace_id=:workspace_id"
                ),
                {
                    "status": status,
                    "last_error": last_error,
                    "id": UUID(job_id),
                    "workspace_id": UUID(self.workspace_id),
                },
            )
            if result.rowcount != 1:
                raise KeyError(f"Vector outbox job {job_id} does not exist.")
        job = self.get(job_id)
        assert job is not None
        return job


class CockroachLearningSignalRepository:
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
        record_id = new_record_id()
        now = utc_now()
        memory_uuid = (
            uuid_for_public("learner_memories", self.workspace_id, memory_id)
            if memory_id is not None else None
        )
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO learning_signals (
                        id, workspace_id, source_type, source_id, source_question_id,
                        topic, signal_type, statement, evidence, confidence, importance,
                        occurrence_count, payload, status, first_observed_at,
                        last_observed_at, created_at, updated_at, signal_key,
                        memory_id, proposal_id
                    ) VALUES (
                        :id, :workspace_id, :source_type, :source_id, :source_question_id,
                        :topic, :signal_type, :statement, CAST(:evidence AS JSONB),
                        :confidence, :importance, :occurrence_count,
                        CAST(:payload AS JSONB), :status, :first_observed_at,
                        :last_observed_at, :created_at, :updated_at, :signal_key,
                        :memory_id, :proposal_id
                    )
                    """
                ),
                {
                    "id": record_id,
                    "workspace_id": UUID(self.workspace_id),
                    "source_type": source_type,
                    "source_id": source_id,
                    "source_question_id": source_question_id,
                    "topic": topic.strip(),
                    "signal_type": signal_type,
                    "statement": statement.strip(),
                    "evidence": json_text(evidence),
                    "confidence": confidence,
                    "importance": importance,
                    "occurrence_count": occurrence_count,
                    "payload": json_text(payload),
                    "status": status,
                    "first_observed_at": timestamp(first_observed_at),
                    "last_observed_at": timestamp(last_observed_at or first_observed_at),
                    "created_at": now,
                    "updated_at": now,
                    "signal_key": signal_key,
                    "memory_id": memory_uuid,
                    "proposal_id": UUID(proposal_id) if proposal_id else None,
                },
            )
        signal = self.get(str(record_id))
        assert signal is not None
        return signal

    def _rows(self, where: str, parameters: dict[str, object]) -> list[Any]:
        with connection_scope() as connection:
            return connection.execute(
                text(
                    """
                    SELECT s.*, m.public_id AS memory_public_id
                    FROM learning_signals s
                    LEFT JOIN learner_memories m ON m.id=s.memory_id
                    WHERE s.workspace_id=:workspace_id AND """ + where
                    + " ORDER BY s.created_at DESC, s.id DESC"
                ),
                {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().all()

    def get(self, signal_id: str) -> LearningSignal | None:
        rows = self._rows("s.id=:id", {"id": UUID(signal_id)})
        return _learning_signal(rows[0]) if rows else None

    def find_by_key(self, signal_key: str) -> LearningSignal | None:
        rows = self._rows("s.signal_key=:signal_key", {"signal_key": signal_key})
        return _learning_signal(rows[0]) if rows else None

    def update(self, signal_id: str, **values: Any) -> LearningSignal:
        columns = {
            "source_type": "source_type", "source_id": "source_id",
            "source_question_id": "source_question_id", "topic": "topic",
            "signal_type": "signal_type", "statement": "statement",
            "evidence": "evidence", "confidence": "confidence",
            "importance": "importance", "occurrence_count": "occurrence_count",
            "payload": "payload", "status": "status",
            "first_observed_at": "first_observed_at",
            "last_observed_at": "last_observed_at", "signal_key": "signal_key",
            "memory_id": "memory_id", "proposal_id": "proposal_id",
        }
        unknown = set(values) - set(columns)
        if unknown:
            raise ValueError("Unsupported learning signal fields: " + ", ".join(sorted(unknown)))
        if not values:
            current = self.get(signal_id)
            if current is None:
                raise KeyError(f"Learning signal {signal_id} does not exist.")
            return current
        assignments = []
        parameters: dict[str, object] = {
            "id": UUID(signal_id), "workspace_id": UUID(self.workspace_id),
            "updated_at": utc_now(),
        }
        for name, value in values.items():
            column = columns[name]
            parameter = name
            if name in {"evidence", "payload"}:
                assignments.append(f"{column}=CAST(:{parameter} AS JSONB)")
                value = json_text(value)
            elif name == "memory_id":
                assignments.append(f"{column}=:{parameter}")
                value = (
                    uuid_for_public("learner_memories", self.workspace_id, int(value))
                    if value is not None else None
                )
            elif name == "proposal_id":
                assignments.append(f"{column}=:{parameter}")
                value = UUID(str(value)) if value is not None else None
            elif name in {"first_observed_at", "last_observed_at"}:
                assignments.append(f"{column}=:{parameter}")
                value = timestamp(value)
            else:
                assignments.append(f"{column}=:{parameter}")
            parameters[parameter] = value
        assignments.append("updated_at=:updated_at")
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "UPDATE learning_signals SET " + ", ".join(assignments)
                    + " WHERE id=:id AND workspace_id=:workspace_id"
                ),
                parameters,
            )
            if result.rowcount != 1:
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
        clauses = ["true"]
        parameters: dict[str, object] = {}
        if status is not None:
            clauses.append("s.status=:status")
            parameters["status"] = status
        if topic is not None:
            clauses.append("lower(s.topic)=lower(:topic)")
            parameters["topic"] = topic
        if signal_types:
            clauses.append("s.signal_type = ANY(:signal_types)")
            parameters["signal_types"] = list(signal_types)
        return [_learning_signal(row) for row in self._rows(" AND ".join(clauses), parameters)]

    def link_memory(
        self,
        signal_ids: tuple[str, ...],
        memory_id: int,
        proposal_id: str | None = None,
    ) -> None:
        if not signal_ids:
            return
        memory_uuid = uuid_for_public("learner_memories", self.workspace_id, memory_id)
        if memory_uuid is None:
            raise KeyError(f"Memory ID {memory_id} does not exist.")
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    UPDATE learning_signals SET memory_id=:memory_id,
                        proposal_id=COALESCE(:proposal_id, proposal_id), updated_at=now()
                    WHERE workspace_id=:workspace_id AND id = ANY(:signal_ids)
                    """
                ),
                {
                    "memory_id": memory_uuid,
                    "proposal_id": UUID(proposal_id) if proposal_id else None,
                    "workspace_id": UUID(self.workspace_id),
                    "signal_ids": [UUID(value) for value in signal_ids],
                },
            )


class CockroachAdaptationEventRepository:
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
        record_id = new_record_id()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO adaptation_events (
                        id, workspace_id, workflow_type, request_id, memory_ids,
                        learning_signal_ids, applied_changes, reason, created_at
                    ) VALUES (
                        :id, :workspace_id, :workflow_type, :request_id,
                        CAST(:memory_ids AS JSONB), CAST(:signal_ids AS JSONB),
                        CAST(:changes AS JSONB), :reason, :created_at
                    )
                    """
                ),
                {
                    "id": record_id, "workspace_id": UUID(self.workspace_id),
                    "workflow_type": workflow_type, "request_id": request_id,
                    "memory_ids": json_text(memory_ids),
                    "signal_ids": json_text(learning_signal_ids),
                    "changes": json_text(applied_changes), "reason": reason.strip(),
                    "created_at": utc_now(),
                },
            )
        event = self.get(str(record_id))
        assert event is not None
        return event

    def get(self, event_id: str) -> AdaptationEvent | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM adaptation_events "
                    "WHERE id=:id AND workspace_id=:workspace_id"
                ),
                {"id": UUID(event_id), "workspace_id": UUID(self.workspace_id)},
            ).mappings().one_or_none()
        return _adaptation_event(row) if row is not None else None

    def list(
        self,
        workflow_type: str | None = None,
        limit: int | None = None,
    ) -> list[AdaptationEvent]:
        clauses = ["workspace_id=:workspace_id"]
        parameters: dict[str, object] = {"workspace_id": UUID(self.workspace_id)}
        if workflow_type is not None:
            clauses.append("workflow_type=:workflow_type")
            parameters["workflow_type"] = workflow_type
        suffix = ""
        if limit is not None:
            suffix = " LIMIT :limit"
            parameters["limit"] = int(limit)
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM adaptation_events WHERE " + " AND ".join(clauses)
                    + " ORDER BY created_at DESC, id DESC" + suffix
                ),
                parameters,
            ).mappings().all()
        return [_adaptation_event(row) for row in rows]


def _workflow_state(row: Any) -> WorkflowState:
    return WorkflowState(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        workflow_type=str(row["workflow_type"]), payload=dict(json_value(row["payload"])),
        status=str(row["status"]), created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]), expires_at=iso(row["expires_at"]),
        version=int(row["version"]),
        decision_metadata=(
            dict(json_value(row["decision_metadata"]))
            if row["decision_metadata"] is not None else None
        ),
    )


def _outbox_job(row: Any) -> VectorOutboxJob:
    return VectorOutboxJob(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        entity_type=str(row["entity_type"]), entity_id=str(row["entity_id"]),
        operation=str(row["operation"]), payload=dict(json_value(row["payload"])),
        status=str(row["status"]), attempts=int(row["attempts"]),
        created_at=iso(row["created_at"]), updated_at=iso(row["updated_at"]),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
    )


def _learning_signal(row: Any) -> LearningSignal:
    return LearningSignal(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        source_type=str(row["source_type"]), source_id=str(row["source_id"]),
        source_question_id=(str(row["source_question_id"]) if row["source_question_id"] is not None else None),
        topic=str(row["topic"]), signal_type=str(row["signal_type"]),
        statement=str(row["statement"]), evidence=tuple(json_value(row["evidence"])),
        confidence=float(row["confidence"]), importance=float(row["importance"]),
        occurrence_count=int(row["occurrence_count"]), payload=dict(json_value(row["payload"])),
        status=str(row["status"]), first_observed_at=iso(row["first_observed_at"]),
        last_observed_at=iso(row["last_observed_at"]), created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]),
        signal_key=str(row["signal_key"]) if row["signal_key"] is not None else None,
        memory_id=int(row["memory_public_id"]) if row["memory_public_id"] is not None else None,
        proposal_id=str(row["proposal_id"]) if row["proposal_id"] is not None else None,
    )


def _adaptation_event(row: Any) -> AdaptationEvent:
    return AdaptationEvent(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        workflow_type=str(row["workflow_type"]), request_id=str(row["request_id"]),
        memory_ids=tuple(int(value) for value in json_value(row["memory_ids"])),
        learning_signal_ids=tuple(str(value) for value in json_value(row["learning_signal_ids"])),
        applied_changes=dict(json_value(row["applied_changes"])),
        reason=str(row["reason"]), created_at=iso(row["created_at"]),
    )
