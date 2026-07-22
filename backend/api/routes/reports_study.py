from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from backend.api.errors import ApiError
from backend.api.report_schemas import (
    CoachingActivityResponse,
    CoachingPlanResponse,
    CoachingRequest,
    IntegrityIssueResponse,
    IntegrityResponse,
    InteractionReportResponse,
    OutcomeCountsResponse,
    ProgressReportResponse,
    ProgressSessionResponse,
    QuizAttemptReportResponse,
    QuizPerformanceResponse,
    QuizTopicPerformanceResponse,
    ReviewActionResponse,
    ReviewGenerateRequest,
    ReviewQueueResponse,
    ReviewRecommendationResponse,
    SessionReportResponse,
    SessionSummaryContentResponse,
    SessionSummaryResponse,
    StoredQuizAttemptResponse,
    StoredQuizQuestionResponse,
    StudyPlanEvidenceResponse,
    StudyPlanItemResponse,
    StudyPlanRequest,
    StudyPlanResponse,
)
from backend.api.schemas import AdaptationResponse, RetrievalScopeRequest, SourceLineageResponse
from backend.application.learning_loop import (
    AdaptationContext,
    record_adaptation_event,
)
from backend.rag.notebooks import get_document_record
from backend.rag.scope import RetrievalScope
from backend.study.coach import generate_coaching_plan
from backend.study.database import list_interaction_sources, list_study_sessions
from backend.study.integrity import run_study_integrity_check
from backend.study.planner import AdaptiveStudyPlan, StudyPlanItem, build_adaptive_study_plan
from backend.study.progress import build_progress_report
from backend.study.quiz_reporting import (
    build_quiz_attempt_report,
    build_quiz_performance_report,
)
from backend.study.recommendations import ReviewRecommendation, build_review_queue
from backend.study.reporting import StudySessionReport, build_session_report
from backend.study.reviewer import generate_review_action
from backend.study.summarizer import generate_session_summary

router = APIRouter(prefix="/api", tags=["reports", "study"])


def _adaptation(
    context: AdaptationContext,
    event_id: str | None = None,
) -> AdaptationResponse:
    return AdaptationResponse(
        adapted_using_learner_memory=context.adapted,
        targeted_topic=(
            str(context.applied_changes.get("targeted_topic"))
            if context.applied_changes.get("targeted_topic") is not None
            else context.topic or None
        ),
        difficulty=(
            str(context.applied_changes.get("difficulty"))
            if context.applied_changes.get("difficulty") is not None
            else None
        ),
        reason=context.reason,
        memory_ids=list(context.memory_ids),
        learning_signal_ids=list(context.learning_signal_ids),
        applied_changes=context.applied_changes,
        event_id=event_id,
    )


def _scope(scope: RetrievalScopeRequest | None) -> RetrievalScope | None:
    if scope is None:
        return None
    return RetrievalScope(
        notebook_id=scope.notebook_id,
        document_ids=(
            tuple(scope.document_ids)
            if scope.document_ids is not None
            else None
        ),
        topic_id=scope.topic_id,
    )


def _source(source: Any) -> SourceLineageResponse:
    document_id = getattr(source, "document_id", None)
    has_notebook_snapshot = hasattr(source, "notebook_id")
    notebook_id = getattr(source, "notebook_id", None)
    if (
        not has_notebook_snapshot
        and notebook_id is None
        and document_id is not None
    ):
        record = get_document_record(document_id)
        notebook_id = record.notebook_id if record is not None else None
    excerpt = getattr(source, "excerpt", None)
    if excerpt is None:
        excerpt = getattr(source, "text", "")
    return SourceLineageResponse(
        index=int(getattr(source, "source_index", getattr(source, "index", 1))),
        document_id=document_id,
        notebook_id=notebook_id,
        filename=str(getattr(source, "filename")),
        mime_type=getattr(source, "mime_type", None),
        page_number=getattr(source, "page_number", None),
        slide_number=getattr(source, "slide_number", None),
        chunk_index=getattr(source, "chunk_index", None),
        distance=getattr(source, "distance", None),
        excerpt=str(excerpt or "")[:2_000],
    )


