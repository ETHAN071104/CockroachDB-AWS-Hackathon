import {
  BrainCircuit,
  Check,
  ClipboardList,
  Lightbulb,
  ListChecks,
  SkipForward,
  Sparkles,
  X,
} from 'lucide-react';
import {
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useSearchParams } from 'react-router-dom';

import { apiClient } from '../api/client';
import type {
  CoachingPlan,
  PresentedQuiz,
  QuizAnswer,
  QuizSubmission,
  RetrievalScope,
  ReviewAction,
  ReviewQueue,
  StudyPlan,
  StudyPlanRequest,
} from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  OutcomeBadge,
  PageHeader,
  ProgressBar,
  SectionHeader,
  SourceCard,
} from '../components';
import { useApiQuery, useAsyncAction } from '../hooks';
import { errorMessage, formatPercent } from '../utils/format';

type ActionView = 'review' | 'quiz' | 'plan' | 'coaching';

const ACTION_TABS: Array<{ id: ActionView; label: string }> = [
  { id: 'review', label: 'Review' },
  { id: 'quiz', label: 'Quiz' },
  { id: 'plan', label: 'Study plan' },
  { id: 'coaching', label: 'Coaching' },
];

export function StudyActionsPage() {
  const [searchParams] = useSearchParams();
  const scopedTopicId = searchParams.get('topic_id');
  const scopedNotebookId = Number(searchParams.get('notebook_id'));
  const scopedDocumentIds = searchParams
    .getAll('document_ids')
    .map(Number)
    .filter((documentId) => Number.isInteger(documentId) && documentId > 0);
  const scopeLabel = searchParams.get('scope_name') ?? searchParams.get('topic') ?? '';
  const scope: RetrievalScope | undefined = scopedTopicId
    ? { topic_id: scopedTopicId }
    : Number.isInteger(scopedNotebookId) && scopedNotebookId > 0
      ? { notebook_id: scopedNotebookId }
      : scopedDocumentIds.length
        ? { document_ids: [...new Set(scopedDocumentIds)] }
        : undefined;
  const [view, setView] = useState<ActionView>(scopedTopicId ? 'quiz' : 'review');

  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const lastIndex = ACTION_TABS.length - 1;
    const nextIndex =
      event.key === 'ArrowRight'
        ? (index + 1) % ACTION_TABS.length
        : event.key === 'ArrowLeft'
          ? (index - 1 + ACTION_TABS.length) % ACTION_TABS.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? lastIndex
              : null;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = ACTION_TABS[nextIndex];
    if (!nextTab) return;
    setView(nextTab.id);
    requestAnimationFrame(() => {
      document.getElementById(`tab-${nextTab.id}`)?.focus();
    });
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Active study"
        title="Study actions"
        description={
          scope
            ? `Work only from ${scopeLabel || 'the selected study scope'}.`
            : 'Turn stored outcomes and grounded sources into the next useful study step.'
        }
        actions={scope ? <Badge tone="primary">Scoped study</Badge> : null}
      />

      <div className="tabs" role="tablist" aria-label="Study action">
        {ACTION_TABS.map((tab, index) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-controls={`panel-${tab.id}`}
            aria-selected={view === tab.id}
            tabIndex={view === tab.id ? 0 : -1}
            className={view === tab.id ? 'is-active' : ''}
            onClick={() => setView(tab.id)}
            onKeyDown={(event) => handleTabKey(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${view}`} aria-labelledby={`tab-${view}`}>
        {view === 'review' ? <ReviewWorkspace scope={scope} /> : null}
        {view === 'quiz' ? (
          <QuizWorkspace scope={scope} initialTopic={scopeLabel} />
        ) : null}
        {view === 'plan' ? <PlanWorkspace scope={scope} /> : null}
        {view === 'coaching' ? <CoachingWorkspace scope={scope} /> : null}
      </div>
    </div>
  );
}

function ReviewWorkspace({ scope }: { scope?: RetrievalScope }) {
  const reviewQueuePath = scope?.topic_id
    ? `/api/study/actions/review-queue?topic_id=${encodeURIComponent(scope.topic_id)}`
    : scope?.notebook_id
      ? `/api/study/actions/review-queue?notebook_id=${scope.notebook_id}`
      : scope?.document_ids
        ? `/api/study/actions/review-queue?${scope.document_ids
            .map((documentId) => `document_ids=${documentId}`)
            .join('&')}`
        : '/api/study/actions/review-queue';
  const queue = useApiQuery<ReviewQueue>(
    ['review-queue', reviewQueuePath],
    (signal) => apiClient.get(reviewQueuePath, { signal }),
  );
  const [result, setResult] = useState<ReviewAction | null>(null);
  const generate = useAsyncAction((interactionId: number, signal: AbortSignal) =>
    apiClient.post<ReviewAction, { interaction_id: number; scope: RetrievalScope | null }>(
      '/api/study/actions/review',
      { interaction_id: interactionId, scope: scope ?? null },
      { signal },
    ),
  );

  async function handleGenerate(interactionId: number) {
    try {
      setResult(await generate.run(interactionId));
    } catch {
      // Queue stays visible for retry.
    }
  }

  return (
    <div className="page-stack">
      <SectionHeader
        title="Review queue"
        description="Partial and confused outcomes rise to the top using deterministic priority."
      />
      {queue.isLoading ? <LoadingState message="Reading unresolved outcomes…" /> : null}
      {queue.error ? (
        <ErrorState message={errorMessage(queue.error)} onRetry={() => void queue.reload()} />
      ) : null}
      {queue.data?.adaptation?.adapted_using_learner_memory ? (
        <Notice tone="info">
          Review order adapted using learner memory or learning signals. {queue.data.adaptation.reason}
        </Notice>
      ) : null}
      {queue.data?.items.length ? (
        <div className="review-grid">
          {queue.data.items.map((item) => (
            <Card key={item.interaction_id} className="review-card">
              <div className="review-card__header">
                <OutcomeBadge outcome={item.outcome} />
                <Badge tone="neutral">Priority {item.priority_score}</Badge>
              </div>
              <h3>{item.question}</h3>
              <p>{item.reason}</p>
              <small>{item.source_filenames.join(', ') || 'No source lineage recorded'}</small>
              <Button
                icon={<Sparkles size={18} aria-hidden="true" />}
                onClick={() => void handleGenerate(item.interaction_id)}
                loading={generate.isPending && generate.status === 'pending'}
                loadingText="Generating…"
              >
                Generate review activity
              </Button>
            </Card>
          ))}
        </div>
      ) : !queue.isLoading && !queue.error ? (
        <EmptyState
          title="Review queue is clear"
          description="Mark a chat answer partial or confused when it needs another pass."
        />
      ) : null}
      {generate.error ? <Notice tone="error">{errorMessage(generate.error)}</Notice> : null}
      {result ? (
        <Card tone="accent" className="reading-card">
          <Badge tone={result.should_generate ? 'success' : 'warning'}>
            {result.should_generate ? result.review_mode : 'Not generated'}
          </Badge>
          <h2>{result.topic || 'Review activity'}</h2>
          {result.adaptation?.adapted_using_learner_memory ? (
            <Notice tone="info">Why this review changed: {result.adaptation.reason}</Notice>
          ) : null}
          {result.should_generate ? (
            <>
              <h3>Explanation</h3>
              <p>{result.explanation}</p>
              <h3>Worked example</h3>
              <p>{result.worked_example}</p>
              <h3>Check your understanding</h3>
              <p>{result.check_question}</p>
              <details>
                <summary>Show expected answer</summary>
                <p>{result.expected_answer}</p>
              </details>
              <div className="source-grid">
                {result.sources.map((source) => (
                  <SourceCard key={`${source.index}-${source.document_id}`} source={source} />
                ))}
              </div>
            </>
          ) : (
            <p>{result.reason}</p>
          )}
        </Card>
      ) : null}
    </div>
  );
}

function QuizWorkspace({
  scope,
  initialTopic,
}: {
  scope?: RetrievalScope;
  initialTopic: string;
}) {
  const [topic, setTopic] = useState(initialTopic);
  const [questionCount, setQuestionCount] = useState(3);
  const [quiz, setQuiz] = useState<PresentedQuiz | null>(null);
  const [answers, setAnswers] = useState<QuizAnswer[]>([]);
  const [submission, setSubmission] = useState<QuizSubmission | null>(null);
  const [proposalDrafts, setProposalDrafts] = useState<Record<string, string>>({});
  const [decidedProposals, setDecidedProposals] = useState<Record<string, string>>({});
  const generate = useAsyncAction((
    requestedTopic: string,
    count: number,
    signal: AbortSignal,
  ) =>
    apiClient.post<PresentedQuiz>(
      '/api/study/actions/quizzes/generate',
      {
        topic: requestedTopic,
        question_count: count,
        ...(scope ?? {}),
      },
      { signal },
    ),
  );
  const submit = useAsyncAction((
    quizId: string,
    responses: QuizAnswer[],
    signal: AbortSignal,
  ) =>
    apiClient.post<QuizSubmission, { responses: QuizAnswer[] }>(
      `/api/study/actions/quizzes/${encodeURIComponent(quizId)}/submit`,
      { responses },
      { signal },
    ),
  );
  const decideProposal = useAsyncAction((
    proposalId: string,
    decision: 'accept' | 'reject',
    editedContent: string | null,
    signal: AbortSignal,
  ) =>
    apiClient.post<{ consumed: boolean }>(
      `/api/memories/proposals/${encodeURIComponent(proposalId)}/decision`,
      {
        decision,
        edited_content: decision === 'accept' ? editedContent : null,
      },
      { signal },
    ),
  );

  const currentQuestion = quiz?.questions[answers.length];

  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestedTopic = topic.trim();
    if (!requestedTopic) return;
    try {
      setQuiz(await generate.run(requestedTopic, questionCount));
      setAnswers([]);
      setSubmission(null);
    } catch {
      // Preserve quiz setup input.
    }
  }

  function recordAnswer(selectedOption: number | null) {
    if (!currentQuestion || submit.isPending) return;
    setAnswers((current) => [
      ...current,
      {
        question_number: currentQuestion.question_number,
        selected_option: selectedOption,
      },
    ]);
  }

  async function handleSubmit() {
    if (!quiz) return;
    try {
      setSubmission(await submit.run(quiz.quiz_id, answers));
      apiClient.invalidate({ prefix: '/api/reports/quizzes' });
      apiClient.invalidate('/api/dashboard');
    } catch {
      // Pending server quiz is retained after a validation failure.
    }
  }

  async function handleProposalDecision(
    proposalId: string,
    decision: 'accept' | 'reject',
    originalContent: string,
  ) {
    try {
      const draft = proposalDrafts[proposalId] ?? originalContent;
      await decideProposal.run(
        proposalId,
        decision,
        draft === originalContent ? null : draft,
      );
      setDecidedProposals((current) => ({ ...current, [proposalId]: decision }));
      apiClient.invalidate({ prefix: '/api/memories' });
    } catch {
      // The durable proposal remains available for retry.
    }
  }

  function resetQuiz() {
    setQuiz(null);
    setAnswers([]);
    setSubmission(null);
    generate.reset();
    submit.reset();
    decideProposal.reset();
    setProposalDrafts({});
    setDecidedProposals({});
  }

  if (submission) {
    return (
      <div className="page-stack">
        <SectionHeader
          title="Quiz result"
          actions={<Button variant="secondary" onClick={resetQuiz}>Start another quiz</Button>}
        />
        <div className="metric-grid">
          <Card>
            <p className="metric-label">Score</p>
            <p className="metric-value">{formatPercent(submission.score_percentage)}</p>
          </Card>
          <Card>
            <p className="metric-label">Answered accuracy</p>
            <p className="metric-value">{formatPercent(submission.accuracy_percentage)}</p>
          </Card>
          <Card>
            <p className="metric-label">Correct</p>
            <p className="metric-value">
              {submission.correct_answers}/{submission.total_questions}
            </p>
          </Card>
          <Card>
            <p className="metric-label">Status</p>
            <p className="metric-value metric-value--compact">{submission.status}</p>
          </Card>
        </div>
        {submission.detected_weaknesses?.length ? (
          <Card tone="accent">
            <h2>Detected weaknesses</h2>
            <ul>
              {submission.detected_weaknesses.map((weakness) => (
                <li key={weakness}>{weakness}</li>
              ))}
            </ul>
          </Card>
        ) : null}
        {submission.learning_signals?.length ? (
          <div className="page-stack">
            <SectionHeader title="Learning signals" description="Trusted quiz outcomes, kept separate from confirmed memory." />
            {submission.learning_signals.map((signal) => (
              <Card key={signal.id}>
                <div className="quiz-feedback__header">
                  <Badge tone={signal.status === 'resolved' ? 'success' : 'warning'}>
                    {signal.signal_type.replaceAll('_', ' ')}
                  </Badge>
                  <span>{signal.occurrence_count} observation(s)</span>
                </div>
                <h3>{signal.topic}</h3>
                <p>{signal.statement}</p>
                <ProgressBar label="Confidence" value={signal.confidence} max={1} />
                <details>
                  <summary>Supporting evidence</summary>
                  <ul>
                    {signal.evidence.map((evidence, index) => (
                      <li key={`${signal.id}-${index}`}>
                        {String(evidence.question ?? 'Quiz question')} — {String(evidence.outcome ?? 'observed')}
                      </li>
                    ))}
                  </ul>
                </details>
              </Card>
            ))}
          </div>
        ) : null}
        {submission.memory_proposals?.length ? (
          <div className="page-stack">
            <SectionHeader title="Memory proposals" description="Review, edit, accept, or reject before anything becomes active learner memory." />
            {submission.memory_proposals.map((proposal) => {
              const decision = decidedProposals[proposal.proposal_id];
              return (
                <Card key={proposal.proposal_id} tone="accent">
                  <Badge tone={decision ? 'success' : 'info'}>
                    {decision ? `${decision}ed` : 'Approval required'}
                  </Badge>
                  <p>{proposal.reason}</p>
                  <label>
                    Proposed memory
                    <textarea
                      value={proposalDrafts[proposal.proposal_id] ?? proposal.content}
                      onChange={(event) => setProposalDrafts((current) => ({
                        ...current,
                        [proposal.proposal_id]: event.target.value,
                      }))}
                      disabled={Boolean(decision)}
                      rows={3}
                    />
                  </label>
                  <ProgressBar label="Confidence" value={proposal.confidence} max={1} />
                  {!decision ? (
                    <div className="card-actions">
                      <Button
                        onClick={() => void handleProposalDecision(proposal.proposal_id, 'accept', proposal.content)}
                        loading={decideProposal.isPending}
                      >
                        Accept{proposalDrafts[proposal.proposal_id] && proposalDrafts[proposal.proposal_id] !== proposal.content ? ' edited memory' : ''}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => void handleProposalDecision(proposal.proposal_id, 'reject', proposal.content)}
                        disabled={decideProposal.isPending}
                      >
                        Reject
                      </Button>
                    </div>
                  ) : null}
                </Card>
              );
            })}
            {decideProposal.error ? <Notice tone="error">{errorMessage(decideProposal.error)} The proposal remains available.</Notice> : null}
          </div>
        ) : null}
        <div className="page-stack">
          {submission.feedback.map((feedback) => (
            <Card key={feedback.question_number} className="quiz-feedback">
              <div className="quiz-feedback__header">
                {feedback.is_correct ? (
                  <Badge tone="success" icon={<Check size={16} aria-hidden="true" />}>Correct</Badge>
                ) : feedback.skipped ? (
                  <Badge tone="warning" icon={<SkipForward size={16} aria-hidden="true" />}>Skipped</Badge>
                ) : (
                  <Badge tone="danger" icon={<X size={16} aria-hidden="true" />}>Incorrect</Badge>
                )}
                <span>Question {feedback.question_number}</span>
              </div>
              <h3>{feedback.question}</h3>
              <p>
                Correct option: <strong>{feedback.correct_option}</strong>
              </p>
              <p>{feedback.explanation}</p>
              <div className="source-grid">
                {feedback.sources.map((source) => (
                  <SourceCard key={`${feedback.question_number}-${source.index}`} source={source} />
                ))}
              </div>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (quiz) {
    const complete = answers.length === quiz.questions.length;
    return (
      <div className="page-stack quiz-workspace">
        <SectionHeader
          title={quiz.topic}
          description={`${answers.length} of ${quiz.questions.length} questions presented.`}
          actions={<Button variant="ghost" onClick={resetQuiz}>Cancel quiz</Button>}
        />
        {quiz.adaptation?.adapted_using_learner_memory ? (
          <Notice tone="info">
            Adapted using learner memory. Target: {quiz.adaptation.targeted_topic ?? quiz.topic}; difficulty: {quiz.adaptation.difficulty ?? 'standard'}. {quiz.adaptation.reason}
          </Notice>
        ) : null}
        <ProgressBar value={answers.length} max={quiz.questions.length} label="Quiz progress" />
        {currentQuestion ? (
          <Card className="quiz-question">
            <p className="eyebrow">Question {currentQuestion.question_number}</p>
            <h2>{currentQuestion.question}</h2>
            <div className="quiz-options" role="group" aria-label="Answer options">
              {currentQuestion.options.map((option, index) => (
                <button
                  type="button"
                  key={`${currentQuestion.question_number}-${option}`}
                  onClick={() => recordAnswer(index + 1)}
                >
                  <span>{String.fromCharCode(65 + index)}</span>
                  <span>{option}</span>
                </button>
              ))}
            </div>
            <div className="card-actions">
              <Button
                variant="secondary"
                icon={<SkipForward size={18} aria-hidden="true" />}
                onClick={() => recordAnswer(null)}
              >
                Skip question
              </Button>
              <Button variant="ghost" onClick={() => void handleSubmit()} disabled={!answers.length}>
                Finish now
              </Button>
            </div>
          </Card>
        ) : complete ? (
          <Card tone="accent" className="quiz-ready">
            <ListChecks size={32} aria-hidden="true" />
            <h2>Ready to submit</h2>
            <p>
              The server will derive correctness from its trusted quiz registry. Answers and
              explanations have not been exposed yet.
            </p>
            <Button onClick={() => void handleSubmit()} loading={submit.isPending} loadingText="Scoring…">
              Submit answers
            </Button>
          </Card>
        ) : null}
        {submit.error ? <Notice tone="error">{errorMessage(submit.error)}</Notice> : null}
      </div>
    );
  }

  return (
    <div className="page-stack">
      <SectionHeader
        title="Grounded quiz"
        description="Correct options and explanations stay on the server until you submit."
      />
      <Card>
        <form className="form-stack" onSubmit={handleGenerate}>
          <label>
            Quiz topic
            <input
              required
              maxLength={300}
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="For example: cellular respiration"
            />
          </label>
          <label>
            Number of questions
            <select
              value={questionCount}
              onChange={(event) => setQuestionCount(Number(event.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((count) => (
                <option value={count} key={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
          {generate.error ? <Notice tone="error">{errorMessage(generate.error)}</Notice> : null}
          <Button
            type="submit"
            icon={<BrainCircuit size={18} aria-hidden="true" />}
            loading={generate.isPending}
            loadingText="Generating grounded quiz…"
          >
            Generate quiz
          </Button>
        </form>
      </Card>
    </div>
  );
}

function PlanWorkspace({ scope }: { scope?: RetrievalScope }) {
  const [minutes, setMinutes] = useState(45);
  const [maxItems, setMaxItems] = useState(5);
  const [plan, setPlan] = useState<StudyPlan | null>(null);
  const build = useAsyncAction((payload: StudyPlanRequest, signal: AbortSignal) =>
    apiClient.post<StudyPlan, StudyPlanRequest>('/api/study/actions/plan', payload, { signal }),
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setPlan(
        await build.run({
          total_minutes: minutes,
          max_items: maxItems,
          scope: scope ?? null,
        }),
      );
    } catch {
      // Keep form values for retry.
    }
  }

  return (
    <div className="page-stack">
      <SectionHeader title="Adaptive study plan" description="A deterministic plan built from stored outcomes and quiz gaps." />
      <Card>
        <PlanForm
          minutes={minutes}
          maxItems={maxItems}
          onMinutes={setMinutes}
          onMaxItems={setMaxItems}
          onSubmit={handleSubmit}
          pending={build.isPending}
          submitLabel="Build study plan"
          error={build.error}
        />
      </Card>
      {plan ? <PlanResult plan={plan} /> : null}
    </div>
  );
}

function CoachingWorkspace({ scope }: { scope?: RetrievalScope }) {
  const [minutes, setMinutes] = useState(45);
  const [maxItems, setMaxItems] = useState(4);
  const [coaching, setCoaching] = useState<CoachingPlan | null>(null);
  const build = useAsyncAction((payload: StudyPlanRequest, signal: AbortSignal) =>
    apiClient.post<CoachingPlan, StudyPlanRequest>(
      '/api/study/actions/coaching-plan',
      payload,
      { signal },
    ),
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setCoaching(
        await build.run({
          total_minutes: minutes,
          max_items: maxItems,
          scope: scope ?? null,
        }),
      );
    } catch {
      // Preserve setup after provider or evidence failure.
    }
  }

  return (
    <div className="page-stack">
      <SectionHeader title="Grounded coaching" description="First builds the deterministic plan, then generates cited practice only when plan items exist." />
      <Card>
        <PlanForm
          minutes={minutes}
          maxItems={maxItems}
          onMinutes={setMinutes}
          onMaxItems={setMaxItems}
          onSubmit={handleSubmit}
          pending={build.isPending}
          submitLabel="Generate coaching plan"
          error={build.error}
        />
      </Card>
      {coaching ? (
        coaching.items.length ? (
          <div className="page-stack">
            {coaching.adaptation ? (
              <Notice tone="info">
                <strong>Why this was recommended:</strong> {coaching.adaptation.reason} What changed: {Object.keys(coaching.adaptation.applied_changes).join(', ') || 'no learner-specific change'}.
              </Notice>
            ) : null}
            {coaching.items.map((item) => (
              <Card key={`${item.plan_item.rank}-${item.plan_item.title}`} className="reading-card">
                <Badge tone={item.should_generate ? 'success' : 'warning'}>
                  {item.should_generate ? item.coaching_mode : 'Not generated'}
                </Badge>
                <h2>{item.topic || item.plan_item.title}</h2>
                {item.should_generate ? (
                  <div className="coaching-steps">
                    <StudyStep icon={<Lightbulb />} label="Objective" text={item.objective} />
                    <StudyStep icon={<ClipboardList />} label="Review" text={item.review_step} />
                    <StudyStep icon={<BrainCircuit />} label="Practice" text={item.practice_step} />
                    <StudyStep icon={<ListChecks />} label="Reassess" text={item.reassessment_question} />
                    <details>
                      <summary>Show expected answer and completion criteria</summary>
                      <p>{item.expected_answer}</p>
                      <p>{item.completion_criteria}</p>
                    </details>
                    <div className="source-grid">
                      {item.sources.map((source) => (
                        <SourceCard key={`${item.plan_item.rank}-${source.index}`} source={source} />
                      ))}
                    </div>
                  </div>
                ) : (
                  <p>{item.reason}</p>
                )}
              </Card>
            ))}
          </div>
        ) : (
          <EmptyState title="No coaching items" description="Complete a study session or quiz to collect unresolved evidence first." />
        )
      ) : null}
    </div>
  );
}

interface PlanFormProps {
  minutes: number;
  maxItems: number;
  onMinutes: (value: number) => void;
  onMaxItems: (value: number) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  pending: boolean;
  submitLabel: string;
  error: unknown;
}

function PlanForm({
  minutes,
  maxItems,
  onMinutes,
  onMaxItems,
  onSubmit,
  pending,
  submitLabel,
  error,
}: PlanFormProps) {
  return (
    <form className="form-stack" onSubmit={onSubmit}>
      <div className="form-grid">
        <label>
          Available minutes
          <input
            type="number"
            min={10}
            max={240}
            value={minutes}
            onChange={(event) => onMinutes(Number(event.target.value))}
          />
        </label>
        <label>
          Maximum items
          <input
            type="number"
            min={1}
            max={20}
            value={maxItems}
            onChange={(event) => onMaxItems(Number(event.target.value))}
          />
        </label>
      </div>
      {error ? <Notice tone="error">{errorMessage(error)}</Notice> : null}
      <Button type="submit" loading={pending} loadingText="Building plan…">
        {submitLabel}
      </Button>
    </form>
  );
}

function PlanResult({ plan }: { plan: StudyPlan }) {
  const allocated = useMemo(
    () => plan.items.reduce((total, item) => total + item.estimated_minutes, 0),
    [plan.items],
  );
  if (!plan.items.length) {
    return <EmptyState title="No unresolved evidence" description="The plan stays empty rather than inventing study work." />;
  }
  return (
    <div className="page-stack">
      {plan.adaptation ? (
        <Notice tone="info">
          <strong>Why this was recommended:</strong> {plan.adaptation.reason} Memory or signal used: {[...plan.adaptation.memory_ids.map(String), ...plan.adaptation.learning_signal_ids].join(', ') || 'none'}. What changed: {Object.keys(plan.adaptation.applied_changes).join(', ') || 'none'}.
        </Notice>
      ) : null}
      <ProgressBar label="Time allocated" value={allocated} max={plan.requested_minutes} />
      <ol className="plan-list">
        {plan.items.map((item) => (
          <li key={`${item.rank}-${item.title}`}>
            <Card>
              <div className="plan-item__header">
                <span className="plan-rank">{item.rank}</span>
                <div>
                  <h3>{item.title}</h3>
                  <p>{item.estimated_minutes} minutes</p>
                </div>
                <Badge tone="info">Priority {item.priority_score}</Badge>
              </div>
              <p>{item.action}</p>
              <ul>
                {item.evidence.map((evidence) => (
                  <li key={`${evidence.evidence_type}-${evidence.reference_id}`}>
                    {evidence.detail}
                  </li>
                ))}
              </ul>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StudyStep({ icon, label, text }: { icon: ReactNode; label: string; text: string }) {
  return (
    <div className="study-step">
      <span aria-hidden="true">{icon}</span>
      <div>
        <h3>{label}</h3>
        <p>{text}</p>
      </div>
    </div>
  );
}
