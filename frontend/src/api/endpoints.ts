import { apiClient, withQuery } from './client';
import type { ApiCallOptions, GetOptions } from './client';
import type * as T from './types';

export interface DocumentListFilters {
  q?: string;
  notebookId?: T.NotebookFilter;
}

export interface ReviewQueueFilters {
  sessionLimit?: number;
  maxItems?: number;
}

const enc = (value: string | number): string => encodeURIComponent(String(value));

function afterMutation<TResult>(
  request: Promise<TResult>,
  invalidate: () => void,
): Promise<TResult> {
  return request.then((result) => {
    invalidate();
    return result;
  });
}

function invalidateDashboard(): void {
  apiClient.invalidate({ prefix: '/api/dashboard' });
}

function invalidateLibrary(): void {
  apiClient.invalidate({ prefix: '/api/notebooks' });
  apiClient.invalidate({ prefix: '/api/documents' });
  apiClient.invalidate({ prefix: '/api/topics' });
  invalidateDashboard();
}

function invalidateStudy(): void {
  apiClient.invalidate({ prefix: '/api/study' });
  apiClient.invalidate({ prefix: '/api/reports' });
  apiClient.invalidate({ prefix: '/api/system/integrity' });
  invalidateDashboard();
}

function invalidateMemory(): void {
  apiClient.invalidate({ prefix: '/api/memories' });
  apiClient.invalidate({ prefix: '/api/system/integrity' });
  invalidateDashboard();
}

function summaryPath(kind: T.SummaryKind, scopeId: string | number): string {
  return `/api/${kind === 'document' ? 'documents' : `${kind}s`}/${enc(scopeId)}/summary`;
}

export const healthApi = {
  get(options?: GetOptions): Promise<T.HealthResponse> {
    return apiClient.get('/api/health', options);
  },
};

