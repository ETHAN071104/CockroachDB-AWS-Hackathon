from __future__ import annotations

from typing import Any

from backend.rag import config
from backend.rag.config import LLM_PROVIDER
from backend.rag.database import get_connection

API_VERSION = "0.8.0"


def check_database() -> dict[str, str]:
    if config.PERSISTENCE_BACKEND == "cockroach":
        from backend.infrastructure.cockroach.health import cockroach_health

        return {"status": str(cockroach_health()["status"])}
    with get_connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok"}


def build_health_payload(
    document_vector_status: dict[str, Any],
    memory_vector_status: dict[str, Any],
) -> dict[str, Any]:
    database_status = check_database()
    statuses = (
        database_status["status"],
        document_vector_status["status"],
        memory_vector_status["status"],
    )
    return {
        "status": "ok" if all(value == "ok" for value in statuses) else "degraded",
        "version": API_VERSION,
        "database": database_status,
        "documents_vector_store": document_vector_status,
        "memory_vector_store": memory_vector_status,
        "llm_provider": LLM_PROVIDER,
        "persistence_backend": config.PERSISTENCE_BACKEND,
        "guest_sessions_configured": config.guest_session_configured(),
    }
