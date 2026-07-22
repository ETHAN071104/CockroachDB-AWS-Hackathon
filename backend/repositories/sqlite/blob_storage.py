from __future__ import annotations

from datetime import datetime, timezone

from backend.domain import BlobMetadata, DEFAULT_WORKSPACE_ID


class SQLiteBlobStorage:
    """Compatibility blob adapter over the legacy documents BLOB column."""

    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def store(
        self,
        document_id: int,
        filename: str,
        mime_type: str,
        content_hash: str,
        data: bytes,
    ) -> BlobMetadata:
        from backend.rag.database import get_connection

        timestamp = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE documents SET file_data = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (data, timestamp, document_id, self.workspace_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Document ID {document_id} does not exist.")
        metadata = self.metadata(document_id)
        assert metadata is not None
        return metadata

    def read(self, document_id: int) -> bytes:
        from backend.rag.database import get_connection

        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT file_data FROM documents
                WHERE id = ? AND workspace_id = ?
                """,
                (document_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Document ID {document_id} does not exist.")
        return bytes(row["file_data"])

    def delete(self, document_id: int) -> bool:
        # The owning document row controls legacy blob lifecycle.
        from backend.rag.database import get_connection

        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE documents SET file_data = X''
                WHERE id = ? AND workspace_id = ?
                """,
                (document_id, self.workspace_id),
            )
        return cursor.rowcount == 1

    def metadata(self, document_id: int) -> BlobMetadata | None:
        from backend.rag.database import get_connection

        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, filename, mime_type, file_hash,
                       length(file_data) AS size_bytes,
                       created_at, COALESCE(updated_at, created_at) AS updated_at
                FROM documents WHERE id = ? AND workspace_id = ?
                """,
                (document_id, self.workspace_id),
            ).fetchone()
        if row is None:
            return None
        return BlobMetadata(
            document_id=int(row["id"]),
            filename=str(row["filename"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            content_hash=str(row["file_hash"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
