from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

import backend.memory.proposals as memory_proposals
import backend.rag.rag_service as rag_service
from backend.application.dependencies import get_application_dependencies
from backend.memory.proposals import PendingMemoryProposal
from backend.rag.scope import (
    RetrievalScope,
    ResolvedRetrievalScope,
    TopicSourceRepository,
    resolve_retrieval_scope,
)
from backend.rag.chat_intent import (
    ChatIntent,
    FeatureRedirect,
    classify_chat_intent,
    route_suggestion,
)
from backend.study.database import (
    StoredInteractionSource,
    StoredStudyInteraction,
    StoredStudySession,
    StudySourceInput,
)


LOGGER = logging.getLogger("study_companion.chat")
MAX_SOURCE_EXCERPT_LENGTH = 2_000
INSUFFICIENT_ANSWER = "I could not find sufficient information in the indexed files."
DOCUMENT_REPHRASING = (
    "Create a recommended learning order for the concepts in the selected study material."
)

ChatResponseType: TypeAlias = Literal["answer", "feature_redirect"]
ChatEvidenceStatus: TypeAlias = Literal[
    "grounded",
    "no_documents_indexed",
    "no_relevant_chunks",
    "retrieved_chunks_insufficient",
    "personal_performance_request",
    "planning_request",
    "citation_validation_failed",
    "unsupported_claims",
]


@dataclass(frozen=True)
class ChatResult:
    """One grounded answer after its study lineage is persisted."""

    session: StoredStudySession
    interaction: StoredStudyInteraction
    sources: tuple[StoredInteractionSource, ...]
    memory_proposal: PendingMemoryProposal | None
    type: ChatResponseType
    intent: ChatIntent
    evidence_status: ChatEvidenceStatus
    redirect: FeatureRedirect | None = None
    suggested_question: str | None = None


def run_chat(
    question: str,
    scope: RetrievalScope | None = None,
    *,
    topic_source_repository: TopicSourceRepository | None = None,
) -> ChatResult:
    """Answer, persist the interaction atomically, then propose memory."""
    cleaned_question = question.strip()

    if not cleaned_question:
        raise ValueError("Question cannot be empty.")

    # Validate referenced notebooks, documents, and topics before creating
    # session state. The RAG service resolves again immediately before its
    # vector query so filtering remains enforced at the retrieval boundary.
    resolved_scope = resolve_retrieval_scope(
        scope,
        topic_source_repository=topic_source_repository,
    )
    dependencies = get_application_dependencies()
    session = dependencies.study_sessions.get_or_create_active()
    intent = classify_chat_intent(cleaned_question)
    redirect = route_suggestion(intent, cleaned_question)

    if redirect is not None:
        interaction, stored_sources = _persist_chat_interaction(
            session_id=session.id,
            question=cleaned_question,
            answer=redirect.message,
            sources=[],
        )
        return ChatResult(
            session=session,
            interaction=interaction,
            sources=tuple(stored_sources),
            memory_proposal=None,
            type="feature_redirect",
            intent=intent,
            evidence_status=(
                "planning_request"
                if redirect.target == "study-plan"
                else "personal_performance_request"
            ),
            redirect=redirect,
        )

    retrieved_sources: list[rag_service.RetrievedSource] = []
    suggested_question: str | None = None
    if not _scope_has_indexed_documents(resolved_scope):
        answer = (
            "No indexed study material is available in this scope. "
            "Upload or index a document, then ask again."
        )
        evidence_status: ChatEvidenceStatus = "no_documents_indexed"
    else:
        answer, retrieved_sources = rag_service.answer_question(
            cleaned_question,
            scope=scope,
            topic_source_repository=topic_source_repository,
        )
        evidence_status, answer = _validate_grounded_answer(
            answer,
            retrieved_sources,
        )
        if evidence_status != "grounded":
            suggested_question = DOCUMENT_REPHRASING

    interaction, stored_sources = _persist_chat_interaction(
        session_id=session.id,
        question=cleaned_question,
        answer=answer,
        sources=retrieved_sources,
    )

    proposal: PendingMemoryProposal | None = None

    try:
        proposal = memory_proposals.create_memory_proposal(
            user_message=cleaned_question,
            assistant_answer=answer,
        )
    except Exception as error:
        # Proposal generation is optional. The grounded chat record has
        # already been committed and must remain available on failure.
        LOGGER.warning(
            "Memory proposal generation failed error_type=%s",
            type(error).__name__,
        )

    return ChatResult(
        session=session,
        interaction=interaction,
        sources=tuple(stored_sources),
        memory_proposal=proposal,
        type="answer",
        intent=intent,
        evidence_status=evidence_status,
        suggested_question=suggested_question,
    )


