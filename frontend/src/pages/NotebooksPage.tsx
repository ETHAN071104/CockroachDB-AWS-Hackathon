import { useState, type FormEvent } from "react";
import {
  FilePlus2,
  FileText,
  FolderInput,
  NotebookTabs,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";

import {
  api,
  getErrorMessage,
  type DocumentRecord,
  type Notebook,
  type NotebookCreate,
  type NotebookUpdate,
  type PublicId,
} from "../api";
import {
  Badge,
  Button,
  Card,
  ConfirmationDialog,
  Dialog,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  PageHeader,
  SectionHeader,
} from "../components";
import { useApiQuery, useAsyncAction } from "../hooks";

interface UpdateNotebookArgs {
  id: PublicId;
  payload: NotebookUpdate;
}

interface UploadDocumentArgs {
  file: File;
  notebookId: PublicId | null;
}

interface AssignDocumentArgs {
  documentId: PublicId;
  notebookId: PublicId | null;
}

function notebookValue(notebookId: PublicId | null) {
  return notebookId == null ? "unsorted" : String(notebookId);
}

function parseNotebookValue(value: string) {
  return value === "unsorted" ? null : value;
}

export function NotebooksPage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [documentSearchInput, setDocumentSearchInput] = useState("");
  const [documentSearch, setDocumentSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [editNotebook, setEditNotebook] = useState<Notebook | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [deleteNotebook, setDeleteNotebook] = useState<Notebook | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [uploadNotebookId, setUploadNotebookId] = useState<string>("unsorted");
  const [assignmentDrafts, setAssignmentDrafts] = useState<
    Record<PublicId, string>
  >({});

  const notebooks = useApiQuery(["notebooks", search], (signal) =>
    api.listNotebooks(search || undefined, { signal }),
  );
  const documents = useApiQuery(["documents", documentSearch], (signal) =>
    api.listDocuments({ q: documentSearch || undefined }, { signal }),
  );
  const notebookOptions = useApiQuery(["notebooks", "assignment-options"], (signal) =>
    api.listNotebooks(undefined, { signal }),
  );

  const createAction = useAsyncAction(
    (payload: NotebookCreate, signal: AbortSignal) =>
    api.createNotebook(payload, { signal }),
  );
  const updateAction = useAsyncAction(
    ({ id, payload }: UpdateNotebookArgs, signal: AbortSignal) =>
      api.updateNotebook(id, payload, { signal }),
  );
  const deleteAction = useAsyncAction((id: PublicId, signal: AbortSignal) =>
    api.deleteNotebook(id, { signal }),
  );
  const uploadAction = useAsyncAction(
    ({ file: selectedFile, notebookId }: UploadDocumentArgs, signal: AbortSignal) =>
      api.uploadDocument(selectedFile, notebookId, { signal }),
  );
  const assignAction = useAsyncAction(
    ({ documentId, notebookId }: AssignDocumentArgs, signal: AbortSignal) =>
      api.assignDocument(documentId, notebookId, { signal }),
  );

  function reloadLibrary() {
    notebooks.reload();
    documents.reload();
    notebookOptions.reload();
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: NotebookCreate = {
      name: createName.trim(),
      description: createDescription.trim() || null,
    };
    if (!payload.name) return;
    try {
      const created = await createAction.run(payload);
      if (!created) return;
      setCreateName("");
      setCreateDescription("");
      setCreateOpen(false);
      notebooks.reload();
      notebookOptions.reload();
    } catch {
      // Dialog and controlled values remain available for correction.
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editNotebook?.id || !editName.trim()) return;
    try {
      const updated = await updateAction.run({
        id: editNotebook.id,
        payload: {
          name: editName.trim(),
          description: editDescription.trim(),
        },
      });
      if (!updated) return;
      setEditNotebook(null);
      notebooks.reload();
      notebookOptions.reload();
    } catch {
      // Keep edit form open with user input intact.
    }
  }

  async function handleDelete() {
    if (!deleteNotebook?.id) return;
    try {
      const deleted = await deleteAction.run(deleteNotebook.id);
      if (!deleted) return;
      setDeleteNotebook(null);
      reloadLibrary();
    } catch {
      // Confirmation stays open and exposes backend reason.
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    try {
      const result = await uploadAction.run({
        file,
        notebookId: parseNotebookValue(uploadNotebookId),
      });
      if (!result) return;
      setFile(null);
      setFileInputKey((value) => value + 1);
      reloadLibrary();
    } catch {
      // Selected file and target notebook remain set for retry.
    }
  }

  async function handleAssignment(document: DocumentRecord) {
    const draft =
      assignmentDrafts[document.id] ?? notebookValue(document.notebook_id);
    try {
      const updated = await assignAction.run({
        documentId: document.id,
        notebookId: parseNotebookValue(draft),
      });
      if (!updated) return;
      setAssignmentDrafts((current) => {
        const next = { ...current };
        delete next[document.id];
        return next;
      });
      reloadLibrary();
    } catch {
      // Draft assignment remains selected.
    }
  }

  if (notebooks.isLoading && !notebooks.data) {
    return <LoadingState message="Loading notebooks…" />;
  }

  if (notebooks.error && !notebooks.data) {
    return (
      <ErrorState
        title="Notebook library unavailable"
        message={getErrorMessage(notebooks.error)}
        onRetry={notebooks.retry}
      />
    );
  }

  if (!notebooks.data) return null;

  const notebookItems = notebooks.data.items;
  const assignmentNotebooks = notebookOptions.data?.items ?? notebookItems;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Study library"
        title="Notebooks and documents"
        description="Organize each indexed document into one notebook, or leave it in Unsorted Documents."
        actions={
          <Button
            icon={<Plus size={18} aria-hidden="true" />}
            onClick={() => {
              createAction.reset();
              setCreateOpen(true);
            }}
          >
            New notebook
          </Button>
        }
      />

      <section className="page-section">
        <SectionHeader
          title="Notebooks"
          description={`${notebooks.data.total} named notebook${notebooks.data.total === 1 ? "" : "s"}`}
          actions={
            <form
              className="search-form"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                setSearch(searchInput.trim());
              }}
            >
              <label className="visually-hidden" htmlFor="notebook-search">
                Search notebooks
              </label>
              <input
                id="notebook-search"
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Search notebooks"
                maxLength={200}
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

        {notebooks.isRefreshing ? (
          <LoadingState compact message="Refreshing notebooks…" />
        ) : null}

        <div className="library-grid">
          <Card tone="muted" className="notebook-card">
            <div className="notebook-card__icon" aria-hidden="true">
              <FolderInput />
            </div>
            <div className="notebook-card__body">
              <div className="notebook-card__heading">
                <h3>
                  <Link to="/notebooks/unsorted">
                    {notebooks.data.unsorted.name}
                  </Link>
                </h3>
                <Badge tone="neutral">Automatic</Badge>
              </div>
              <p>{notebooks.data.unsorted.description}</p>
              <p className="notebook-card__count">
                {notebooks.data.unsorted.document_count} document
                {notebooks.data.unsorted.document_count === 1 ? "" : "s"}
              </p>
            </div>
          </Card>

          {notebookItems.map((notebook) => (
            <Card key={notebook.id} className="notebook-card">
              <div className="notebook-card__icon" aria-hidden="true">
                <NotebookTabs />
              </div>
              <div className="notebook-card__body">
                <div className="notebook-card__heading">
                  <h3>
                    <Link to={`/notebooks/${notebook.id}`}>{notebook.name}</Link>
                  </h3>
                  <Badge tone="primary">
                    {notebook.document_count} document
                    {notebook.document_count === 1 ? "" : "s"}
                  </Badge>
                </div>
                <p>{notebook.description || "No description added."}</p>
                <div className="button-group notebook-card__actions">
                  <Button
                    variant="ghost"
                    icon={<Pencil size={17} aria-hidden="true" />}
                    onClick={() => {
                      updateAction.reset();
                      setEditNotebook(notebook);
                      setEditName(notebook.name);
                      setEditDescription(notebook.description);
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    icon={<Trash2 size={17} aria-hidden="true" />}
                    disabled={notebook.document_count > 0}
                    title={
                      notebook.document_count > 0
                        ? "Move or remove every document before deleting"
                        : undefined
                    }
                    onClick={() => {
                      deleteAction.reset();
                      setDeleteNotebook(notebook);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>

        {notebookItems.length === 0 && search ? (
          <EmptyState
            title="No matching notebooks"
            description={`No notebook matched “${search}”. Clear the search or create a new notebook.`}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setSearch("");
                  setSearchInput("");
                }}
              >
                Clear search
              </Button>
            }
          />
        ) : null}
      </section>

      <section className="page-section">
        <SectionHeader
          title="Upload a document"
          description="PDF, TXT, and PPTX files up to the configured 50 MiB limit."
        />
        <Card>
          <form className="form-grid" onSubmit={handleUpload}>
            <div className="field-stack form-grid__wide">
              <label htmlFor="document-upload">Document file</label>
              <input
                key={fileInputKey}
                id="document-upload"
                type="file"
                accept=".pdf,.txt,.pptx,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                required
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  uploadAction.reset();
                }}
                disabled={uploadAction.isPending}
              />
              <p className="field-help">
                Protected, empty, corrupt, unsupported, and oversized files are rejected.
              </p>
            </div>
            <div className="field-stack">
              <label htmlFor="upload-notebook">Destination</label>
              <select
                id="upload-notebook"
                value={uploadNotebookId}
                onChange={(event) => setUploadNotebookId(event.target.value)}
                disabled={uploadAction.isPending}
              >
                <option value="unsorted">Unsorted Documents</option>
                {assignmentNotebooks.map((notebook) => (
                  <option key={notebook.id} value={notebook.id ?? ""}>
                    {notebook.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-actions">
              <Button
                type="submit"
                loading={uploadAction.isPending}
                loadingText="Indexing document…"
                icon={<FilePlus2 size={18} aria-hidden="true" />}
                disabled={!file}
              >
                Upload and index
              </Button>
            </div>
          </form>
          {uploadAction.error ? (
            <Notice tone="error" title="Upload failed">
              {getErrorMessage(uploadAction.error)} Choose another file or retry.
            </Notice>
          ) : null}
          {uploadAction.data ? (
            <Notice
              tone={uploadAction.data.duplicate ? "warning" : "success"}
              title={
                uploadAction.data.duplicate
                  ? "Document already indexed"
                  : "Document indexed"
              }
            >
              {uploadAction.data.document.filename}
              {uploadAction.data.duplicate
                ? " was returned without changing its notebook."
                : " is ready to study."}
            </Notice>
          ) : null}
        </Card>
      </section>

      <section className="page-section">
        <SectionHeader
          title="All documents"
          description="Search documents and change their single notebook assignment."
          actions={
            <form
              className="search-form"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                setDocumentSearch(documentSearchInput.trim());
              }}
            >
              <label className="visually-hidden" htmlFor="document-search">
                Search documents
              </label>
              <input
                id="document-search"
                type="search"
                value={documentSearchInput}
                onChange={(event) => setDocumentSearchInput(event.target.value)}
                placeholder="Search documents"
                maxLength={255}
              />
              <Button variant="secondary" type="submit">
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
        ) : documents.data?.items.length ? (
          <div className="document-list">
            {documents.data.items.map((document) => {
              const actualValue = notebookValue(document.notebook_id);
              const draftValue = assignmentDrafts[document.id] ?? actualValue;
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
                      <label htmlFor={`assignment-${document.id}`}>Notebook</label>
                      <select
                        id={`assignment-${document.id}`}
                        value={draftValue}
                        disabled={assignAction.isPending}
                        onChange={(event) =>
                          setAssignmentDrafts((current) => ({
                            ...current,
                            [document.id]: event.target.value,
                          }))
                        }
                      >
                        <option value="unsorted">Unsorted Documents</option>
                        {assignmentNotebooks.map((notebook) => (
                          <option key={notebook.id} value={notebook.id ?? ""}>
                            {notebook.name}
                          </option>
                        ))}
                      </select>
                      <Button
                        variant="secondary"
                        icon={<FolderInput size={17} aria-hidden="true" />}
                        disabled={draftValue === actualValue || assignAction.isPending}
                        onClick={() => void handleAssignment(document)}
                      >
                        Save assignment
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title={documentSearch ? "No matching documents" : "No documents yet"}
            description={
              documentSearch
                ? `No document matched “${documentSearch}”.`
                : "Upload a PDF, TXT, or PPTX file to begin."
            }
            icon={<FileText />}
          />
        )}

        {assignAction.error ? (
          <Notice tone="error" title="Assignment was not saved">
            {getErrorMessage(assignAction.error)} Your selected destination is preserved.
          </Notice>
        ) : null}
      </section>

      <Dialog
        open={createOpen}
        onClose={() => {
          if (!createAction.isPending) setCreateOpen(false);
        }}
        title="Create notebook"
        description="Add a focused home for related study material."
        actions={
          <>
            <Button
              variant="ghost"
              onClick={() => setCreateOpen(false)}
              disabled={createAction.isPending}
            >
              Cancel
            </Button>
            <Button
              form="create-notebook-form"
              type="submit"
              loading={createAction.isPending}
              loadingText="Creating…"
              disabled={!createName.trim()}
            >
              Create notebook
            </Button>
          </>
        }
      >
        <form id="create-notebook-form" className="field-stack" onSubmit={handleCreate}>
          <label htmlFor="create-notebook-name">Name</label>
          <input
            id="create-notebook-name"
            value={createName}
            onChange={(event) => {
              setCreateName(event.target.value);
              createAction.reset();
            }}
            disabled={createAction.isPending}
            maxLength={120}
            required
            autoFocus
          />
          <label htmlFor="create-notebook-description">Description</label>
          <textarea
            id="create-notebook-description"
            value={createDescription}
            onChange={(event) => {
              setCreateDescription(event.target.value);
              createAction.reset();
            }}
            disabled={createAction.isPending}
            maxLength={1000}
            rows={4}
          />
          {createAction.error ? (
            <Notice tone="error">{getErrorMessage(createAction.error)}</Notice>
          ) : null}
        </form>
      </Dialog>

      <Dialog
        open={Boolean(editNotebook)}
        onClose={() => {
          if (!updateAction.isPending) setEditNotebook(null);
        }}
        title="Edit notebook"
        actions={
          <>
            <Button
              variant="ghost"
              onClick={() => setEditNotebook(null)}
              disabled={updateAction.isPending}
            >
              Cancel
            </Button>
            <Button
              form="edit-notebook-form"
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
        <form id="edit-notebook-form" className="field-stack" onSubmit={handleUpdate}>
          <label htmlFor="edit-notebook-name">Name</label>
          <input
            id="edit-notebook-name"
            value={editName}
            onChange={(event) => {
              setEditName(event.target.value);
              updateAction.reset();
            }}
            disabled={updateAction.isPending}
            maxLength={120}
            required
          />
          <label htmlFor="edit-notebook-description">Description</label>
          <textarea
            id="edit-notebook-description"
            value={editDescription}
            onChange={(event) => {
              setEditDescription(event.target.value);
              updateAction.reset();
            }}
            disabled={updateAction.isPending}
            maxLength={1000}
            rows={4}
          />
          {updateAction.error ? (
            <Notice tone="error">{getErrorMessage(updateAction.error)}</Notice>
          ) : null}
        </form>
      </Dialog>

      <ConfirmationDialog
        open={Boolean(deleteNotebook)}
        onClose={() => {
          if (!deleteAction.isPending) setDeleteNotebook(null);
        }}
        onConfirm={() => void handleDelete()}
        title="Delete empty notebook?"
        description={
          <>
            <strong>{deleteNotebook?.name}</strong> will be removed. Documents are
            never deleted through this action.
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

export default NotebooksPage;
