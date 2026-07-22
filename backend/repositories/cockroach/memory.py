from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.memory.database import StoredMemory
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import iso, new_public_identity, utc_now, uuid_for_public
from backend.repositories.interfaces import RepositoryConflictError


class CockroachLearnerMemoryRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def insert(self, **values) -> int:
        record_id, public_id = new_public_identity()
        now = utc_now()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO learner_memories (
                        id, workspace_id, public_id, memory_type, content,
                        confidence, importance, status, created_at, updated_at
                    ) VALUES (
                        :id, :workspace_id, :public_id, :memory_type, :content,
                        :confidence, :importance, :status, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": record_id, "workspace_id": UUID(self.workspace_id),
                    "public_id": public_id, "memory_type": values["memory_type"],
                    "content": values["content"].strip(),
                    "confidence": float(values.get("confidence", 1.0)),
                    "importance": float(values.get("importance", 0.5)),
                    "status": values.get("status", "active"),
                    "created_at": now, "updated_at": now,
                },
            )
        return public_id

    def get(self, memory_id: int) -> StoredMemory | None:
        rows = self._rows("public_id=:public_id", {"public_id": int(memory_id)})
        return _memory(rows[0]) if rows else None

    def get_many(self, memory_ids: list[int]) -> list[StoredMemory]:
        if not memory_ids:
            return []
        rows = self._rows("public_id = ANY(:ids)", {"ids": [int(value) for value in memory_ids]})
        by_id = {int(row["public_id"]): _memory(row) for row in rows}
        return [by_id[value] for value in memory_ids if value in by_id]

    def list(self, include_archived: bool = False) -> list[StoredMemory]:
        clause = "true" if include_archived else "status='active'"
        return [_memory(row) for row in self._rows(clause, {})]

    def update(self, **values) -> bool:
        memory_id = int(values.pop("memory_id"))
        allowed = {"memory_type", "content", "confidence", "importance", "status"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError("Unsupported memory fields: " + ", ".join(sorted(unknown)))
        if not values:
            return self.get(memory_id) is not None
        assignments = [f"{name}=:{name}" for name in values]
        parameters = dict(values)
        assignments.append("updated_at=now()")
        parameters.update(
            workspace_id=UUID(self.workspace_id), public_id=memory_id
        )
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "UPDATE learner_memories SET " + ", ".join(assignments)
                    + " WHERE workspace_id=:workspace_id AND public_id=:public_id"
                ), parameters,
            )
        return result.rowcount == 1

    def archive(self, memory_id: int) -> bool:
        return self.update(memory_id=memory_id, status="archived")

    def activate(self, memory_id: int) -> bool:
        return self.update(memory_id=memory_id, status="active")

    def delete(self, memory_id: int) -> bool:
        try:
            with connection_scope() as connection:
                result = connection.execute(
                    text(
                        "DELETE FROM learner_memories "
                        "WHERE workspace_id=:workspace_id AND public_id=:public_id"
                    ),
                    {"workspace_id": UUID(self.workspace_id), "public_id": int(memory_id)},
                )
        except IntegrityError as error:
            raise RepositoryConflictError("Memory has retained relationships.") from error
        return result.rowcount == 1

    def insert_relationships(self, **values) -> None:
        target_public_id = int(values["target_memory_id"])
        target_uuid = uuid_for_public("learner_memories", self.workspace_id, target_public_id)
        if target_uuid is None:
            raise KeyError(f"Memory ID {target_public_id} does not exist.")
        relationship_type = values.get("relationship_type", "consolidated_into")
        with connection_scope() as connection:
            for source_public_id in values["source_memory_ids"]:
                source_uuid = uuid_for_public(
                    "learner_memories", self.workspace_id, int(source_public_id)
                )
                if source_uuid is None:
                    raise KeyError(f"Memory ID {source_public_id} does not exist.")
                record_id, public_id = new_public_identity()
                connection.execute(
                    text(
                        """
                        INSERT INTO memory_relationships (
                            id, workspace_id, public_id, source_memory_id,
                            target_memory_id, relationship_type, created_at
                        ) VALUES (
                            :id,:workspace_id,:public_id,:source_id,
                            :target_id,:relationship_type,:created_at
                        ) ON CONFLICT (source_memory_id,target_memory_id,relationship_type)
                        DO NOTHING
                        """
                    ),
                    {
                        "id": record_id, "workspace_id": UUID(self.workspace_id),
                        "public_id": public_id, "source_id": source_uuid,
                        "target_id": target_uuid, "relationship_type": relationship_type,
                        "created_at": utc_now(),
                    },
                )

    def delete_relationships_for_target(self, memory_id: int) -> int:
        target_uuid = uuid_for_public("learner_memories", self.workspace_id, memory_id)
        if target_uuid is None:
            return 0
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "DELETE FROM memory_relationships "
                    "WHERE workspace_id=:workspace_id AND target_memory_id=:target_id"
                ),
                {"workspace_id": UUID(self.workspace_id), "target_id": target_uuid},
            )
        return int(result.rowcount)

    def _rows(self, clause: str, parameters: dict[str, object]):
        with connection_scope() as connection:
            return connection.execute(
                text(
                    "SELECT * FROM learner_memories "
                    "WHERE workspace_id=:workspace_id AND " + clause
                    + " ORDER BY updated_at DESC, public_id DESC"
                ), {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().all()


def _memory(row) -> StoredMemory:
    return StoredMemory(
        id=int(row["public_id"]), memory_type=str(row["memory_type"]),
        content=str(row["content"]), confidence=float(row["confidence"]),
        importance=float(row["importance"]), status=str(row["status"]),
        created_at=iso(row["created_at"]), updated_at=iso(row["updated_at"]),
    )
