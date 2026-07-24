import { useMemo, useState, type FormEvent } from "react";
import {
  BookOpen,
  Check,
  CircleStop,
  FileText,
  Globe2,
  Lightbulb,
  MessageCircleMore,
  NotebookTabs,
  Send,
  Tags,
} from "lucide-react";

import {
  api,
  getErrorMessage,
  isPublicId,
  type ChatRequest,
  type ChatResponse,
  type MemoryDecision,
  type PublicId,
  type StudyOutcome,
} from "../api";
import {
  Badge,
  Button,
  Card,
  ConfirmationDialog,
  EmptyState,
  ErrorNotice,
  FeatureRedirectCard,
  Notice,
  OutcomeBadge,
  PageHeader,
  SourceCard,
} from "../components";
import { useApiQuery, useAsyncAction } from "../hooks";

type ScopeKind = "global" | "notebook" | "document" | "topic";

interface ChatExchange {
  question: string;
  response: ChatResponse;
}

interface OutcomeActionArgs {
  interactionId: PublicId;
  outcome: StudyOutcome;
}

interface ProposalActionArgs {
  proposalId: string;
  decision: MemoryDecision;
}

const OUTCOME_OPTIONS: Array<{
  value: Exclude<StudyOutcome, "unrated">;
  label: string;
}> = [
  { value: "understood", label: "I understand" },
  { value: "partial", label: "Partly" },
  { value: "confused", label: "I’m confused" },
];

const DECISION_LABELS: Record<MemoryDecision, string> = {
  accept: "Save memory",
  replace: "Replace old memory",
  keep_both: "Keep both",
  reject: "Reject",
  cancel: "Decide later",
};

function buildChatRequest(
  question: string,
  scopeKind: ScopeKind,
  scopeValue: string,
): ChatRequest | null {
  const payload: ChatRequest = { question: question.trim() };
  if (scopeKind === "global") return payload;
  if (!scopeValue) return null;
  if (scopeKind === "topic") return { ...payload, topic_id: scopeValue };

  if (!isPublicId(scopeValue)) return null;
  if (scopeKind === "notebook") {
    return { ...payload, notebook_id: scopeValue };
  }
  return { ...payload, document_ids: [scopeValue] };
}

