from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.rag.database import get_connection, initialize_database


MAX_NOTEBOOK_NAME_LENGTH = 160
MAX_NOTEBOOK_DESCRIPTION_LENGTH = 2_000


class NotebookNotFoundError(LookupError):
    """Raised when a requested notebook does not exist."""


class DocumentNotFoundError(LookupError):
    """Raised when a requested document does not exist."""


class DuplicateNotebookNameError(ValueError):
    """Raised when a notebook name already exists."""


class NotebookNotEmptyError(ValueError):
    """Raised when deletion is attempted on a non-empty notebook."""


@dataclass(frozen=True)
class Notebook:
    id: int
    name: str
    description: str
    document_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DocumentRecord:
    id: int
    filename: str
    mime_type: str
    file_hash: str
    chunk_count: int
    created_at: str
    updated_at: str
    notebook_id: int | None
    notebook_name: str | None
    assigned_at: str | None


def initialize_notebook_database() -> None:
    """Initialize document and notebook tables using additive migrations."""
    initialize_database()


def create_notebook(
    name: str,
    description: str = "",
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Notebook:
    normalized_name = _normalize_name(name)
    normalized_description = _normalize_description(description)
    timestamp = _utc_now()

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO notebooks (
                    workspace_id,
                    name,
                    description,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    normalized_name,
                    normalized_description,
                    timestamp,
                    timestamp,
                ),
            )
            notebook_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise DuplicateNotebookNameError(
            f'Notebook "{normalized_name}" already exists.'
        ) from exc

    if notebook_id is None:
        raise RuntimeError("SQLite did not return a notebook ID.")

    notebook = get_notebook(int(notebook_id), workspace_id=workspace_id)
    if notebook is None:
        raise RuntimeError("Created notebook could not be loaded.")
    return notebook


def get_notebook(
    notebook_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Notebook | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                n.id,
                n.name,
                n.description,
                n.created_at,
                n.updated_at,
                COUNT(nd.document_id) AS document_count
            FROM notebooks AS n
            LEFT JOIN notebook_documents AS nd
                ON nd.notebook_id = n.id
            WHERE n.id = ? AND n.workspace_id = ?
            GROUP BY n.id
            """,
            (notebook_id, workspace_id),
        ).fetchone()

    return _notebook_from_row(row) if row is not None else None


def list_notebooks(
    search: str | None = None,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[Notebook]:
    parameters: list[object] = [workspace_id]
    where_clause = "WHERE n.workspace_id = ?"
    if search is not None and search.strip():
        pattern = _literal_like_pattern(search.strip())
        where_clause = (
            "WHERE n.workspace_id = ? AND ("
            "n.name LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR n.description LIKE ? ESCAPE '\\' COLLATE NOCASE)"
        )
        parameters.extend((pattern, pattern))

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                n.id,
                n.name,
                n.description,
                n.created_at,
                n.updated_at,
                COUNT(nd.document_id) AS document_count
            FROM notebooks AS n
            LEFT JOIN notebook_documents AS nd
                ON nd.notebook_id = n.id
            {where_clause}
            GROUP BY n.id
            ORDER BY n.name COLLATE NOCASE ASC, n.id ASC
            """,
            parameters,
        ).fetchall()

    return [_notebook_from_row(row) for row in rows]


