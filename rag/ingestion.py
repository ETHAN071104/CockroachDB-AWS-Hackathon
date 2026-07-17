from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.config import CHUNK_OVERLAP, CHUNK_SIZE
from rag.database import (
    delete_document_record_if_exists,
    find_document_by_hash,
    get_document_file_data,
    insert_document,
    update_chunk_count,
)
from rag.loaders import (
    get_mime_type,
    load_documents_from_bytes,
    validate_file_path,
)
from rag.vector_store import (
    delete_document_vectors,
    get_vector_store,
)


def calculate_sha256(file_data: bytes) -> str:
    return hashlib.sha256(file_data).hexdigest()


def create_text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            "",
        ],
        length_function=len,
        add_start_index=True,
    )


def clean_metadata(
    metadata: dict,
) -> dict[str, str | int | float | bool]:
    cleaned: dict[str, str | int | float | bool] = {}

    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)):
            cleaned[str(key)] = value

    return cleaned


def prepare_chunks(
    documents: list[Document],
    document_id: int,
    filename: str,
) -> list[Document]:
    splitter = create_text_splitter()
    split_chunks = splitter.split_documents(documents)

    prepared_chunks: list[Document] = []

    for chunk_index, chunk in enumerate(split_chunks):
        text = chunk.page_content.strip()

        if not text:
            continue

        raw_page = chunk.metadata.get("page")

        if isinstance(raw_page, int):
            page_number = raw_page + 1
        else:
            page_number = 0

        metadata = dict(chunk.metadata)

        metadata.update(
            {
                "document_id": document_id,
                "filename": filename,
                "chunk_index": chunk_index,
                "page_number": page_number,
            }
        )

        prepared_chunks.append(
            Document(
                page_content=text,
                metadata=clean_metadata(metadata),
            )
        )

    return prepared_chunks


def create_chunk_ids(
    document_id: int,
    chunks: list[Document],
) -> list[str]:
    return [
        f"document-{document_id}-chunk-{index}"
        for index in range(len(chunks))
    ]


def index_file(
    file_path_string: str,
) -> dict[str, str | int]:
    if not file_path_string.strip():
        raise ValueError("File path cannot be empty.")

    file_path = Path(
        file_path_string.strip().strip('"').strip("'")
    ).expanduser().resolve()

    validate_file_path(file_path)

    try:
        file_data = file_path.read_bytes()
    except OSError as error:
        raise RuntimeError(
            f"Could not read file: {error}"
        ) from error

    if not file_data:
        raise ValueError("The selected file is empty.")

    file_hash = calculate_sha256(file_data)

    existing_document = find_document_by_hash(file_hash)

    if existing_document is not None:
        return {
            "status": "duplicate",
            "document_id": existing_document.id,
            "filename": existing_document.filename,
            "chunk_count": existing_document.chunk_count,
        }

    document_id = insert_document(
        filename=file_path.name,
        mime_type=get_mime_type(file_path),
        file_hash=file_hash,
        file_data=file_data,
    )

    try:
        stored_filename, stored_file_data = (
            get_document_file_data(document_id)
        )

        loaded_documents = load_documents_from_bytes(
            filename=stored_filename,
            file_data=stored_file_data,
        )

        if not loaded_documents:
            raise ValueError(
                "No readable content was extracted from the file."
            )

        chunks = prepare_chunks(
            documents=loaded_documents,
            document_id=document_id,
            filename=stored_filename,
        )

        if not chunks:
            raise ValueError(
                "The document produced no usable text chunks."
            )

        chunk_ids = create_chunk_ids(
            document_id=document_id,
            chunks=chunks,
        )

        vector_store = get_vector_store()

        vector_store.add_documents(
            documents=chunks,
            ids=chunk_ids,
        )

        update_chunk_count(
            document_id=document_id,
            chunk_count=len(chunks),
        )

        return {
            "status": "indexed",
            "document_id": document_id,
            "filename": stored_filename,
            "pages": len(loaded_documents),
            "chunk_count": len(chunks),
        }

    except Exception:
        try:
            delete_document_vectors(document_id)
        except Exception as cleanup_error:
            print(
                "Warning: failed to remove partial "
                f"vectors: {cleanup_error}"
            )

        delete_document_record_if_exists(document_id)
        raise