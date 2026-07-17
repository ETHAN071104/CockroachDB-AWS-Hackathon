from __future__ import annotations
from memory.database import initialize_memory_database
import sys
from study.database import initialize_study_database
from pathlib import Path
from memory.extractor import propose_memory_candidate
from memory.validator import validate_memory_candidate
from rag.config import ENABLE_MEMORY_PROPOSALS
from memory.conflict_detector import (
    detect_memory_conflict,
)
from memory.models import MemoryCandidate
from memory.consolidator import (
    propose_memory_consolidation,
)
from memory.service import (
    add_memory,
    apply_memory_consolidation,
    archive_memory,
    delete_memory,
    get_all_memories,
    replace_memory_with_candidate,
    search_memories,
    update_memory,
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
    replace_memory_with_candidate,
    search_memories,
    update_memory,
)

from study.database import (
    StudySourceInput,
    end_study_session,
    get_or_create_active_study_session,
    initialize_study_database,
    insert_study_interaction_with_sources,
    list_study_sessions,
    update_interaction_outcome,
)

from study.progress import (
    build_progress_report,
    format_progress_report,
)
from study.reporting import (
    build_session_report,
    format_session_report,
)
from study.summarizer import (
    generate_session_summary,
)
from study.recommendations import (
    build_review_queue,
    format_review_queue,
)
from study.reviewer import (
    format_review_action,
    generate_review_action,
)
from study.quiz_generator import (
    format_grounded_quiz,
    generate_grounded_quiz,
)
from study.quiz_runner import (
    format_quiz_run_result,
    run_quiz_interactively,
)

def save_candidate_memory(
    candidate: MemoryCandidate,
) -> None:
    """
    Save one confirmed memory candidate.
    """
    try:
        saved_memory = add_memory(
            memory_type=candidate.memory_type,
            content=candidate.content,
            confidence=candidate.confidence,
            importance=candidate.importance,
        )

        print(
            "\nMemory saved successfully with ID "
            f"{saved_memory.id}."
        )

    except Exception as error:
        print(
            f"\nCould not save proposed memory: {error}"
        )


def replace_existing_with_candidate(
    existing_memory_id: int,
    candidate: MemoryCandidate,
) -> None:
    """
    Archive an existing memory and save the candidate as its
    active replacement.
    """
    try:
        result = replace_memory_with_candidate(
            existing_memory_id=existing_memory_id,
            memory_type=candidate.memory_type,
            content=candidate.content,
            confidence=candidate.confidence,
            importance=candidate.importance,
        )

        print("\nMemory replaced successfully.")
        print(
            "Archived memory ID: "
            f"{result.archived_memory.id}"
        )
        print(
            "New active memory ID: "
            f"{result.new_memory.id}"
        )

    except Exception as error:
        print(
            f"\nMemory replacement failed: {error}"
        )

