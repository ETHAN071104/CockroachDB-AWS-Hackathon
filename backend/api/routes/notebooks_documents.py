from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Query, UploadFile

from backend.api.errors import ApiError
from backend.api.public_ids import PublicIdInput, parse_public_id
from backend.api.schemas import (
    DeleteResponse,
    DocumentAssignment,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
    NotebookCreate,
    NotebookListResponse,
    NotebookResponse,
    NotebookUpdate,
)
from backend.application.dependencies import get_application_dependencies
from backend.rag.config import MAX_UPLOAD_BYTES
from backend.rag.document_service import delete_document
from backend.rag.ingestion import index_file_bytes
from backend.rag.notebooks import (
    DocumentNotFoundError,
    DocumentRecord,
    DuplicateNotebookNameError,
    Notebook,
    NotebookNotEmptyError,
    NotebookNotFoundError,
)

router = APIRouter(prefix="/api", tags=["library"])


def _repository():
    return get_application_dependencies().notebooks


def _notebook_response(notebook: Notebook) -> NotebookResponse:
    return NotebookResponse(
        id=notebook.id,
        name=notebook.name,
        description=notebook.description,
        document_count=notebook.document_count,
        created_at=notebook.created_at,
        updated_at=notebook.updated_at,
        is_virtual=False,
    )


def _unsorted_response() -> NotebookResponse:
    return NotebookResponse(
        id=None,
        name="Unsorted Documents",
        description="Documents not assigned to a notebook.",
        document_count=_repository().count_documents(None),
        created_at=None,
        updated_at=None,
        is_virtual=True,
    )


def _document_response(document: DocumentRecord) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
        notebook_id=document.notebook_id,
    )


def _notebook_or_404(notebook_id: int) -> Notebook:
    notebook = _repository().get(notebook_id)
    if notebook is None:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        )
    return notebook


def _document_or_404(document_id: int) -> DocumentRecord:
    document = _repository().get_document(document_id)
    if document is None:
        raise ApiError(
            status_code=404,
            code="document_not_found",
            message="Document was not found.",
        )
    return document


@router.get("/notebooks", response_model=NotebookListResponse)
def get_notebooks(
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> NotebookListResponse:
    notebooks = _repository().list(search=q)
    return NotebookListResponse(
        items=[_notebook_response(item) for item in notebooks],
        total=len(notebooks),
        unsorted=_unsorted_response(),
    )


@router.post(
    "/notebooks",
    response_model=NotebookResponse,
    status_code=201,
)
def post_notebook(payload: NotebookCreate) -> NotebookResponse:
    try:
        notebook = _repository().create(
            payload.name,
            payload.description or "",
        )
    except DuplicateNotebookNameError as error:
        raise ApiError(
            status_code=409,
            code="notebook_name_conflict",
            message="A notebook with that name already exists.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=400,
            code="invalid_notebook",
            message=str(error),
        ) from error
    return _notebook_response(notebook)


@router.get("/notebooks/unsorted", response_model=NotebookResponse)
def get_unsorted_notebook() -> NotebookResponse:
    return _unsorted_response()


@router.get(
    "/notebooks/unsorted/documents",
    response_model=DocumentListResponse,
)
def get_unsorted_documents(
    q: Annotated[str | None, Query(max_length=255)] = None,
) -> DocumentListResponse:
    documents = _repository().list_documents(
        unsorted_only=True,
        search=q,
    )
    return DocumentListResponse(
        items=[_document_response(item) for item in documents],
        total=len(documents),
    )


@router.get("/notebooks/{notebook_id}", response_model=NotebookResponse)
def get_notebook_route(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> NotebookResponse:
    return _notebook_response(_notebook_or_404(notebook_id))


@router.patch("/notebooks/{notebook_id}", response_model=NotebookResponse)
def patch_notebook(
    notebook_id: Annotated[PublicIdInput, Path()],
    payload: NotebookUpdate,
) -> NotebookResponse:
    try:
        notebook = _repository().update(
            notebook_id,
            name=payload.name,
            description=payload.description,
        )
    except NotebookNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        ) from error
    except DuplicateNotebookNameError as error:
        raise ApiError(
            status_code=409,
            code="notebook_name_conflict",
            message="A notebook with that name already exists.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=400,
            code="invalid_notebook",
            message=str(error),
        ) from error
    return _notebook_response(notebook)


@router.delete("/notebooks/{notebook_id}", response_model=DeleteResponse)
def delete_notebook_route(
    notebook_id: Annotated[PublicIdInput, Path()],
) -> DeleteResponse:
    try:
        deleted = _repository().delete(notebook_id)
    except NotebookNotEmptyError as error:
        raise ApiError(
            status_code=409,
            code="notebook_not_empty",
            message="Move or remove its documents before deleting this notebook.",
        ) from error
    if not deleted:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        )
    return DeleteResponse(deleted=True)


@router.get(
    "/notebooks/{notebook_id}/documents",
    response_model=DocumentListResponse,
)
def get_notebook_documents(
    notebook_id: Annotated[PublicIdInput, Path()],
    q: Annotated[str | None, Query(max_length=255)] = None,
) -> DocumentListResponse:
    _notebook_or_404(notebook_id)
    documents = _repository().list_documents(
        notebook_id=notebook_id,
        search=q,
    )
    return DocumentListResponse(
        items=[_document_response(item) for item in documents],
        total=len(documents),
    )