def _counts(counts: Any) -> OutcomeCountsResponse:
    return OutcomeCountsResponse(
        understood=counts.understood,
        partial=counts.partial,
        confused=counts.confused,
        unrated=counts.unrated,
    )


def _session_report(report: StudySessionReport) -> SessionReportResponse:
    interactions: list[InteractionReportResponse] = []
    for interaction in report.interactions:
        interactions.append(
            InteractionReportResponse(
                id=interaction.id,
                session_id=interaction.session_id,
                question=interaction.question,
                answer=interaction.answer,
                outcome=interaction.outcome,
                created_at=interaction.created_at,
                sources=[
                    _source(source)
                    for source in list_interaction_sources(interaction.id)
                ],
            )
        )
    return SessionReportResponse(
        id=report.session.id,
        status=report.session.status,
        started_at=report.session.started_at,
        ended_at=report.session.ended_at,
        interaction_count=report.interaction_count,
        outcome_counts=_counts(report.outcome_counts),
        source_filenames=list(report.source_filenames),
        interactions=interactions,
    )


def _stored_attempt(attempt: Any) -> StoredQuizAttemptResponse:
    return StoredQuizAttemptResponse(
        id=attempt.id,
        requested_topic=attempt.requested_topic,
        quiz_topic=attempt.quiz_topic,
        status=attempt.status,
        total_questions=attempt.total_questions,
        presented_questions=attempt.presented_questions,
        answered_questions=attempt.answered_questions,
        skipped_questions=attempt.skipped_questions,
        correct_answers=attempt.correct_answers,
        score_percentage=attempt.score_percentage,
        accuracy_percentage=attempt.accuracy_percentage,
        confidence=attempt.confidence,
        created_at=attempt.created_at,
    )


def _quiz_attempt_report(report: Any) -> QuizAttemptReportResponse:
    questions = []
    for item in report.questions:
        question = item.question_attempt
        presented = question.presented
        questions.append(
            StoredQuizQuestionResponse(
                id=question.id,
                question_number=question.question_number,
                question=question.question,
                options=list(question.options),
                presented=question.presented,
                selected_option=question.selected_option,
                correct_option=(
                    question.correct_option
                    if presented
                    else None
                ),
                is_correct=question.is_correct,
                skipped=question.skipped,
                status=item.status,
                explanation=(
                    question.explanation
                    if presented
                    else None
                ),
                sources=(
                    [_source(source) for source in item.sources]
                    if presented
                    else []
                ),
            )
        )
    return QuizAttemptReportResponse(
        attempt=_stored_attempt(report.attempt),
        questions=questions,
    )


def _recommendation(item: ReviewRecommendation) -> ReviewRecommendationResponse:
    return ReviewRecommendationResponse(
        interaction_id=item.interaction_id,
        session_id=item.session_id,
        question=item.question,
        outcome=item.outcome,
        priority_score=item.priority_score,
        unresolved_count=item.unresolved_count,
        source_filenames=list(item.source_filenames),
        source_document_ids=list(item.source_document_ids),
        created_at=item.created_at,
        reason=item.reason,
        memory_ids=list(item.memory_ids),
        learning_signal_ids=list(item.learning_signal_ids),
        adaptation_reason=item.adaptation_reason,
    )


def _plan_item(item: StudyPlanItem) -> StudyPlanItemResponse:
    return StudyPlanItemResponse(
        rank=item.rank,
        title=item.title,
        action=item.action,
        priority_score=item.priority_score,
        estimated_minutes=item.estimated_minutes,
        evidence=[
            StudyPlanEvidenceResponse(
                evidence_type=evidence.evidence_type,
                status=evidence.status,
                reference_id=evidence.reference_id,
                detail=evidence.detail,
            )
            for evidence in item.evidence
        ],
        source_filenames=list(item.source_filenames),
        source_document_ids=list(item.source_document_ids),
    )


