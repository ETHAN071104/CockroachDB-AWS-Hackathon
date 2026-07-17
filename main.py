from __future__ import annotations
from memory.database import initialize_memory_database
import sys
from pathlib import Path
from memory.extractor import propose_memory_candidate
from memory.validator import validate_memory_candidate
from rag.config import ENABLE_MEMORY_PROPOSALS
from memory.duplicate_detector import (
    find_duplicate_memory,
)

from rag.database import (
    delete_document_record,
    initialize_database,
    list_documents,
)
from rag.ingestion import index_file
from rag.rag_service import (
    RetrievedSource,
    answer_question,
)

from rag.vector_store import delete_document_vectors

from memory.service import (
    add_memory,
    archive_memory,
    delete_memory,
    get_all_memories,
    search_memories,
    update_memory,
)

def handle_memory_proposal(
    user_message: str,
    assistant_answer: str,
) -> None:
    """
    Extract, validate, deduplicate and optionally save one
    learner memory.
    """

    if not ENABLE_MEMORY_PROPOSALS:
        return

    # ========================================================
    # STEP 1: LLM EXTRACTION
    # ========================================================

    try:
        candidate = propose_memory_candidate(
            user_message=user_message,
            assistant_answer=assistant_answer,
        )

    except Exception as error:
        print(
            "\nMemory proposal skipped because extraction "
            f"failed: {error}"
        )
        return

    # ========================================================
    # STEP 2: DETERMINISTIC VALIDATION
    # ========================================================

    validation = validate_memory_candidate(candidate)

    if not validation.accepted:
        if candidate.should_store:
            print(
                "\nMemory proposal rejected by validator:"
            )
            print(validation.reason)
        else:
            print(
                "\nNo durable learner memory detected "
                "from this interaction."
            )

        return

    # ========================================================
    # STEP 3: DUPLICATE DETECTION
    # ========================================================

    try:
        duplicate_result = find_duplicate_memory(
            candidate
        )

    except Exception as error:
        # Duplicate checking failure should not silently save
        # an unchecked memory.
        print(
            "\nMemory proposal skipped because duplicate "
            f"detection failed: {error}"
        )
        return

    if duplicate_result.is_duplicate:
        existing = duplicate_result.existing_memory

        print("\n" + "=" * 60)
        print("DUPLICATE MEMORY DETECTED")
        print("=" * 60)

        if existing is not None:
            print(f"Existing memory ID: {existing.memory_id}")
            print(f"Type: {existing.memory_type}")
            print(f"Content: {existing.content}")
            print(f"Distance: {existing.distance:.4f}")

        print(f"Reason: {duplicate_result.reason}")
        print(
            "\nThe proposed memory was not saved because an "
            "equivalent active memory already exists."
        )

        return

    # ========================================================
    # STEP 4: DISPLAY CANDIDATE
    # ========================================================

    print("\n" + "=" * 60)
    print("PROPOSED LEARNER MEMORY")
    print("=" * 60)
    print(f"Type: {candidate.memory_type}")
    print(f"Content: {candidate.content}")
    print(f"Confidence: {candidate.confidence:.2f}")
    print(f"Importance: {candidate.importance:.2f}")
    print(f"Reason: {candidate.reason}")

    if duplicate_result.existing_memory is not None:
        closest = duplicate_result.existing_memory

        print("\nClosest existing same-type memory:")
        print(f"ID: {closest.memory_id}")
        print(f"Content: {closest.content}")
        print(f"Distance: {closest.distance:.4f}")
        print(
            "This distance is outside the configured duplicate "
            "threshold, so the candidate is treated as distinct."
        )

    # ========================================================
    # STEP 5: USER CONFIRMATION
    # ========================================================

    confirmation = input(
        "\nSave this memory? [y/N]: "
    ).strip().lower()

    if confirmation not in {"y", "yes"}:
        print("Memory discarded.")
        return

    # ========================================================
    # STEP 6: SAVE TO SQLITE AND CHROMA
    # ========================================================

    try:
        saved_memory = add_memory(
            memory_type=candidate.memory_type,
            content=candidate.content,
            confidence=candidate.confidence,
            importance=candidate.importance,
        )

        print(
            f"Memory saved successfully with ID "
            f"{saved_memory.id}."
        )

    except Exception as error:
        print(
            f"Could not save proposed memory: {error}"
        )

def quit_program() -> None:
    """
    Exit the terminal application cleanly.
    """
    print("\nClosing Local Study Companion RAG.")
    print("Goodbye.")

    raise SystemExit(0)

