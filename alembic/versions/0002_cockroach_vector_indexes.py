"""Create workspace-prefixed cosine vector indexes after import validation."""

from alembic import op


revision = "0002_cockroach_vector_indexes"
down_revision = "0001_agentbook_cockroach_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE VECTOR INDEX idx_document_chunks_workspace_embedding "
        "ON document_chunks (workspace_id, embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE VECTOR INDEX idx_memory_embeddings_workspace_embedding "
        "ON learner_memory_embeddings (workspace_id, embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS document_chunks@idx_document_chunks_workspace_embedding"
    )
    op.execute(
        "DROP INDEX IF EXISTS learner_memory_embeddings@idx_memory_embeddings_workspace_embedding"
    )
