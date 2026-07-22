from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID

from backend.rag.notebooks import (
    DocumentNotFoundError,
    NotebookNotFoundError,
)
from backend.application.dependencies import get_application_dependencies


ChromaFilter: TypeAlias = dict[str, Any]
TopicSourcePair: TypeAlias = tuple[int, int]


class TopicSourceRepository(Protocol):
    def __call__(
        self,
        topic_id: str,
    ) -> Iterable[object] | None:
        """Return exact (document_id, chunk_index) topic sources."""


class TopicNotFoundError(LookupError):
    """Raised when a requested generated topic does not exist."""


@dataclass(frozen=True)
class RetrievalScope:
    """One optional retrieval selector; no selector means global."""

    notebook_id: int | None = None
    document_ids: tuple[int, ...] | None = None
    topic_id: str | None = None

    def __post_init__(self) -> None:
        selected_count = sum(
            selector is not None
            for selector in (
                self.notebook_id,
                self.document_ids,
                self.topic_id,
            )
        )

        if selected_count != 1:
            raise ValueError(
                "Choose exactly one retrieval scope: notebook, "
                "documents, or topic. Use no scope object for "
                "global retrieval."
            )

        if self.notebook_id is not None:
            _validate_positive_id(
                self.notebook_id,
                label="Notebook ID",
            )

        if self.topic_id is not None:
            object.__setattr__(
                self,
                "topic_id",
                _normalize_topic_id(
                    self.topic_id
                ),
            )

        if self.document_ids is not None:
            if isinstance(self.document_ids, (str, bytes)):
                raise ValueError(
                    "Document IDs must be a sequence of integers."
                )

            try:
                requested_ids = tuple(self.document_ids)
            except TypeError as error:
                raise ValueError(
                    "Document IDs must be a sequence of integers."
                ) from error

            normalized_ids: list[int] = []
            seen_ids: set[int] = set()

            for document_id in requested_ids:
                _validate_positive_id(
                    document_id,
                    label="Document ID",
                )

                if document_id in seen_ids:
                    continue

                seen_ids.add(document_id)
                normalized_ids.append(document_id)

            object.__setattr__(
                self,
                "document_ids",
                tuple(normalized_ids),
            )

@dataclass(frozen=True)
class ResolvedRetrievalScope:
    """Validated scope and its Chroma-compatible metadata filter."""

    kind: Literal[
        "global",
        "notebook",
        "documents",
        "topic",
    ]
    document_ids: tuple[int, ...] = ()
    source_pairs: tuple[TopicSourcePair, ...] = ()
    chroma_filter: ChromaFilter | None = None

    @property
    def is_global(self) -> bool:
        return self.kind == "global"

    @property
    def is_empty(self) -> bool:
        return not self.is_global and self.chroma_filter is None


def resolve_retrieval_scope(
    scope: RetrievalScope | None,
    *,
    topic_source_repository: TopicSourceRepository | None = None,
) -> ResolvedRetrievalScope:
    """Validate a requested scope and build its pre-query filter."""
    if scope is None:
        return ResolvedRetrievalScope(
            kind="global",
        )

    if scope.notebook_id is not None:
        notebooks = get_application_dependencies().notebooks
        notebook = notebooks.get(scope.notebook_id)

        if notebook is None:
            raise NotebookNotFoundError(
                f"Notebook ID {scope.notebook_id} does not exist."
            )

        document_ids = tuple(
            record.id
            for record in notebooks.list_documents(
                notebook_id=scope.notebook_id
            )
        )

        return _resolved_document_scope(
            kind="notebook",
            document_ids=document_ids,
        )

    if scope.document_ids is not None:
        notebooks = get_application_dependencies().notebooks
        for document_id in scope.document_ids:
            if notebooks.get_document(document_id) is None:
                raise DocumentNotFoundError(
                    f"Document ID {document_id} does not exist."
                )

        return _resolved_document_scope(
            kind="documents",
            document_ids=scope.document_ids,
        )

    if scope.topic_id is None:
        raise RuntimeError("Retrieval scope could not be resolved.")

    repository = (
        topic_source_repository
        or _load_topic_source_pairs
    )
    raw_pairs = repository(scope.topic_id)

    if raw_pairs is None:
        raise TopicNotFoundError(
            f"Topic ID {scope.topic_id} does not exist."
        )

    source_pairs = _normalize_topic_source_pairs(
        raw_pairs
    )
    document_ids = tuple(
        dict.fromkeys(
            document_id
            for document_id, _ in source_pairs
        )
    )

    for document_id in document_ids:
        if get_application_dependencies().notebooks.get_document(document_id) is None:
            raise DocumentNotFoundError(
                "A topic source references missing document ID "
                f"{document_id}."
            )

    return ResolvedRetrievalScope(
        kind="topic",
        document_ids=document_ids,
        source_pairs=source_pairs,
        chroma_filter=_build_topic_filter(
            source_pairs
        ),
    )


