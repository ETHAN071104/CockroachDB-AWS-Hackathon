"""Create the complete Agentbook CockroachDB relational and vector schema."""

from alembic import op


revision = "0001_agentbook_cockroach_schema"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    """
    CREATE TABLE workspaces (
        id UUID PRIMARY KEY,
        name STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE notebooks (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        name STRING NOT NULL,
        normalized_name STRING NOT NULL,
        description STRING NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (workspace_id, normalized_name)
    )
    """,
    """
    CREATE TABLE documents (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        filename STRING NOT NULL,
        mime_type STRING NOT NULL,
        file_hash STRING NOT NULL,
        chunk_count INT8 NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (workspace_id, file_hash)
    )
    """,
    """
    CREATE TABLE document_blobs (
        document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        data BYTES NOT NULL,
        size_bytes INT8 NOT NULL CHECK (size_bytes >= 0),
        content_hash STRING NOT NULL,
        filename STRING NOT NULL,
        mime_type STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE notebook_documents (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        notebook_id UUID NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        assigned_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, document_id)
    )
    """,
    """
    CREATE TABLE cached_intelligence (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        legacy_sqlite_id INT8,
        kind STRING NOT NULL,
        scope_kind STRING NOT NULL CHECK (scope_kind IN ('global','notebook','documents','topic')),
        scope_key STRING NOT NULL,
        result JSONB NOT NULL,
        source_snapshot JSONB NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        fingerprint STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, kind, scope_kind, scope_key),
        UNIQUE (workspace_id, legacy_sqlite_id)
    )
    """,
    """
    CREATE TABLE topics (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name STRING NOT NULL,
        description STRING NOT NULL DEFAULT '',
        extraction_scope_kind STRING NOT NULL CHECK (extraction_scope_kind IN ('global','notebook','documents')),
        extraction_scope_key STRING NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        source_fingerprint STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE document_chunks (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index INT8 NOT NULL CHECK (chunk_index >= 0),
        content STRING NOT NULL,
        page_number INT8,
        slide_number INT8,
        filename_snapshot STRING NOT NULL,
        mime_type STRING NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
        embedding VECTOR(384),
        embedding_model STRING NOT NULL,
        embedding_version STRING NOT NULL,
        content_hash STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, document_id, chunk_index)
    )
    """,
    """
    CREATE TABLE topic_sources (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
        document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
        document_chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
        chunk_index INT8 NOT NULL CHECK (chunk_index >= 0),
        source_index INT8 NOT NULL CHECK (source_index > 0),
        filename STRING NOT NULL,
        mime_type STRING NOT NULL,
        page_number INT8,
        slide_number INT8,
        excerpt STRING NOT NULL DEFAULT '',
        distance FLOAT8,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (topic_id, source_index)
    )
    """,
    """
    CREATE TABLE study_sessions (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        status STRING NOT NULL CHECK (status IN ('active','completed')),
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        version INT8 NOT NULL DEFAULT 1 CHECK (version > 0),
        CHECK ((status='active' AND ended_at IS NULL) OR (status='completed' AND ended_at IS NOT NULL)),
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id)
    )
    """,
    """
    CREATE TABLE study_interactions (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        session_id UUID NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
        question STRING NOT NULL,
        answer STRING NOT NULL,
        outcome STRING NOT NULL DEFAULT 'unrated' CHECK (outcome IN ('unrated','understood','partial','confused')),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id)
    )
    """,
    """
    CREATE TABLE study_interaction_sources (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        interaction_id UUID NOT NULL REFERENCES study_interactions(id) ON DELETE CASCADE,
        document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
        document_chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
        source_index INT8 NOT NULL CHECK (source_index > 0),
        filename STRING NOT NULL,
        page_number INT8,
        chunk_index INT8,
        distance FLOAT8 NOT NULL CHECK (distance >= 0),
        notebook_public_id INT8,
        mime_type STRING,
        slide_number INT8,
        excerpt STRING,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (interaction_id, source_index)
    )
    """,
    """
    CREATE TABLE quiz_attempts (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        requested_topic STRING NOT NULL,
        quiz_topic STRING NOT NULL,
        status STRING NOT NULL CHECK (status IN ('completed','aborted')),
        total_questions INT8 NOT NULL CHECK (total_questions > 0),
        presented_questions INT8 NOT NULL CHECK (presented_questions >= 0),
        answered_questions INT8 NOT NULL CHECK (answered_questions >= 0),
        skipped_questions INT8 NOT NULL CHECK (skipped_questions >= 0),
        correct_answers INT8 NOT NULL CHECK (correct_answers >= 0),
        score_percentage FLOAT8 NOT NULL CHECK (score_percentage BETWEEN 0 AND 100),
        accuracy_percentage FLOAT8 CHECK (accuracy_percentage IS NULL OR accuracy_percentage BETWEEN 0 AND 100),
        confidence FLOAT8 NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id)
    )
    """,
    """
    CREATE TABLE quiz_question_attempts (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        quiz_attempt_id UUID NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
        question_number INT8 NOT NULL CHECK (question_number > 0),
        question STRING NOT NULL,
        options JSONB NOT NULL,
        presented BOOL NOT NULL,
        selected_option INT8 CHECK (selected_option IS NULL OR selected_option BETWEEN 1 AND 4),
        correct_option INT8 NOT NULL CHECK (correct_option BETWEEN 1 AND 4),
        is_correct BOOL NOT NULL,
        skipped BOOL NOT NULL,
        explanation STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (quiz_attempt_id, question_number)
    )
    """,
    """
    CREATE TABLE quiz_question_sources (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        question_attempt_id UUID NOT NULL REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
        document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
        document_chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
        source_index INT8 NOT NULL CHECK (source_index > 0),
        filename STRING NOT NULL,
        page_number INT8,
        chunk_index INT8,
        distance FLOAT8,
        notebook_public_id INT8,
        mime_type STRING,
        slide_number INT8,
        excerpt STRING,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (question_attempt_id, source_index)
    )
    """,
    """
    CREATE TABLE learner_memories (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        memory_type STRING NOT NULL CHECK (memory_type IN ('profile','learning_state','episodic','procedural')),
        content STRING NOT NULL,
        confidence FLOAT8 NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        importance FLOAT8 NOT NULL CHECK (importance BETWEEN 0 AND 1),
        status STRING NOT NULL CHECK (status IN ('active','archived')),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id)
    )
    """,
    """
    CREATE TABLE memory_relationships (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        public_id INT8 NOT NULL,
        legacy_sqlite_id INT8,
        source_memory_id UUID NOT NULL REFERENCES learner_memories(id) ON DELETE RESTRICT,
        target_memory_id UUID NOT NULL REFERENCES learner_memories(id) ON DELETE RESTRICT,
        relationship_type STRING NOT NULL CHECK (relationship_type IN ('consolidated_into')),
        created_at TIMESTAMPTZ NOT NULL,
        CHECK (source_memory_id != target_memory_id),
        UNIQUE (workspace_id, public_id),
        UNIQUE (workspace_id, legacy_sqlite_id),
        UNIQUE (source_memory_id, target_memory_id, relationship_type)
    )
    """,
    """
    CREATE TABLE learner_memory_embeddings (
        memory_id UUID PRIMARY KEY REFERENCES learner_memories(id) ON DELETE CASCADE,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        embedding VECTOR(384) NOT NULL,
        embedding_model STRING NOT NULL,
        embedding_version STRING NOT NULL,
        content_hash STRING NOT NULL,
        last_retrieved_at TIMESTAMPTZ,
        retrieval_count INT8 NOT NULL DEFAULT 0 CHECK (retrieval_count >= 0),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE workflow_states (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        workflow_type STRING NOT NULL,
        payload JSONB NOT NULL,
        status STRING NOT NULL CHECK (status IN ('pending','accepted','rejected','completed','expired','failed')),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        version INT8 NOT NULL DEFAULT 1 CHECK (version > 0),
        decision_metadata JSONB
    )
    """,
    """
    CREATE TABLE learning_signals (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        source_type STRING NOT NULL,
        source_id STRING NOT NULL,
        source_question_id STRING,
        topic STRING NOT NULL DEFAULT '',
        signal_type STRING NOT NULL,
        statement STRING NOT NULL DEFAULT '',
        evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
        confidence FLOAT8 NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        importance FLOAT8 NOT NULL CHECK (importance BETWEEN 0 AND 1),
        occurrence_count INT8 NOT NULL CHECK (occurrence_count > 0),
        payload JSONB NOT NULL,
        status STRING NOT NULL,
        first_observed_at TIMESTAMPTZ NOT NULL,
        last_observed_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        signal_key STRING,
        memory_id UUID REFERENCES learner_memories(id) ON DELETE SET NULL,
        proposal_id UUID,
        UNIQUE (workspace_id, signal_key)
    )
    """,
    """
    CREATE TABLE adaptation_events (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        workflow_type STRING NOT NULL,
        request_id STRING NOT NULL,
        memory_ids JSONB NOT NULL,
        learning_signal_ids JSONB NOT NULL,
        applied_changes JSONB NOT NULL,
        reason STRING NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE embedding_jobs (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        entity_type STRING NOT NULL CHECK (entity_type IN ('document','memory')),
        entity_id STRING NOT NULL,
        operation STRING NOT NULL CHECK (operation IN ('upsert','delete')),
        payload JSONB NOT NULL,
        status STRING NOT NULL CHECK (status IN ('pending','processing','completed','failed')),
        attempts INT8 NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        last_error STRING,
        idempotency_key STRING NOT NULL,
        claimed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (workspace_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE migration_runs (
        id UUID PRIMARY KEY,
        source_fingerprint STRING NOT NULL,
        status STRING NOT NULL CHECK (status IN ('planned','running','completed','failed')),
        manifest JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE migration_items (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
        source_table STRING NOT NULL,
        source_identity STRING NOT NULL,
        target_id UUID NOT NULL,
        checksum STRING NOT NULL,
        status STRING NOT NULL CHECK (status IN ('planned','migrated','verified','failed')),
        error_code STRING,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, source_table, source_identity)
    )
    """,
)


