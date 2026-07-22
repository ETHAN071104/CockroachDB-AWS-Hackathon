from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.errors import install_error_handlers
from backend.api.health import API_VERSION, build_health_payload
from backend.api.routes.chat import router as chat_router
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.intelligence import router as intelligence_router
from backend.api.routes.memory import router as memory_router
from backend.api.routes.notebooks_documents import router as library_router
from backend.api.routes.quiz import router as quiz_router
from backend.api.routes.reports_study import router as reports_router
from backend.api.routes.system import router as system_router
from backend.api.schemas import HealthResponse
from backend.application.dependencies import (
    ApplicationDependencies,
    configure_application_dependencies,
    get_application_dependencies,
    initialize_application_foundation,
)
from backend.memory.database import initialize_memory_database
from backend.memory.vector_store import probe_memory_vector_store
from backend.rag.database import initialize_database
from backend.rag.vector_store import probe_vector_store
from backend.study.database import initialize_study_database


def initialize_storage() -> dict[str, Any]:
    initialize_database()
    initialize_memory_database()
    initialize_study_database()
    initialize_application_foundation()
    return {
        "document_vector_status": probe_vector_store(),
        "memory_vector_status": probe_memory_vector_store(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    storage_status = initialize_storage()
    app.state.document_vector_status = storage_status[
        "document_vector_status"
    ]
    app.state.memory_vector_status = storage_status[
        "memory_vector_status"
    ]
    yield


def create_app(
    dependencies: ApplicationDependencies | None = None,
) -> FastAPI:
    if dependencies is not None:
        configure_application_dependencies(dependencies)
    application = FastAPI(
        title="Local Study Companion API",
        version=API_VERSION,
        lifespan=lifespan,
    )
    application.state.dependencies = get_application_dependencies()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )
    install_error_handlers(application)
    application.include_router(dashboard_router)
    application.include_router(library_router)
    application.include_router(intelligence_router)
    application.include_router(quiz_router)
    application.include_router(reports_router)
    application.include_router(chat_router)
    application.include_router(memory_router)
    application.include_router(system_router)

    @application.get(
        "/api/health",
        response_model=HealthResponse,
        response_model_exclude_none=True,
        tags=["system"],
    )
    def health(request: Request) -> HealthResponse:
        return HealthResponse.model_validate(
            build_health_payload(
                request.app.state.document_vector_status,
                request.app.state.memory_vector_status,
            )
        )

    return application


app = create_app()
