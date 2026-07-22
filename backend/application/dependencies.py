from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.repositories.chroma import (
    ChromaDocumentVectorRepository,
    ChromaMemoryVectorRepository,
)
from backend.repositories.interfaces import (
    AdaptationEventRepository,
    BlobStorage,
    DashboardRepository,
    DocumentRepository,
    DocumentVectorRepository,
    IntelligenceRepository,
    LearnerMemoryRepository,
    LearningSignalRepository,
    MemoryVectorRepository,
    NotebookRepository,
    QuizRepository,
    StudySessionRepository,
    UnitOfWork,
    VectorOutboxRepository,
    WorkflowStateRepository,
    WorkspaceRepository,
)
from backend.repositories.sqlite import (
    SQLiteAdaptationEventRepository,
    SQLiteDocumentRepository,
    SQLiteIntelligenceRepository,
    SQLiteLearnerMemoryRepository,
    SQLiteLearningSignalRepository,
    SQLiteNotebookRepository,
    SQLiteQuizRepository,
    SQLiteStudySessionRepository,
    SQLiteUnitOfWork,
    SQLiteVectorOutboxRepository,
    SQLiteWorkflowStateRepository,
    SQLiteWorkspaceRepository,
    SQLiteBlobStorage,
    initialize_foundation_schema,
)
from backend.repositories.sqlite.dashboard import SQLiteDashboardRepository


@dataclass(frozen=True)
class ApplicationDependencies:
    workspace_id: str
    workspaces: WorkspaceRepository
    notebooks: NotebookRepository
    documents: DocumentRepository
    blobs: BlobStorage
    intelligence: IntelligenceRepository
    dashboard: DashboardRepository
    study_sessions: StudySessionRepository
    quizzes: QuizRepository
    memories: LearnerMemoryRepository
    learning_signals: LearningSignalRepository
    adaptation_events: AdaptationEventRepository
    workflows: WorkflowStateRepository
    document_vectors: DocumentVectorRepository
    memory_vectors: MemoryVectorRepository
    vector_outbox: VectorOutboxRepository
    unit_of_work: Callable[[], UnitOfWork]


def _sqlite_unit_of_work() -> SQLiteUnitOfWork:
    # Resolve the legacy path dynamically so existing isolated test fixtures
    # that patch rag.database.DATABASE_PATH remain valid.
    from backend.rag import database

    return SQLiteUnitOfWork(
        database_path=lambda: database.DATABASE_PATH,
        ensure_parent=database.ensure_directories,
    )


def build_application_dependencies(
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> ApplicationDependencies:
    from backend.rag import config

    config.validate_persistence_config()
    if config.PERSISTENCE_BACKEND == "cockroach":
        return _build_cockroach_dependencies(workspace_id)

    from backend.memory import vector_store as memory_vector_store
    from backend.rag import vector_store as document_vector_store

    return ApplicationDependencies(
        workspace_id=workspace_id,
        workspaces=SQLiteWorkspaceRepository(),
        notebooks=SQLiteNotebookRepository(workspace_id),
        documents=SQLiteDocumentRepository(workspace_id),
        blobs=SQLiteBlobStorage(workspace_id),
        intelligence=SQLiteIntelligenceRepository(workspace_id),
        dashboard=SQLiteDashboardRepository(workspace_id),
        study_sessions=SQLiteStudySessionRepository(workspace_id),
        quizzes=SQLiteQuizRepository(workspace_id),
        memories=SQLiteLearnerMemoryRepository(workspace_id),
        learning_signals=SQLiteLearningSignalRepository(workspace_id),
        adaptation_events=SQLiteAdaptationEventRepository(workspace_id),
        workflows=SQLiteWorkflowStateRepository(workspace_id),
        document_vectors=ChromaDocumentVectorRepository(
            document_vector_store.get_vector_store
        ),
        memory_vectors=ChromaMemoryVectorRepository(
            memory_vector_store.get_memory_vector_store
        ),
        vector_outbox=SQLiteVectorOutboxRepository(workspace_id),
        unit_of_work=_sqlite_unit_of_work,
    )


def _build_cockroach_dependencies(workspace_id: str) -> ApplicationDependencies:
    from backend.repositories.cockroach import (
        CockroachAdaptationEventRepository,
        CockroachBlobStorage,
        CockroachDashboardRepository,
        CockroachDocumentRepository,
        CockroachDocumentVectorRepository,
        CockroachIntelligenceRepository,
        CockroachLearnerMemoryRepository,
        CockroachLearningSignalRepository,
        CockroachMemoryVectorRepository,
        CockroachNotebookRepository,
        CockroachQuizRepository,
        CockroachStudySessionRepository,
        CockroachUnitOfWork,
        CockroachVectorOutboxRepository,
        CockroachWorkflowStateRepository,
        CockroachWorkspaceRepository,
    )

    return ApplicationDependencies(
        workspace_id=workspace_id,
        workspaces=CockroachWorkspaceRepository(),
        notebooks=CockroachNotebookRepository(workspace_id),
        documents=CockroachDocumentRepository(workspace_id),
        blobs=CockroachBlobStorage(workspace_id),
        intelligence=CockroachIntelligenceRepository(workspace_id),
        dashboard=CockroachDashboardRepository(workspace_id),
        study_sessions=CockroachStudySessionRepository(workspace_id),
        quizzes=CockroachQuizRepository(workspace_id),
        memories=CockroachLearnerMemoryRepository(workspace_id),
        learning_signals=CockroachLearningSignalRepository(workspace_id),
        adaptation_events=CockroachAdaptationEventRepository(workspace_id),
        workflows=CockroachWorkflowStateRepository(workspace_id),
        document_vectors=CockroachDocumentVectorRepository(workspace_id),
        memory_vectors=CockroachMemoryVectorRepository(workspace_id),
        vector_outbox=CockroachVectorOutboxRepository(workspace_id),
        unit_of_work=CockroachUnitOfWork,
    )


_DEFAULT_DEPENDENCIES: ApplicationDependencies | None = None


def get_application_dependencies() -> ApplicationDependencies:
    global _DEFAULT_DEPENDENCIES
    if _DEFAULT_DEPENDENCIES is None:
        _DEFAULT_DEPENDENCIES = build_application_dependencies()
    return _DEFAULT_DEPENDENCIES


def configure_application_dependencies(
    dependencies: ApplicationDependencies | None,
) -> ApplicationDependencies:
    global _DEFAULT_DEPENDENCIES
    _DEFAULT_DEPENDENCIES = dependencies or build_application_dependencies()
    return _DEFAULT_DEPENDENCIES


def initialize_application_foundation() -> ApplicationDependencies:
    from backend.rag import config

    dependencies = get_application_dependencies()
    if config.PERSISTENCE_BACKEND == "sqlite":
        initialize_foundation_schema()
    dependencies.workspaces.ensure_default()
    dependencies.workflows.cleanup_expired()
    return dependencies