def _resolved_document_scope(
    *,
    kind: Literal["notebook", "documents"],
    document_ids: Sequence[int],
) -> ResolvedRetrievalScope:
    normalized_ids = tuple(document_ids)

    return ResolvedRetrievalScope(
        kind=kind,
        document_ids=normalized_ids,
        chroma_filter=(
            {
                "document_id": {
                    "$in": list(normalized_ids),
                }
            }
            if normalized_ids
            else None
        ),
    )


def _build_topic_filter(
    source_pairs: Sequence[TopicSourcePair],
) -> ChromaFilter | None:
    pair_filters: list[ChromaFilter] = [
        {
            "$and": [
                {
                    "document_id": {
                        "$eq": document_id,
                    }
                },
                {
                    "chunk_index": {
                        "$eq": chunk_index,
                    }
                },
            ]
        }
        for document_id, chunk_index in source_pairs
    ]

    if not pair_filters:
        return None

    if len(pair_filters) == 1:
        return pair_filters[0]

    return {
        "$or": pair_filters,
    }


def _normalize_topic_source_pairs(
    raw_pairs: Iterable[object],
) -> tuple[TopicSourcePair, ...]:
    normalized_pairs: list[TopicSourcePair] = []
    seen_pairs: set[TopicSourcePair] = set()

    for raw_pair in raw_pairs:
        document_id, chunk_index = _read_topic_source_pair(
            raw_pair
        )
        _validate_positive_id(
            document_id,
            label="Topic source document ID",
        )

        if (
            not isinstance(chunk_index, int)
            or isinstance(chunk_index, bool)
            or chunk_index < 0
        ):
            raise ValueError(
                "Topic source chunk index must be a non-negative "
                "integer."
            )

        pair = (
            document_id,
            chunk_index,
        )

        if pair in seen_pairs:
            continue

        seen_pairs.add(pair)
        normalized_pairs.append(pair)

    return tuple(normalized_pairs)


def _read_topic_source_pair(
    value: object,
) -> TopicSourcePair:
    if isinstance(value, Mapping):
        document_id = value.get("document_id")
        chunk_index = value.get("chunk_index")

    elif (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 2
    ):
        document_id = value[0]
        chunk_index = value[1]

    else:
        document_id = getattr(
            value,
            "document_id",
            None,
        )
        chunk_index = getattr(
            value,
            "chunk_index",
            None,
        )

    if (
        not isinstance(document_id, int)
        or isinstance(document_id, bool)
        or not isinstance(chunk_index, int)
        or isinstance(chunk_index, bool)
    ):
        raise ValueError(
            "Topic sources must contain integer document_id and "
            "chunk_index values."
        )

    return document_id, chunk_index


def _validate_positive_id(
    value: object,
    *,
    label: str,
) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(
            f"{label} must be a positive integer."
        )


def _load_topic_source_pairs(
    topic_id: str,
) -> Iterable[object] | None:
    from backend.application.dependencies import get_application_dependencies

    topic = get_application_dependencies().intelligence.get_topic(topic_id)
    if topic is None:
        return None
    return topic.sources


def _normalize_topic_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Topic ID must be a canonical UUID string."
        )

    normalized = value.strip()

    try:
        canonical = str(UUID(normalized))
    except (ValueError, AttributeError) as error:
        raise ValueError(
            "Topic ID must be a canonical UUID string."
        ) from error

    if normalized != canonical:
        raise ValueError(
            "Topic ID must use canonical lowercase UUID format."
        )

    return canonical
