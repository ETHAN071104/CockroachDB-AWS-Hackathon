from __future__ import annotations

import tempfile
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document


SUPPORTED_EXTENSIONS = {".pdf", ".txt"}


def validate_file_path(file_path: Path) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported file type: {file_path.suffix}. "
            f"Supported types: {supported}"
        )


def get_mime_type(file_path: Path) -> str:
    extension = file_path.suffix.lower()

    if extension == ".pdf":
        return "application/pdf"

    if extension == ".txt":
        return "text/plain"

    return "application/octet-stream"


def load_documents_from_bytes(
    filename: str,
    file_data: bytes,
) -> list[Document]:
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix}")

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=suffix,
            delete=False,
        ) as temporary_file:
            temporary_file.write(file_data)
            temporary_path = Path(temporary_file.name)

        if suffix == ".pdf":
            loader = PyPDFLoader(str(temporary_path))
        else:
            loader = TextLoader(
                str(temporary_path),
                encoding="utf-8",
                autodetect_encoding=True,
            )

        documents = loader.load()

        valid_documents = [
            document
            for document in documents
            if document.page_content.strip()
        ]

        if not valid_documents:
            raise ValueError(
                "No readable text was extracted from the file. "
                "The PDF may be scanned or image-based."
            )

        for document in valid_documents:
            document.metadata["filename"] = filename

        return valid_documents

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)