def _plan(
    plan: AdaptiveStudyPlan,
    *,
    event_id: str | None = None,
) -> StudyPlanResponse:
    return StudyPlanResponse(
        requested_minutes=plan.requested_minutes,
        allocated_minutes=plan.allocated_minutes,
        remaining_minutes=plan.remaining_minutes,
        item_count=plan.item_count,
        completed_sessions_scanned=plan.completed_sessions_scanned,
        interactions_scanned=plan.interactions_scanned,
        quiz_attempts_scanned=plan.quiz_attempts_scanned,
        items=[_plan_item(item) for item in plan.items],
        adaptation=_adaptation(plan.adaptation, event_id),
    )


@router.get(
    "/reports/study/sessions",
    response_model=list[SessionReportResponse],
)
@router.get(
    "/reports/sessions",
    response_model=list[SessionReportResponse],
    include_in_schema=False,
)
def get_session_reports(
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[SessionReportResponse]:
    sessions = list_study_sessions()
    if limit is not None:
        sessions = sessions[:limit]
    return [_session_report(build_session_report(session.id)) for session in sessions]


@router.get(
    "/reports/study/sessions/{session_id}",
    response_model=SessionReportResponse,
)
@router.get(
    "/reports/sessions/{session_id}",
    response_model=SessionReportResponse,
    include_in_schema=False,
)
def get_session_report(
    session_id: Annotated[int, Path(ge=1)],
) -> SessionReportResponse:
    try:
        return _session_report(build_session_report(session_id))
    except ValueError as error:
        raise ApiError(
            status_code=404,
            code="session_not_found",
            message="Study session was not found.",
        ) from error


@router.post(
    "/reports/study/sessions/{session_id}/summary",
    response_model=SessionSummaryResponse,
)
@router.post(
    "/reports/sessions/{session_id}/summary",
    response_model=SessionSummaryResponse,
    include_in_schema=False,
)
def post_session_summary(
    session_id: Annotated[int, Path(ge=1)],
) -> SessionSummaryResponse:
    try:
        result = generate_session_summary(session_id)
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="session_summary_unavailable",
            message=str(error),
        ) from error
    except RuntimeError as error:
        raise ApiError(
            status_code=502,
            code="session_summary_failed",
            message="Session summary generation failed.",
        ) from error
    summary = result.summary
    return SessionSummaryResponse(
        session=_session_report(result.report),
        summary=SessionSummaryContentResponse(
            overview=summary.overview,
            strengths=summary.strengths,
            review_topics=summary.review_topics,
            next_steps=summary.next_steps,
            confidence=summary.confidence,
        ),
    )