INDEXES = (
    "CREATE INDEX idx_notebooks_workspace ON notebooks(workspace_id, normalized_name)",
    "CREATE INDEX idx_documents_workspace ON documents(workspace_id, created_at DESC)",
    "CREATE INDEX idx_notebook_documents_notebook ON notebook_documents(workspace_id, notebook_id)",
    "CREATE INDEX idx_cached_intelligence_scope ON cached_intelligence(workspace_id, scope_kind, scope_key)",
    "CREATE INDEX idx_topics_scope ON topics(workspace_id, extraction_scope_kind, extraction_scope_key)",
    "CREATE INDEX idx_document_chunks_document ON document_chunks(workspace_id, document_id, chunk_index)",
    "CREATE INDEX idx_topic_sources_document ON topic_sources(workspace_id, document_id)",
    "CREATE UNIQUE INDEX idx_study_sessions_one_active ON study_sessions(workspace_id) WHERE status = 'active'",
    "CREATE INDEX idx_study_interactions_session ON study_interactions(workspace_id, session_id, created_at)",
    "CREATE INDEX idx_quiz_attempts_workspace ON quiz_attempts(workspace_id, created_at DESC)",
    "CREATE INDEX idx_quiz_questions_attempt ON quiz_question_attempts(workspace_id, quiz_attempt_id, question_number)",
    "CREATE INDEX idx_memories_status ON learner_memories(workspace_id, status, updated_at DESC)",
    "CREATE INDEX idx_workflow_states_lookup ON workflow_states(workspace_id, workflow_type, status, expires_at)",
    "CREATE INDEX idx_learning_signals_lookup ON learning_signals(workspace_id, status, topic, created_at DESC)",
    "CREATE INDEX idx_adaptation_events_lookup ON adaptation_events(workspace_id, workflow_type, created_at DESC)",
    "CREATE INDEX idx_embedding_jobs_retry ON embedding_jobs(workspace_id, status, created_at)",
)


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "migration_items",
        "migration_runs",
        "embedding_jobs",
        "adaptation_events",
        "learning_signals",
        "workflow_states",
        "learner_memory_embeddings",
        "memory_relationships",
        "learner_memories",
        "quiz_question_sources",
        "quiz_question_attempts",
        "quiz_attempts",
        "study_interaction_sources",
        "study_interactions",
        "study_sessions",
        "topic_sources",
        "document_chunks",
        "topics",
        "cached_intelligence",
        "notebook_documents",
        "document_blobs",
        "documents",
        "notebooks",
        "workspaces",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
