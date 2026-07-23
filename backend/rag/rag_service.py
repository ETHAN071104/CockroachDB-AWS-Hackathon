from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from backend.llm.factory import create_chat_model
from backend.application.dependencies import get_application_dependencies
from backend.memory.service import (
    MemorySearchResult,
    search_memories,
)
from backend.rag.config import RETRIEVAL_K
from backend.rag import config
from backend.rag.vector_store import get_vector_store
from backend.repositories.chroma import ChromaDocumentVectorRepository
from backend.rag.scope import (
    RetrievalScope,
    TopicSourceRepository,
    resolve_retrieval_scope,
)


LOGGER = logging.getLogger("study_companion.rag")


@dataclass(frozen=True)
class RetrievedSource:
    """
    One document chunk returned by Chroma retrieval.
    """

    index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float
    text: str
    document_id: int | None = None
    mime_type: str | None = None
    slide_number: int | None = None


RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a study companion answering questions from uploaded
study material.

You receive two different types of context:

1. Learner memory
   - Use it only to personalize the explanation.
   - It may describe the learner's preferences, current
     understanding, difficulties, or useful procedures.
   - Do not treat learner memory as factual evidence.
   - Do not cite learner memory as a source.
   - Do not mention stored memory unless it is naturally useful.

2. Document excerpts
   - These are the factual sources for the answer.
   - Cite supporting excerpts using [1], [2], and so on.

Rules:

- Use only the supplied document excerpts for factual content.
- Use learner memory only to adjust explanation style, depth,
  examples, or emphasis.
- Do not use outside knowledge.
- Do not cite a source unless it supports the claim.
- Include at least one visible source citation in every factual paragraph.
- If the document excerpts do not contain enough information,
  reply exactly:
  "I could not find sufficient information in the indexed files."
- Keep the answer clear and suitable for a student.
""".strip(),
        ),
        (
            "human",
            """
Relevant learner memory:

{memory_context}

Document excerpts:

{document_context}

Question:

{question}
""".strip(),
        ),
    ]
)


def create_llm() -> BaseChatModel:
    """
    Create the language model through the shared provider factory.

    The selected provider, model, API key and base URL come from
    rag/config.py and the .env file.
    """
    return create_chat_model(
        max_tokens=1200,
        temperature=0,
        max_retries=2,
    )


def retrieve_sources(
    question: str,
    k: int = RETRIEVAL_K,
    scope: RetrievalScope | None = None,
    *,
    topic_source_repository: TopicSourceRepository | None = None,
) -> list[RetrievedSource]:
    """
    Retrieve the nearest document chunks from Chroma.

    Lower Chroma distance values generally indicate closer
    vector matches.
    """
    cleaned_question = question.strip()

    if not cleaned_question:
        raise ValueError("Question cannot be empty.")

    if k <= 0:
        raise ValueError(
            "Retrieval result count must be greater than zero."
        )

    resolved_scope = resolve_retrieval_scope(
        scope,
        topic_source_repository=topic_source_repository,
    )

    if resolved_scope.is_empty:
        return []

    dependencies = get_application_dependencies()
    vectors = (
        ChromaDocumentVectorRepository(get_vector_store)
        if config.PERSISTENCE_BACKEND == "sqlite"
        else dependencies.document_vectors
    )
    raw_results: list[tuple[Document, float]] = vectors.search(
        cleaned_question,
        k,
        resolved_scope.chroma_filter,
    )

    sources: list[RetrievedSource] = []

    for result in raw_results:
        document, raw_distance = result
        metadata = document.metadata

        page_value = metadata.get("page_number")
        slide_value = metadata.get("slide_number")
        chunk_value = metadata.get("chunk_index")
        document_value = metadata.get("document_id")

        page_number: int | None = None
        slide_number: int | None = None
        chunk_index: int | None = None
        document_id: int | None = None

        if isinstance(page_value, (int, float)):
            converted_page = int(page_value)

            if converted_page > 0:
                page_number = converted_page

        if isinstance(slide_value, (int, float)):
            converted_slide = int(slide_value)

            if converted_slide > 0:
                slide_number = converted_slide

        if isinstance(chunk_value, (int, float)):
            converted_chunk = int(chunk_value)

            if converted_chunk >= 0:
                chunk_index = converted_chunk

        if isinstance(document_value, (int, float)):
            converted_document_id = int(document_value)

            if converted_document_id > 0:
                document_id = converted_document_id

        # Chroma collections created before workspace metadata existed remain
        # readable. The authoritative relational repository still prevents a
        # chunk owned by another workspace from being returned.
        if (
            document_id is not None
            and get_application_dependencies().documents.get(document_id) is None
        ):
            continue

        raw_mime_type = metadata.get("mime_type")
        mime_type = (
            raw_mime_type.strip()
            if isinstance(raw_mime_type, str)
            and raw_mime_type.strip()
            else None
        )

        raw_filename = metadata.get("filename")
        filename = (
            raw_filename.strip()
            if isinstance(raw_filename, str)
            and raw_filename.strip()
            else "Unknown file"
        )

        text = document.page_content.strip()

        if not text:
            continue

        sources.append(
            RetrievedSource(
                index=len(sources) + 1,
                filename=filename,
                page_number=page_number,
                chunk_index=chunk_index,
                distance=float(raw_distance),
                text=text,
                document_id=document_id,
                mime_type=mime_type,
                slide_number=slide_number,
            )
        )

    return sources


def format_document_context(
    sources: list[RetrievedSource],
) -> str:
    """
    Format retrieved document chunks for the LLM prompt.
    """
    if not sources:
        return "No document excerpts were retrieved."

    sections: list[str] = []

    for source in sources:
        if source.slide_number is not None:
            location_label = (
                f"Slide: {source.slide_number}"
            )
        elif source.page_number is not None:
            location_label = (
                f"Page: {source.page_number}"
            )
        else:
            location_label = "Location: N/A"

        chunk_label = (
            str(source.chunk_index)
            if source.chunk_index is not None
            else "N/A"
        )

        sections.append(
            "\n".join(
                [
                    f"[{source.index}]",
                    f"File: {source.filename}",
                    location_label,
                    f"Chunk: {chunk_label}",
                    "Content:",
                    source.text,
                ]
            )
        )

    return "\n\n".join(sections)


def format_memory_context(
    memories: list[MemorySearchResult],
) -> str:
    """
    Format learner memories for personalization.

    Memories are not factual document sources and must not
    receive source citation numbers.
    """
    if not memories:
        return "No relevant learner memory was found."

    sections: list[str] = []

    for memory in memories:
        sections.append(
            "\n".join(
                [
                    f"- Type: {memory.memory_type}",
                    f"  Content: {memory.content}",
                    (
                        "  Confidence: "
                        f"{memory.confidence:.2f}"
                    ),
                    (
                        "  Importance: "
                        f"{memory.importance:.2f}"
                    ),
                ]
            )
        )

    return "\n".join(sections)


def extract_response_text(response: Any) -> str:
    """
    Convert a LangChain model response into printable text.

    Different providers may return content as either a string
    or a list of content blocks.
    """
    content = getattr(response, "content", response)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue

            if isinstance(item, dict):
                text_value = item.get("text")

                if isinstance(text_value, str):
                    text_parts.append(text_value)
                    continue

            text_parts.append(str(item))

        return "\n".join(text_parts).strip()

    return str(content).strip()


def answer_question(
    question: str,
    scope: RetrievalScope | None = None,
    *,
    topic_source_repository: TopicSourceRepository | None = None,
) -> tuple[str, list[RetrievedSource]]:
    """
    Answer one independent question.

    There is no conversation history.

    Flow:
    1. Retrieve document chunks.
    2. Retrieve relevant learner memories.
    3. Build the combined prompt.
    4. Call the configured LLM provider.
    5. Return the answer and factual document sources.
    """
    cleaned_question = question.strip()

    if not cleaned_question:
        raise ValueError("Question cannot be empty.")

    # --------------------------------------------------------
    # DOCUMENT RETRIEVAL
    # --------------------------------------------------------

    sources = retrieve_sources(
        question=cleaned_question,
        scope=scope,
        topic_source_repository=topic_source_repository,
    )

    if not sources:
        return (
            "I could not find sufficient information "
            "in the indexed files.",
            [],
        )

    # --------------------------------------------------------
    # LEARNER MEMORY RETRIEVAL
    # --------------------------------------------------------

    try:
        memories = search_memories(
            query=cleaned_question,
            k=3,
        )

    except Exception as error:
        # Memory failure should not stop factual document RAG.
        LOGGER.warning(
            "Learner memory retrieval failed error_type=%s",
            type(error).__name__,
        )

        memories = []

    # --------------------------------------------------------
    # PROMPT CONSTRUCTION
    # --------------------------------------------------------

    document_context = format_document_context(
        sources
    )

    memory_context = format_memory_context(
        memories
    )

    prompt_messages = RAG_PROMPT.format_messages(
        memory_context=memory_context,
        document_context=document_context,
        question=cleaned_question,
    )

    # --------------------------------------------------------
    # LLM GENERATION
    # --------------------------------------------------------

    llm = create_llm()

    response = llm.invoke(
        prompt_messages
    )

    answer = extract_response_text(
        response
    )

    if not answer:
        raise RuntimeError(
            "The configured language model returned "
            "an empty answer."
        )

    return answer, sources