export const guestSessionApi = {
  create(
    idempotencyKey: string,
    options?: ApiCallOptions,
  ): Promise<T.GuestSessionCreateResponse> {
    return apiClient.post(
      '/api/guest-session',
      undefined,
      {
        ...options,
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    );
  },
  inspect(options?: GetOptions): Promise<T.GuestSessionInspectResponse> {
    return apiClient.get('/api/guest-session', {
      ...options,
      forceRefresh: true,
      cacheTtlMs: 0,
    });
  },
};

export const dashboardApi = {
  get(recentLimit = 5, options?: GetOptions): Promise<T.Dashboard> {
    return apiClient.get(
      withQuery('/api/dashboard', { recent_limit: recentLimit }),
      options,
    );
  },
};

export const notebookApi = {
  list(q?: string, options?: GetOptions): Promise<T.NotebookList> {
    return apiClient.get(withQuery('/api/notebooks', { q }), options);
  },
  get(id: number, options?: GetOptions): Promise<T.Notebook> {
    return apiClient.get(`/api/notebooks/${enc(id)}`, options);
  },
  getUnsorted(options?: GetOptions): Promise<T.Notebook> {
    return apiClient.get('/api/notebooks/unsorted', options);
  },
  create(payload: T.NotebookCreate, options?: ApiCallOptions): Promise<T.Notebook> {
    return afterMutation(
      apiClient.post('/api/notebooks', payload, options),
      invalidateLibrary,
    );
  },
  update(
    id: number,
    payload: T.NotebookUpdate,
    options?: ApiCallOptions,
  ): Promise<T.Notebook> {
    return afterMutation(
      apiClient.patch(`/api/notebooks/${enc(id)}`, payload, options),
      invalidateLibrary,
    );
  },
  delete(id: number, options?: ApiCallOptions): Promise<T.DeleteResult> {
    return afterMutation(
      apiClient.delete(`/api/notebooks/${enc(id)}`, options),
      invalidateLibrary,
    );
  },
  listDocuments(
    id: number,
    q?: string,
    options?: GetOptions,
  ): Promise<T.DocumentList> {
    return apiClient.get(
      withQuery(`/api/notebooks/${enc(id)}/documents`, { q }),
      options,
    );
  },
  listUnsortedDocuments(q?: string, options?: GetOptions): Promise<T.DocumentList> {
    return apiClient.get(
      withQuery('/api/notebooks/unsorted/documents', { q }),
      options,
    );
  },
  addDocument(
    notebookId: number,
    documentId: number,
    options?: ApiCallOptions,
  ): Promise<T.DocumentRecord> {
    return afterMutation(
      apiClient.post(
        `/api/notebooks/${enc(notebookId)}/documents/${enc(documentId)}`,
        undefined,
        options,
      ),
      invalidateLibrary,
    );
  },
  removeDocument(
    notebookId: number,
    documentId: number,
    options?: ApiCallOptions,
  ): Promise<T.DocumentRecord> {
    return afterMutation(
      apiClient.delete(
        `/api/notebooks/${enc(notebookId)}/documents/${enc(documentId)}`,
        options,
      ),
      invalidateLibrary,
    );
  },
};

export const documentApi = {
  list(
    filters: DocumentListFilters = {},
    options?: GetOptions,
  ): Promise<T.DocumentList> {
    return apiClient.get(
      withQuery('/api/documents', {
        q: filters.q,
        notebook_id: filters.notebookId,
      }),
      options,
    );
  },
  get(id: number, options?: GetOptions): Promise<T.DocumentRecord> {
    return apiClient.get(`/api/documents/${enc(id)}`, options);
  },
  upload(
    file: File,
    notebookId?: number | null,
    options?: ApiCallOptions,
  ): Promise<T.DocumentUploadResult> {
    return afterMutation(
      apiClient.upload('/api/documents', file, {
        ...options,
        fields: { notebook_id: notebookId },
      }),
      invalidateLibrary,
    );
  },
  assign(
    id: number,
    notebookId: number | null,
    options?: ApiCallOptions,
  ): Promise<T.DocumentRecord> {
    return afterMutation(
      apiClient.patch<T.DocumentRecord, T.DocumentAssignment>(
        `/api/documents/${enc(id)}/notebook`,
        { notebook_id: notebookId },
        options,
      ),
      invalidateLibrary,
    );
  },
  delete(id: number, options?: ApiCallOptions): Promise<T.DeleteResult> {
    return afterMutation(
      apiClient.delete(`/api/documents/${enc(id)}`, options),
      invalidateLibrary,
    );
  },
};

export const intelligenceApi = {
  getSummary(
    kind: T.SummaryKind,
    scopeId: string | number,
    options?: GetOptions,
  ): Promise<T.Summary> {
    return apiClient.get(summaryPath(kind, scopeId), options);
  },
  generateSummary(
    kind: T.SummaryKind,
    scopeId: string | number,
    options?: ApiCallOptions,
  ): Promise<T.Summary> {
    return afterMutation(
      apiClient.post(summaryPath(kind, scopeId), undefined, options),
      () => apiClient.invalidate(summaryPath(kind, scopeId)),
    );
  },
  listTopics(q?: string, options?: GetOptions): Promise<T.TopicList> {
    return apiClient.get(withQuery('/api/topics', { q }), options);
  },
  getTopic(topicId: string, options?: GetOptions): Promise<T.Topic> {
    return apiClient.get(`/api/topics/${enc(topicId)}`, options);
  },
  listDocumentTopics(documentId: number, options?: GetOptions): Promise<T.TopicList> {
    return apiClient.get(`/api/documents/${enc(documentId)}/topics`, options);
  },
  extractDocumentTopics(
    documentId: number,
    options?: ApiCallOptions,
  ): Promise<T.TopicList> {
    return afterMutation(
      apiClient.post(`/api/documents/${enc(documentId)}/topics`, undefined, options),
      () => apiClient.invalidate({ prefix: `/api/documents/${enc(documentId)}/topics` }),
    );
  },
  listNotebookTopics(notebookId: number, options?: GetOptions): Promise<T.TopicList> {
    return apiClient.get(`/api/notebooks/${enc(notebookId)}/topics`, options);
  },
  extractNotebookTopics(
    notebookId: number,
    options?: ApiCallOptions,
  ): Promise<T.TopicList> {
    return afterMutation(
      apiClient.post(`/api/notebooks/${enc(notebookId)}/topics`, undefined, options),
      () => apiClient.invalidate({ prefix: `/api/notebooks/${enc(notebookId)}/topics` }),
    );
  },
  extractTopics(scope: T.RetrievalScope, options?: ApiCallOptions): Promise<T.TopicList> {
    return afterMutation(
      apiClient.post('/api/topics/extract', { scope }, options),
      () => apiClient.invalidate({ prefix: '/api/topics' }),
    );
  },
};

export const chatApi = {
  send(payload: T.ChatRequest, options?: ApiCallOptions): Promise<T.ChatResponse> {
    return afterMutation(apiClient.post('/api/chat', payload, options), invalidateStudy);
  },
  updateOutcome(
    interactionId: number,
    outcome: T.StudyOutcome,
    options?: ApiCallOptions,
  ): Promise<T.StudyInteraction> {
    return afterMutation(
      apiClient.patch(
        `/api/study/interactions/${enc(interactionId)}/outcome`,
        { outcome },
        options,
      ),
      invalidateStudy,
    );
  },
};

export const sessionApi = {
  list(options?: GetOptions): Promise<T.StudySessionList> {
    return apiClient.get('/api/study/sessions', options);
  },
  get(id: number, options?: GetOptions): Promise<T.SessionDetail> {
    return apiClient.get(`/api/study/sessions/${enc(id)}`, options);
  },
  endActive(options?: ApiCallOptions): Promise<T.StudySession> {
    return afterMutation(
      apiClient.post('/api/study/sessions/active/end', undefined, options),
      invalidateStudy,
    );
  },
};

export const memoryApi = {
  list(includeArchived = false, options?: GetOptions): Promise<T.MemoryList> {
    return apiClient.get(
      withQuery('/api/memories', { include_archived: includeArchived }),
      options,
    );
  },
  get(id: number, options?: GetOptions): Promise<T.MemoryRecord> {
    return apiClient.get(`/api/memories/${enc(id)}`, options);
  },
  search(q: string, limit = 5, options?: GetOptions): Promise<T.MemorySearchResult> {
    return apiClient.get(withQuery('/api/memories/search', { q, limit }), options);
  },
  create(payload: T.MemoryCreate, options?: ApiCallOptions): Promise<T.MemoryRecord> {
    return afterMutation(apiClient.post('/api/memories', payload, options), invalidateMemory);
  },
  update(
    id: number,
    payload: T.MemoryUpdate,
    options?: ApiCallOptions,
  ): Promise<T.MemoryRecord> {
    return afterMutation(
      apiClient.patch(`/api/memories/${enc(id)}`, payload, options),
      invalidateMemory,
    );
  },
  archive(id: number, options?: ApiCallOptions): Promise<T.MemoryRecord> {
    return afterMutation(
      apiClient.post(`/api/memories/${enc(id)}/archive`, undefined, options),
      invalidateMemory,
    );
  },
  delete(id: number, options?: ApiCallOptions): Promise<T.DeleteResult> {
    return afterMutation(
      apiClient.delete(`/api/memories/${enc(id)}`, options),
      invalidateMemory,
    );
  },
  decideProposal(
    proposalId: string,
    payload: T.MemoryProposalDecisionRequest,
    options?: ApiCallOptions,
  ): Promise<T.MemoryProposalDecisionResult> {
    return afterMutation(
      apiClient.post(
        `/api/memories/proposals/${enc(proposalId)}/decision`,
        payload,
        options,
      ),
      invalidateMemory,
    );
  },
  proposeConsolidation(
    memoryIds: number[],
    options?: ApiCallOptions,
  ): Promise<T.ConsolidationProposal> {
    return apiClient.post(
      '/api/memories/consolidation/propose',
      { memory_ids: memoryIds },
      options,
    );
  },
  applyConsolidation(
    proposalId: string,
    options?: ApiCallOptions,
  ): Promise<T.ConsolidationApplyResult> {
    return afterMutation(
      apiClient.post(
        '/api/memories/consolidation/apply',
        { proposal_id: proposalId },
        options,
      ),
      invalidateMemory,
    );
  },
};

export const quizApi = {
  generate(
    payload: T.QuizGenerateRequest,
    options?: ApiCallOptions,
  ): Promise<T.PresentedQuiz> {
    return apiClient.post('/api/study/actions/quizzes/generate', payload, options);
  },
  submit(
    quizId: string,
    responses: T.QuizAnswer[],
    options?: ApiCallOptions,
  ): Promise<T.QuizSubmission> {
    return afterMutation(
      apiClient.post(
        `/api/study/actions/quizzes/${enc(quizId)}/submit`,
        { responses },
        options,
      ),
      invalidateStudy,
    );
  },
};

export const reportApi = {
  listSessions(limit?: number, options?: GetOptions): Promise<T.SessionReport[]> {
    return apiClient.get(
      withQuery('/api/reports/study/sessions', { limit }),
      options,
    );
  },
  getSession(id: number, options?: GetOptions): Promise<T.SessionReport> {
    return apiClient.get(`/api/reports/study/sessions/${enc(id)}`, options);
  },
  summarizeSession(id: number, options?: ApiCallOptions): Promise<T.SessionSummary> {
    return apiClient.post(
      `/api/reports/study/sessions/${enc(id)}/summary`,
      undefined,
      options,
    );
  },
  getProgress(sessionLimit?: number, options?: GetOptions): Promise<T.ProgressReport> {
    return apiClient.get(
      withQuery('/api/reports/study/progress', { session_limit: sessionLimit }),
      options,
    );
  },
  getQuizPerformance(
    attemptLimit?: number,
    options?: GetOptions,
  ): Promise<T.QuizPerformance> {
    return apiClient.get(
      withQuery('/api/reports/quizzes/performance', { attempt_limit: attemptLimit }),
      options,
    );
  },
  getQuizAttempt(id: number, options?: GetOptions): Promise<T.QuizAttemptReport> {
    return apiClient.get(`/api/reports/quizzes/${enc(id)}`, options);
  },
};

export const studyActionApi = {
  getReviewQueue(
    filters: ReviewQueueFilters = {},
    options?: GetOptions,
  ): Promise<T.ReviewQueue> {
    return apiClient.get(
      withQuery('/api/study/actions/review-queue', {
        session_limit: filters.sessionLimit,
        max_items: filters.maxItems,
      }),
      options,
    );
  },
  generateReview(
    interactionId: number,
    scope?: T.RetrievalScope | null,
    options?: ApiCallOptions,
  ): Promise<T.ReviewAction> {
    return apiClient.post(
      '/api/study/actions/review',
      { interaction_id: interactionId, scope: scope ?? null },
      options,
    );
  },
  buildPlan(payload: T.StudyPlanRequest, options?: ApiCallOptions): Promise<T.StudyPlan> {
    return apiClient.post('/api/study/actions/plan', payload, options);
  },
  buildCoachingPlan(
    payload: T.StudyPlanRequest,
    options?: ApiCallOptions,
  ): Promise<T.CoachingPlan> {
    return apiClient.post('/api/study/actions/coaching-plan', payload, options);
  },
};

export const systemApi = {
  getIntegrity(options?: GetOptions): Promise<T.IntegrityReport> {
    return apiClient.get('/api/system/integrity', options);
  },
};

export const api = {
  health: healthApi,
  guestSession: guestSessionApi,
  dashboard: dashboardApi,
  notebooks: notebookApi,
  documents: documentApi,
  intelligence: intelligenceApi,
  chat: chatApi,
  sessions: sessionApi,
  memories: memoryApi,
  quizzes: quizApi,
  reports: reportApi,
  studyActions: studyActionApi,
  system: systemApi,

  getHealth: healthApi.get,
  getDashboard: dashboardApi.get,
  listNotebooks: notebookApi.list,
  getNotebook: notebookApi.get,
  getUnsortedNotebook: notebookApi.getUnsorted,
  createNotebook: notebookApi.create,
  updateNotebook: notebookApi.update,
  deleteNotebook: notebookApi.delete,
  listNotebookDocuments: notebookApi.listDocuments,
  listUnsortedDocuments: notebookApi.listUnsortedDocuments,
  addDocumentToNotebook: notebookApi.addDocument,
  removeDocumentFromNotebook: notebookApi.removeDocument,
  listDocuments: documentApi.list,
  getDocument: documentApi.get,
  uploadDocument: documentApi.upload,
  assignDocument: documentApi.assign,
  deleteDocument: documentApi.delete,
  getCachedSummary: intelligenceApi.getSummary,
  generateSummary: intelligenceApi.generateSummary,
  listTopics: intelligenceApi.listTopics,
  getTopic: intelligenceApi.getTopic,
  listDocumentTopics: intelligenceApi.listDocumentTopics,
  extractDocumentTopics: intelligenceApi.extractDocumentTopics,
  listNotebookTopics: intelligenceApi.listNotebookTopics,
  extractNotebookTopics: intelligenceApi.extractNotebookTopics,
  extractTopics: intelligenceApi.extractTopics,
  sendChat: chatApi.send,
  updateInteractionOutcome: chatApi.updateOutcome,
  listStudySessions: sessionApi.list,
  getStudySession: sessionApi.get,
  endActiveSession: sessionApi.endActive,
  listMemories: memoryApi.list,
  getMemory: memoryApi.get,
  searchMemories: memoryApi.search,
  createMemory: memoryApi.create,
  updateMemory: memoryApi.update,
  archiveMemory: memoryApi.archive,
  deleteMemory: memoryApi.delete,
  decideMemoryProposal: memoryApi.decideProposal,
  proposeMemoryConsolidation: memoryApi.proposeConsolidation,
  applyMemoryConsolidation: memoryApi.applyConsolidation,
  generateQuiz: quizApi.generate,
  submitQuiz: quizApi.submit,
  listSessionReports: reportApi.listSessions,
  getSessionReport: reportApi.getSession,
  summarizeSession: reportApi.summarizeSession,
  getProgress: reportApi.getProgress,
  getQuizPerformance: reportApi.getQuizPerformance,
  getQuizAttempt: reportApi.getQuizAttempt,
  getReviewQueue: studyActionApi.getReviewQueue,
  generateReview: studyActionApi.generateReview,
  buildStudyPlan: studyActionApi.buildPlan,
  buildCoachingPlan: studyActionApi.buildCoachingPlan,
  getIntegrity: systemApi.getIntegrity,
} as const;
