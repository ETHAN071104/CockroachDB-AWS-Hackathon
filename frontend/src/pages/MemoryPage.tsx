import { Archive, Layers3, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';

import { apiClient } from '../api/client';
import type {
  ConsolidationApplyResult,
  ConsolidationProposal,
  DeleteResult,
  MemoryCreate,
  MemoryList,
  MemoryRecord,
  MemorySearchResult,
  MemoryType,
} from '../api/types';
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
  ProgressBar,
  SectionHeader,
} from '../components';
import { useApiQuery, useAsyncAction } from '../hooks';
import { errorMessage, formatDateTime, titleCase } from '../utils/format';

const MEMORY_TYPES: MemoryType[] = ['profile', 'learning_state', 'episodic', 'procedural'];

interface MemoryDraft {
  memory_type: MemoryType;
  content: string;
  confidence: number;
  importance: number;
}

const EMPTY_DRAFT: MemoryDraft = {
  memory_type: 'learning_state',
  content: '',
  confidence: 0.85,
  importance: 0.6,
};

export function MemoryPage() {
  const memories = useApiQuery<MemoryList>(
    'memories',
    (signal) => apiClient.get('/api/memories?include_archived=true', { signal }),
  );
  const [draft, setDraft] = useState<MemoryDraft>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<{
    memory: MemoryRecord;
    action: 'archive' | 'delete';
  } | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [proposal, setProposal] = useState<ConsolidationProposal | null>(null);
  const [searchText, setSearchText] = useState('');
  const [searchResults, setSearchResults] = useState<MemorySearchResult | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const createAction = useAsyncAction(
    (payload: MemoryCreate, signal: AbortSignal) =>
      apiClient.post<MemoryRecord, MemoryCreate>('/api/memories', payload, { signal }),
  );
  const updateAction = useAsyncAction(
    (id: number, payload: MemoryDraft, signal: AbortSignal) =>
      apiClient.patch<MemoryRecord, MemoryDraft>(`/api/memories/${id}`, payload, { signal }),
  );
  const archiveAction = useAsyncAction((id: number, signal: AbortSignal) =>
    apiClient.post<MemoryRecord>(`/api/memories/${id}/archive`, undefined, { signal }),
  );
  const deleteAction = useAsyncAction((id: number, signal: AbortSignal) =>
    apiClient.delete<DeleteResult>(`/api/memories/${id}`, { signal }),
  );
  const searchAction = useAsyncAction((query: string, signal: AbortSignal) =>
    apiClient.get<MemorySearchResult>(`/api/memories/search?q=${encodeURIComponent(query)}`, {
      signal,
      dedupe: false,
    }),
  );
  const proposeAction = useAsyncAction((memoryIds: number[], signal: AbortSignal) =>
    apiClient.post<ConsolidationProposal, { memory_ids: number[] }>(
      '/api/memories/consolidation/propose',
      { memory_ids: memoryIds },
      { signal },
    ),
  );
  const applyAction = useAsyncAction((proposalId: string, signal: AbortSignal) =>
    apiClient.post<ConsolidationApplyResult, { proposal_id: string }>(
      '/api/memories/consolidation/apply',
      { proposal_id: proposalId },
      { signal },
    ),
  );

  const activeMemories = useMemo(
    () => memories.data?.items.filter((memory) => memory.status === 'active') ?? [],
    [memories.data],
  );
  const archivedMemories = useMemo(
    () => memories.data?.items.filter((memory) => memory.status === 'archived') ?? [],
    [memories.data],
  );

  const refresh = async () => {
    apiClient.invalidate({ prefix: '/api/memories' });
    await memories.reload();
  };

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSuccess(null);
    try {
      await createAction.run(draft);
      setDraft(EMPTY_DRAFT);
      setSuccess('Memory saved.');
      await refresh();
    } catch {
      // Hook state renders the structured failure while preserving draft input.
    }
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setSuccess(null);
    try {
      await updateAction.run(editing.id, {
        memory_type: editing.memory_type,
        content: editing.content,
        confidence: editing.confidence,
        importance: editing.importance,
      });
      setEditing(null);
      setSuccess('Memory updated.');
      await refresh();
    } catch {
      // Keep the dialog and edited content available for retry.
    }
  }

  async function handleConfirmedAction() {
    if (!confirmTarget) return;
    setSuccess(null);
    try {
      if (confirmTarget.action === 'archive') {
        await archiveAction.run(confirmTarget.memory.id);
        setSuccess('Memory archived.');
      } else {
        await deleteAction.run(confirmTarget.memory.id);
        setSuccess('Memory deleted.');
      }
      setConfirmTarget(null);
      await refresh();
    } catch {
      // Confirmation remains open so the learner can retry or cancel.
    }
  }

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!searchText.trim()) return;
    try {
      setSearchResults(await searchAction.run(searchText.trim()));
    } catch {
      // Search input remains unchanged.
    }
  }

  async function handleProposeConsolidation() {
    try {
      setProposal(await proposeAction.run([...selectedIds]));
    } catch {
      // Selected memories remain selected for retry.
    }
  }

  async function handleApplyConsolidation() {
    if (!proposal) return;
    try {
      await applyAction.run(proposal.proposal_id);
      setProposal(null);
      setSelectedIds(new Set());
      setSuccess('Memories consolidated and source memories archived.');
      await refresh();
    } catch {
      // Proposal remains visible when the snapshot can still be retried.
    }
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Learner memory"
        title="What your companion remembers"
        description="Keep durable preferences and learning context accurate. Chat proposals always wait for your decision."
      />

      {success ? <Notice tone="success">{success}</Notice> : null}

      <section aria-labelledby="add-memory-title">
        <SectionHeader headingId="add-memory-title" title="Add a memory" />
        <Card>
          <form className="form-stack" onSubmit={handleCreate}>
            <div className="form-grid">
              <label>
                Memory type
                <select
                  value={draft.memory_type}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      memory_type: event.target.value as MemoryType,
                    }))
                  }
                >
                  {MEMORY_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {titleCase(type)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field--wide">
                Memory content
                <textarea
                  required
                  maxLength={500}
                  value={draft.content}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, content: event.target.value }))
                  }
                  placeholder="For example: I learn best when explanations begin with a concrete example."
                />
              </label>
              <RangeField
                label="Confidence"
                value={draft.confidence}
                onChange={(confidence) => setDraft((current) => ({ ...current, confidence }))}
              />
              <RangeField
                label="Importance"
                value={draft.importance}
                onChange={(importance) => setDraft((current) => ({ ...current, importance }))}
              />
            </div>
            {createAction.error ? (
              <Notice tone="error">{errorMessage(createAction.error)}</Notice>
            ) : null}
            <Button
              type="submit"
              icon={<Plus size={18} aria-hidden="true" />}
              loading={createAction.isPending}
              loadingText="Saving…"
            >
              Save memory
            </Button>
          </form>
        </Card>
      </section>

      <section aria-labelledby="search-memory-title">
        <SectionHeader headingId="search-memory-title" title="Search memory" />
        <Card>
          <form className="inline-form" onSubmit={handleSearch}>
            <label className="visually-hidden" htmlFor="memory-search">
              Search learner memory
            </label>
            <input
              id="memory-search"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search preferences or learning context"
            />
            <Button
              type="submit"
              variant="secondary"
              icon={<Search size={18} aria-hidden="true" />}
              loading={searchAction.isPending}
              loadingText="Searching…"
            >
              Search
            </Button>
          </form>
          {searchAction.error ? (
            <Notice tone="error">{errorMessage(searchAction.error)}</Notice>
          ) : null}
          {searchResults ? (
            searchResults.items.length ? (
              <div className="memory-grid">
                {searchResults.items.map((result) => (
                  <Card key={result.memory_id} tone="muted" padding="small">
                    <Badge tone="info">{titleCase(result.memory_type)}</Badge>
                    <p>{result.content}</p>
                    <small>Match distance {result.distance.toFixed(3)}</small>
                  </Card>
                ))}
              </div>
            ) : (
              <EmptyState compact title="No matching memories" />
            )
          ) : null}
        </Card>
      </section>

      <section aria-labelledby="active-memory-title">
        <SectionHeader
          headingId="active-memory-title"
          title="Active memories"
          description={`${activeMemories.length} available during grounded study.`}
          actions={
            selectedIds.size >= 2 ? (
              <Button
                variant="secondary"
                icon={<Layers3 size={18} aria-hidden="true" />}
                onClick={() => void handleProposeConsolidation()}
                loading={proposeAction.isPending}
                loadingText="Reviewing…"
              >
                Consolidate {selectedIds.size}
              </Button>
            ) : null
          }
        />
        {memories.isLoading ? <LoadingState message="Loading learner memory…" /> : null}
        {memories.error ? (
          <ErrorState message={errorMessage(memories.error)} onRetry={() => void memories.reload()} />
        ) : null}
        {activeMemories.length ? (
          <div className="memory-grid">
            {activeMemories.map((memory) => (
              <MemoryCard
                key={memory.id}
                memory={memory}
                selected={selectedIds.has(memory.id)}
                onSelect={(selected) =>
                  setSelectedIds((current) => {
                    const next = new Set(current);
                    if (selected) next.add(memory.id);
                    else next.delete(memory.id);
                    return next;
                  })
                }
                onEdit={() => setEditing({ ...memory })}
                onArchive={() => setConfirmTarget({ memory, action: 'archive' })}
                onDelete={() => setConfirmTarget({ memory, action: 'delete' })}
              />
            ))}
          </div>
        ) : !memories.isLoading && !memories.error ? (
          <EmptyState
            title="No active memories"
            description="Add one explicitly, or review a proposal after a chat answer."
          />
        ) : null}
        {proposeAction.error ? (
          <Notice tone="error">{errorMessage(proposeAction.error)}</Notice>
        ) : null}
      </section>

      {archivedMemories.length ? (
        <section aria-labelledby="archived-memory-title">
          <SectionHeader headingId="archived-memory-title" title="Archived memories" />
          <div className="memory-grid">
            {archivedMemories.map((memory) => (
              <Card key={memory.id} tone="muted">
                <Badge>{titleCase(memory.memory_type)}</Badge>
                <p>{memory.content}</p>
                <small>Archived memory · updated {formatDateTime(memory.updated_at)}</small>
                <Button
                  variant="danger"
                  icon={<Trash2 size={17} aria-hidden="true" />}
                  onClick={() => setConfirmTarget({ memory, action: 'delete' })}
                >
                  Delete permanently
                </Button>
              </Card>
            ))}
          </div>
        </section>
      ) : null}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit memory"
        description="Changes update both SQLite and the local memory index."
        actions={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)} disabled={updateAction.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="edit-memory-form" loading={updateAction.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        {editing ? (
          <form id="edit-memory-form" className="form-stack" onSubmit={handleUpdate}>
            <label>
              Memory type
              <select
                value={editing.memory_type}
                onChange={(event) =>
                  setEditing((current) =>
                    current
                      ? { ...current, memory_type: event.target.value as MemoryType }
                      : current,
                  )
                }
              >
                {MEMORY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {titleCase(type)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Content
              <textarea
                required
                maxLength={500}
                value={editing.content}
                onChange={(event) =>
                  setEditing((current) =>
                    current ? { ...current, content: event.target.value } : current,
                  )
                }
              />
            </label>
            {updateAction.error ? (
              <Notice tone="error">{errorMessage(updateAction.error)}</Notice>
            ) : null}
          </form>
        ) : null}
      </Dialog>

      <ConfirmationDialog
        open={confirmTarget !== null}
        onClose={() => setConfirmTarget(null)}
        onConfirm={() => void handleConfirmedAction()}
        title={confirmTarget?.action === 'delete' ? 'Delete this memory?' : 'Archive this memory?'}
        description={
          <div className="form-stack">
            <p>
              {confirmTarget?.action === 'delete'
                ? 'This permanently removes the memory and its local vector entry.'
                : 'Archived memories stop influencing retrieval and cannot currently be restored in the web app.'}
            </p>
            {confirmTarget?.action === 'archive' && archiveAction.error ? (
              <Notice tone="error">{errorMessage(archiveAction.error)}</Notice>
            ) : null}
            {confirmTarget?.action === 'delete' && deleteAction.error ? (
              <Notice tone="error">{errorMessage(deleteAction.error)}</Notice>
            ) : null}
          </div>
        }
        confirmLabel={confirmTarget?.action === 'delete' ? 'Delete memory' : 'Archive memory'}
        destructive
        loading={archiveAction.isPending || deleteAction.isPending}
      />

      <Dialog
        open={proposal !== null}
        onClose={() => setProposal(null)}
        title="Review consolidation"
        description="The proposal is held server-side until you explicitly apply it."
        actions={
          <>
            <Button variant="ghost" onClick={() => setProposal(null)} disabled={applyAction.isPending}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleApplyConsolidation()}
              loading={applyAction.isPending}
              disabled={!proposal?.should_consolidate}
            >
              Apply consolidation
            </Button>
          </>
        }
      >
        {proposal ? (
          <div className="form-stack">
            <Badge tone={proposal.should_consolidate ? 'success' : 'warning'}>
              {proposal.should_consolidate ? 'Ready to consolidate' : 'Not recommended'}
            </Badge>
            <p>{proposal.reason}</p>
            {proposal.should_consolidate ? (
              <Card tone="accent">
                <strong>{titleCase(proposal.memory_type)}</strong>
                <p>{proposal.content}</p>
              </Card>
            ) : null}
            {applyAction.error ? (
              <Notice tone="error">{errorMessage(applyAction.error)}</Notice>
            ) : null}
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}

interface MemoryCardProps {
  memory: MemoryRecord;
  selected: boolean;
  onSelect: (selected: boolean) => void;
  onEdit: () => void;
  onArchive: () => void;
  onDelete: () => void;
}

function MemoryCard({
  memory,
  selected,
  onSelect,
  onEdit,
  onArchive,
  onDelete,
}: MemoryCardProps) {
  return (
    <Card className="memory-card">
      <div className="memory-card__header">
        <Badge tone="primary">{titleCase(memory.memory_type)}</Badge>
        <label className="selection-control">
          <input type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} />
          Select for consolidation
        </label>
      </div>
      <p className="memory-card__content">{memory.content}</p>
      <div className="memory-card__scores">
        <ProgressBar label="Confidence" value={memory.confidence} max={1} />
        <ProgressBar label="Importance" value={memory.importance} max={1} />
      </div>
      <small>Updated {formatDateTime(memory.updated_at)}</small>
      {memory.occurrence_count ? (
        <small>
          Evidence occurrences: {memory.occurrence_count}
          {memory.improvement_state ? ` · State: ${memory.improvement_state}` : ''}
          {memory.source_quiz_id ? ` · Source quiz: ${memory.source_quiz_id}` : ''}
        </small>
      ) : null}
      {memory.evidence?.length ? (
        <details>
          <summary>Supporting evidence ({memory.evidence.length})</summary>
          <ul>
            {memory.evidence.map((evidence, index) => (
              <li key={`${memory.id}-evidence-${index}`}>
                {String(evidence.question ?? 'Recorded interaction')} — {String(evidence.outcome ?? 'observed')}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {memory.latest_use ? (
        <small>
          Latest use: {memory.latest_use.workflow_type} · {memory.latest_use.reason}
        </small>
      ) : null}
      <div className="card-actions">
        <Button variant="ghost" icon={<Pencil size={17} aria-hidden="true" />} onClick={onEdit}>
          Edit
        </Button>
        <Button variant="ghost" icon={<Archive size={17} aria-hidden="true" />} onClick={onArchive}>
          Archive
        </Button>
        <Button variant="ghost" icon={<Trash2 size={17} aria-hidden="true" />} onClick={onDelete}>
          Delete
        </Button>
      </div>
    </Card>
  );
}

interface RangeFieldProps {
  label: string;
  value: number;
  onChange: (value: number) => void;
}

function RangeField({ label, value, onChange }: RangeFieldProps) {
  return (
    <label>
      {label}: {value.toFixed(2)}
      <input
        type="range"
        min="0"
        max="1"
        step="0.05"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