export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [scopeKind, setScopeKind] = useState<ScopeKind>("global");
  const [scopeValue, setScopeValue] = useState("");
  const [exchanges, setExchanges] = useState<ChatExchange[]>([]);
  const [outcomes, setOutcomes] = useState<Record<PublicId, StudyOutcome>>({});
  const [proposalDecisions, setProposalDecisions] = useState<
    Record<string, MemoryDecision>
  >({});
  const [formError, setFormError] = useState<string | null>(null);
  const [endDialogOpen, setEndDialogOpen] = useState(false);
  const [endedSessionId, setEndedSessionId] = useState<PublicId | null>(null);

  const notebooks = useApiQuery(["chat-scopes", "notebooks"], (signal) =>
    api.listNotebooks(undefined, { signal }),
  );
  const documents = useApiQuery(["chat-scopes", "documents"], (signal) =>
    api.listDocuments({}, { signal }),
  );
  const topics = useApiQuery(["chat-scopes", "topics"], (signal) =>
    api.listTopics(undefined, { signal }),
  );
  const sessions = useApiQuery(["chat", "sessions"], (signal) =>
    api.listStudySessions({ signal }),
  );

  const sendMessage = useAsyncAction((payload: ChatRequest, signal: AbortSignal) =>
    api.sendChat(payload, { signal }),
  );
  const updateOutcome = useAsyncAction(
    ({ interactionId, outcome }: OutcomeActionArgs, signal: AbortSignal) =>
      api.updateInteractionOutcome(interactionId, outcome, { signal }),
  );
  const decideProposal = useAsyncAction(
    ({ proposalId, decision }: ProposalActionArgs, signal: AbortSignal) =>
      api.decideMemoryProposal(proposalId, { decision }, { signal }),
  );
  const endSession = useAsyncAction((_payload: null, signal: AbortSignal) =>
    api.endActiveSession({ signal }),
  );

  const latestSessionId = exchanges.at(-1)?.response.session_id ?? null;
  const storedActiveSessionId = sessions.data?.items.find(
    (session) => session.status === "active",
  )?.id;
  const activeSessionId =
    (latestSessionId ?? storedActiveSessionId ?? null) !== endedSessionId
      ? (latestSessionId ?? storedActiveSessionId ?? null)
      : null;
  const selectedScopeLabel = useMemo(() => {
    if (scopeKind === "global") return "All indexed documents";
    if (!scopeValue) return "Choose a source";
    if (scopeKind === "notebook") {
      return (
        notebooks.data?.items.find((item) => item.id === scopeValue)?.name ??
        `Notebook ${scopeValue}`
      );
    }
    if (scopeKind === "document") {
      return (
        documents.data?.items.find((item) => item.id === scopeValue)
          ?.filename ?? `Document ${scopeValue}`
      );
    }
    return (
      topics.data?.items.find((item) => item.id === scopeValue)?.name ??
      "Selected topic"
    );
  }, [documents.data, notebooks.data, scopeKind, scopeValue, topics.data]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    if (!question.trim()) {
      setFormError("Enter a question before sending.");
      return;
    }
    const payload = buildChatRequest(question, scopeKind, scopeValue);
    if (!payload) {
      setFormError("Choose a valid source for this question.");
      return;
    }

    try {
      const response = await sendMessage.run(payload);
      setExchanges((current) => [
        ...current,
        { question: payload.question, response },
      ]);
      setOutcomes((current) => ({
        ...current,
        ...(response.type === "feature_redirect"
          ? {}
          : { [response.interaction_id]: "unrated" }),
      }));
      setQuestion("");
      setEndedSessionId(null);
      endSession.reset();
      sessions.reload();
    } catch {
      // Hook exposes structured error while controlled input stays intact.
    }
  }

  async function handleOutcome(interactionId: PublicId, outcome: StudyOutcome) {
    try {
      await updateOutcome.run({ interactionId, outcome });
      setOutcomes((current) => ({ ...current, [interactionId]: outcome }));
    } catch {
      // Inline action error remains available for retry.
    }
  }

  async function handleProposal(proposalId: string, decision: MemoryDecision) {
    try {
      await decideProposal.run({ proposalId, decision });
      setProposalDecisions((current) => ({ ...current, [proposalId]: decision }));
    } catch {
      // Proposal stays visible and actionable after recoverable failure.
    }
  }

  async function handleEndSession() {
    try {
      const session = await endSession.run(null);
      setEndedSessionId(session.id);
      setEndDialogOpen(false);
      sessions.reload();
    } catch {
      // Dialog remains open so user can retry or cancel.
    }
  }

  return (
    <div className="page-stack chat-page">
      <PageHeader
        eyebrow="Grounded study"
        title="Study chat"
        description="Ask questions about your uploaded study materials. For weakness analysis, use Coaching."
        actions={
          activeSessionId ? (
            <Button
              variant="secondary"
              icon={<CircleStop size={18} aria-hidden="true" />}
              onClick={() => setEndDialogOpen(true)}
            >
              End session
            </Button>
          ) : null
        }
      />

      <div className="chat-layout">
        <aside className="chat-scope" aria-labelledby="chat-scope-heading">
          <Card>
            <h2 id="chat-scope-heading">Answer scope</h2>
            <p className="muted-copy">
              Scope is applied before retrieval. Empty scopes never fall back globally.
            </p>
            <div className="field-stack">
              <label htmlFor="chat-scope-kind">Source type</label>
              <select
                id="chat-scope-kind"
                value={scopeKind}
                onChange={(event) => {
                  setScopeKind(event.target.value as ScopeKind);
                  setScopeValue("");
                  setFormError(null);
                }}
                disabled={sendMessage.isPending}
              >
                <option value="global">Global</option>
                <option value="notebook">Notebook</option>
                <option value="document">Document</option>
                <option value="topic">Topic</option>
              </select>

              {scopeKind !== "global" ? (
                <>
                  <label htmlFor="chat-scope-value">
                    {scopeKind === "notebook"
                      ? "Notebook"
                      : scopeKind === "document"
                        ? "Document"
                        : "Topic"}
                  </label>
                  <select
                    id="chat-scope-value"
                    value={scopeValue}
                    onChange={(event) => setScopeValue(event.target.value)}
                    disabled={sendMessage.isPending}
                    aria-describedby="chat-scope-help"
                  >
                    <option value="">Choose one</option>
                    {scopeKind === "notebook"
                      ? notebooks.data?.items.map((notebook) => (
                          <option key={notebook.id} value={notebook.id ?? ""}>
                            {notebook.name} ({notebook.document_count})
                          </option>
                        ))
                      : null}
                    {scopeKind === "document"
                      ? documents.data?.items.map((document) => (
                          <option key={document.id} value={document.id}>
                            {document.filename}
                          </option>
                        ))
                      : null}
                    {scopeKind === "topic"
                      ? topics.data?.items.map((topic) => (
                          <option key={topic.id} value={topic.id}>
                            {topic.name}
                          </option>
                        ))
                      : null}
                  </select>
                  <p id="chat-scope-help" className="field-help">
                    Answers use only matching cited chunks.
                  </p>
                </>
              ) : null}
            </div>

            <div className="scope-summary">
              {scopeKind === "global" ? (
                <Globe2 size={18} aria-hidden="true" />
              ) : scopeKind === "notebook" ? (
                <NotebookTabs size={18} aria-hidden="true" />
              ) : scopeKind === "document" ? (
                <FileText size={18} aria-hidden="true" />
              ) : (
                <Tags size={18} aria-hidden="true" />
              )}
              <span>Active source: {selectedScopeLabel}</span>
            </div>

            {notebooks.error || documents.error || topics.error ? (
              <Notice tone="warning" title="Some scopes are unavailable">
                Refresh this page to retry source lists. Global chat remains available.
              </Notice>
            ) : null}
          </Card>
        </aside>

        <div className="chat-workspace">
          <section className="chat-transcript" aria-label="Study conversation">
            {exchanges.length === 0 ? (
              <EmptyState
                title="Ask your first grounded question"
                description="Try asking for an explanation, comparison, example, or quick recap."
                icon={<MessageCircleMore />}
              />
            ) : (
              <div className="chat-exchanges">
                {exchanges.map(({ question: askedQuestion, response }) => {
                  const proposal = response.memory_proposal;
                  const proposalDecision = proposal
                    ? proposalDecisions[proposal.proposal_id]
                    : undefined;
                  const savedDecision =
                    proposalDecision && proposalDecision !== "cancel";
                  return (
                    <article
                      className="chat-exchange"
                      key={response.interaction_id}
                    >
                      <div className="chat-message chat-message--user">
                        <p className="chat-message__label">You</p>
                        <p>{askedQuestion}</p>
                      </div>
                      <Card className="chat-message chat-message--assistant">
                        {response.type === "feature_redirect" && response.redirect ? (
                          <FeatureRedirectCard redirect={response.redirect} />
                        ) : (
                          <>
                            <div className="chat-message__heading">
                              <p className="chat-message__label">Study companion</p>
                              <OutcomeBadge
                                outcome={outcomes[response.interaction_id] ?? "unrated"}
                              />
                            </div>
                            <div className="prose-copy">{response.answer}</div>

                            {response.suggested_question ? (
                              <Notice tone="info" title="Try a document-grounded question">
                                “{response.suggested_question}”
                              </Notice>
                            ) : null}

                            {response.sources.length > 0 ? (
                              <details className="citation-disclosure">
                                <summary>
                                  {response.sources.length}{' '}
                                  {response.evidence_status === "grounded"
                                    ? "cited"
                                    : "retrieved"}{' '}
                                  source{response.sources.length === 1 ? "" : "s"}
                                </summary>
                                <div className="source-grid">
                                  {response.sources.map((source) => (
                                    <SourceCard
                                      key={`${response.interaction_id}-${source.index}`}
                                      source={source}
                                    />
                                  ))}
                                </div>
                              </details>
                            ) : response.evidence_status !== "grounded" ? (
                              <Notice tone="warning">
                                No document citations support this response.
                              </Notice>
                            ) : null}

                            <fieldset className="outcome-fieldset">
                              <legend>How well did this answer land?</legend>
                              <div className="button-group">
                                {OUTCOME_OPTIONS.map((option) => (
                                  <Button
                                    key={option.value}
                                    variant={
                                      outcomes[response.interaction_id] === option.value
                                        ? "primary"
                                        : "secondary"
                                    }
                                    disabled={updateOutcome.isPending}
                                    aria-pressed={
                                      outcomes[response.interaction_id] === option.value
                                    }
                                    onClick={() =>
                                      void handleOutcome(
                                        response.interaction_id,
                                        option.value,
                                      )
                                    }
                                  >
                                    {option.label}
                                  </Button>
                                ))}
                              </div>
                            </fieldset>
                          </>
                        )}

                        {response.type !== "feature_redirect" && proposal ? (
                          <aside
                            className="memory-proposal"
                            aria-labelledby={`proposal-${proposal.proposal_id}`}
                          >
                            <div className="memory-proposal__heading">
                              <Lightbulb size={20} aria-hidden="true" />
                              <div>
                                <h3 id={`proposal-${proposal.proposal_id}`}>
                                  Proposed learner memory
                                </h3>
                                <Badge tone="info">{proposal.memory_type}</Badge>
                              </div>
                            </div>
                            <p>{proposal.content}</p>
                            <p className="muted-copy">{proposal.reason}</p>
                            {savedDecision ? (
                              <Notice tone="success" title="Decision saved">
                                {DECISION_LABELS[proposalDecision]}
                              </Notice>
                            ) : (
                              <div className="button-group">
                                {proposal.allowed_decisions.map((decision) => (
                                  <Button
                                    key={decision}
                                    variant={
                                      decision === "accept" || decision === "replace"
                                        ? "primary"
                                        : "ghost"
                                    }
                                    disabled={decideProposal.isPending}
                                    onClick={() =>
                                      void handleProposal(
                                        proposal.proposal_id,
                                        decision,
                                      )
                                    }
                                  >
                                    {DECISION_LABELS[decision]}
                                  </Button>
                                ))}
                              </div>
                            )}
                            {proposalDecision === "cancel" ? (
                              <p className="field-help" role="status">
                                Proposal remains pending for a later decision.
                              </p>
                            ) : null}
                          </aside>
                        ) : null}
                      </Card>
                    </article>
                  );
                })}
              </div>
            )}
          </section>

          {updateOutcome.error ? (
            <Notice tone="error" title="Outcome was not saved">
              {getErrorMessage(updateOutcome.error)} You can choose the outcome again.
            </Notice>
          ) : null}
          {decideProposal.error ? (
            <Notice tone="error" title="Memory decision was not saved">
              {getErrorMessage(decideProposal.error)} The proposal remains available.
            </Notice>
          ) : null}
          {endSession.data ? (
            <Notice tone="success" title="Session completed">
              Your next question will start a new study session.
            </Notice>
          ) : null}

          <form className="chat-composer" onSubmit={handleSubmit}>
            <label htmlFor="chat-question">Your question</label>
            <textarea
              id="chat-question"
              rows={4}
              maxLength={4000}
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                setFormError(null);
                sendMessage.reset();
              }}
              placeholder="Ask about your study material…"
              disabled={sendMessage.isPending}
              aria-invalid={Boolean(formError || sendMessage.error)}
              aria-describedby="chat-question-help chat-question-error"
            />
            <div className="chat-composer__footer">
              <p id="chat-question-help" className="field-help">
                Scope: {selectedScopeLabel}. {question.length}/4000 characters.
              </p>
              <Button
                type="submit"
                loading={sendMessage.isPending}
                loadingText="Finding evidence…"
                icon={<Send size={18} aria-hidden="true" />}
              >
                Send question
              </Button>
            </div>
            {formError ? (
              <p id="chat-question-error" className="field-error" role="alert">
                {formError}
              </p>
            ) : sendMessage.error ? (
              <div id="chat-question-error">
                <ErrorNotice
                  error={sendMessage.error}
                  onRetry={() => sendMessage.retry()}
                />
              </div>
            ) : null}
          </form>
        </div>
      </div>

      <ConfirmationDialog
        open={endDialogOpen}
        onClose={() => {
          if (!endSession.isPending) setEndDialogOpen(false);
        }}
        onConfirm={() => void handleEndSession()}
        title="End this study session?"
        description={
          <>
            The conversation and its source lineage stay saved. Your next question
            starts a new session.
            {endSession.error ? (
              <Notice tone="error">{getErrorMessage(endSession.error)}</Notice>
            ) : null}
          </>
        }
        confirmLabel="End session"
        loading={endSession.isPending}
      />
    </div>
  );
}

export default ChatPage;
