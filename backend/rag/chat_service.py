from __future__ import annotations

import logging
from dataclasses import dataclass

import backend.memory.proposals as memory_proposals
import backend.rag.rag_service as rag_service
from backend.application.dependencies import get_application_dependencies
from backend.memory.proposals import PendingMemoryProposal
from backend.rag.scope import (
    RetrievalScope,
    TopicSourceRepository,
    resolve_retrieval_scope,
)
from backend.study.database import (
    StoredInteractionSource,
    StoredStudyInteraction,
    StoredStudySession,
    StudySourceInput,
)


LOGGER = logging.getLogger("study_companion.chat")
MAX_SOURCE_EXCERPT_LENGTH = 2_000


@dataclass(frozen=True)
class ChatResult:
    """One grounded answer after its study lineage is persisted."""

    session: StoredStudySession
    interaction: StoredStudyInteraction
    sources: tuple[StoredInteractionSource, ...]
    memory_proposal: PendingMemoryProposal | None


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
    resolve_retrieval_scope(
        scope,
        topic_source_repository=topic_source_repository,
    )
    dependencies = get_application_dependencies()
    session = dependencies.study_sessions.get_or_create_active()
    answer, retrieved_sources = rag_service.answer_question(
        cleaned_question,
        scope=scope,
        topic_source_repository=topic_source_repository,
    )

    source_inputs = [
        _study_source_input(source)
        for source in retrieved_sources
    ]
    def persist(_unit_of_work):
        return dependencies.study_sessions.insert_interaction_with_sources(
            session_id=session.id,
            question=cleaned_question,
            answer=answer,
            sources=source_inputs,
            outcome="unrated",
        )
    interaction, stored_sources = dependencies.unit_of_work().run(persist)

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
    )


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
