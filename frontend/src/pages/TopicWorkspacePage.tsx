import { BookOpenCheck, MessageCircleQuestion, RefreshCw, Sparkles } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';

import { ApiError, apiClient } from '../api/client';
import type { ChatResponse, StudyInteraction, StudyOutcome, Summary, Topic } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  ErrorState,
  FeatureRedirectCard,
  LoadingState,
  Notice,
  OutcomeBadge,
  PageHeader,
  ProgressBar,
  SectionHeader,
  SourceCard,
} from '../components';
import { useApiQuery, useAsyncAction } from '../hooks';
import { errorMessage, formatDateTime } from '../utils/format';

export function TopicWorkspacePage() {
  const { topicId = '' } = useParams();
  const [question, setQuestion] = useState('');
  const [chatResult, setChatResult] = useState<ChatResponse | null>(null);
  const [outcome, setOutcome] = useState<StudyOutcome>('unrated');

  const topic = useApiQuery<Topic>(
    `topic-${topicId}`,
    (signal) => apiClient.get(`/api/topics/${encodeURIComponent(topicId)}`, { signal }),
  );
  const summary = useApiQuery<Summary>(
    `topic-summary-${topicId}`,
    (signal) =>
      apiClient.get(`/api/topics/${encodeURIComponent(topicId)}/summary`, {
        signal,
      }),
  );
  const generateSummary = useAsyncAction((signal: AbortSignal) =>
    apiClient.post<Summary>(
      `/api/topics/${encodeURIComponent(topicId)}/summary`,
      undefined,
      { signal },
    ),
  );
  const ask = useAsyncAction((prompt: string, signal: AbortSignal) =>
    apiClient.post<ChatResponse, { question: string; topic_id: string }>(
      '/api/chat',
      { question: prompt, topic_id: topicId },
      { signal },
    ),
  );
  const rate = useAsyncAction((
    interactionId: number,
    nextOutcome: StudyOutcome,
    signal: AbortSignal,
  ) =>
    apiClient.patch<StudyInteraction, { outcome: StudyOutcome }>(
      `/api/study/interactions/${interactionId}/outcome`,
      { outcome: nextOutcome },
      { signal },
    ),
  );

  const summaryMissing =
    summary.error instanceof ApiError
    && (
      summary.error.code === 'SUMMARY_NOT_GENERATED'
      || summary.error.legacyCode === 'summary_not_generated'
    );

  async function handleSummary() {
    try {
      const generated = await generateSummary.run();
      summary.setData(generated);
      apiClient.invalidate(`/api/topics/${encodeURIComponent(topicId)}/summary`);
    } catch {
      // The previous cached summary remains visible and retryable.
    }
  }

  async function handleQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt) return;
    try {
      const result = await ask.run(prompt);
      setChatResult(result);
      setQuestion('');
      setOutcome('unrated');
    } catch {
      // Preserve question after recoverable failure.
    }
  }

  async function handleOutcome(nextOutcome: StudyOutcome) {
    if (!chatResult) return;
    try {
      await rate.run(chatResult.interaction_id, nextOutcome);
      setOutcome(nextOutcome);
      apiClient.invalidate({ prefix: '/api/reports' });
      apiClient.invalidate('/api/dashboard');
    } catch {
      // Keep the current visible rating until persistence succeeds.
    }
  }

  if (topic.isLoading) return <LoadingState message="Opening topic workspace…" />;
  if (topic.error) {
    return <ErrorState message={errorMessage(topic.error)} onRetry={() => void topic.reload()} />;
  }
  if (!topic.data) return <EmptyState title="Topic not found" />;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Topic workspace"
        title={topic.data.name}
        description={topic.data.description}
        actions={
          <Link
            className="button button--secondary"
            to={`/study-actions?topic_id=${encodeURIComponent(topic.data.id)}&topic=${encodeURIComponent(topic.data.name)}`}
          >
            <BookOpenCheck size={18} aria-hidden="true" />
            <span>Study this topic</span>
          </Link>
        }
      />

      <div className="workspace-grid">
        <div className="page-stack">
          <section aria-labelledby="topic-summary-title">
            <SectionHeader
              headingId="topic-summary-title"
              title="Grounded summary"
              actions={
                <Button
                  variant={summary.data ? 'secondary' : 'primary'}
                  icon={
                    summary.data ? (
                      <RefreshCw size={18} aria-hidden="true" />
                    ) : (
                      <Sparkles size={18} aria-hidden="true" />
                    )
                  }
                  onClick={() => void handleSummary()}
                  loading={generateSummary.isPending}
                  loadingText="Generating…"
                >
                  {summary.data ? 'Regenerate' : 'Generate summary'}
                </Button>
              }
            />
            {summary.isLoading ? <LoadingState compact message="Checking summary cache…" /> : null}
            {summary.error && !summaryMissing ? (
              <ErrorState
                compact
                message={errorMessage(summary.error)}
                onRetry={() => void summary.reload()}
              />
            ) : null}
            {generateSummary.error ? (
              <ErrorNotice
                error={generateSummary.error}
                onRetry={() => generateSummary.retry()}
              />
            ) : null}
            {summary.data ? (
              <Card className="reading-card">
                <div className="summary-heading">
                  <div>
                    <Badge tone={summary.data.stale ? 'warning' : 'success'}>
                      {summary.data.stale ? 'Sources changed' : 'Current'}
                    </Badge>
                    <h3>{summary.data.summary.title}</h3>
                  </div>
                  <ProgressBar
                    label="Evidence support"
                    value={summary.data.summary.confidence}
                    max={1}
                  />
                </div>
                <p className="reading-copy">{summary.data.summary.overview}</p>
                <ul className="key-point-list">
                  {summary.data.summary.key_points.map((point, index) => (
                    <li key={`${point.text}-${index}`}>
                      {point.text}{' '}
                      <span className="citation-indexes" aria-label="Citations">
                        {point.source_indexes.map((sourceIndex) => `[${sourceIndex}]`).join(' ')}
                      </span>
                    </li>
                  ))}
                </ul>
                <small>Generated {formatDateTime(summary.data.generated_at)}</small>
                <details className="citation-disclosure">
                  <summary>{summary.data.sources.length} cited sources</summary>
                  <div className="source-grid">
                    {summary.data.sources.map((source) => (
                      <SourceCard
                        key={`summary-${source.document_id}-${source.chunk_index}-${source.index}`}
                        source={source}
                      />
                    ))}
                  </div>
                </details>
              </Card>
            ) : !summary.isLoading && (!summary.error || summaryMissing) ? (
              <EmptyState
                title="No topic summary yet"
                description="Generation uses only the exact document and chunk pairs cited by this topic."
              />
            ) : null}
          </section>

          <section aria-labelledby="topic-question-title">
            <SectionHeader
              headingId="topic-question-title"
              title="Ask about this topic"
              description="Ask from this topic's uploaded source excerpts. For weakness analysis, use Coaching."
            />
            <Card>
              <form className="composer" onSubmit={handleQuestion}>
                <label htmlFor="topic-question">Question</label>
                <textarea
                  id="topic-question"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  maxLength={4000}
                  required
                  placeholder={`Ask a grounded question about ${topic.data.name}`}
                />
                <div className="composer__footer">
                  <span>{question.length}/4000</span>
                  <Button
                    type="submit"
                    icon={<MessageCircleQuestion size={18} aria-hidden="true" />}
                    loading={ask.isPending}
                    loadingText="Finding evidence…"
                  >
                    Ask question
                  </Button>
                </div>
                {ask.error ? (
                  <ErrorNotice
                    error={ask.error}
                    onRetry={() => ask.retry()}
                  />
                ) : null}
              </form>
            </Card>
          </section>

          {chatResult ? (
            <section aria-labelledby="topic-answer-title">
              <SectionHeader
                headingId="topic-answer-title"
                title={
                  chatResult.type === 'feature_redirect'
                    ? 'Suggested study tool'
                    : 'Grounded answer'
                }
                actions={
                  chatResult.type === 'feature_redirect'
                    ? null
                    : <OutcomeBadge outcome={outcome} />
                }
              />
              <Card className="reading-card">
                {chatResult.type === 'feature_redirect' && chatResult.redirect ? (
                  <FeatureRedirectCard redirect={chatResult.redirect} />
                ) : (
                  <>
                    <p className="answer-copy">{chatResult.answer}</p>
                    {chatResult.suggested_question ? (
                      <Notice tone="info" title="Try a document-grounded question">
                        “{chatResult.suggested_question}”
                      </Notice>
                    ) : null}
                    <fieldset className="outcome-controls" disabled={rate.isPending}>
                      <legend>How well did you understand this answer?</legend>
                      {(['understood', 'partial', 'confused', 'unrated'] as StudyOutcome[]).map(
                        (value) => (
                          <button
                            type="button"
                            className={outcome === value ? 'is-selected' : ''}
                            aria-pressed={outcome === value}
                            key={value}
                            onClick={() => void handleOutcome(value)}
                          >
                            <OutcomeBadge outcome={value} />
                          </button>
                        ),
                      )}
                    </fieldset>
                    {rate.error ? <Notice tone="error">{errorMessage(rate.error)}</Notice> : null}
                  </>
                )}
              </Card>
              {chatResult.type !== 'feature_redirect' ? (
                <div className="source-grid">
                  {chatResult.sources.map((source) => (
                    <SourceCard key={`${source.index}-${source.document_id}`} source={source} />
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
        </div>

        <aside className="workspace-aside" aria-labelledby="topic-evidence-title">
          <SectionHeader headingId="topic-evidence-title" title="Topic evidence" />
          <div className="source-stack">
            {topic.data.sources.map((source) => (
              <SourceCard key={`${source.index}-${source.document_id}-${source.chunk_index}`} source={source} />
            ))}
          </div>
          {topic.data.stale ? (
            <Notice tone="warning">
              A cited source changed after extraction. Regenerate topics from the source workspace.
            </Notice>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
