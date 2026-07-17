from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from llm.factory import create_chat_model
from memory.service import (
    MemorySearchResult,
    search_memories,
)
from rag.config import RETRIEVAL_K
from rag.vector_store import get_vector_store


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

    vector_store = get_vector_store()

    raw_results: list[tuple[Document, float]] = (
        vector_store.similarity_search_with_score(
            query=cleaned_question,
            k=k,
        )
    )

    sources: list[RetrievedSource] = []

    for index, result in enumerate(
        raw_results,
        start=1,
    ):
        document, raw_distance = result
        metadata = document.metadata

        page_value = metadata.get("page_number")
        chunk_value = metadata.get("chunk_index")

        page_number: int | None = None
        chunk_index: int | None = None

        if isinstance(page_value, (int, float)):
            converted_page = int(page_value)

            if converted_page > 0:
                page_number = converted_page

        if isinstance(chunk_value, (int, float)):
            chunk_index = int(chunk_value)

        filename = str(
            metadata.get(
                "filename",
                metadata.get(
                    "source",
                    "Unknown file",
                ),
            )
        )

        text = document.page_content.strip()

        if not text:
            continue

        sources.append(
            RetrievedSource(
                index=index,
                filename=filename,
                page_number=page_number,
                chunk_index=chunk_index,
                distance=float(raw_distance),
                text=text,
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
        page_label = (
            str(source.page_number)
            if source.page_number is not None
            else "N/A"
        )

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
                    f"Page: {page_label}",
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
        print(
            "\nWarning: learner memory retrieval failed: "
            f"{error}"
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