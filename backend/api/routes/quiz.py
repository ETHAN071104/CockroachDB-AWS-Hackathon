from __future__ import annotations

from backend.api.errors import ApiError, map_exception
from backend.api.schemas import (
    AdaptationResponse,
    LearningSignalResponse,
    PresentedQuizQuestionResponse,
    PresentedQuizResponse,
    QuizGenerateRequest,
    QuizQuestionFeedbackResponse,
    QuizMemoryProposalResponse,
    QuizScopeResponse,
    QuizSubmissionResponse,
    QuizSubmitRequest,
    SourceLineageResponse,
)
from fastapi import APIRouter, Path
from typing import Annotated

from backend.rag.scope import RetrievalScope
from backend.rag.notebooks import DocumentNotFoundError, NotebookNotFoundError
from backend.rag.scope import TopicNotFoundError
from backend.study.quiz_api import (
    PendingQuizNotFoundError,
    QuizGenerationRejectedError,
    QuizResponse,
    generate_quiz_for_api,
    submit_quiz,
)
from backend.study.quiz_scope import QuizScopeUnavailableError

router = APIRouter(prefix="/api/study", tags=["study"])


def _scope_from_request(payload: QuizGenerateRequest) -> RetrievalScope | None:
    scope = payload.scope
    if scope is not None:
        return RetrievalScope(
            notebook_id=scope.notebook_id,
            document_ids=(
                tuple(scope.document_ids)
                if scope.document_ids is not None
                else None
            ),
            topic_id=scope.topic_id,
        )
    if payload.notebook_id is not None:
        return RetrievalScope(notebook_id=payload.notebook_id)
    if payload.document_ids is not None:
        return RetrievalScope(document_ids=tuple(payload.document_ids))
    if payload.topic_id is not None:
        return RetrievalScope(topic_id=payload.topic_id)
    return None


@router.post(
    "/actions/quizzes/generate",
    response_model=PresentedQuizResponse,
)
@router.post(
    "/quiz",
    response_model=PresentedQuizResponse,
    include_in_schema=False,
)
def generate_quiz(payload: QuizGenerateRequest) -> PresentedQuizResponse:
    try:
        quiz = generate_quiz_for_api(
            payload.topic,
            payload.question_count,
            _scope_from_request(payload),
        )
    except (DocumentNotFoundError, NotebookNotFoundError, TopicNotFoundError) as error:
        if isinstance(error, DocumentNotFoundError):
            message = "The selected document is unavailable in this workspace."
        elif isinstance(error, NotebookNotFoundError):
            message = "The selected notebook is unavailable in this workspace."
        else:
            message = "The selected topic is unavailable in this workspace."
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message=message,
        ) from error
    except QuizScopeUnavailableError as error:
        raise ApiError(
            status_code=422,
            code=error.code,
            message=str(error),
        ) from error
    except QuizGenerationRejectedError as error:
        raise ApiError(
            status_code=422,
            code="insufficient_evidence",
            message=str(error),
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_quiz_request",
            message=str(error),
        ) from error
    except Exception as error:
        raise map_exception(
            error,
            fallback_code="INTERNAL_ERROR",
            context="quiz_generation",
        ) from error

    return PresentedQuizResponse(
        quiz_id=quiz.quiz_id,
        requested_topic=quiz.requested_topic,
        topic=quiz.topic,
        confidence=quiz.confidence,
        questions=[
            PresentedQuizQuestionResponse(
                question_number=question.question_number,
                question=question.question,
                options=list(question.options),
            )
            for question in quiz.questions
        ],
        scope=QuizScopeResponse(
            type=quiz.scope.type,
            label=quiz.scope.label,
            document_count=quiz.scope.document_count,
            personalized=quiz.scope.personalized,
            resolved_document_ids=list(quiz.scope.resolved_document_ids),
            description=quiz.scope.description,
            notebook_name=quiz.scope.notebook_name,
            document_name=quiz.scope.document_name,
        ),
        adaptation=AdaptationResponse(
            adapted_using_learner_memory=quiz.adaptation.adapted,
            targeted_topic=(
                str(quiz.adaptation.applied_changes.get("targeted_topic"))
                if quiz.adaptation.applied_changes.get("targeted_topic") is not None
                else None
            ),
            difficulty=(
                str(quiz.adaptation.applied_changes.get("difficulty"))
                if quiz.adaptation.applied_changes.get("difficulty") is not None
                else None
            ),
            reason=quiz.adaptation.reason,
            memory_ids=list(quiz.adaptation.memory_ids),
            learning_signal_ids=list(quiz.adaptation.learning_signal_ids),
            applied_changes=quiz.adaptation.applied_changes,
            event_id=quiz.adaptation_event_id,
        ),
    )


