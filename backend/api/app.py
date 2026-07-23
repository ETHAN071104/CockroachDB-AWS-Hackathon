from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.errors import install_error_handlers
from backend.api.health import API_VERSION, build_health_payload
from backend.api.routes.chat import router as chat_router
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.intelligence import router as intelligence_router
from backend.api.routes.guest_sessions import router as guest_session_router
from backend.api.routes.memory import router as memory_router
from backend.api.routes.notebooks_documents import router as library_router
from backend.api.routes.quiz import router as quiz_router
from backend.api.routes.reports_study import router as reports_router
from backend.api.routes.system import router as system_router
from backend.api.schemas import HealthResponse
from backend.api.guest_auth import bind_protected_workspace
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
from backend.rag import config


def initialize_storage() -> dict[str, Any]:
    if config.PERSISTENCE_BACKEND == "cockroach":
        initialize_application_foundation()
        from backend.infrastructure.cockroach.health import cockroach_health

        status = cockroach_health()
        return {
            "document_vector_status": {"status": status["status"], "collection_present": True},
            "memory_vector_status": {"status": status["status"], "collection_present": True},
        }
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
    *,
    allow_legacy_default_workspace: bool | None = None,
) -> FastAPI:
    if dependencies is not None:
        configure_application_dependencies(dependencies)
    application = FastAPI(
        title="Local Study Companion API",
        version=API_VERSION,
        lifespan=lifespan,
    )
    application.state.dependencies = get_application_dependencies()
    application.state.allow_legacy_default_workspace = (
        config.ALLOW_LEGACY_DEFAULT_WORKSPACE
        if allow_legacy_default_workspace is None
        else bool(allow_legacy_default_workspace)
    )
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    if (
        config.FRONTEND_ORIGIN
        and config.FRONTEND_ORIGIN != "*"
        and config.FRONTEND_ORIGIN not in allowed_origins
    ):
        allowed_origins.append(config.FRONTEND_ORIGIN)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "Authorization",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-ID"],
    )
    install_error_handlers(application)
    application.include_router(guest_session_router)
    protected_dependencies = [Depends(bind_protected_workspace)]
    application.include_router(
        dashboard_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        library_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        intelligence_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        quiz_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        reports_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        chat_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        memory_router,
        dependencies=protected_dependencies,
    )
    application.include_router(
        system_router,
        dependencies=protected_dependencies,
    )

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
