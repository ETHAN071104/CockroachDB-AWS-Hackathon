import type { ErrorResponse } from './types';

const DEFAULT_CACHE_TTL_MS = 3_000;

export interface ApiCallOptions {
  signal?: AbortSignal;
}

export interface GetOptions extends ApiCallOptions {
  cacheTtlMs?: number;
  dedupe?: boolean;
  forceRefresh?: boolean;
}

export interface MutationOptions extends ApiCallOptions {
  headers?: HeadersInit;
}

export interface UploadOptions extends ApiCallOptions {
  fieldName?: string;
  fields?: Record<string, string | number | boolean | null | undefined>;
}

interface CacheEntry {
  data: unknown;
  expiresAt: number;
}

interface InFlightEntry {
  controller: AbortController;
  promise: Promise<unknown>;
  subscribers: number;
  settled: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly reason: string;
  readonly nextAction: string;
  readonly retryable: boolean;
  readonly requestId?: string;
  readonly details?: unknown;
  readonly legacyCode?: string;

  constructor(
    message: string,
    options: {
      status: number;
      code: string;
      title?: string;
      reason?: string;
      nextAction?: string;
      retryable?: boolean;
      requestId?: string;
      details?: unknown;
      legacyCode?: string;
    },
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.title = options.title ?? message;
    this.reason = options.reason ?? message;
    this.nextAction = options.nextAction ?? (
      options.status === 0
        ? 'Check the backend connection and try again.'
        : 'Review the request and try again.'
    );
    this.retryable = options.retryable ?? (
      options.status === 0 || options.status >= 500
    );
    this.requestId = options.requestId;
    this.details = options.details;
    this.legacyCode = options.legacyCode;
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    error instanceof DOMException && error.name === 'AbortError'
  ) || (
    error instanceof Error && error.name === 'AbortError'
  );
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof Error) {
    return new ApiError('Agentbook could not be reached', {
      status: 0,
      code: 'NETWORK_ERROR',
      reason: 'The browser could not complete the request to the Agentbook backend.',
      nextAction: 'Confirm the backend is running, then try again.',
      retryable: true,
    });
  }
  return new ApiError('Agentbook could not be reached', {
    status: 0,
    code: 'NETWORK_ERROR',
    reason: 'The browser could not complete the request to the Agentbook backend.',
    nextAction: 'Confirm the backend is running, then try again.',
    retryable: true,
  });
}

export function getErrorMessage(error: unknown): string {
  return toApiError(error).reason;
}

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? '').trim().replace(/\/$/, '');
}

export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
);

let guestToken: string | null = null;

function requestUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE_URL}${normalizedPath}`;
}

function abortError(): DOMException {
  return new DOMException('The request was aborted.', 'AbortError');
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const structured = payload as (
      Partial<ErrorResponse> & {
        detail?: unknown;
        message?: unknown;
      }
    ) | undefined;
    const body = structured?.error;
    const legacyDetail = legacyDetailMessage(structured?.detail);
    const fallbackMessage = (
      body?.message
      || legacyDetail
      || (typeof structured?.message === 'string' ? structured.message : '')
      || response.statusText
      || 'The request could not be completed.'
    );
    const title = body?.title || fallbackMessage;
    const reason = body?.reason || fallbackMessage;
    const requestId = safeRequestId(
      body?.request_id || response.headers.get('X-Request-ID'),
    );
    throw new ApiError(
      fallbackMessage,
      {
        status: response.status,
        code: body?.code || `http_${response.status}`,
        title,
        reason,
        nextAction: body?.next_action || legacyNextAction(response.status),
        retryable: body?.retryable ?? legacyRetryable(response.status),
        requestId,
        details: body?.details,
        legacyCode: body?.legacy_code ?? undefined,
      },
    );
  }

  return payload as T;
}

function requestHeaders(
  path: string,
  method: string,
  initial: HeadersInit = {},
): Headers {
  const headers = new Headers(initial);
  const isAgentbookPath = !/^https?:\/\//i.test(path);
  const isPublicBootstrap = (
    method === 'POST'
    && path.replace(/\/$/, '') === '/api/guest-session'
  );
  if (guestToken && isAgentbookPath && !isPublicBootstrap) {
    headers.set('Authorization', `Bearer ${guestToken}`);
  }
  return headers;
}

export function setGuestSessionToken(token: string | null): void {
  guestToken = token?.trim() || null;
  apiClient.invalidate();
}

function legacyDetailMessage(value: unknown): string {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (
    typeof value === 'object'
    && value !== null
    && 'message' in value
    && typeof value.message === 'string'
  ) {
    return value.message.trim();
  }
  return '';
}

function legacyRetryable(status: number): boolean {
  return status === 408
    || status === 429
    || status === 500
    || status === 502
    || status === 503
    || status === 504;
}

function legacyNextAction(status: number): string {
  if (status === 404) return 'Return to the previous screen and choose an available resource.';
  if (status === 409) return 'Refresh the page and review the current state.';
  if (status === 422) return 'Review the submitted values and try again.';
  return legacyRetryable(status)
    ? 'Try again shortly.'
    : 'Use the request details when asking for support.';
}

function safeRequestId(value: string | null | undefined): string | undefined {
  const cleaned = value?.trim() ?? '';
  return /^[A-Za-z0-9_-]{8,80}$/.test(cleaned) ? cleaned : undefined;
}

async function execute<T>(
  url: string,
  init: RequestInit,
): Promise<T> {
  try {
    return await parseResponse<T>(await fetch(url, init));
  } catch (error) {
    if (isAbortError(error) || error instanceof ApiError) {
      throw error;
    }
    throw toApiError(error);
  }
}

function subscribe<T>(
  entry: InFlightEntry,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) {
    return Promise.reject(abortError());
  }

  entry.subscribers += 1;
  return new Promise<T>((resolve, reject) => {
    let completed = false;

    const release = () => {
      if (completed) return;
      completed = true;
      signal?.removeEventListener('abort', onAbort);
      entry.subscribers -= 1;
      if (entry.subscribers === 0 && !entry.settled) {
        entry.controller.abort();
      }
    };

    const onAbort = () => {
      release();
      reject(abortError());
    };

    signal?.addEventListener('abort', onAbort, { once: true });
    entry.promise.then(
      (data) => {
        if (completed) return;
        release();
        resolve(data as T);
      },
      (error: unknown) => {
        if (completed) return;
        release();
        reject(error);
      },
    );
  });
}

class ApiClient {
  private readonly cache = new Map<string, CacheEntry>();
  private readonly inFlight = new Map<string, InFlightEntry>();

  async get<T>(path: string, options: GetOptions = {}): Promise<T> {
    const url = requestUrl(path);
    const ttl = options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
    const cached = this.cache.get(url);
    if (!options.forceRefresh && cached && cached.expiresAt > Date.now()) {
      if (options.signal?.aborted) throw abortError();
      return cached.data as T;
    }
    if (cached) this.cache.delete(url);

    const shouldDedupe = options.dedupe !== false;
    const candidate = shouldDedupe ? this.inFlight.get(url) : undefined;
    const existing = candidate && !candidate.controller.signal.aborted
      ? candidate
      : undefined;
    if (candidate && !existing) this.inFlight.delete(url);
    if (existing) {
      return subscribe<T>(existing, options.signal);
    }

    const controller = new AbortController();
    const entry: InFlightEntry = {
      controller,
      subscribers: 0,
      settled: false,
      promise: Promise.resolve(undefined),
    };
    entry.promise = execute<T>(url, {
      method: 'GET',
      headers: requestHeaders(path, 'GET', { Accept: 'application/json' }),
      signal: controller.signal,
    }).then((data) => {
      if (ttl > 0) {
        this.cache.set(url, { data, expiresAt: Date.now() + ttl });
      }
      return data;
    }).finally(() => {
      entry.settled = true;
      if (this.inFlight.get(url) === entry) {
        this.inFlight.delete(url);
      }
    });
    if (shouldDedupe) this.inFlight.set(url, entry);
    return subscribe<T>(entry, options.signal);
  }

  post<T, TBody = unknown>(
    path: string,
    body?: TBody,
    options: MutationOptions = {},
  ): Promise<T> {
    return this.json<T, TBody>('POST', path, body, options);
  }

  patch<T, TBody = unknown>(
    path: string,
    body: TBody,
    options: MutationOptions = {},
  ): Promise<T> {
    return this.json<T, TBody>('PATCH', path, body, options);
  }

  put<T, TBody = unknown>(
    path: string,
    body?: TBody,
    options: MutationOptions = {},
  ): Promise<T> {
    return this.json<T, TBody>('PUT', path, body, options);
  }

  delete<T>(
    path: string,
    options: MutationOptions = {},
  ): Promise<T> {
    return execute<T>(requestUrl(path), {
      method: 'DELETE',
      headers: requestHeaders(
        path,
        'DELETE',
        { Accept: 'application/json', ...options.headers },
      ),
      signal: options.signal,
    });
  }

  upload<T>(
    path: string,
    file: File,
    options: UploadOptions = {},
  ): Promise<T> {
    const form = new FormData();
    form.append(options.fieldName ?? 'file', file, file.name);
    for (const [key, value] of Object.entries(options.fields ?? {})) {
      if (value !== null && value !== undefined) {
        form.append(key, String(value));
      }
    }
    return execute<T>(requestUrl(path), {
      method: 'POST',
      body: form,
      headers: requestHeaders(path, 'POST', { Accept: 'application/json' }),
      signal: options.signal,
    });
  }

  async download(path: string, options: ApiCallOptions = {}): Promise<Blob> {
    try {
      const response = await fetch(requestUrl(path), {
        method: 'GET',
        headers: requestHeaders(path, 'GET', { Accept: 'application/zip' }),
        signal: options.signal,
      });
      if (!response.ok) {
        return await parseResponse<never>(response);
      }
      return await response.blob();
    } catch (error) {
      if (isAbortError(error) || error instanceof ApiError) throw error;
      throw toApiError(error);
    }
  }

  invalidate(): void;
  invalidate(path: string): void;
  invalidate(target: { prefix: string }): void;
  invalidate(target?: string | { prefix: string }): void {
    if (target === undefined) {
      this.cache.clear();
      return;
    }
    if (typeof target === 'string') {
      this.cache.delete(requestUrl(target));
      return;
    }
    const prefix = requestUrl(target.prefix);
    for (const key of this.cache.keys()) {
      if (key.startsWith(prefix)) this.cache.delete(key);
    }
  }

  private json<T, TBody>(
    method: 'POST' | 'PATCH' | 'PUT',
    path: string,
    body: TBody | undefined,
    options: MutationOptions,
  ): Promise<T> {
    return execute<T>(requestUrl(path), {
      method,
      headers: requestHeaders(path, method, {
          Accept: 'application/json',
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...options.headers,
        }),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options.signal,
    });
  }
}

export const apiClient = new ApiClient();

export type QueryValue =
  | string
  | number
  | boolean
  | null
  | undefined;

export function withQuery(
  path: string,
  params: Record<string, QueryValue>,
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}
