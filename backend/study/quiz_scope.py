from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from backend.application.dependencies import get_application_dependencies
from backend.rag.scope import RetrievalScope, resolve_retrieval_scope


BaseQuizScopeType: TypeAlias = Literal[
    "global",
    "notebook",
    "document",
    "documents",
    "topic",
]
QuizScopeType: TypeAlias = Literal[
    "global",
    "notebook",
    "document",
    "documents",
    "topic",
    "adaptive-global",
    "adaptive-notebook",
    "adaptive-document",
    "adaptive-documents",
    "adaptive-topic",
]


class QuizScopeUnavailableError(ValueError):
    """Raised when a valid scope has no indexed material for a quiz."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QuizScope:
    """User-safe description of the material eligible for quiz retrieval."""

    type: QuizScopeType
    label: str
    document_count: int
    personalized: bool
    resolved_document_ids: tuple[int, ...]
    description: str
    notebook_name: str | None = None
    document_name: str | None = None


def resolve_quiz_scope(
    scope: RetrievalScope | None,
    *,
    personalized: bool,
) -> QuizScope:
    """Resolve and validate quiz scope without exposing internal identifiers."""
    dependencies = get_application_dependencies()
    notebooks = dependencies.notebooks

    if scope is None:
        base_type: BaseQuizScopeType = "global"
        documents = notebooks.list_documents()
        label = "All indexed documents"
        notebook_name = None
        document_name = None
        empty_code = "no_study_material"
        empty_message = (
            "No study material is available. Upload and index at least one "
            "document before generating a quiz."
        )
    else:
        resolved = resolve_retrieval_scope(scope)

        if resolved.kind == "notebook":
            notebook = notebooks.get(scope.notebook_id)
            # resolve_retrieval_scope already validates this lookup.
            assert notebook is not None
            base_type = "notebook"
            documents = notebooks.list_documents(notebook_id=notebook.id)
            label = notebook.name
            notebook_name = notebook.name
            document_name = None
            empty_code = "notebook_has_no_indexed_material"
            empty_message = (
                f'The notebook "{notebook.name}" has no indexed study material. '
                "Add or index a document in this notebook before generating a quiz."
            )
        elif resolved.kind == "documents":
            documents = [
                document
                for document_id in resolved.document_ids
                if (document := notebooks.get_document(document_id)) is not None
            ]
            single_document = len(documents) == 1
            base_type = "document" if single_document else "documents"
            label = (
                documents[0].filename
                if single_document
                else f"{len(documents)} selected documents"
            )
            notebook_name = (
                documents[0].notebook_name if single_document else None
            )
            document_name = documents[0].filename if single_document else None
            empty_code = "document_not_ready"
            empty_message = (
                f'The document "{documents[0].filename}" is not indexed yet. '
                "Finish indexing it before generating a quiz."
                if single_document
                else "None of the selected documents is indexed yet. Finish "
                "indexing at least one selected document before generating a quiz."
            )
        else:
            topic = dependencies.intelligence.get_topic(scope.topic_id or "")
            # resolve_retrieval_scope already validates this lookup.
            assert topic is not None
            base_type = "topic"
            documents = [
                document
                for document_id in resolved.document_ids
                if (document := notebooks.get_document(document_id)) is not None
            ]
            label = topic.name
            notebook_name = None
            document_name = None
            empty_code = "topic_has_no_indexed_material"
            empty_message = (
                f'The topic "{topic.name}" has no indexed study material. '
                "Regenerate its topics after indexing source documents."
            )

    indexed_documents = tuple(
        document for document in documents if int(document.chunk_count) > 0
    )
    if not indexed_documents:
        raise QuizScopeUnavailableError(empty_code, empty_message)

    resolved_document_ids = tuple(
        dict.fromkeys(int(document.id) for document in indexed_documents)
    )
    scope_type = _scope_type(base_type, personalized)
    description = _description(
        base_type,
        label=label,
        count=len(resolved_document_ids),
        personalized=personalized,
    )
    return QuizScope(
        type=scope_type,
        label=label,
        document_count=len(resolved_document_ids),
        personalized=personalized,
        resolved_document_ids=resolved_document_ids,
        description=description,
        notebook_name=notebook_name,
        document_name=document_name,
    )


def quiz_evidence_failure_message(scope: QuizScope) -> str:
    """Give a useful next action when retrieval found no matching excerpts."""
    if scope.type.removeprefix("adaptive-") == "global":
        return (
            "No relevant indexed excerpts were found across your study materials. "
            "Try a more specific quiz topic or start from a document or notebook."
        )
    return (
        f'No relevant indexed excerpts were found in "{scope.label}". '
        "Try a topic that appears in this study material or choose another source."
    )


def _scope_type(
    base_type: BaseQuizScopeType,
    personalized: bool,
) -> QuizScopeType:
    if personalized:
        return cast(QuizScopeType, f"adaptive-{base_type}")
    return base_type


def _description(
    base_type: BaseQuizScopeType,
    *,
    label: str,
    count: int,
    personalized: bool,
) -> str:
    if base_type == "global":
        grounded = (
            f"Questions use relevant excerpts from {count} indexed document"
            f"{'s' if count != 1 else ''}."
        )
    elif base_type == "notebook":
        grounded = (
            f'Questions use {count} indexed document'
            f"{'s' if count != 1 else ''} in the \"{label}\" notebook."
        )
    elif base_type == "document":
        grounded = f'Questions use only the indexed document "{label}".'
    elif base_type == "documents":
        grounded = f"Questions use only the {count} selected indexed documents."
    else:
        grounded = f'Questions use the indexed source excerpts for topic "{label}".'

    if not personalized:
        return grounded
    return (
        "Questions focus on relevant previous weaknesses while remaining grounded "
        f"in the selected study material. {grounded}"
    )