def handle_memory_proposal(
    user_message: str,
    assistant_answer: str,
) -> None:
    """
    Extract, validate, classify and optionally store one
    learner memory.
    """
    if not ENABLE_MEMORY_PROPOSALS:
        return

    # ========================================================
    # STEP 1: EXTRACT CANDIDATE
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
    # STEP 2: VALIDATE CANDIDATE
    # ========================================================

    validation = validate_memory_candidate(
        candidate
    )

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
    # STEP 3: CLASSIFY RELATIONSHIP
    # ========================================================

    try:
        conflict_result = detect_memory_conflict(
            candidate
        )

    except Exception as error:
        # Never save an unchecked candidate automatically.
        print(
            "\nMemory proposal skipped because relationship "
            f"classification failed: {error}"
        )
        return

    existing = conflict_result.existing_memory

    # ========================================================
    # STEP 4: HANDLE DUPLICATE
    # ========================================================

    if conflict_result.conflict_type == "duplicate":
        print("\n" + "=" * 60)
        print("DUPLICATE MEMORY DETECTED")
        print("=" * 60)

        if existing is not None:
            print(
                f"Existing memory ID: "
                f"{existing.memory_id}"
            )
            print(
                f"Type: {existing.memory_type}"
            )
            print(
                f"Content: {existing.content}"
            )
            print(
                f"Distance: {existing.distance:.4f}"
            )

        print(
            f"Reason: {conflict_result.reason}"
        )
        print(
            "\nThe proposed memory was not saved because an "
            "equivalent active memory already exists."
        )

        return

    # ========================================================
    # STEP 5: DISPLAY CANDIDATE
    # ========================================================

    print("\n" + "=" * 60)
    print("PROPOSED LEARNER MEMORY")
    print("=" * 60)
    print(
        f"Relationship: "
        f"{conflict_result.conflict_type}"
    )
    print(
        f"Relationship confidence: "
        f"{conflict_result.confidence:.2f}"
    )
    print(
        f"Type: {candidate.memory_type}"
    )
    print(
        f"Content: {candidate.content}"
    )
    print(
        f"Candidate confidence: "
        f"{candidate.confidence:.2f}"
    )
    print(
        f"Importance: {candidate.importance:.2f}"
    )
    print(
        f"Candidate reason: {candidate.reason}"
    )
    print(
        f"Relationship reason: "
        f"{conflict_result.reason}"
    )

    if existing is not None:
        print("\nRelated existing memory:")
        print(
            f"ID: {existing.memory_id}"
        )
        print(
            f"Type: {existing.memory_type}"
        )
        print(
            f"Content: {existing.content}"
        )
        print(
            f"Distance: {existing.distance:.4f}"
        )

    # ========================================================
    # STEP 6: HANDLE NEW MEMORY
    # ========================================================

    if conflict_result.conflict_type == "new":
        confirmation = input(
            "\nSave this new memory? [y/N]: "
        ).strip().lower()

        if confirmation not in {"y", "yes"}:
            print("Memory discarded.")
            return

        save_candidate_memory(candidate)
        return

    # ========================================================
    # STEP 7: HANDLE REFINEMENT OR CONTRADICTION
    # ========================================================

    if existing is None:
        print(
            "\nThe classifier reported a relationship but did "
            "not return an existing memory. Nothing was saved."
        )
        return

    print("\nChoose how to handle this memory:")
    print("1. Keep the existing memory")
    print("2. Replace the existing memory")
    print("3. Keep both memories")
    print("4. Cancel")

    choice = input(
        "\nSelection: "
    ).strip()

    if choice == "1":
        print(
            "Existing memory retained. "
            "The proposed memory was discarded."
        )
        return

    if choice == "2":
        confirmation = input(
            "Type REPLACE to confirm: "
        ).strip()

        if confirmation != "REPLACE":
            print("Replacement cancelled.")
            return

        replace_existing_with_candidate(
            existing_memory_id=existing.memory_id,
            candidate=candidate,
        )
        return

    if choice == "3":
        confirmation = input(
            "Type BOTH to keep both memories: "
        ).strip()

        if confirmation != "BOTH":
            print("Save cancelled.")
            return

        save_candidate_memory(candidate)
        return

    if choice == "4":
        print("Memory decision cancelled.")
        return

    print(
        "Invalid selection. The proposed memory was not saved."
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

def consolidate_memories_interface() -> None:
    """
    Select active memories, generate a consolidation proposal,
    and apply it only after explicit confirmation.
    """
    print("\nCONSOLIDATE LEARNER MEMORIES")

    memories = get_all_memories(
        include_archived=False
    )

    if len(memories) < 2:
        print(
            "At least two active memories are required."
        )
        return

    print(
        "\nOnly active memories of the same type "
        "can be consolidated."
    )

    for memory in memories:
        print("\n" + "-" * 60)
        print(f"ID: {memory.id}")
        print(f"Type: {memory.memory_type}")
        print(f"Confidence: {memory.confidence:.2f}")
        print(f"Importance: {memory.importance:.2f}")
        print(f"Content: {memory.content}")

    raw_ids = input(
        "\nEnter memory IDs separated by commas "
        "or type /back: "
    ).strip()

    if raw_ids.lower() == "/back":
        print("Consolidation cancelled.")
        return

    try:
        memory_ids = [
            int(part.strip())
            for part in raw_ids.split(",")
            if part.strip()
        ]

    except ValueError:
        print(
            "Every memory ID must be a number."
        )
        return

    try:
        print(
            "\nGenerating consolidation proposal..."
        )

        proposal = propose_memory_consolidation(
            memory_ids
        )

    except Exception as error:
        print(
            f"\nCould not generate consolidation "
            f"proposal: {error}"
        )
        return

    candidate = proposal.candidate

    print("\n" + "=" * 60)
    print("MEMORY CONSOLIDATION REVIEW")
    print("=" * 60)

    print("\nSelected source memories:")

    for memory in proposal.source_memories:
        print("\n" + "-" * 60)
        print(f"ID: {memory.id}")
        print(f"Type: {memory.memory_type}")
        print(f"Content: {memory.content}")

    if not candidate.should_consolidate:
        print("\nConsolidation rejected.")
        print(f"Reason: {candidate.reason}")
        print(
            "\nNo memories were changed."
        )
        return

    print("\nProposed consolidated memory:")
    print(f"Type: {candidate.memory_type}")
    print(f"Content: {candidate.content}")
    print(f"Confidence: {candidate.confidence:.2f}")
    print(f"Importance: {candidate.importance:.2f}")
    print(f"Reason: {candidate.reason}")

    confirmation = input(
        "\nType CONSOLIDATE to archive the source "
        "memories and save this result: "
    ).strip()

    if confirmation != "CONSOLIDATE":
        print(
            "Consolidation cancelled. "
            "No memories were changed."
        )
        return

    try:
        result = apply_memory_consolidation(
            proposal
        )

    except Exception as error:
        print(
            f"\nConsolidation failed: {error}"
        )
        return

    print("\nMemory consolidation completed.")
    print(
        "Archived source IDs: "
        + ", ".join(
            str(memory.id)
            for memory in result.source_memories
        )
    )
    print(
        "New consolidated memory ID: "
        f"{result.consolidated_memory.id}"
    )
    print(
        "Content: "
        f"{result.consolidated_memory.content}"
    )

def list_study_sessions_interface() -> None:
    """
    Display every stored study session.
    """
    sessions = list_study_sessions()

    print("\nSTUDY SESSIONS")

    if not sessions:
        print("No study sessions found.")
        return

    for session in sessions:
        print("\n" + "-" * 60)
        print(f"Session ID: {session.id}")
        print(f"Status: {session.status}")
        print(f"Started: {session.started_at}")
        print(
            "Ended: "
            f"{session.ended_at or 'Not completed'}"
        )

def select_study_session_id(
    *,
    completed_only: bool = False,
) -> int | None:
    """
    Show available sessions and ask the user to select one.

    Returns None when the user enters /back.
    """
    sessions = list_study_sessions()

    if completed_only:
        sessions = [
            session
            for session in sessions
            if session.status == "completed"
        ]

    if not sessions:
        if completed_only:
            print(
                "No completed study sessions were found."
            )
        else:
            print("No study sessions were found.")

        return None

    print("\nAvailable study sessions:")

    for session in sessions:
        print(
            f"- ID {session.id}: "
            f"{session.status}, "
            f"started {session.started_at}"
        )

    raw_id = input(
        "\nEnter session ID or /back: "
    ).strip()

    if raw_id.lower() == "/back":
        return None

    try:
        session_id = int(raw_id)
    except ValueError:
        print("Session ID must be a number.")
        return None

    selected = next(
        (
            session
            for session in sessions
            if session.id == session_id
        ),
        None,
    )

    if selected is None:
        print(
            f"Session ID {session_id} was not found "
            "in the available sessions."
        )
        return None

    return session_id

def view_session_report_interface() -> None:
    """
    Display a deterministic report for one study session.
    """
    print("\nVIEW STUDY SESSION REPORT")

    session_id = select_study_session_id()

    if session_id is None:
        return

    try:
        report = build_session_report(
            session_id
        )

        print()
        print(
            format_session_report(
                report
            )
        )

    except Exception as error:
        print(
            f"\nCould not build session report: {error}"
        )

def generate_session_summary_interface() -> None:
    """
    Generate a grounded AI summary for one completed session.
    """
    print("\nGENERATE AI SESSION SUMMARY")

    session_id = select_study_session_id(
        completed_only=True
    )

    if session_id is None:
        return

    try:
        print("\nGenerating session summary...")

        result = generate_session_summary(
            session_id
        )

        summary = result.summary

        print("\n" + "=" * 60)
        print(
            f"AI SUMMARY — SESSION "
            f"{summary.session_id}"
        )
        print("=" * 60)

        print("\nOverview:")
        print(summary.overview)

        print("\nStrengths:")

        if summary.strengths:
            for strength in summary.strengths:
                print(f"- {strength}")
        else:
            print("- None supported by recorded outcomes")

        print("\nTopics requiring review:")

        if summary.review_topics:
            for topic in summary.review_topics:
                print(f"- {topic}")
        else:
            print("- None supported by recorded outcomes")

        print("\nSuggested next steps:")

        if summary.next_steps:
            for step in summary.next_steps:
                print(f"- {step}")
        else:
            print("- No specific next steps generated")

        print(
            "\nSummary confidence: "
            f"{summary.confidence:.2f}"
        )

    except Exception as error:
        print(
            f"\nCould not generate session summary: {error}"
        )

def view_study_progress_interface() -> None:
    """
    Display deterministic progress across completed sessions.
    """
    print("\nVIEW STUDY PROGRESS")

    raw_limit = input(
        "Number of recent sessions "
        "[blank for all]: "
    ).strip()

    session_limit: int | None = None

    if raw_limit:
        try:
            session_limit = int(
                raw_limit
            )
        except ValueError:
            print(
                "Session limit must be a number."
            )
            return

        if session_limit <= 0:
            print(
                "Session limit must be greater than zero."
            )
            return

    try:
        report = build_progress_report(
            session_limit=session_limit
        )

        print()
        print(
            format_progress_report(
                report
            )
        )

    except Exception as error:
        print(
            f"\nCould not build progress report: {error}"
        )

def study_reports_interface() -> None:
    """
    Study-session history, summaries, and progress submenu.
    """
    while True:
        print("\nSTUDY REPORTS")
        print("1. List study sessions")
        print("2. View session report")
        print("3. Generate AI session summary")
        print("4. View overall progress")
        print("5. Back")

        try:
            choice = input(
                "\nSelection: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "1":
            list_study_sessions_interface()

        elif choice == "2":
            view_session_report_interface()

        elif choice == "3":
            generate_session_summary_interface()

        elif choice == "4":
            view_study_progress_interface()

        elif choice == "5":
            return

        else:
            print(
                "Invalid selection. "
                "Enter a number from 1 to 5."
            )

def view_review_queue_interface() -> None:
    """
    Display unresolved partial and confused questions.
    """
    print("\nVIEW REVIEW QUEUE")

    raw_limit = input(
        "Maximum review items [default 10]: "
    ).strip()

    max_items = 10

    if raw_limit:
        try:
            max_items = int(raw_limit)
        except ValueError:
            print("Maximum items must be a number.")
            return

        if max_items <= 0:
            print(
                "Maximum items must be greater than zero."
            )
            return

    try:
        queue = build_review_queue(
            max_items=max_items
        )

        print()
        print(format_review_queue(queue))

    except Exception as error:
        print(
            f"\nCould not build review queue: {error}"
        )

def select_review_recommendation():
    """
    Display the current review queue and let the learner
    select one recommendation.

    Returns None when the queue is empty or selection is
    cancelled.
    """
    try:
        queue = build_review_queue(
            max_items=20
        )
    except Exception as error:
        print(
            f"\nCould not build review queue: {error}"
        )
        return None

    if not queue.recommendations:
        print(
            "\nNo unresolved partial or confused "
            "questions were found."
        )
        return None

    print("\nAvailable review items:")

    for position, recommendation in enumerate(
        queue.recommendations,
        start=1,
    ):
        print(
            f"{position}. "
            f"[{recommendation.outcome}] "
            f"{recommendation.question}"
        )
        print(
            "   Priority: "
            f"{recommendation.priority_score}"
        )

    raw_choice = input(
        "\nSelect item number or /back: "
    ).strip()

    if raw_choice.lower() == "/back":
        return None

    try:
        selected_position = int(raw_choice)
    except ValueError:
        print("Selection must be a number.")
        return None

    if not (
        1
        <= selected_position
        <= len(queue.recommendations)
    ):
        print(
            "Selected review item does not exist."
        )
        return None

    return queue.recommendations[
        selected_position - 1
    ]

def generate_review_activity_interface() -> None:
    """
    Generate a grounded review activity for one unresolved
    question.
    """
    print("\nGENERATE REVIEW ACTIVITY")

    recommendation = (
        select_review_recommendation()
    )

    if recommendation is None:
        return

    try:
        print(
            "\nRetrieving sources and generating "
            "review activity..."
        )

        result = generate_review_action(
            recommendation
        )

        print()
        print(format_review_action(result))

    except Exception as error:
        print(
            "\nCould not generate review activity: "
            f"{error}"
        )

def take_grounded_quiz_interface() -> None:
    """
    Generate and run a grounded multiple-choice quiz.

    Quiz attempts are not stored during Milestone 6B.
    """
    print("\nTAKE GROUNDED QUIZ")

    try:
        topic = input(
            "Quiz topic or /back: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if topic.lower() == "/back":
        return

    if not topic:
        print("Quiz topic cannot be empty.")
        return

    try:
        raw_count = input(
            "Number of questions [default 3]: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if raw_count.lower() == "/back":
        return

    question_count = 3

    if raw_count:
        try:
            question_count = int(raw_count)
        except ValueError:
            print(
                "Question count must be a number."
            )
            return

    if not 1 <= question_count <= 10:
        print(
            "Question count must be between 1 and 10."
        )
        return

    try:
        print(
            "\nRetrieving documents and generating quiz..."
        )

        generated_quiz = generate_grounded_quiz(
            topic=topic,
            question_count=question_count,
        )

    except Exception as error:
        print(
            f"\nCould not generate quiz: {error}"
        )
        return

    if not generated_quiz.quiz.should_generate:
        print()
        print(
            format_grounded_quiz(
                generated_quiz
            )
        )
        return

    print(
        "\nQuiz generated successfully."
    )
    print(
        f"Topic: {generated_quiz.quiz.topic}"
    )
    print(
        "Questions: "
        f"{len(generated_quiz.quiz.questions)}"
    )
    print(
        "Confidence: "
        f"{generated_quiz.quiz.confidence:.2f}"
    )

    try:
        result = run_quiz_interactively(
            generated_quiz
        )
    except Exception as error:
        print(
            f"\nCould not run quiz: {error}"
        )
        return

    print()
    print(
        format_quiz_run_result(
            result
        )
    )

def study_actions_interface() -> None:
    """
    Adaptive study tools submenu.
    """
    while True:
        print("\nSTUDY ACTIONS")
        print("1. View review queue")
        print("2. Generate grounded review activity")
        print("3. Take grounded quiz")
        print("4. Back")

        try:
            choice = input(
                "\nSelection: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "1":
            view_review_queue_interface()

        elif choice == "2":
            generate_review_activity_interface()

        elif choice == "3":
            take_grounded_quiz_interface()

        elif choice == "4":
            return

        else:
            print(
                "Invalid selection. "
                "Enter a number from 1 to 4."
            )

def study_actions_interface() -> None:
    """
    Adaptive study tools submenu.
    """
    while True:
        print("\nSTUDY ACTIONS")
        print("1. View review queue")
        print("2. Generate grounded review activity")
        print("3. Take grounded quiz")
        print("4. Back")

        try:
            choice = input(
                "\nSelection: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "1":
            view_review_queue_interface()

        elif choice == "2":
            generate_review_activity_interface()

        elif choice == "3":
            take_grounded_quiz_interface()

        elif choice == "4":
            return

        else:
            print(
                "Invalid selection. "
                "Enter a number from 1 to 4."
            )

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
    print("11. Consolidate learner memories")
    print("12. Study reports")
    print("13. Study actions")
    print("14. Quit")


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

def complete_chat_study_session(
    session_id: int,
) -> None:
    """
    Complete the study session used by the current chat mode.
    """
    try:
        completed_session = end_study_session(
            session_id
        )

        print(
            "\nStudy session completed."
        )
        print(
            f"Session ID: {completed_session.id}"
        )
        print(
            f"Started: {completed_session.started_at}"
        )
        print(
            f"Ended: {completed_session.ended_at}"
        )

    except Exception as error:
        print(
            "\nWarning: the study session could not be "
            f"completed: {error}"
        )

def collect_interaction_outcome(
    interaction_id: int,
) -> None:
    """
    Ask the learner how well they understood one answer and
    store the result.

    Skipping keeps the existing outcome as 'unrated'.
    """
    print("\nHow well did you understand this answer?")
    print("1. Understood")
    print("2. Partially understood")
    print("3. Confused")
    print("4. Skip")

    outcome_map = {
        "1": "understood",
        "understood": "understood",

        "2": "partial",
        "partial": "partial",
        "partially": "partial",

        "3": "confused",
        "confused": "confused",

        "4": "unrated",
        "skip": "unrated",
        "": "unrated",
    }

    while True:
        try:
            raw_choice = input(
                "\nOutcome [1-4]: "
            ).strip().lower()

        except (EOFError, KeyboardInterrupt):
            print(
                "\nOutcome skipped. "
                "The interaction remains unrated."
            )
            return

        outcome = outcome_map.get(
            raw_choice
        )

        if outcome is None:
            print(
                "Invalid selection. Enter 1, 2, 3, or 4."
            )
            continue

        if outcome == "unrated":
            print(
                "Outcome skipped. "
                "The interaction remains unrated."
            )
            return

        try:
            updated_interaction = (
                update_interaction_outcome(
                    interaction_id=interaction_id,
                    outcome=outcome,
                )
            )

        except Exception as error:
            print(
                "\nWarning: learner outcome could not be "
                f"saved: {error}"
            )
            return

        print(
            "\nLearning outcome saved: "
            f"{updated_interaction.outcome}"
        )

        return

def chat_interface() -> None:
    documents = list_documents()

    if not documents:
        print("\nNo files are indexed.")
        print("Use option 1 to add a file first.")
        return

    # ========================================================
    # CREATE OR RESUME STUDY SESSION
    # ========================================================

    try:
        study_session = (
            get_or_create_active_study_session()
        )

    except Exception as error:
        print(
            "\nChat could not start because a study session "
            f"could not be created: {error}"
        )
        return

    print("\nCHAT MODE")
    print(f"Study session ID: {study_session.id}")
    print("Each question is independent.")
    print("Commands:")
    print("  /sources  Show sources from the latest answer")
    print("  /back     Complete this session and return")

    latest_sources: list[RetrievedSource] = []

    while True:
        try:
            question = input(
                "\nYou: "
            ).strip()

        except (EOFError, KeyboardInterrupt):
            print()

            complete_chat_study_session(
                study_session.id
            )

            return

        if not question:
            continue

        command = question.lower()

        if command == "/back":
            complete_chat_study_session(
                study_session.id
            )

            return

        if command == "/sources":
            print_sources(
                latest_sources
            )

            continue

        try:
            answer, latest_sources = answer_question(
                question
            )

            print("\nAssistant:")
            print(answer)

            if latest_sources:
                source_labels: list[str] = []

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

            # =================================================
            # SAVE STUDY HISTORY
            # =================================================

            study_source_inputs = [
                StudySourceInput(
                    source_index=source.index,
                    filename=source.filename,
                    page_number=source.page_number,
                    chunk_index=source.chunk_index,
                    distance=source.distance,
                )
                for source in latest_sources
            ]

            try:
                interaction, stored_sources = (
                    insert_study_interaction_with_sources(
                        session_id=study_session.id,
                        question=question,
                        answer=answer,
                        sources=study_source_inputs,
                        outcome="unrated",
                    )
                )

                print(
                    "\nStudy history saved."
                )
                print(
                    f"Interaction ID: {interaction.id}"
                )
                print(
                    "Sources recorded: "
                    f"{len(stored_sources)}"
                )
                collect_interaction_outcome(
                    interaction_id=interaction.id,
                    )

            except Exception as history_error:
                # The generated answer remains useful even when
                # local history storage fails.
                print(
                    "\nWarning: the answer was generated, but "
                    "study history could not be saved: "
                    f"{history_error}"
                )

            # Memory extraction is separate from study history.
            handle_memory_proposal(
                user_message=question,
                assistant_answer=answer,
            )

        except Exception as error:
            # Failed questions are not added to study history.
            print(
                f"\nQuestion failed: {error}"
            )

def list_files_interface() -> None:
    """
    Display all files currently indexed in the local RAG system.
    """
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
    initialize_study_database()
    initialize_study_database()

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
            consolidate_memories_interface()
        elif choice == "12":
            study_reports_interface()

        elif choice == "13":
            study_actions_interface()

        elif choice == "14":
            quit_program()

        else:
            print(
                "Invalid selection. "
                "Enter a number from 1 to 14."
            )
    


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped.")
        sys.exit(0)