@router.post(
    "/actions/quizzes/{quiz_id}/submit",
    response_model=QuizSubmissionResponse,
)
@router.post(
    "/quiz/{quiz_id}/submit",
    response_model=QuizSubmissionResponse,
    include_in_schema=False,
)
def submit_quiz_route(
    quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: QuizSubmitRequest,
) -> QuizSubmissionResponse:
    responses = [
        QuizResponse(
            question_number=response.question_number,
            selected_option=response.selected_option,
        )
        for response in payload.responses
    ]
    try:
        result = submit_quiz(quiz_id, responses)
    except PendingQuizNotFoundError as error:
        raise ApiError(
            status_code=404,
            code="pending_quiz_not_found",
            message="Pending quiz was not found or has already been submitted.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_quiz_submission",
            message=str(error),
        ) from error

    return QuizSubmissionResponse(
        attempt_id=result.attempt_id,
        status=result.status,
        total_questions=result.total_questions,
        presented_questions=result.presented_questions,
        answered_questions=result.answered_questions,
        skipped_questions=result.skipped_questions,
        correct_answers=result.correct_answers,
        score_percentage=result.score_percentage,
        accuracy_percentage=result.accuracy_percentage,
        feedback=[
            QuizQuestionFeedbackResponse(
                question_number=item.question_number,
                question=item.question,
                selected_option=item.selected_option,
                correct_option=item.correct_option,
                is_correct=item.is_correct,
                skipped=item.skipped,
                explanation=item.explanation,
                sources=[
                    SourceLineageResponse(
                        index=source.index,
                        document_id=source.document_id,
                        notebook_id=source.notebook_id,
                        filename=source.filename,
                        mime_type=source.mime_type,
                        page_number=source.page_number,
                        slide_number=source.slide_number,
                        chunk_index=source.chunk_index,
                        distance=source.distance,
                        excerpt=source.excerpt,
                    )
                    for source in item.sources
                ],
            )
            for item in result.feedback
        ],
        learning_signals=[
            LearningSignalResponse(
                id=signal.id,
                source_type=signal.source_type,
                source_id=signal.source_id,
                source_question_id=signal.source_question_id,
                topic=signal.topic,
                signal_type=signal.signal_type,
                statement=signal.statement,
                evidence=list(signal.evidence),
                confidence=signal.confidence,
                importance=signal.importance,
                occurrence_count=signal.occurrence_count,
                status=signal.status,
                first_observed_at=signal.first_observed_at,
                last_observed_at=signal.last_observed_at,
                memory_id=signal.memory_id,
                proposal_id=signal.proposal_id,
            )
            for signal in result.learning_signals
        ],
        detected_weaknesses=list(result.detected_weaknesses),
        memory_proposals=[
            QuizMemoryProposalResponse(
                proposal_id=proposal.id,
                memory_type=proposal.candidate.memory_type,
                content=proposal.candidate.content,
                confidence=proposal.candidate.confidence,
                importance=proposal.candidate.importance,
                allowed_decisions=list(proposal.allowed_decisions),
                reason=proposal.conflict.reason,
                evidence=list(proposal.evidence),
                occurrence_count=proposal.occurrence_count,
                created_at=proposal.created_at,
            )
            for proposal in result.memory_proposals
        ],
        enrichment_workflow_id=result.enrichment_workflow_id,
    )