@router.post(
    "/notebooks/{notebook_id}/documents/{document_id}",
    response_model=DocumentResponse,
)
def put_document_in_notebook(
    notebook_id: Annotated[PublicIdInput, Path()],
    document_id: Annotated[PublicIdInput, Path()],
) -> DocumentResponse:
    try:
        return _document_response(
            _repository().assign_document(document_id, notebook_id)
        )
    except NotebookNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        ) from error
    except DocumentNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="document_not_found",
            message="Document was not found.",
        ) from error


@router.delete(
    "/notebooks/{notebook_id}/documents/{document_id}",
    response_model=DocumentResponse,
)
def remove_document_from_named_notebook(
    notebook_id: Annotated[PublicIdInput, Path()],
    document_id: Annotated[PublicIdInput, Path()],
) -> DocumentResponse:
    document = _document_or_404(document_id)
    _notebook_or_404(notebook_id)
    if document.notebook_id != notebook_id:
        raise ApiError(
            status_code=409,
            code="document_not_in_notebook",
            message="Document is not assigned to this notebook.",
        )
    _repository().remove_document(document_id)
    return _document_response(_document_or_404(document_id))


@router.get("/documents", response_model=DocumentListResponse)
def get_documents(
    q: Annotated[str | None, Query(max_length=255)] = None,
    notebook_id: Annotated[str | None, Query(max_length=32)] = None,
) -> DocumentListResponse:
    parsed_notebook_id: int | None = None
    unsorted_only = False
    if notebook_id is not None:
        if notebook_id.casefold() == "unsorted":
            unsorted_only = True
        else:
            try:
                parsed_notebook_id = parse_public_id(notebook_id)
            except ValueError as error:
                raise ApiError(
                    status_code=422,
                    code="INVALID_PUBLIC_ID",
                    message=(
                        "notebook_id must be a positive decimal string "
                        "or 'unsorted'."
                    ),
                ) from error

    try:
        documents = _repository().list_documents(
            notebook_id=parsed_notebook_id,
            unsorted_only=unsorted_only,
            search=q,
        )
    except NotebookNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        ) from error
    return DocumentListResponse(
        items=[_document_response(item) for item in documents],
        total=len(documents),
    )


@router.post("/documents", response_model=DocumentUploadResponse)
@router.post(
    "/documents/upload",
    response_model=DocumentUploadResponse,
    include_in_schema=False,
)
def upload_document(
    file: Annotated[UploadFile, File()],
    notebook_id: Annotated[PublicIdInput | None, Form()] = None,
) -> DocumentUploadResponse:
    if notebook_id is not None:
        _notebook_or_404(notebook_id)

    filename = file.filename or ""
    try:
        file_data = file.file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        file.file.close()

    try:
        result = index_file_bytes(
            filename=filename,
            file_data=file_data,
            max_bytes=MAX_UPLOAD_BYTES,
        )
    except ValueError as error:
        message = str(error)
        if "exceeds" in message and "limit" in message:
            status_code = 413
            code = "upload_too_large"
        elif "Unsupported file type" in message:
            status_code = 415
            code = "unsupported_file_type"
        else:
            status_code = 400
            code = "invalid_upload"
        raise ApiError(
            status_code=status_code,
            code=code,
            message=message,
        ) from error
    except TypeError as error:
        raise ApiError(
            status_code=400,
            code="invalid_upload",
            message="Uploaded file data is invalid.",
        ) from error

    document_id = int(result["document_id"])
    if result["status"] == "indexed" and notebook_id is not None:
        try:
            _repository().assign_document(document_id, notebook_id)
        except Exception:
            delete_document(document_id)
            raise

    document = _document_or_404(document_id)
    status = str(result["status"])
    return DocumentUploadResponse(
        status="duplicate" if status == "duplicate" else "indexed",
        duplicate=status == "duplicate",
        document=_document_response(document),
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document_route(
    document_id: Annotated[PublicIdInput, Path()],
) -> DocumentResponse:
    return _document_response(_document_or_404(document_id))


@router.patch(
    "/documents/{document_id}/notebook",
    response_model=DocumentResponse,
)
def patch_document_notebook(
    document_id: Annotated[PublicIdInput, Path()],
    payload: DocumentAssignment,
) -> DocumentResponse:
    try:
        if payload.notebook_id is None:
            _repository().remove_document(document_id)
            document = _document_or_404(document_id)
        else:
            document = _repository().assign_document(
                document_id,
                payload.notebook_id,
            )
    except NotebookNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="notebook_not_found",
            message="Notebook was not found.",
        ) from error
    except DocumentNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="document_not_found",
            message="Document was not found.",
        ) from error
    return _document_response(document)


@router.delete("/documents/{document_id}", response_model=DeleteResponse)
def delete_document_route(
    document_id: Annotated[PublicIdInput, Path()],
) -> DeleteResponse:
    _document_or_404(document_id)
    delete_document(document_id)
    return DeleteResponse(deleted=True)
