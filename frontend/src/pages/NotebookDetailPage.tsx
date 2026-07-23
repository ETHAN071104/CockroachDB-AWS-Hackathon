import { useState, type FormEvent } from "react";
import {
  BookOpenCheck,
  FilePlus2,
  FileText,
  FolderInput,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  api,
  getErrorMessage,
  type DocumentRecord,
  type NotebookFilter,
  type NotebookUpdate,
  type Summary,
} from "../api";
import {
  Badge,
  Button,
  Card,
  ConfirmationDialog,
  Dialog,
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

interface UpdateNotebookArgs {
  id: number;
  payload: NotebookUpdate;
}

interface MoveDocumentArgs {
  documentId: number;
  notebookId: number | null;
}

function formatDate(value: string | null) {
  if (!value) return "Not available";
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

function SummaryContent({ summary }: { summary: Summary }) {
  return (
    <div className="summary-content">
      {summary.stale ? (
        <Notice tone="warning" title="Summary may be out of date">
          Source content changed after this summary was generated. Regenerate it to
          refresh the evidence snapshot.
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
          <ul className="key-point-list">
            {summary.summary.key_points.map((point, index) => (
              <li key={`${index}-${point.text}`}>
                <span>{point.text}</span>
                {point.source_indexes.length > 0 ? (
                  <span className="citation-indexes">
                    {point.source_indexes.map((sourceIndex) => (
                      <span key={sourceIndex}>[{sourceIndex}]</span>
                    ))}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {summary.sources.length > 0 ? (
        <details className="citation-disclosure">
          <summary>
            {summary.sources.length} summary source
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

export function NotebookDetailPage() {
  const { notebookId = "" } = useParams<{ notebookId: string }>();
  const navigate = useNavigate();
  const isUnsorted = notebookId === "unsorted";
  const numericId = Number(notebookId);
  const isValidNamedId = Number.isInteger(numericId) && numericId > 0;
  const validRoute = isUnsorted || isValidNamedId;
  const notebookFilter: NotebookFilter = isUnsorted ? "unsorted" : numericId;

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [moveDrafts, setMoveDrafts] = useState<Record<number, string>>({});

  const notebook = useApiQuery(
    ["notebook", notebookId],
    (signal) =>
      isUnsorted
        ? api.getUnsortedNotebook({ signal })
        : api.getNotebook(numericId, { signal }),
    { enabled: validRoute },
  );
  const documents = useApiQuery(
    ["notebook-documents", notebookId, search],
    (signal) =>
      api.listDocuments(
        { notebookId: notebookFilter, q: search || undefined },
        { signal },
      ),
    { enabled: validRoute },
  );
  const allNotebooks = useApiQuery(["notebooks", "move-options"], (signal) =>
    api.listNotebooks(undefined, { signal }),
  );
  const summary = useApiQuery(
    ["summary", "notebook", numericId],
    (signal) => api.getCachedSummary("notebook", numericId, { signal }),
    { enabled: isValidNamedId },
  );

  const updateAction = useAsyncAction(
    ({ id, payload }: UpdateNotebookArgs, signal: AbortSignal) =>
      api.updateNotebook(id, payload, { signal }),
  );
  const deleteAction = useAsyncAction((id: number, signal: AbortSignal) =>
    api.deleteNotebook(id, { signal }),
  );
  const uploadAction = useAsyncAction((selectedFile: File, signal: AbortSignal) =>
    api.uploadDocument(selectedFile, isUnsorted ? null : numericId, { signal }),
  );
  const moveAction = useAsyncAction(
    ({ documentId, notebookId: destinationId }: MoveDocumentArgs, signal: AbortSignal) =>
      api.assignDocument(documentId, destinationId, { signal }),
  );
  const generateSummary = useAsyncAction((id: number, signal: AbortSignal) =>
    api.generateSummary("notebook", id, { signal }),
  );

  function reloadNotebook() {
    notebook.reload();
    documents.reload();
    allNotebooks.reload();
  }

  async function handleEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isValidNamedId || !editName.trim()) return;
    try {
      const result = await updateAction.run({
        id: numericId,
        payload: {
          name: editName.trim(),
          description: editDescription.trim(),
        },
      });
      if (!result) return;
      setEditOpen(false);
      notebook.reload();
      allNotebooks.reload();
    } catch {
      // Keep dialog values for correction and retry.
    }
  }

  async function handleDelete() {
    if (!isValidNamedId) return;
    try {
      const result = await deleteAction.run(numericId);
      if (!result) return;
      navigate("/notebooks", { replace: true });
    } catch {
      // Keep confirmation open and show backend conflict.
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    try {
      const result = await uploadAction.run(file);
      if (!result) return;
      setFile(null);
      setFileInputKey((value) => value + 1);
      reloadNotebook();
    } catch {
      // File selection remains available after recoverable error.
    }
  }

  async function handleMove(document: DocumentRecord) {
    const destination = moveDrafts[document.id];
    if (destination === undefined) return;
    const destinationId = destination === "unsorted" ? null : Number(destination);
    try {
      const result = await moveAction.run({
        documentId: document.id,
        notebookId: destinationId,
      });
      if (!result) return;
      setMoveDrafts((current) => {
        const next = { ...current };
        delete next[document.id];
        return next;
      });
      reloadNotebook();
    } catch {
      // Preserve selected destination for retry.
    }
  }

  async function handleGenerateSummary() {
    if (!isValidNamedId) return;
    try {
      const result = await generateSummary.run(numericId);
      if (result) summary.setData(result);
    } catch {
      // Previous cached summary stays rendered by design.
    }
  }

  if (!validRoute) {
    return (
      <ErrorState
        title="Invalid notebook"
        message="Notebook IDs must be positive numbers, or use the Unsorted Documents view."
      />
    );
  }

  if (notebook.isLoading && !notebook.data) {
    return <LoadingState message="Loading notebook…" />;
  }

  if (notebook.error && !notebook.data) {
    return (
      <ErrorState
        title="Notebook unavailable"
        message={getErrorMessage(notebook.error)}
        onRetry={notebook.retry}
      />
    );
  }

  if (!notebook.data) return null;

  const currentNotebook = notebook.data;
  const documentItems = documents.data?.items ?? [];

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={isUnsorted ? "Virtual notebook" : "Notebook"}
        title={currentNotebook.name}
        description={currentNotebook.description || "No description added."}
        actions={
          isUnsorted ? null : (
            <div className="button-group">
              <Link
                className="button button--secondary"
                to={`/study-actions?notebook_id=${currentNotebook.id}&scope_name=${encodeURIComponent(currentNotebook.name)}`}
              >
                <BookOpenCheck size={18} aria-hidden="true" />
                <span>Study notebook</span>
              </Link>
              <Button
                variant="secondary"
                icon={<Pencil size={18} aria-hidden="true" />}
                onClick={() => {
                  updateAction.reset();
                  setEditName(currentNotebook.name);
                  setEditDescription(currentNotebook.description);
                  setEditOpen(true);
                }}
              >
                Edit
              </Button>
              <Button
                variant="danger"
                icon={<Trash2 size={18} aria-hidden="true" />}
                disabled={currentNotebook.document_count > 0}
                title={
                  currentNotebook.document_count > 0
                    ? "Move or remove every document before deleting"
                    : undefined
                }
                onClick={() => {
                  deleteAction.reset();
                  setDeleteOpen(true);
                }}
              >
                Delete
              </Button>
            </div>
          )
        }
      />

      <div className="metadata-strip" aria-label="Notebook details">
        <div>
          <span>Documents</span>
          <strong>{currentNotebook.document_count}</strong>
        </div>
        {!isUnsorted ? (
          <>
            <div>
              <span>Created</span>
              <strong>{formatDate(currentNotebook.created_at)}</strong>
            </div>
            <div>
              <span>Updated</span>
              <strong>{formatDate(currentNotebook.updated_at)}</strong>
            </div>
          </>
        ) : null}
      </div>

      <section className="page-section">
        <SectionHeader
          title="Documents"
          description="Move a document to another notebook or back to Unsorted Documents without re-indexing."
          actions={
            <form
              className="search-form"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                setSearch(searchInput.trim());
              }}
            >
              <label className="visually-hidden" htmlFor="notebook-document-search">
                Search this notebook
              </label>
              <input
                id="notebook-document-search"
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Search this notebook"
                maxLength={255}
              />
              <Button
                variant="secondary"
                type="submit"
                icon={<Search size={18} aria-hidden="true" />}
              >
                Search
              </Button>
            </form>
          }
        />

        {documents.isLoading && !documents.data ? (
          <LoadingState message="Loading documents…" />
        ) : documents.error && !documents.data ? (
          <ErrorState
            title="Documents unavailable"
            message={getErrorMessage(documents.error)}
            onRetry={documents.retry}
          />
        ) : documentItems.length === 0 ? (
          <EmptyState
            title={search ? "No matching documents" : "This notebook is empty"}
            description={
              search
                ? `No document matched “${search}”.`
                : "Upload a new document here or move one from another notebook."
            }
            icon={<FileText />}
          />
        ) : (
          <div className="document-list">
            {documentItems.map((document) => {
              const currentValue = document.notebook_id == null
                ? "unsorted"
                : String(document.notebook_id);
              const draftValue = moveDrafts[document.id] ?? currentValue;
              return (
                <Card key={document.id} padding="small">
                  <div className="document-row">
                    <div className="document-row__identity">
                      <FileText size={20} aria-hidden="true" />
                      <div>
                        <h3>
                          <Link to={`/documents/${document.id}`}>
                            {document.filename}
                          </Link>
                        </h3>
                        <p>
                          {document.chunk_count} chunk
                          {document.chunk_count === 1 ? "" : "s"} ·{" "}
                          {document.mime_type}
                        </p>
                      </div>
                    </div>
                    <div className="document-row__assignment">
                      <label htmlFor={`move-document-${document.id}`}>Move to</label>
                      <select
                        id={`move-document-${document.id}`}
                        value={draftValue}
                        disabled={moveAction.isPending || Boolean(allNotebooks.error)}
                        onChange={(event) =>
                          setMoveDrafts((current) => ({
                            ...current,
                            [document.id]: event.target.value,
                          }))
                        }
                      >
                        <option value="unsorted">Unsorted Documents</option>
                        {allNotebooks.data?.items.map((item) => (
                          <option key={item.id} value={item.id ?? ""}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                      <Button
                        variant="secondary"
                        icon={<FolderInput size={17} aria-hidden="true" />}
                        disabled={draftValue === currentValue || moveAction.isPending}
                        onClick={() => void handleMove(document)}
                      >
                        Move
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
        {moveAction.error ? (
          <Notice tone="error" title="Document was not moved">
            {getErrorMessage(moveAction.error)} Your destination choice is preserved.
          </Notice>
        ) : null}
        {allNotebooks.error ? (
          <Notice tone="warning" title="Move destinations unavailable">
            {getErrorMessage(allNotebooks.error)} Refresh this page before moving a
            document.
          </Notice>
        ) : null}
      </section>

      <section className="page-section">
        <SectionHeader
          title="Add a document"
          description={`New uploads are assigned directly to ${currentNotebook.name}.`}
        />
        <Card>
          <form className="form-grid" onSubmit={handleUpload}>
            <div className="field-stack form-grid__wide">
              <label htmlFor="notebook-document-upload">Document file</label>
              <input
                key={fileInputKey}
                id="notebook-document-upload"
                type="file"
                accept=".pdf,.txt,.pptx,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                required
                disabled={uploadAction.isPending}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  uploadAction.reset();
                }}
              />
              <p className="field-help">PDF, TXT, or PPTX; maximum 50 MiB.</p>
            </div>
            <div className="form-actions">
              <Button
                type="submit"
                icon={<FilePlus2 size={18} aria-hidden="true" />}
                loading={uploadAction.isPending}
                loadingText="Indexing…"
                disabled={!file}
              >
                Upload and index
              </Button>
            </div>
          </form>
          {uploadAction.error ? (
            <Notice tone="error" title="Upload failed">
              {getErrorMessage(uploadAction.error)}
            </Notice>
          ) : null}
          {uploadAction.data ? (
            <Notice
              tone={uploadAction.data.duplicate ? "warning" : "success"}
              title={
                uploadAction.data.duplicate
                  ? "Existing document returned"
                  : "Document indexed"
              }
            >
              {uploadAction.data.duplicate
                ? "Duplicate uploads do not silently change notebook assignment."
                : `${uploadAction.data.document.filename} is ready to study.`}
            </Notice>
          ) : null}
        </Card>
      </section>

      {!isUnsorted ? (
        <section className="page-section">
          <SectionHeader
            title="Notebook summary"
            description="Cached GET requests never invoke a model. Generate explicitly when you want a fresh summary."
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
              <SummaryContent summary={summary.data} />
            ) : (
              <EmptyState
                compact
                title="No summary generated"
                description="Generate a summary when this notebook has enough indexed evidence."
                icon={<Sparkles />}
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
      ) : null}

      <Dialog
        open={editOpen}
        onClose={() => {
          if (!updateAction.isPending) setEditOpen(false);
        }}
        title="Edit notebook"
        actions={
          <>
            <Button
              variant="ghost"
              onClick={() => setEditOpen(false)}
              disabled={updateAction.isPending}
            >
              Cancel
            </Button>
            <Button
              form="notebook-detail-edit-form"
              type="submit"
              loading={updateAction.isPending}
              loadingText="Saving…"
              disabled={!editName.trim()}
            >
              Save changes
            </Button>
          </>
        }
      >
        <form
          id="notebook-detail-edit-form"
          className="field-stack"
          onSubmit={handleEdit}
        >
          <label htmlFor="notebook-detail-name">Name</label>
          <input
            id="notebook-detail-name"
            value={editName}
            onChange={(event) => {
              setEditName(event.target.value);
              updateAction.reset();
            }}
            disabled={updateAction.isPending}
            required
            maxLength={120}
          />
          <label htmlFor="notebook-detail-description">Description</label>
          <textarea
            id="notebook-detail-description"
            value={editDescription}
            onChange={(event) => {
              setEditDescription(event.target.value);
              updateAction.reset();
            }}
            disabled={updateAction.isPending}
            rows={4}
            maxLength={1000}
          />
          {updateAction.error ? (
            <Notice tone="error">{getErrorMessage(updateAction.error)}</Notice>
          ) : null}
        </form>
      </Dialog>

      <ConfirmationDialog
        open={deleteOpen}
        onClose={() => {
          if (!deleteAction.isPending) setDeleteOpen(false);
        }}
        onConfirm={() => void handleDelete()}
        title="Delete empty notebook?"
        description={
          <>
            This removes <strong>{currentNotebook.name}</strong>. It cannot be deleted
            until every document has been moved or removed.
            {deleteAction.error ? (
              <Notice tone="error">{getErrorMessage(deleteAction.error)}</Notice>
            ) : null}
          </>
        }
        confirmLabel="Delete notebook"
        destructive
        loading={deleteAction.isPending}
      />
    </div>
  );
}

export default NotebookDetailPage;
