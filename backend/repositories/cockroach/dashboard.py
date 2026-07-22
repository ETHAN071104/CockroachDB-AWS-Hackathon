from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import iso


class CockroachDashboardRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def build(self, recent_limit: int) -> dict[str, Any]:
        workspace = UUID(self.workspace_id)
        with connection_scope() as connection:
            counts_row = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM documents WHERE workspace_id=:workspace_id) AS documents,
                      (SELECT count(*) FROM notebooks WHERE workspace_id=:workspace_id) AS notebooks,
                      (SELECT count(*) FROM documents d LEFT JOIN notebook_documents nd
                         ON nd.document_id=d.id AND nd.workspace_id=d.workspace_id
                         WHERE d.workspace_id=:workspace_id AND nd.document_id IS NULL) AS unsorted_documents,
                      (SELECT count(*) FROM learner_memories WHERE workspace_id=:workspace_id AND status='active') AS active_memories,
                      (SELECT count(*) FROM learner_memories WHERE workspace_id=:workspace_id AND status='archived') AS archived_memories,
                      (SELECT count(*) FROM study_sessions WHERE workspace_id=:workspace_id) AS study_sessions,
                      (SELECT count(*) FROM study_sessions WHERE workspace_id=:workspace_id AND status='completed') AS completed_sessions,
                      (SELECT count(*) FROM study_interactions WHERE workspace_id=:workspace_id) AS interactions,
                      (SELECT count(*) FROM quiz_attempts WHERE workspace_id=:workspace_id) AS quiz_attempts,
                      (SELECT count(*) FROM topics WHERE workspace_id=:workspace_id) AS topics
                    """
                ),
                {"workspace_id": workspace},
            ).mappings().one()
            session_rows = connection.execute(
                text(
                    """
                    SELECT s.public_id AS id,s.status,s.started_at,s.ended_at,
                           count(i.id) AS interaction_count
                    FROM study_sessions s LEFT JOIN study_interactions i
                      ON i.session_id=s.id AND i.workspace_id=s.workspace_id
                    WHERE s.workspace_id=:workspace_id
                    GROUP BY s.id,s.public_id,s.status,s.started_at,s.ended_at
                    ORDER BY s.started_at DESC,s.public_id DESC LIMIT :limit
                    """
                ),
                {"workspace_id": workspace, "limit": int(recent_limit)},
            ).mappings().all()
            outcome_rows = connection.execute(
                text(
                    "SELECT outcome,count(*) AS total FROM study_interactions "
                    "WHERE workspace_id=:workspace_id GROUP BY outcome"
                ),
                {"workspace_id": workspace},
            ).mappings().all()
            quiz_stats_row = connection.execute(
                text(
                    """
                    SELECT count(*) AS total,
                      count(*) FILTER (WHERE status='completed') AS completed,
                      count(*) FILTER (WHERE status='aborted') AS aborted,
                      avg(score_percentage) AS average_score,
                      avg(accuracy_percentage) AS average_accuracy
                    FROM quiz_attempts WHERE workspace_id=:workspace_id
                    """
                ),
                {"workspace_id": workspace},
            ).mappings().one()
            quiz_rows = connection.execute(
                text(
                    """
                    SELECT public_id AS id,quiz_topic,status,score_percentage,
                           accuracy_percentage,created_at
                    FROM quiz_attempts WHERE workspace_id=:workspace_id
                    ORDER BY created_at DESC,public_id DESC LIMIT :limit
                    """
                ),
                {"workspace_id": workspace, "limit": int(recent_limit)},
            ).mappings().all()

        counts = {key: int(value) for key, value in counts_row.items()}
        sessions = [
            {
                "id": int(row["id"]), "status": str(row["status"]),
                "started_at": iso(row["started_at"]),
                "ended_at": iso(row["ended_at"]) if row["ended_at"] else None,
                "interaction_count": int(row["interaction_count"]),
            }
            for row in session_rows
        ]
        outcomes = {key: 0 for key in ("unrated", "understood", "partial", "confused")}
        for row in outcome_rows:
            outcomes[str(row["outcome"])] = int(row["total"])
        recent_quizzes = [
            {
                "id": int(row["id"]), "quiz_topic": str(row["quiz_topic"]),
                "status": str(row["status"]),
                "score_percentage": float(row["score_percentage"]),
                "accuracy_percentage": (
                    float(row["accuracy_percentage"])
                    if row["accuracy_percentage"] is not None else None
                ),
                "created_at": iso(row["created_at"]),
            }
            for row in quiz_rows
        ]
        return {
            "counts": counts,
            "active_session": next((item for item in sessions if item["status"] == "active"), None),
            "recent_sessions": sessions,
            "outcomes": outcomes,
            "quiz": {
                "total": int(quiz_stats_row["total"]),
                "completed": int(quiz_stats_row["completed"]),
                "aborted": int(quiz_stats_row["aborted"]),
                "average_score_percentage": (
                    float(quiz_stats_row["average_score"])
                    if quiz_stats_row["average_score"] is not None else None
                ),
                "average_accuracy_percentage": (
                    float(quiz_stats_row["average_accuracy"])
                    if quiz_stats_row["average_accuracy"] is not None else None
                ),
            },
            "recent_quizzes": recent_quizzes,
        }
