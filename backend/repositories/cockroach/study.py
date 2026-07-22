from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import iso, new_public_identity, utc_now, uuid_for_public
from backend.repositories.interfaces import RepositoryConflictError
from backend.study.database import (
    ALLOWED_INTERACTION_OUTCOMES,
    StoredInteractionSource,
    StoredQuizAttempt,
    StoredQuizQuestionAttempt,
    StoredQuizQuestionSource,
    StoredStudyInteraction,
    StoredStudySession,
    validate_quiz_question_inputs,
)


def _resolve_quiz_citation_lineage(
    connection,
    workspace_id: str,
    document_public_id: int,
    chunk_index: int,
):
    document_rows = connection.execute(
        text(
            "SELECT id FROM documents "
            "WHERE workspace_id=:workspace_id AND public_id=:public_id"
        ),
        {
            "workspace_id": UUID(workspace_id),
            "public_id": int(document_public_id),
        },
    ).scalars().all()
    if len(document_rows) != 1:
        raise RepositoryConflictError(
            "Quiz citation document ownership is invalid."
        )
    document_uuid = document_rows[0]
    chunk_rows = connection.execute(
        text(
            """
            SELECT id FROM document_chunks
            WHERE workspace_id=:workspace_id
              AND document_id=:document_id
              AND chunk_index=:chunk_index
            """
        ),
        {
            "workspace_id": UUID(workspace_id),
            "document_id": document_uuid,
            "chunk_index": int(chunk_index),
        },
    ).scalars().all()
    if len(chunk_rows) != 1:
        raise RepositoryConflictError(
            "Quiz citation chunk lineage is missing or ambiguous."
        )
    return document_uuid, chunk_rows[0]


class CockroachStudySessionRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def get_or_create_active(self) -> StoredStudySession:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM study_sessions
                    WHERE workspace_id=:workspace_id AND status='active'
                    ORDER BY started_at DESC, public_id DESC LIMIT 1
                    """
                ),
                {"workspace_id": UUID(self.workspace_id)},
            ).mappings().first()
            if row is None:
                record_id, public_id = new_public_identity()
                now = utc_now()
                row = connection.execute(
                    text(
                        """
                        INSERT INTO study_sessions (
                            id,workspace_id,public_id,status,started_at,ended_at,
                            created_at,updated_at,version
                        ) VALUES (
                            :id,:workspace_id,:public_id,'active',:now,NULL,:now,:now,1
                        ) RETURNING *
                        """
                    ),
                    {
                        "id": record_id,
                        "workspace_id": UUID(self.workspace_id),
                        "public_id": public_id,
                        "now": now,
                    },
                ).mappings().one()
        return _session(row)

    def get_active(self) -> StoredStudySession | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM study_sessions WHERE workspace_id=:workspace_id "
                    "AND status='active' ORDER BY started_at DESC,public_id DESC LIMIT 1"
                ),
                {"workspace_id": UUID(self.workspace_id)},
            ).mappings().first()
        return _session(row) if row else None

    def insert_interaction_with_sources(self, **values: Any):
        session_id = int(values["session_id"])
        question = str(values["question"]).strip()
        answer = str(values["answer"]).strip()
        outcome = str(values.get("outcome", "unrated")).strip().lower()
        sources = list(values.get("sources", []))
        if not question or not answer:
            raise ValueError("Study question and answer cannot be empty.")
        if outcome not in ALLOWED_INTERACTION_OUTCOMES:
            raise ValueError("Invalid interaction outcome.")
        session_uuid = uuid_for_public("study_sessions", self.workspace_id, session_id)
        if session_uuid is None:
            raise ValueError(f"Study session ID {session_id} does not exist.")
        interaction_uuid, public_id = new_public_identity()
        now = utc_now()
        with connection_scope() as connection:
            status = connection.execute(
                text("SELECT status FROM study_sessions WHERE id=:id AND workspace_id=:workspace_id"),
                {"id": session_uuid, "workspace_id": UUID(self.workspace_id)},
            ).scalar_one()
            if status != "active":
                raise ValueError("Interactions can only be added to an active study session.")
            connection.execute(
                text(
                    """
                    INSERT INTO study_interactions (
                        id,workspace_id,public_id,session_id,question,answer,outcome,
                        created_at,updated_at
                    ) VALUES (
                        :id,:workspace_id,:public_id,:session_id,:question,:answer,
                        :outcome,:now,:now
                    )
                    """
                ),
                {
                    "id": interaction_uuid,
                    "workspace_id": UUID(self.workspace_id),
                    "public_id": public_id,
                    "session_id": session_uuid,
                    "question": question,
                    "answer": answer,
                    "outcome": outcome,
                    "now": now,
                },
            )
            for source in sources:
                source_id, source_public_id = new_public_identity()
                document_uuid = None
                if source.document_id is not None:
                    document_uuid = uuid_for_public(
                        "documents", self.workspace_id, int(source.document_id)
                    )
                connection.execute(
                    text(
                        """
                        INSERT INTO study_interaction_sources (
                            id,workspace_id,public_id,interaction_id,document_id,
                            source_index,filename,page_number,chunk_index,distance,
                            notebook_public_id,mime_type,slide_number,excerpt,created_at
                        ) VALUES (
                            :id,:workspace_id,:public_id,:interaction_id,:document_id,
                            :source_index,:filename,:page_number,:chunk_index,:distance,
                            :notebook_id,:mime_type,:slide_number,:excerpt,:created_at
                        )
                        """
                    ),
                    {
                        "id": source_id,
                        "workspace_id": UUID(self.workspace_id),
                        "public_id": source_public_id,
                        "interaction_id": interaction_uuid,
                        "document_id": document_uuid,
                        "source_index": int(source.source_index),
                        "filename": source.filename.strip(),
                        "page_number": source.page_number,
                        "chunk_index": source.chunk_index,
                        "distance": float(source.distance),
                        "notebook_id": source.notebook_id,
                        "mime_type": source.mime_type,
                        "slide_number": source.slide_number,
                        "excerpt": source.excerpt,
                        "created_at": now,
                    },
                )
        interaction = self._get_interaction(public_id)
        assert interaction is not None
        return interaction, self.list_sources(public_id)

    def get(self, session_id: int) -> StoredStudySession | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM study_sessions "
                    "WHERE workspace_id=:workspace_id AND public_id=:public_id"
                ),
                {"workspace_id": UUID(self.workspace_id), "public_id": int(session_id)},
            ).mappings().first()
        return _session(row) if row else None

    def get_interaction(self, interaction_id: int) -> StoredStudyInteraction | None:
        return self._get_interaction(interaction_id)

    def list(self) -> list[StoredStudySession]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM study_sessions WHERE workspace_id=:workspace_id "
                    "ORDER BY started_at DESC, public_id DESC"
                ),
                {"workspace_id": UUID(self.workspace_id)},
            ).mappings().all()
        return [_session(row) for row in rows]

    def list_interactions(self, session_id: int) -> list[StoredStudyInteraction]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT i.*, s.public_id AS session_public_id
                    FROM study_interactions i JOIN study_sessions s ON s.id=i.session_id
                    WHERE i.workspace_id=:workspace_id AND s.public_id=:session_id
                    ORDER BY i.created_at, i.public_id
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "session_id": int(session_id)},
            ).mappings().all()
        return [_interaction(row) for row in rows]

    def list_sources(self, interaction_id: int) -> list[StoredInteractionSource]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT src.*, i.public_id AS interaction_public_id,
                           d.public_id AS document_public_id
                    FROM study_interaction_sources src
                    JOIN study_interactions i ON i.id=src.interaction_id
                    LEFT JOIN documents d ON d.id=src.document_id
                    WHERE src.workspace_id=:workspace_id AND i.public_id=:interaction_id
                    ORDER BY src.source_index
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "interaction_id": int(interaction_id)},
            ).mappings().all()
        return [_interaction_source(row) for row in rows]

    def update_outcome(self, interaction_id: int, outcome: str) -> StoredStudyInteraction:
        normalized = outcome.strip().lower()
        if normalized not in ALLOWED_INTERACTION_OUTCOMES:
            raise ValueError("Invalid interaction outcome.")
        with connection_scope() as connection:
            result = connection.execute(
                text(
                    "UPDATE study_interactions SET outcome=:outcome,updated_at=now() "
                    "WHERE workspace_id=:workspace_id AND public_id=:public_id"
                ),
                {
                    "outcome": normalized,
                    "workspace_id": UUID(self.workspace_id),
                    "public_id": int(interaction_id),
                },
            )
        if result.rowcount != 1:
            raise ValueError(f"Interaction ID {interaction_id} does not exist.")
        updated = self._get_interaction(interaction_id)
        assert updated is not None
        return updated

    def end(self, session_id: int) -> StoredStudySession:
        existing = self.get(session_id)
        if existing is None:
            raise ValueError(f"Study session ID {session_id} does not exist.")
        if existing.status == "completed":
            return existing
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    UPDATE study_sessions SET status='completed',ended_at=now(),
                        updated_at=now(),version=version+1
                    WHERE workspace_id=:workspace_id AND public_id=:public_id
                      AND status='active'
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "public_id": int(session_id)},
            )
        completed = self.get(session_id)
        assert completed is not None
        return completed

    def _get_interaction(self, interaction_id: int) -> StoredStudyInteraction | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT i.*, s.public_id AS session_public_id
                    FROM study_interactions i JOIN study_sessions s ON s.id=i.session_id
                    WHERE i.workspace_id=:workspace_id AND i.public_id=:public_id
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "public_id": int(interaction_id)},
            ).mappings().first()
        return _interaction(row) if row else None


class CockroachQuizRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def save_run_result(self, result: Any):
        from backend.study.quiz_history import build_quiz_question_inputs

        questions = build_quiz_question_inputs(result)
        validate_quiz_question_inputs(questions)
        quiz = result.generated_quiz.quiz
        total = len(questions)
        presented = sum(1 for item in questions if item.presented)
        answered = sum(
            1
            for item in questions
            if item.presented and not item.skipped and item.selected_option is not None
        )
        skipped = sum(1 for item in questions if item.skipped)
        correct = sum(1 for item in questions if item.is_correct)
        attempt_uuid, attempt_public_id = new_public_identity()
        now = utc_now()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO quiz_attempts (
                        id,workspace_id,public_id,requested_topic,quiz_topic,status,
                        total_questions,presented_questions,answered_questions,
                        skipped_questions,correct_answers,score_percentage,
                        accuracy_percentage,confidence,created_at,updated_at
                    ) VALUES (
                        :id,:workspace_id,:public_id,:requested_topic,:quiz_topic,:status,
                        :total,:presented,:answered,:skipped,:correct,:score,
                        :accuracy,:confidence,:now,:now
                    )
                    """
                ),
                {
                    "id": attempt_uuid,
                    "workspace_id": UUID(self.workspace_id),
                    "public_id": attempt_public_id,
                    "requested_topic": result.generated_quiz.requested_topic.strip(),
                    "quiz_topic": quiz.topic.strip(),
                    "status": "aborted" if result.aborted else "completed",
                    "total": total,
                    "presented": presented,
                    "answered": answered,
                    "skipped": skipped,
                    "correct": correct,
                    "score": correct / total * 100,
                    "accuracy": correct / answered * 100 if answered else None,
                    "confidence": float(quiz.confidence),
                    "now": now,
                },
            )
            for question in questions:
                question_uuid, question_public_id = new_public_identity()
                connection.execute(
                    text(
                        """
                        INSERT INTO quiz_question_attempts (
                            id,workspace_id,public_id,quiz_attempt_id,question_number,
                            question,options,presented,selected_option,correct_option,
                            is_correct,skipped,explanation,created_at,updated_at
                        ) VALUES (
                            :id,:workspace_id,:public_id,:attempt_id,:number,:question,
                            CAST(:options AS JSONB),:presented,:selected,:correct,
                            :is_correct,:skipped,:explanation,:now,:now
                        )
                        """
                    ),
                    {
                        "id": question_uuid,
                        "workspace_id": UUID(self.workspace_id),
                        "public_id": question_public_id,
                        "attempt_id": attempt_uuid,
                        "number": question.question_number,
                        "question": question.question.strip(),
                        "options": __import__("json").dumps(list(question.options)),
                        "presented": question.presented,
                        "selected": question.selected_option,
                        "correct": question.correct_option,
                        "is_correct": question.is_correct,
                        "skipped": question.skipped,
                        "explanation": question.explanation.strip(),
                        "now": now,
                    },
                )
                for source in question.sources:
                    source_uuid, source_public_id = new_public_identity()
                    document_uuid = None
                    document_chunk_uuid = None
                    if (
                        source.document_id is None
                        and source.chunk_index is not None
                    ) or (
                        source.document_id is not None
                        and source.chunk_index is None
                    ):
                        raise RepositoryConflictError(
                            "Quiz citation document and chunk lineage must be supplied together."
                        )
                    if source.document_id is not None and source.chunk_index is not None:
                        document_uuid, document_chunk_uuid = (
                            _resolve_quiz_citation_lineage(
                                connection,
                                self.workspace_id,
                                int(source.document_id),
                                int(source.chunk_index),
                            )
                        )
                    connection.execute(
                        text(
                            """
                            INSERT INTO quiz_question_sources (
                                id,workspace_id,public_id,question_attempt_id,document_id,
                                document_chunk_id,source_index,filename,page_number,chunk_index,
                                distance,notebook_public_id,mime_type,slide_number,excerpt,created_at
                            ) VALUES (
                                :id,:workspace_id,:public_id,:question_id,:document_id,
                                :document_chunk_id,:source_index,:filename,:page_number,
                                :chunk_index,:distance,:notebook_id,:mime_type,:slide_number,
                                :excerpt,:now
                            )
                            """
                        ),
                        {
                            "id": source_uuid,
                            "workspace_id": UUID(self.workspace_id),
                            "public_id": source_public_id,
                            "question_id": question_uuid,
                            "document_id": document_uuid,
                            "document_chunk_id": document_chunk_uuid,
                            "source_index": source.source_index,
                            "filename": source.filename.strip(),
                            "page_number": source.page_number,
                            "chunk_index": source.chunk_index,
                            "distance": source.distance,
                            "notebook_id": source.notebook_id,
                            "mime_type": source.mime_type,
                            "slide_number": source.slide_number,
                            "excerpt": source.excerpt,
                            "now": now,
                        },
                    )
        attempt = self.get_attempt(attempt_public_id)
        assert attempt is not None
        return attempt, tuple(self.list_questions(attempt_public_id))

    def get_attempt(self, attempt_id: int) -> StoredQuizAttempt | None:
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM quiz_attempts "
                    "WHERE workspace_id=:workspace_id AND public_id=:public_id"
                ),
                {"workspace_id": UUID(self.workspace_id), "public_id": int(attempt_id)},
            ).mappings().first()
        return _quiz_attempt(row) if row else None

    def list_attempts(self, limit: int | None = None) -> list[StoredQuizAttempt]:
        query = (
            "SELECT * FROM quiz_attempts WHERE workspace_id=:workspace_id "
            "ORDER BY created_at DESC, public_id DESC"
        )
        parameters: dict[str, object] = {"workspace_id": UUID(self.workspace_id)}
        if limit is not None:
            query += " LIMIT :limit"
            parameters["limit"] = int(limit)
        with connection_scope() as connection:
            rows = connection.execute(text(query), parameters).mappings().all()
        return [_quiz_attempt(row) for row in rows]

    def list_questions(self, attempt_id: int) -> list[StoredQuizQuestionAttempt]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT q.*, a.public_id AS attempt_public_id
                    FROM quiz_question_attempts q JOIN quiz_attempts a ON a.id=q.quiz_attempt_id
                    WHERE q.workspace_id=:workspace_id AND a.public_id=:attempt_id
                    ORDER BY q.question_number
                    """
                ),
                {"workspace_id": UUID(self.workspace_id), "attempt_id": int(attempt_id)},
            ).mappings().all()
        return [_quiz_question(row) for row in rows]

    def list_sources(self, question_attempt_id: int) -> list[StoredQuizQuestionSource]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT src.*, q.public_id AS question_public_id,
                           d.public_id AS document_public_id
                    FROM quiz_question_sources src
                    JOIN quiz_question_attempts q ON q.id=src.question_attempt_id
                    LEFT JOIN documents d ON d.id=src.document_id
                    WHERE src.workspace_id=:workspace_id AND q.public_id=:question_id
                    ORDER BY src.source_index
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "question_id": int(question_attempt_id),
                },
            ).mappings().all()
        return [_quiz_source(row) for row in rows]


def _session(row: Any) -> StoredStudySession:
    return StoredStudySession(
        id=int(row["public_id"]), status=str(row["status"]),
        started_at=iso(row["started_at"]),
        ended_at=iso(row["ended_at"]) if row["ended_at"] else None,
    )


def _interaction(row: Any) -> StoredStudyInteraction:
    return StoredStudyInteraction(
        id=int(row["public_id"]), session_id=int(row["session_public_id"]),
        question=str(row["question"]), answer=str(row["answer"]),
        outcome=str(row["outcome"]), created_at=iso(row["created_at"]),
    )


def _interaction_source(row: Any) -> StoredInteractionSource:
    return StoredInteractionSource(
        id=int(row["public_id"]), interaction_id=int(row["interaction_public_id"]),
        source_index=int(row["source_index"]), filename=str(row["filename"]),
        page_number=int(row["page_number"]) if row["page_number"] is not None else None,
        chunk_index=int(row["chunk_index"]) if row["chunk_index"] is not None else None,
        distance=float(row["distance"]),
        document_id=int(row["document_public_id"]) if row["document_public_id"] is not None else None,
        notebook_id=int(row["notebook_public_id"]) if row["notebook_public_id"] is not None else None,
        mime_type=str(row["mime_type"]) if row["mime_type"] is not None else None,
        slide_number=int(row["slide_number"]) if row["slide_number"] is not None else None,
        excerpt=str(row["excerpt"]) if row["excerpt"] is not None else None,
    )


def _quiz_attempt(row: Any) -> StoredQuizAttempt:
    return StoredQuizAttempt(
        id=int(row["public_id"]), requested_topic=str(row["requested_topic"]),
        quiz_topic=str(row["quiz_topic"]), status=str(row["status"]),
        total_questions=int(row["total_questions"]),
        presented_questions=int(row["presented_questions"]),
        answered_questions=int(row["answered_questions"]),
        skipped_questions=int(row["skipped_questions"]),
        correct_answers=int(row["correct_answers"]),
        score_percentage=float(row["score_percentage"]),
        accuracy_percentage=(float(row["accuracy_percentage"]) if row["accuracy_percentage"] is not None else None),
        confidence=float(row["confidence"]), created_at=iso(row["created_at"]),
    )


def _quiz_question(row: Any) -> StoredQuizQuestionAttempt:
    options = list(row["options"])
    return StoredQuizQuestionAttempt(
        id=int(row["public_id"]), quiz_attempt_id=int(row["attempt_public_id"]),
        question_number=int(row["question_number"]), question=str(row["question"]),
        options=(str(options[0]), str(options[1]), str(options[2]), str(options[3])),
        presented=bool(row["presented"]), selected_option=row["selected_option"],
        correct_option=int(row["correct_option"]), is_correct=bool(row["is_correct"]),
        skipped=bool(row["skipped"]), explanation=str(row["explanation"]),
    )


def _quiz_source(row: Any) -> StoredQuizQuestionSource:
    return StoredQuizQuestionSource(
        id=int(row["public_id"]), question_attempt_id=int(row["question_public_id"]),
        source_index=int(row["source_index"]), filename=str(row["filename"]),
        page_number=int(row["page_number"]) if row["page_number"] is not None else None,
        chunk_index=int(row["chunk_index"]) if row["chunk_index"] is not None else None,
        distance=float(row["distance"]) if row["distance"] is not None else None,
        document_id=int(row["document_public_id"]) if row["document_public_id"] is not None else None,
        notebook_id=int(row["notebook_public_id"]) if row["notebook_public_id"] is not None else None,
        mime_type=str(row["mime_type"]) if row["mime_type"] is not None else None,
        slide_number=int(row["slide_number"]) if row["slide_number"] is not None else None,
        excerpt=str(row["excerpt"]) if row["excerpt"] is not None else None,
    )
