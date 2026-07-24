import { useState, type FormEvent } from "react";
import {
  BookOpenCheck,
  FileText,
  FolderInput,
  FolderMinus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  api,
  getErrorMessage,
  isPublicId,
  type PublicId,
  type Summary,
} from "../api";
import {
  Badge,
  Button,
  Card,
  ConfirmationDialog,
  EmptyState,
  ErrorNotice,
  ErrorState,
  LoadingState,
  Notice,
  PageHeader,
  ProgressBar,
  SectionHeader,
  SourceCard,
} from "../components";
import { useApiQuery, useAsyncAction } from "../hooks";

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function isMissingSummary(error: unknown) {
  return error instanceof ApiError && (
    error.code === "SUMMARY_NOT_GENERATED"
    || error.legacyCode === "summary_not_generated"
  );
}

function DocumentSummary({ summary }: { summary: Summary }) {
  return (
    <div className="summary-content">
      {summary.stale ? (
        <Notice tone="warning" title="Cached summary is stale">
          Document content changed after generation. Regenerate to use the latest
          source snapshot.
        </Notice>
      ) : null}
      <div className="summary-content__heading">
        <div>
          <h3>{summary.summary.title}</h3>
          <p>Generated {formatDate(summary.generated_at)}</p>
        </div>
        <Badge tone={summary.stale ? "warning" : "success"}>
          {summary.stale ? "Stale" : "Current"}
        </Badge>
      </div>
      <p className="prose-copy">{summary.summary.overview}</p>
      <ProgressBar
        label="Generation confidence"
        value={summary.summary.confidence}
        max={1}
      />
      {summary.summary.key_points.length > 0 ? (
        <div>
          <h4>Key points</h4>
          <ol className="key-point-list">
            {summary.summary.key_points.map((point, index) => (
              <li key={`${index}-${point.text}`}>
                <span>{point.text}</span>
                {point.source_indexes.length > 0 ? (
                  <span className="citation-indexes" aria-label="Citations">
                    {point.source_indexes.map((sourceIndex) => (
                      <span key={sourceIndex}>[{sourceIndex}]</span>
                    ))}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {summary.sources.length > 0 ? (
        <details className="citation-disclosure">
          <summary>
            {summary.sources.length} cited excerpt
            {summary.sources.length === 1 ? "" : "s"}
          </summary>
          <div className="source-grid">
            {summary.sources.map((source) => (
              <SourceCard key={source.index} source={source} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

export function DocumentDetailPage() {
  const { documentId = "" } = useParams<{ documentId: string }>();
  const navigate = useNavigate();
  const validId = isPublicId(documentId);
  const [destination, setDestination] = useState("unsorted");
  const [destinationTouched, setDestinationTouched] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const document = useApiQuery(
    ["document", documentId],
    (signal) => api.getDocument(documentId, { signal }),
    {
      enabled: validId,
      onSuccess: (record) => {
        if (!destinationTouched) {
          setDestination(
            record.notebook_id == null ? "unsorted" : String(record.notebook_id),
          );
        }
      },
    },
  );
  const notebooks = useApiQuery(["notebooks", "document-detail"], (signal) =>
    api.listNotebooks(undefined, { signal }),
  );
  const summary = useApiQuery(
    ["summary", "document", documentId],
    (signal) => api.getCachedSummary("document", documentId, { signal }),
    { enabled: validId },
  );

  const assignAction = useAsyncAction(
    (notebookId: PublicId | null, signal: AbortSignal) =>
      api.assignDocument(documentId, notebookId, { signal }),
  );
  const deleteAction = useAsyncAction((id: PublicId, signal: AbortSignal) =>
    api.deleteDocument(id, { signal }),
  );
  const generateSummary = useAsyncAction((id: PublicId, signal: AbortSignal) =>
    api.generateSummary("document", id, { signal }),
  );

  async function handleAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const notebookId = destination === "unsorted" ? null : destination;
    try {
      const updated = await assignAction.run(notebookId);
      if (!updated) return;
      document.setData(updated);
      setDestinationTouched(false);
      notebooks.reload();
    } catch {
      // Destination remains selected for retry.
    }
  }

  async function handleRemoveFromNotebook() {
    try {
      const updated = await assignAction.run(null);
      if (!updated) return;
      document.setData(updated);
      setDestination("unsorted");
      setDestinationTouched(false);
      notebooks.reload();
    } catch {
      // Current document remains intact.
    }
  }

  async function handleDelete() {
    try {
      const result = await deleteAction.run(documentId);
      if (!result) return;
      navigate("/notebooks", { replace: true });
    } catch {
      // Confirmation remains open so deletion can be retried.
    }
  }

  async function handleGenerateSummary() {
    try {
      const result = await generateSummary.run(documentId);
      if (result) summary.setData(result);
    } catch {
      // Existing cache stays visible if regeneration fails.
    }
  }

  if (!validId) {
    return (
      <ErrorState
        title="Invalid document"
        message="Document IDs must contain positive decimal digits."
      />
    );
  }

  if (document.isLoading && !document.data) {
    return <LoadingState message="Loading document…" />;
  }

  if (document.error && !document.data) {
    return (
      <ErrorState
        title="Document unavailable"
        message={getErrorMessage(document.error)}
        onRetry={document.retry}
      />
    );
  }

  if (!document.data) return null;

  const record = document.data;
  const currentNotebook = record.notebook_id
    ? notebooks.data?.items.find((item) => item.id === record.notebook_id)
    : notebooks.data?.unsorted;
  const currentDestination =
    record.notebook_id == null ? "unsorted" : String(record.notebook_id);

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Indexed document"
        title={record.filename}
        description="Review metadata, change notebook membership, or generate a cited summary."
        actions={
          <div className="button-group">
            <Link
              className="button button--secondary"
              to={`/study-actions?document_ids=${record.id}&scope_name=${encodeURIComponent(record.filename)}`}
            >
              <BookOpenCheck size={18} aria-hidden="true" />
              <span>Study document</span>
            </Link>
            <Button
              variant="danger"
              icon={<Trash2 size={18} aria-hidden="true" />}
              onClick={() => {
                deleteAction.reset();
                setDeleteOpen(true);
              }}
            >
              Delete document
            </Button>
          </div>
        }
      />

      <div className="metadata-strip" aria-label="Document details">
        <div>
          <span>Type</span>
          <strong>{record.mime_type}</strong>
        </div>
        <div>
          <span>Chunks</span>
          <strong>{record.chunk_count}</strong>
        </div>
        <div>
          <span>Indexed</span>
          <strong>{formatDate(record.created_at)}</strong>
        </div>
        <div>
          <span>Updated</span>
          <strong>{formatDate(record.updated_at)}</strong>
        </div>
      </div>

      <section className="page-section">
        <SectionHeader
          title="Notebook assignment"
          description="Moving this document changes SQLite membership only. Its embeddings are reused."
        />
        <Card>
          <div className="assignment-current">
            <FolderInput size={22} aria-hidden="true" />
            <div>
              <span>Currently in</span>
              {record.notebook_id ? (
                <Link to={`/notebooks/${record.notebook_id}`}>
                  {currentNotebook?.name ?? `Notebook ${record.notebook_id}`}
                </Link>
              ) : (
                <Link to="/notebooks/unsorted">Unsorted Documents</Link>
              )}
            </div>
          </div>
          <form className="form-grid" onSubmit={handleAssignment}>
            <div className="field-stack form-grid__wide">
              <label htmlFor="document-destination">Move document to</label>
              <select
                id="document-destination"
                value={destination}
                disabled={
                  assignAction.isPending || notebooks.isLoading || Boolean(notebooks.error)
                }
                onChange={(event) => {
                  setDestination(event.target.value);
                  setDestinationTouched(true);
                  assignAction.reset();
                }}
              >
                <option value="unsorted">Unsorted Documents</option>
                {notebooks.data?.items.map((notebook) => (
                  <option key={notebook.id} value={notebook.id ?? ""}>
                    {notebook.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-actions">
              <Button
                type="submit"
                loading={assignAction.isPending}
                loadingText="Moving…"
                icon={<FolderInput size={18} aria-hidden="true" />}
                disabled={destination === currentDestination}
              >
                Move document
              </Button>
              {record.notebook_id != null ? (
                <Button
                  variant="ghost"
                  icon={<FolderMinus size={18} aria-hidden="true" />}
                  disabled={assignAction.isPending}
                  onClick={() => void handleRemoveFromNotebook()}
                >
                  Remove to Unsorted
                </Button>
              ) : null}
            </div>
          </form>
          {assignAction.error ? (
            <Notice tone="error" title="Assignment was not changed">
              {getErrorMessage(assignAction.error)} Your destination choice is preserved.
            </Notice>
          ) : null}
          {assignAction.data ? (
            <Notice tone="success">Notebook assignment updated.</Notice>
          ) : null}
          {notebooks.error ? (
            <Notice tone="warning" title="Notebook list unavailable">
              {getErrorMessage(notebooks.error)} Retry by refreshing this page before
              changing assignment.
            </Notice>
          ) : null}
        </Card>
      </section>

      <section className="page-section">
        <SectionHeader
          title="Document summary"
          description="Cached summary reads are model-free. Generation runs only when you request it."
          actions={
            <Button
              icon={
                summary.data ? (
                  <RefreshCw size={18} aria-hidden="true" />
                ) : (
                  <Sparkles size={18} aria-hidden="true" />
                )
              }
              loading={generateSummary.isPending}
              loadingText="Generating summary…"
              onClick={() => void handleGenerateSummary()}
            >
              {summary.data ? "Regenerate" : "Generate summary"}
            </Button>
          }
        />
        <Card>
          {summary.isLoading && !summary.data ? (
            <LoadingState message="Loading cached summary…" />
          ) : summary.error && !summary.data && !isMissingSummary(summary.error) ? (
            <ErrorState
              compact
              title="Cached summary unavailable"
              message={getErrorMessage(summary.error)}
              onRetry={summary.retry}
            />
          ) : summary.data ? (
            <DocumentSummary summary={summary.data} />
          ) : (
            <EmptyState
              compact
              title="No summary generated"
              description="Generate one when this document contains enough indexed evidence."
              icon={<FileText />}
            />
          )}
          {generateSummary.error ? (
            <ErrorNotice
              error={generateSummary.error}
              onRetry={() => generateSummary.retry()}
            />
          ) : null}
        </Card>
      </section>

      <ConfirmationDialog
        open={deleteOpen}
        onClose={() => {
          if (!deleteAction.isPending) setDeleteOpen(false);
        }}
        onConfirm={() => void handleDelete()}
        title="Delete this document?"
        description={
          <>
            <strong>{record.filename}</strong> and its SQLite/Chroma records will be
            removed with coordinated compensation. This action cannot be undone.
            {deleteAction.error ? (
              <Notice tone="error">{getErrorMessage(deleteAction.error)}</Notice>
            ) : null}
          </>
        }
        confirmLabel="Delete document"
        destructive
        loading={deleteAction.isPending}
      />
    </div>
  );
}

export default DocumentDetailPage;
