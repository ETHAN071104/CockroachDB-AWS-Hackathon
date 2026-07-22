from __future__ import annotations

from typing import Any

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.rag.database import get_connection


class SQLiteDashboardRepository:
    """SQLite read-model adapter for the deterministic dashboard."""

    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def build(self, recent_limit: int) -> dict[str, Any]:
        with get_connection() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            def count(table: str, extra: str = "", values: tuple[Any, ...] = ()) -> int:
                if table not in tables:
                    return 0
                row = connection.execute(
                    f"SELECT COUNT(*) AS total FROM {table} "
                    f"WHERE workspace_id = ? {extra}",
                    (self.workspace_id, *values),
                ).fetchone()
                return int(row["total"]) if row is not None else 0

            unsorted = count("documents")
            if {"documents", "notebook_documents"}.issubset(tables):
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total FROM documents AS d
                    LEFT JOIN notebook_documents AS nd
                        ON nd.document_id = d.id AND nd.workspace_id = ?
                    WHERE d.workspace_id = ? AND nd.document_id IS NULL
                    """,
                    (self.workspace_id, self.workspace_id),
                ).fetchone()
                unsorted = int(row["total"]) if row is not None else 0

            session_rows: list[Any] = []
            if "study_sessions" in tables:
                join = (
                    "LEFT JOIN study_interactions AS i ON i.session_id = s.id "
                    "AND i.workspace_id = s.workspace_id"
                    if "study_interactions" in tables
                    else ""
                )
                session_rows = connection.execute(
                    f"""
                    SELECT s.id, s.status, s.started_at, s.ended_at,
                        {('COUNT(i.id)' if join else '0')} AS interaction_count
                    FROM study_sessions AS s {join}
                    WHERE s.workspace_id = ?
                    GROUP BY s.id
                    ORDER BY s.started_at DESC, s.id DESC LIMIT ?
                    """,
                    (self.workspace_id, recent_limit),
                ).fetchall()

            outcomes = {key: 0 for key in ("unrated", "understood", "partial", "confused")}
            if "study_interactions" in tables:
                rows = connection.execute(
                    """
                    SELECT outcome, COUNT(*) AS total FROM study_interactions
                    WHERE workspace_id = ? GROUP BY outcome
                    """,
                    (self.workspace_id,),
                ).fetchall()
                for row in rows:
                    if str(row["outcome"]) in outcomes:
                        outcomes[str(row["outcome"])] = int(row["total"])

            quiz_stats = {
                "total": 0,
                "completed": 0,
                "aborted": 0,
                "average_score_percentage": None,
                "average_accuracy_percentage": None,
            }
            quiz_rows: list[Any] = []
            if "quiz_attempts" in tables:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                        SUM(CASE WHEN status = 'aborted' THEN 1 ELSE 0 END) AS aborted,
                        AVG(score_percentage) AS average_score,
                        AVG(accuracy_percentage) AS average_accuracy
                    FROM quiz_attempts WHERE workspace_id = ?
                    """,
                    (self.workspace_id,),
                ).fetchone()
                quiz_stats = {
                    "total": int(row["total"]),
                    "completed": int(row["completed"] or 0),
                    "aborted": int(row["aborted"] or 0),
                    "average_score_percentage": (
                        float(row["average_score"])
                        if row["average_score"] is not None
                        else None
                    ),
                    "average_accuracy_percentage": (
                        float(row["average_accuracy"])
                        if row["average_accuracy"] is not None
                        else None
                    ),
                }
                quiz_rows = connection.execute(
                    """
                    SELECT id, quiz_topic, status, score_percentage,
                        accuracy_percentage, created_at
                    FROM quiz_attempts WHERE workspace_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (self.workspace_id, recent_limit),
                ).fetchall()

            counts = {
                "documents": count("documents"),
                "notebooks": count("notebooks"),
                "unsorted_documents": unsorted,
                "active_memories": count(
                    "memories", "AND status = ?", ("active",)
                ),
                "archived_memories": count(
                    "memories", "AND status = ?", ("archived",)
                ),
                "study_sessions": count("study_sessions"),
                "completed_sessions": count(
                    "study_sessions", "AND status = ?", ("completed",)
                ),
                "interactions": count("study_interactions"),
                "quiz_attempts": count("quiz_attempts"),
                "topics": count("topics"),
            }

        sessions = [dict(row) for row in session_rows]
        return {
            "counts": counts,
            "active_session": next(
                (item for item in sessions if item["status"] == "active"),
                None,
            ),
            "recent_sessions": sessions,
            "outcomes": outcomes,
            "quiz": quiz_stats,
            "recent_quizzes": [dict(row) for row in quiz_rows],
        }