@router.get(
    "/reports/study/progress",
    response_model=ProgressReportResponse,
)
@router.get(
    "/reports/progress",
    response_model=ProgressReportResponse,
    include_in_schema=False,
)
def get_progress_report(
    session_limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> ProgressReportResponse:
    report = build_progress_report(session_limit=session_limit)
    return ProgressReportResponse(
        sessions=[
            ProgressSessionResponse(
                session_id=item.session_id,
                started_at=item.started_at,
                ended_at=item.ended_at,
                interaction_count=item.interaction_count,
                outcome_counts=_counts(item.outcome_counts),
            )
            for item in report.sessions
        ],
        session_count=report.session_count,
        total_questions=report.total_questions,
        rated_question_count=report.rated_question_count,
        understanding_rate=report.understanding_rate,
        outcome_counts=_counts(report.outcome_counts),
        source_filenames=list(report.source_filenames),
    )


@router.get(
    "/reports/quizzes/performance",
    response_model=QuizPerformanceResponse,
)
@router.get("/reports/quizzes", response_model=QuizPerformanceResponse)
def get_quiz_performance(
    attempt_limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> QuizPerformanceResponse:
    report = build_quiz_performance_report(attempt_limit=attempt_limit)
    return QuizPerformanceResponse(
        attempts=[_stored_attempt(item.attempt) for item in report.attempts],
        attempt_count=report.attempt_count,
        completed_attempt_count=report.completed_attempt_count,
        aborted_attempt_count=report.aborted_attempt_count,
        total_questions=report.total_questions,
        presented_questions=report.presented_questions,
        answered_questions=report.answered_questions,
        correct_answers=report.correct_answers,
        overall_score_percentage=report.overall_score_percentage,
        answered_accuracy_percentage=report.answered_accuracy_percentage,
        topic_performance=[
            QuizTopicPerformanceResponse(
                topic=item.topic,
                attempt_count=item.attempt_count,
                total_questions=item.total_questions,
                answered_questions=item.answered_questions,
                correct_answers=item.correct_answers,
                score_percentage=item.score_percentage,
                accuracy_percentage=item.accuracy_percentage,
            )
            for item in report.topic_performance
        ],
        source_filenames=list(report.source_filenames),
    )


@router.get(
    "/reports/quizzes/{attempt_id}",
    response_model=QuizAttemptReportResponse,
)
def get_quiz_attempt_report(
    attempt_id: Annotated[int, Path(ge=1)],
) -> QuizAttemptReportResponse:
    try:
        return _quiz_attempt_report(build_quiz_attempt_report(attempt_id))
    except ValueError as error:
        raise ApiError(
            status_code=404,
            code="quiz_attempt_not_found",
            message="Quiz attempt was not found.",
        ) from error


@router.get(
    "/study/actions/review-queue",
    response_model=ReviewQueueResponse,
)
@router.get(
    "/review",
    response_model=ReviewQueueResponse,
    include_in_schema=False,
)
def get_review_queue(
    session_limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    max_items: Annotated[int, Query(ge=1, le=100)] = 10,
    notebook_id: Annotated[int | None, Query(ge=1)] = None,
    document_ids: Annotated[list[int] | None, Query()] = None,
    topic_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> ReviewQueueResponse:
    selected = sum(
        value is not None
        for value in (notebook_id, document_ids, topic_id)
    )
    if selected > 1:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Choose exactly one review scope.",
        )
    try:
        selected_scope = (
            RetrievalScope(
                notebook_id=notebook_id,
                document_ids=(
                    tuple(document_ids)
                    if document_ids is not None
                    else None
                ),
                topic_id=topic_id,
            )
            if selected == 1
            else None
        )
        queue = build_review_queue(
            session_limit=session_limit,
            max_items=max_items,
            scope=selected_scope,
        )
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Review scope was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Review scope is invalid.",
        ) from error
    items = [_recommendation(item) for item in queue.recommendations]
    event = record_adaptation_event(queue.adaptation)
    return ReviewQueueResponse(
        items=items,
        total=len(items),
        completed_session_count=queue.completed_session_count,
        scanned_interaction_count=queue.scanned_interaction_count,
        adaptation=_adaptation(queue.adaptation, event.id),
    )


@router.post(
    "/study/actions/review",
    response_model=ReviewActionResponse,
)
@router.post(
    "/review/generate",
    response_model=ReviewActionResponse,
    include_in_schema=False,
)
def post_review_action(payload: ReviewGenerateRequest) -> ReviewActionResponse:
    try:
        selected_scope = _scope(payload.scope)
        queue = build_review_queue(
            max_items=100,
            scope=selected_scope,
        )
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Review scope was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Review scope is invalid.",
        ) from error
    recommendation = next(
        (
            item
            for item in queue.recommendations
            if item.interaction_id == payload.interaction_id
        ),
        None,
    )
    if recommendation is None:
        raise ApiError(
            status_code=404,
            code="review_item_not_found",
            message="Review recommendation was not found.",
        )
    try:
        result = generate_review_action(
            recommendation,
            scope=selected_scope,
        )
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Review scope was not found.",
        ) from error
    except RuntimeError as error:
        raise ApiError(
            status_code=502,
            code="review_generation_failed",
            message="Grounded review generation failed.",
        ) from error
    action = result.action
    event = record_adaptation_event(result.adaptation)
    return ReviewActionResponse(
        recommendation=_recommendation(recommendation),
        should_generate=action.should_generate,
        review_mode=action.review_mode,
        topic=action.topic,
        explanation=action.explanation,
        worked_example=action.worked_example,
        check_question=action.check_question,
        expected_answer=action.expected_answer,
        source_indexes=action.source_indexes,
        confidence=action.confidence,
        reason=action.reason,
        sources=[_source(source) for source in result.sources],
        adaptation=_adaptation(result.adaptation, event.id),
    )