def _persist_chat_interaction(
    *,
    session_id: int,
    question: str,
    answer: str,
    sources: list[rag_service.RetrievedSource],
) -> tuple[StoredStudyInteraction, list[StoredInteractionSource]]:
    dependencies = get_application_dependencies()
    source_inputs = [_study_source_input(source) for source in sources]

    def persist(_unit_of_work):
        return dependencies.study_sessions.insert_interaction_with_sources(
            session_id=session_id,
            question=question,
            answer=answer,
            sources=source_inputs,
            outcome="unrated",
        )

    return dependencies.unit_of_work().run(persist)


def _scope_has_indexed_documents(scope: ResolvedRetrievalScope) -> bool:
    notebooks = get_application_dependencies().notebooks
    documents = (
        notebooks.list_documents()
        if scope.is_global
        else [
            document
            for document_id in scope.document_ids
            if (document := notebooks.get_document(document_id)) is not None
        ]
    )
    return any(int(document.chunk_count) > 0 for document in documents)


def _validate_grounded_answer(
    answer: str,
    sources: list[rag_service.RetrievedSource],
) -> tuple[ChatEvidenceStatus, str]:
    cleaned = answer.strip()
    if not sources:
        return (
            "no_relevant_chunks",
            "No relevant excerpts were found in the selected study material. "
            "Try a narrower question or choose another source.",
        )
    if cleaned == INSUFFICIENT_ANSWER:
        return (
            "retrieved_chunks_insufficient",
            "Relevant excerpts were retrieved, but they do not contain enough "
            "information to answer this question safely.",
        )

    cited_indexes = {
        int(value)
        for value in re.findall(r"\[(\d+)\]", cleaned)
    }
    valid_indexes = {source.index for source in sources}
    if not cited_indexes or not cited_indexes.issubset(valid_indexes):
        return (
            "citation_validation_failed",
            "Relevant material was found, but the generated answer did not pass "
            "citation validation. Retry or ask a narrower question.",
        )

    unsupported_paragraphs = [
        paragraph
        for paragraph in re.split(r"\n\s*\n", cleaned)
        if len(re.findall(r"\b\w+\b", paragraph, flags=re.UNICODE)) >= 8
        and not any(f"[{index}]" in paragraph for index in cited_indexes)
    ]
    if unsupported_paragraphs:
        return (
            "unsupported_claims",
            "Relevant material was found, but the generated answer contained "
            "claims without document citations. Retry or ask a narrower question.",
        )
    return "grounded", cleaned


def _study_source_input(
    source: rag_service.RetrievedSource,
) -> StudySourceInput:
    notebook_id = (
        get_application_dependencies().notebooks.get_document_notebook_id(
            source.document_id
        )
        if source.document_id is not None
        else None
    )
    excerpt = source.text.strip()

    if len(excerpt) > MAX_SOURCE_EXCERPT_LENGTH:
        excerpt = excerpt[:MAX_SOURCE_EXCERPT_LENGTH].rstrip()

    return StudySourceInput(
        source_index=source.index,
        document_id=source.document_id,
        notebook_id=notebook_id,
        filename=source.filename,
        mime_type=source.mime_type,
        page_number=source.page_number,
        slide_number=source.slide_number,
        chunk_index=source.chunk_index,
        distance=source.distance,
        excerpt=excerpt or None,
    )