def delete_memory_interface() -> None:
    list_memories_interface()

    raw_id = input(
        "\nEnter memory ID to permanently delete: "
    ).strip()

    try:
        memory_id = int(raw_id)

        confirmation = input(
            "Type DELETE to confirm: "
        ).strip()

        if confirmation != "DELETE":
            print("Deletion cancelled.")
            return

        if delete_memory(memory_id):
            print("Memory permanently deleted.")
        else:
            print("Memory was not found.")

    except Exception as error:
        print(f"\nDeletion failed: {error}")

def archive_memory_interface() -> None:
    list_memories_interface()

    raw_id = input(
        "\nEnter memory ID to archive "
        "or type /back: "
    ).strip()

    if raw_id.lower() == "/back":
        print("Archive cancelled.")
        return

    try:
        memory_id = int(raw_id)
    except ValueError:
        print("Memory ID must be a number.")
        return

    confirmation = input(
        "Type ARCHIVE to confirm "
        "or /back to cancel: "
    ).strip()

    if confirmation.lower() == "/back":
        print("Archive cancelled.")
        return

    if confirmation != "ARCHIVE":
        print("Archive cancelled.")
        return

    try:
        if archive_memory(memory_id):
            print("Memory archived.")
        else:
            print("Memory was not found.")

    except Exception as error:
        print(f"\nArchive failed: {error}")

def update_memory_interface() -> None:
    list_memories_interface()

    raw_id = input(
        "\nEnter memory ID to update: "
    ).strip()

    try:
        memory_id = int(raw_id)

        memory_type = input(
            "New memory type: "
        ).strip()

        content = input(
            "New memory content: "
        ).strip()

        confidence = float(
            input("New confidence 0.0-1.0: ").strip()
        )

        importance = float(
            input("New importance 0.0-1.0: ").strip()
        )

        memory = update_memory(
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
        )

        print("\nMemory updated.")
        print(f"ID: {memory.id}")
        print(f"Content: {memory.content}")

    except Exception as error:
        print(f"\nMemory update failed: {error}")

def list_memories_interface() -> None:
    print("\nLEARNER MEMORIES")

    memories = get_all_memories(
        include_archived=True
    )

    if not memories:
        print("No learner memories stored.")
        return

    for memory in memories:
        print("\n" + "-" * 60)
        print(f"ID: {memory.id}")
        print(f"Type: {memory.memory_type}")
        print(f"Status: {memory.status}")
        print(f"Confidence: {memory.confidence:.2f}")
        print(f"Importance: {memory.importance:.2f}")
        print(f"Content: {memory.content}")
        print(f"Updated: {memory.updated_at}")

def search_memory_interface() -> None:
    print("\nSEARCH LEARNER MEMORY")

    query = input("Search query: ").strip()

    try:
        results = search_memories(query)

        if not results:
            print("No relevant memories found.")
            return

        for result in results:
            print("\n" + "-" * 60)
            print(f"Memory ID: {result.memory_id}")
            print(f"Type: {result.memory_type}")
            print(f"Confidence: {result.confidence:.2f}")
            print(f"Importance: {result.importance:.2f}")
            print(f"Distance: {result.distance:.4f}")
            print(f"Content: {result.content}")

    except Exception as error:
        print(f"\nMemory search failed: {error}")

def add_memory_interface() -> None:
    print("\nADD LEARNER MEMORY")
    print("Types:")
    print("- profile")
    print("- learning_state")
    print("- episodic")
    print("- procedural")

    memory_type = input("Memory type: ").strip()
    content = input("Memory content: ").strip()

    try:
        raw_confidence = input(
            "Confidence 0.0-1.0 [1.0]: "
        ).strip()

        raw_importance = input(
            "Importance 0.0-1.0 [0.5]: "
        ).strip()

        confidence = (
            float(raw_confidence)
            if raw_confidence
            else 1.0
        )

        importance = (
            float(raw_importance)
            if raw_importance
            else 0.5
        )

        memory = add_memory(
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
        )

        print("\nMemory saved.")
        print(f"ID: {memory.id}")
        print(f"Type: {memory.memory_type}")
        print(f"Content: {memory.content}")

    except Exception as error:
        print(f"\nCould not save memory: {error}")




def print_header() -> None:
    print("\n" + "=" * 60)
    print("LOCAL STUDY COMPANION RAG")
    print("=" * 60)


def print_menu() -> None:
    print("\nChoose an option:")
    print("1. Add and index a file")
    print("2. Chat with indexed files")
    print("3. List indexed files")
    print("4. Delete an indexed file")
    print("5. Add learner memory")
    print("6. Search learner memory")
    print("7. List learner memories")
    print("8. Update learner memory")
    print("9. Archive learner memory")
    print("10. Delete learner memory")
    print("11. Quit")