def update_notebook(
    notebook_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Notebook:
    assignments: list[str] = []
    parameters: list[object] = []

    if name is not None:
        assignments.append("name = ?")
        parameters.append(_normalize_name(name))
    if description is not None:
        assignments.append("description = ?")
        parameters.append(_normalize_description(description))

    if not assignments:
        notebook = get_notebook(notebook_id, workspace_id=workspace_id)
        if notebook is None:
            raise NotebookNotFoundError(
                f"Notebook ID {notebook_id} does not exist."
            )
        return notebook

    assignments.append("updated_at = ?")
    parameters.append(_utc_now())
    parameters.append(notebook_id)
    parameters.append(workspace_id)

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE notebooks
                SET {', '.join(assignments)}
                WHERE id = ? AND workspace_id = ?
                """,
                parameters,
            )
            if cursor.rowcount == 0:
                raise NotebookNotFoundError(
                    f"Notebook ID {notebook_id} does not exist."
                )
    except sqlite3.IntegrityError as exc:
        attempted_name = name.strip() if name is not None else ""
        raise DuplicateNotebookNameError(
            f'Notebook "{attempted_name}" already exists.'
        ) from exc

    notebook = get_notebook(notebook_id, workspace_id=workspace_id)
    if notebook is None:
        raise RuntimeError("Updated notebook could not be loaded.")
    return notebook


def delete_notebook(
    notebook_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    """Delete an existing notebook only when it has no documents."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                n.id,
                COUNT(nd.document_id) AS document_count
            FROM notebooks AS n
            LEFT JOIN notebook_documents AS nd
                ON nd.notebook_id = n.id
            WHERE n.id = ? AND n.workspace_id = ?
            GROUP BY n.id
            """,
            (notebook_id, workspace_id),
        ).fetchone()

        if row is None:
            return False
        if int(row["document_count"]) > 0:
            raise NotebookNotEmptyError(
                "Only empty notebooks can be deleted."
            )

        cursor = connection.execute(
            "DELETE FROM notebooks WHERE id = ? AND workspace_id = ?",
            (notebook_id, workspace_id),
        )
        return cursor.rowcount > 0


def assign_document_to_notebook(
    document_id: int,
    notebook_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> DocumentRecord:
    """Assign or move a document without changing its vector data."""
    timestamp = _utc_now()

    with get_connection() as connection:
        _require_document(connection, document_id, workspace_id)
        _require_notebook(connection, notebook_id, workspace_id)

        existing = connection.execute(
            """
            SELECT notebook_id
            FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()
        previous_notebook_id = (
            int(existing["notebook_id"])
            if existing is not None
            else None
        )

        if previous_notebook_id != notebook_id:
            connection.execute(
                """
                INSERT INTO notebook_documents (
                    workspace_id,
                    document_id,
                    notebook_id,
                    assigned_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    notebook_id = excluded.notebook_id,
                    assigned_at = excluded.assigned_at
                """,
                (workspace_id, document_id, notebook_id, timestamp),
            )
            _touch_notebook(connection, notebook_id, timestamp)
            if previous_notebook_id is not None:
                _touch_notebook(
                    connection,
                    previous_notebook_id,
                    timestamp,
                )

    record = get_document_record(document_id, workspace_id=workspace_id)
    if record is None:
        raise RuntimeError("Assigned document could not be loaded.")
    return record


def remove_document_from_notebook(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    with get_connection() as connection:
        _require_document(connection, document_id, workspace_id)
        row = connection.execute(
            """
            SELECT notebook_id
            FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()
        if row is None:
            return False

        notebook_id = int(row["notebook_id"])
        cursor = connection.execute(
            """
            DELETE FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        )
        _touch_notebook(connection, notebook_id, _utc_now())
        return cursor.rowcount > 0


def get_document_notebook_id(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int | None:
    with get_connection() as connection:
        _require_document(connection, document_id, workspace_id)
        row = connection.execute(
            """
            SELECT notebook_id
            FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()

    return int(row["notebook_id"]) if row is not None else None


def get_document_record(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> DocumentRecord | None:
    records = _query_document_records(
        where_clauses=["d.id = ?"],
        parameters=[document_id],
        workspace_id=workspace_id,
    )
    return records[0] if records else None


def list_document_records(
    *,
    notebook_id: int | None = None,
    unsorted_only: bool = False,
    search: str | None = None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[DocumentRecord]:
    """List global, notebook-scoped, or virtual Unsorted documents."""
    if notebook_id is not None and unsorted_only:
        raise ValueError(
            "notebook_id and unsorted_only cannot be combined."
        )

    where_clauses: list[str] = []
    parameters: list[object] = []

    if notebook_id is not None:
        with get_connection() as connection:
            _require_notebook(connection, notebook_id, workspace_id)
        where_clauses.append("nd.notebook_id = ?")
        parameters.append(notebook_id)
    elif unsorted_only:
        where_clauses.append("nd.document_id IS NULL")

    if search is not None and search.strip():
        where_clauses.append(
            "d.filename LIKE ? ESCAPE '\\' COLLATE NOCASE"
        )
        parameters.append(_literal_like_pattern(search.strip()))

    return _query_document_records(
        where_clauses=where_clauses,
        parameters=parameters,
        workspace_id=workspace_id,
    )


def search_document_records(
    query: str,
    *,
    notebook_id: int | None = None,
    unsorted_only: bool = False,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[DocumentRecord]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Search query cannot be empty.")
    return list_document_records(
        notebook_id=notebook_id,
        unsorted_only=unsorted_only,
        search=normalized_query,
        workspace_id=workspace_id,
    )


def count_documents_by_notebook(
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> dict[int | None, int]:
    """Return every notebook count plus virtual Unsorted under None."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                n.id AS notebook_id,
                COUNT(nd.document_id) AS document_count
            FROM notebooks AS n
            LEFT JOIN notebook_documents AS nd
                ON nd.notebook_id = n.id
            WHERE n.workspace_id = ?
            GROUP BY n.id
            """,
            (workspace_id,),
        ).fetchall()
        unsorted_row = connection.execute(
            """
            SELECT COUNT(*) AS document_count
            FROM documents AS d
            LEFT JOIN notebook_documents AS nd
                ON nd.document_id = d.id
            WHERE d.workspace_id = ? AND nd.document_id IS NULL
            """,
            (workspace_id,),
        ).fetchone()

    counts: dict[int | None, int] = {
        int(row["notebook_id"]): int(row["document_count"])
        for row in rows
    }
    counts[None] = (
        int(unsorted_row["document_count"])
        if unsorted_row is not None
        else 0
    )
    return counts


def count_notebook_documents(
    notebook_id: int | None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int:
    if notebook_id is None:
        return count_documents_by_notebook(workspace_id).get(None, 0)

    counts = count_documents_by_notebook(workspace_id)
    if notebook_id not in counts:
        raise NotebookNotFoundError(
            f"Notebook ID {notebook_id} does not exist."
        )
    return counts[notebook_id]


def _query_document_records(
    *,
    where_clauses: list[str],
    parameters: list[object],
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[DocumentRecord]:
    where_clauses = ["d.workspace_id = ?", *where_clauses]
    parameters = [workspace_id, *parameters]
    where_sql = (
        f"WHERE {' AND '.join(where_clauses)}"
        if where_clauses
        else ""
    )
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                d.id,
                d.filename,
                d.mime_type,
                d.file_hash,
                d.chunk_count,
                d.created_at,
                COALESCE(d.updated_at, d.created_at) AS updated_at,
                nd.notebook_id,
                n.name AS notebook_name,
                nd.assigned_at
            FROM documents AS d
            LEFT JOIN notebook_documents AS nd
                ON nd.document_id = d.id
            LEFT JOIN notebooks AS n
                ON n.id = nd.notebook_id
            {where_sql}
            ORDER BY d.created_at DESC, d.id DESC
            """,
            parameters,
        ).fetchall()

    return [_document_record_from_row(row) for row in rows]


def _notebook_from_row(row: sqlite3.Row) -> Notebook:
    return Notebook(
        id=int(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        document_count=int(row["document_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _document_record_from_row(row: sqlite3.Row) -> DocumentRecord:
    notebook_id = row["notebook_id"]
    notebook_name = row["notebook_name"]
    assigned_at = row["assigned_at"]
    return DocumentRecord(
        id=int(row["id"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        file_hash=str(row["file_hash"]),
        chunk_count=int(row["chunk_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        notebook_id=(
            int(notebook_id) if notebook_id is not None else None
        ),
        notebook_name=(
            str(notebook_name) if notebook_name is not None else None
        ),
        assigned_at=(
            str(assigned_at) if assigned_at is not None else None
        ),
    )


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Notebook name cannot be empty.")
    if len(normalized) > MAX_NOTEBOOK_NAME_LENGTH:
        raise ValueError(
            "Notebook name cannot exceed "
            f"{MAX_NOTEBOOK_NAME_LENGTH} characters."
        )
    return normalized


def _normalize_description(description: str) -> str:
    normalized = description.strip()
    if len(normalized) > MAX_NOTEBOOK_DESCRIPTION_LENGTH:
        raise ValueError(
            "Notebook description cannot exceed "
            f"{MAX_NOTEBOOK_DESCRIPTION_LENGTH} characters."
        )
    return normalized


def _literal_like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _require_notebook(
    connection: sqlite3.Connection,
    notebook_id: int,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM notebooks WHERE id = ? AND workspace_id = ?",
        (notebook_id, workspace_id),
    ).fetchone()
    if row is None:
        raise NotebookNotFoundError(
            f"Notebook ID {notebook_id} does not exist."
        )


def _require_document(
    connection: sqlite3.Connection,
    document_id: int,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM documents WHERE id = ? AND workspace_id = ?",
        (document_id, workspace_id),
    ).fetchone()
    if row is None:
        raise DocumentNotFoundError(
            f"Document ID {document_id} does not exist."
        )


def _touch_notebook(
    connection: sqlite3.Connection,
    notebook_id: int,
    timestamp: str,
) -> None:
    connection.execute(
        "UPDATE notebooks SET updated_at = ? WHERE id = ?",
        (timestamp, notebook_id),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
