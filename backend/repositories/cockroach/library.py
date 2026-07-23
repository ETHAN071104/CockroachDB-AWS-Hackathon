from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.domain import BlobMetadata, DEFAULT_WORKSPACE_ID, new_record_id
from backend.rag.database import StoredDocument
from backend.rag.notebooks import (
    DocumentNotFoundError,
    DocumentRecord,
    DuplicateNotebookNameError,
    Notebook,
    NotebookNotEmptyError,
    NotebookNotFoundError,
)
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import (
    content_sha256,
    iso,
    new_public_identity,
    utc_now,
    uuid_for_public,
)
from backend.repositories.interfaces import RepositoryConflictError


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class CockroachBlobStorage:
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
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            raise KeyError(f"Document ID {document_id} does not exist.")
        now = utc_now()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_blobs (
                        document_id, workspace_id, data, size_bytes, content_hash,
                        filename, mime_type, created_at, updated_at
                    ) VALUES (
                        :document_id, :workspace_id, :data, :size_bytes, :content_hash,
                        :filename, :mime_type, :created_at, :updated_at
                    )
                    ON CONFLICT (document_id) DO UPDATE SET
                        data=excluded.data, size_bytes=excluded.size_bytes,
                        content_hash=excluded.content_hash, filename=excluded.filename,
                        mime_type=excluded.mime_type, updated_at=excluded.updated_at
                    WHERE document_blobs.workspace_id=excluded.workspace_id
                    """
                ),
                {
                    "document_id": document_uuid,
                    "workspace_id": UUID(self.workspace_id),
                    "data": data,
                    "size_bytes": len(data),
                    "content_hash": content_hash,
                    "filename": filename,
                    "mime_type": mime_type,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        metadata = self.metadata(document_id)
        assert metadata is not None
        return metadata

    def read(self, document_id: int) -> bytes:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            raise KeyError(f"Document ID {document_id} does not exist.")
        with connection_scope() as connection:
            value = connection.execute(
                text(
                    "SELECT data FROM document_blobs "
                    "WHERE document_id=:document_id AND workspace_id=:workspace_id"
                ),
                {"document_id": document_uuid, "workspace_id": UUID(self.workspace_id)},
            ).scalar_one_or_none()
        if value is None:
            raise KeyError(f"Blob for document ID {document_id} does not exist.")
        return bytes(value)

    def delete(self, document_id: int) -> bool:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            return False
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "DELETE FROM document_blobs "
                    "WHERE document_id=:document_id AND workspace_id=:workspace_id"
                ),
                {"document_id": document_uuid, "workspace_id": UUID(self.workspace_id)},
            )
        return result.rowcount == 1

    def metadata(self, document_id: int) -> BlobMetadata | None:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            return None
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT b.*, d.public_id FROM document_blobs b
                    JOIN documents d ON d.id=b.document_id
                    WHERE b.document_id=:document_id AND b.workspace_id=:workspace_id
                    """
                ),
                {"document_id": document_uuid, "workspace_id": UUID(self.workspace_id)},
            ).mappings().one_or_none()
        if row is None:
            return None
        return BlobMetadata(
            document_id=int(row["public_id"]), filename=str(row["filename"]),
            mime_type=str(row["mime_type"]), size_bytes=int(row["size_bytes"]),
            content_hash=str(row["content_hash"]), created_at=iso(row["created_at"]),
            updated_at=iso(row["updated_at"]),
        )


class CockroachDocumentRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id
        self.blobs = CockroachBlobStorage(workspace_id)

    def find_by_hash(self, file_hash: str) -> StoredDocument | None:
        return self._one("file_hash=:value", {"value": file_hash})

    def insert(
        self,
        filename: str,
        mime_type: str,
        file_hash: str,
        file_data: bytes,
    ) -> int:
        record_id, public_id = new_public_identity()
        now = utc_now()
        try:
            with connection_scope() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO documents (
                            id, workspace_id, public_id, filename, mime_type,
                            file_hash, chunk_count, created_at, updated_at
                        ) VALUES (
                            :id, :workspace_id, :public_id, :filename, :mime_type,
                            :file_hash, 0, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": record_id, "workspace_id": UUID(self.workspace_id),
                        "public_id": public_id, "filename": filename,
                        "mime_type": mime_type, "file_hash": file_hash,
                        "created_at": now, "updated_at": now,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO document_blobs (
                            document_id, workspace_id, data, size_bytes, content_hash,
                            filename, mime_type, created_at, updated_at
                        ) VALUES (
                            :document_id, :workspace_id, :data, :size_bytes,
                            :content_hash, :filename, :mime_type, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "document_id": record_id, "workspace_id": UUID(self.workspace_id),
                        "data": file_data, "size_bytes": len(file_data),
                        "content_hash": file_hash, "filename": filename,
                        "mime_type": mime_type, "created_at": now, "updated_at": now,
                    },
                )
        except IntegrityError as error:
            raise RepositoryConflictError("Document already exists.") from error
        return public_id

    def get(self, document_id: int) -> StoredDocument | None:
        return self._one("public_id=:value", {"value": int(document_id)})

    def get_file_data(self, document_id: int) -> tuple[str, bytes]:
        document = self.get(document_id)
        if document is None:
            raise ValueError(f"Document ID {document_id} does not exist.")
        return document.filename, self.blobs.read(document_id)

    def update_chunk_count(self, document_id: int, chunk_count: int) -> None:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE documents SET chunk_count=:chunk_count, updated_at=now()
                    WHERE workspace_id=:workspace_id AND public_id=:public_id
                    """
                ),
                {
                    "chunk_count": int(chunk_count),
                    "workspace_id": UUID(self.workspace_id),
                    "public_id": int(document_id),
                },
            )
            if result.rowcount != 1:
                raise KeyError(f"Document ID {document_id} does not exist.")

    def delete(self, document_id: int) -> bool:
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "DELETE FROM documents "
                    "WHERE workspace_id=:workspace_id AND public_id=:public_id"
                ),
                {"workspace_id": UUID(self.workspace_id), "public_id": int(document_id)},
            )
        return result.rowcount == 1

    def list(self) -> list[StoredDocument]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM documents WHERE workspace_id=:workspace_id "
                    "ORDER BY created_at DESC, public_id DESC"
                ),
                {"workspace_id": UUID(self.workspace_id)},
            ).mappings().all()
        return [_stored_document(row) for row in rows]

    def _one(self, clause: str, parameters: dict[str, object]) -> StoredDocument | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM documents WHERE workspace_id=:workspace_id AND " + clause
                ),
                {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().one_or_none()
        return _stored_document(row) if row is not None else None


class CockroachNotebookRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def create(self, name: str, description: str = "") -> Notebook:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Notebook name cannot be empty.")
        record_id, public_id = new_public_identity()
        now = utc_now()
        try:
            with connection_scope() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO notebooks (
                            id, workspace_id, public_id, name, normalized_name,
                            description, created_at, updated_at
                        ) VALUES (
                            :id, :workspace_id, :public_id, :name, :normalized_name,
                            :description, :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": record_id, "workspace_id": UUID(self.workspace_id),
                        "public_id": public_id, "name": normalized_name,
                        "normalized_name": normalized_name.casefold(),
                        "description": description.strip(),
                        "created_at": now, "updated_at": now,
                    },
                )
        except IntegrityError as error:
            raise DuplicateNotebookNameError(
                f'Notebook "{normalized_name}" already exists.'
            ) from error
        notebook = self.get(public_id)
        assert notebook is not None
        return notebook

    def get(self, notebook_id: int) -> Notebook | None:
        rows = self._notebook_rows("n.public_id=:public_id", {"public_id": int(notebook_id)})
        return _notebook(rows[0]) if rows else None

    def list(self, search: str | None = None) -> list[Notebook]:
        where = "true"
        parameters: dict[str, object] = {}
        if search is not None and search.strip():
            where = "(n.name ILIKE :search ESCAPE '\\' OR n.description ILIKE :search ESCAPE '\\')"
            parameters["search"] = _like(search.strip())
        return [_notebook(row) for row in self._notebook_rows(where, parameters)]

    def update(
        self,
        notebook_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Notebook:
        assignments: list[str] = []
        parameters: dict[str, object] = {
            "workspace_id": UUID(self.workspace_id), "public_id": int(notebook_id)
        }
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValueError("Notebook name cannot be empty.")
            assignments.extend(("name=:name", "normalized_name=:normalized_name"))
            parameters.update(name=cleaned, normalized_name=cleaned.casefold())
        if description is not None:
            assignments.append("description=:description")
            parameters["description"] = description.strip()
        if not assignments:
            notebook = self.get(notebook_id)
            if notebook is None:
                raise NotebookNotFoundError(f"Notebook ID {notebook_id} does not exist.")
            return notebook
        assignments.append("updated_at=now()")
        try:
            with connection_scope() as connection:
                result = connection.execute(
                    text(
                        "UPDATE notebooks SET " + ", ".join(assignments)
                        + " WHERE workspace_id=:workspace_id AND public_id=:public_id"
                    ), parameters,
                )
                if result.rowcount != 1:
                    raise NotebookNotFoundError(
                        f"Notebook ID {notebook_id} does not exist."
                    )
        except IntegrityError as error:
            raise DuplicateNotebookNameError("Notebook name already exists.") from error
        notebook = self.get(notebook_id)
        assert notebook is not None
        return notebook

    def delete(self, notebook_id: int) -> bool:
        notebook_uuid = uuid_for_public("notebooks", self.workspace_id, notebook_id)
        if notebook_uuid is None:
            return False
        with connection_scope() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM notebook_documents "
                    "WHERE notebook_id=:id AND workspace_id=:workspace_id"
                ),
                {
                    "id": notebook_uuid,
                    "workspace_id": UUID(self.workspace_id),
                },
            ).scalar_one()
            if int(count):
                raise NotebookNotEmptyError(
                    f"Notebook ID {notebook_id} still contains documents."
                )
            result = connection.execute(
                text("DELETE FROM notebooks WHERE id=:id AND workspace_id=:workspace_id"),
                {"id": notebook_uuid, "workspace_id": UUID(self.workspace_id)},
            )
        return result.rowcount == 1

    def assign_document(self, document_id: int, notebook_id: int) -> DocumentRecord:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        notebook_uuid = uuid_for_public("notebooks", self.workspace_id, notebook_id)
        if document_uuid is None:
            raise DocumentNotFoundError(f"Document ID {document_id} does not exist.")
        if notebook_uuid is None:
            raise NotebookNotFoundError(f"Notebook ID {notebook_id} does not exist.")
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO notebook_documents (
                        id, workspace_id, notebook_id, document_id, assigned_at
                    ) VALUES (:id,:workspace_id,:notebook_id,:document_id,:assigned_at)
                    ON CONFLICT (workspace_id, document_id) DO UPDATE SET
                        notebook_id=excluded.notebook_id, assigned_at=excluded.assigned_at
                    """
                ),
                {
                    "id": new_record_id(), "workspace_id": UUID(self.workspace_id),
                    "notebook_id": notebook_uuid, "document_id": document_uuid,
                    "assigned_at": utc_now(),
                },
            )
        document = self.get_document(document_id)
        assert document is not None
        return document

    def remove_document(self, document_id: int) -> bool:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            raise DocumentNotFoundError(f"Document ID {document_id} does not exist.")
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "DELETE FROM notebook_documents "
                    "WHERE workspace_id=:workspace_id AND document_id=:document_id"
                ),
                {"workspace_id": UUID(self.workspace_id), "document_id": document_uuid},
            )
        return result.rowcount == 1

    def count_documents(self, notebook_id: int | None) -> int:
        with connection_scope() as connection:
            if notebook_id is None:
                value = connection.execute(
                    text(
                        """
                        SELECT count(*) FROM documents d
                        LEFT JOIN notebook_documents nd ON nd.document_id=d.id
                        WHERE d.workspace_id=:workspace_id AND nd.document_id IS NULL
                        """
                    ), {"workspace_id": UUID(self.workspace_id)},
                ).scalar_one()
            else:
                notebook_uuid = uuid_for_public("notebooks", self.workspace_id, notebook_id)
                if notebook_uuid is None:
                    raise NotebookNotFoundError(f"Notebook ID {notebook_id} does not exist.")
                value = connection.execute(
                    text(
                        "SELECT count(*) FROM notebook_documents "
                        "WHERE workspace_id=:workspace_id AND notebook_id=:notebook_id"
                    ), {"workspace_id": UUID(self.workspace_id), "notebook_id": notebook_uuid},
                ).scalar_one()
        return int(value)

    def get_document(self, document_id: int) -> DocumentRecord | None:
        rows = self._document_rows("d.public_id=:public_id", {"public_id": int(document_id)})
        return _document_record(rows[0]) if rows else None

    def list_documents(
        self,
        *,
        notebook_id: int | None = None,
        unsorted_only: bool = False,
        search: str | None = None,
    ) -> list[DocumentRecord]:
        clauses = ["true"]
        parameters: dict[str, object] = {}
        if notebook_id is not None:
            notebook_uuid = uuid_for_public("notebooks", self.workspace_id, notebook_id)
            if notebook_uuid is None:
                raise NotebookNotFoundError(f"Notebook ID {notebook_id} does not exist.")
            clauses.append("nd.notebook_id=:notebook_id")
            parameters["notebook_id"] = notebook_uuid
        if unsorted_only:
            clauses.append("nd.document_id IS NULL")
        if search is not None and search.strip():
            clauses.append("d.filename ILIKE :search ESCAPE '\\'")
            parameters["search"] = _like(search.strip())
        return [
            _document_record(row)
            for row in self._document_rows(" AND ".join(clauses), parameters)
        ]

    def get_document_notebook_id(self, document_id: int) -> int | None:
        document = self.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError(f"Document ID {document_id} does not exist.")
        return document.notebook_id

    def _notebook_rows(self, clause: str, parameters: dict[str, object]):
        with connection_scope() as connection:
            return connection.execute(
                text(
                    """
                    SELECT n.*, count(nd.document_id) AS document_count
                    FROM notebooks n
                    LEFT JOIN notebook_documents nd ON nd.notebook_id=n.id
                    WHERE n.workspace_id=:workspace_id AND """ + clause
                    + " GROUP BY n.id ORDER BY n.normalized_name ASC, n.public_id ASC"
                ), {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().all()

    def _document_rows(self, clause: str, parameters: dict[str, object]):
        with connection_scope() as connection:
            return connection.execute(
                text(
                    """
                    SELECT d.*, n.public_id AS notebook_public_id, n.name AS notebook_name,
                           nd.assigned_at
                    FROM documents d
                    LEFT JOIN notebook_documents nd ON nd.document_id=d.id
                    LEFT JOIN notebooks n ON n.id=nd.notebook_id
                    WHERE d.workspace_id=:workspace_id AND """ + clause
                    + " ORDER BY d.created_at DESC, d.public_id DESC"
                ), {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().all()


def _stored_document(row) -> StoredDocument:
    return StoredDocument(
        id=int(row["public_id"]), filename=str(row["filename"]),
        mime_type=str(row["mime_type"]), file_hash=str(row["file_hash"]),
        chunk_count=int(row["chunk_count"]), created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]),
    )


def _notebook(row) -> Notebook:
    return Notebook(
        id=int(row["public_id"]), name=str(row["name"]),
        description=str(row["description"]), document_count=int(row["document_count"]),
        created_at=iso(row["created_at"]), updated_at=iso(row["updated_at"]),
    )


def _document_record(row) -> DocumentRecord:
    return DocumentRecord(
        id=int(row["public_id"]), filename=str(row["filename"]),
        mime_type=str(row["mime_type"]), file_hash=str(row["file_hash"]),
        chunk_count=int(row["chunk_count"]), created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]),
        notebook_id=(int(row["notebook_public_id"]) if row["notebook_public_id"] is not None else None),
        notebook_name=(str(row["notebook_name"]) if row["notebook_name"] is not None else None),
        assigned_at=(iso(row["assigned_at"]) if row["assigned_at"] is not None else None),
    )