@router.post(
    "/study/actions/plan",
    response_model=StudyPlanResponse,
)
@router.post(
    "/study/plan",
    response_model=StudyPlanResponse,
    include_in_schema=False,
)
def post_study_plan(payload: StudyPlanRequest) -> StudyPlanResponse:
    try:
        plan = build_adaptive_study_plan(
            total_minutes=payload.total_minutes,
            max_items=payload.max_items,
            session_limit=payload.session_limit,
            attempt_limit=payload.attempt_limit,
            scope=_scope(payload.scope),
        )
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Study-plan scope was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_study_plan",
            message="Study-plan request is invalid.",
        ) from error
    event = record_adaptation_event(plan.adaptation)
    return _plan(plan, event_id=event.id)


@router.post(
    "/study/actions/coaching-plan",
    response_model=CoachingPlanResponse,
)
@router.post(
    "/study/coaching",
    response_model=CoachingPlanResponse,
    include_in_schema=False,
)
def post_coaching(payload: CoachingRequest) -> CoachingPlanResponse:
    try:
        selected_scope = _scope(payload.scope)
        plan = build_adaptive_study_plan(
            total_minutes=payload.total_minutes,
            max_items=payload.max_items,
            session_limit=payload.session_limit,
            attempt_limit=payload.attempt_limit,
            scope=selected_scope,
        )
        result = generate_coaching_plan(plan, scope=selected_scope)
    except LookupError as error:
        raise ApiError(
            status_code=404,
            code="scope_not_found",
            message="Coaching scope was not found.",
        ) from error
    except ValueError as error:
        raise ApiError(
            status_code=422,
            code="invalid_scope",
            message="Coaching request is invalid.",
        ) from error
    except RuntimeError as error:
        raise ApiError(
            status_code=502,
            code="coaching_generation_failed",
            message="Grounded coaching generation failed.",
        ) from error
    event = record_adaptation_event(result.adaptation)
    return CoachingPlanResponse(
        plan=_plan(plan),
        generated_count=result.generated_count,
        rejected_count=result.rejected_count,
        items=[
            CoachingActivityResponse(
                plan_item=_plan_item(item.plan_item),
                should_generate=item.activity.should_generate,
                coaching_mode=item.activity.coaching_mode,
                topic=item.activity.topic,
                objective=item.activity.objective,
                review_step=item.activity.review_step,
                practice_step=item.activity.practice_step,
                reassessment_question=item.activity.reassessment_question,
                expected_answer=item.activity.expected_answer,
                completion_criteria=item.activity.completion_criteria,
                source_indexes=item.activity.source_indexes,
                confidence=item.activity.confidence,
                reason=item.activity.reason,
                sources=[_source(source) for source in item.sources],
            )
            for item in result.items
        ],
        adaptation=_adaptation(result.adaptation, event.id),
    )


@router.get("/system/integrity", response_model=IntegrityResponse)
@router.get(
    "/integrity",
    response_model=IntegrityResponse,
    include_in_schema=False,
)
def get_integrity() -> IntegrityResponse:
    report = run_study_integrity_check()
    return IntegrityResponse(
        passed=report.passed,
        error_count=report.error_count,
        warning_count=report.warning_count,
        table_counts=dict(report.table_counts),
        issues=[
            IntegrityIssueResponse(
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                record_type=issue.record_type,
                record_id=issue.record_id,
            )
            for issue in report.issues
        ],
    )