def add_file_interface() -> None:
    print("\nADD FILE")
    print("Supported formats: PDF and TXT")

    raw_path = input("Enter the full file path: ").strip()

    # Permit Windows paths pasted with surrounding quotation marks.
    raw_path = raw_path.strip('"').strip("'")

    if not raw_path:
        print("No file path entered.")
        return

    try:
        print("\nReading and indexing file...")
        result = index_file(raw_path)

        if result["status"] == "duplicate":
            print("\nThis file has already been indexed.")
            print(f"Document ID: {result['document_id']}")
            print(f"Filename: {result['filename']}")
            print(f"Chunks: {result['chunk_count']}")
            return

        print("\nFile indexed successfully.")
        print(f"Document ID: {result['document_id']}")
        print(f"Filename: {result['filename']}")
        print(f"Pages/sections loaded: {result['pages']}")
        print(f"Chunks created: {result['chunk_count']}")

    except Exception as error:
        print(f"\nIndexing failed: {error}")


def print_sources(sources: list[RetrievedSource]) -> None:
    if not sources:
        print("\nNo sources were retrieved.")
        return

    print("\nRetrieved sources:")

    for source in sources:
        print("\n" + "-" * 60)
        print(f"[{source.index}] {source.filename}")

        if source.page_number is not None:
            print(f"Page: {source.page_number}")

        if source.chunk_index is not None:
            print(f"Chunk: {source.chunk_index}")

        print(f"Distance: {source.distance:.4f}")
        print("-" * 60)
        print(source.text)


def chat_interface() -> None:
    documents = list_documents()

    if not documents:
        print("\nNo files are indexed.")
        print("Use option 1 to add a file first.")
        return

    print("\nCHAT MODE")
    print("Each question is independent.")
    print("Commands:")
    print("  /sources  Show sources from the latest answer")
    print("  /back     Return to the main menu")

    latest_sources: list[RetrievedSource] = []

    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not question:
            continue

        command = question.lower()

        if command == "/back":
            return

        if command == "/sources":
            print_sources(latest_sources)
            continue

        try:
            answer, latest_sources = answer_question(question)

            print("\nAssistant:")
            print(answer)

            if latest_sources:
                source_labels = []

                for source in latest_sources:
                    if source.page_number is not None:
                        source_labels.append(
                            f"[{source.index}] "
                            f"{source.filename}, "
                            f"page {source.page_number}"
                        )
                    else:
                        source_labels.append(
                            f"[{source.index}] "
                            f"{source.filename}"
                        )

                print("\nSources retrieved:")
                for label in source_labels:
                    print(f"- {label}")
            handle_memory_proposal(
                user_message=question,
                assistant_answer=answer,
            )

        except Exception as error:
            print(f"\nQuestion failed: {error}")


def list_files_interface() -> None:
    documents = list_documents()

    print("\nINDEXED FILES")

    if not documents:
        print("No files have been indexed.")
        return

    for document in documents:
        print("\n" + "-" * 60)
        print(f"ID: {document.id}")
        print(f"Filename: {document.filename}")
        print(f"Type: {document.mime_type}")
        print(f"Chunks: {document.chunk_count}")
        print(f"Added: {document.created_at}")


def delete_file_interface() -> None:
    documents = list_documents()

    if not documents:
        print("\nNo indexed files to delete.")
        return

    list_files_interface()

    raw_document_id = input(
        "\nEnter the document ID to delete: "
    ).strip()

    try:
        document_id = int(raw_document_id)
    except ValueError:
        print("Document ID must be a number.")
        return

    selected_document = next(
        (
            document
            for document in documents
            if document.id == document_id
        ),
        None,
    )

    if selected_document is None:
        print(f"Document ID {document_id} was not found.")
        return

    confirmation = input(
        f'Type DELETE to remove "{selected_document.filename}": '
    ).strip()

    if confirmation != "DELETE":
        print("Deletion cancelled.")
        return

    try:
        # Delete vectors first. If this fails, retain the SQLite file.
        delete_document_vectors(document_id)

        deleted = delete_document_record(document_id)

        if not deleted:
            print("The SQLite record was not found.")
            return

        print(f'Deleted "{selected_document.filename}".')

    except Exception as error:
        print(f"Deletion failed: {error}")


def main() -> None:
    initialize_memory_database()
    initialize_database()
    print_header()

    while True:
        print_menu()

        try:
            choice = input("\nSelection: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return
        
        if choice == "1":
            add_file_interface()
        elif choice == "2":
            chat_interface()
        elif choice == "3":
            list_files_interface()
        elif choice == "4":
            delete_file_interface()
        elif choice == "5":
            add_memory_interface()
        elif choice == "6":
            search_memory_interface()
        elif choice == "7":
            list_memories_interface()
        elif choice == "8":
            update_memory_interface()
        elif choice == "9":
            archive_memory_interface()
        elif choice == "10":
            delete_memory_interface()
        elif choice == "11":
            quit_program()
        else:
            print("Invalid selection. Enter a number from 1 to 11.")
    


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped.")
        sys.exit(0)