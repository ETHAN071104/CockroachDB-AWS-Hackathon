import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiClient, setGuestSessionToken } from '../api/client';


function response(): Response {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  setGuestSessionToken(null);
  vi.unstubAllGlobals();
});

describe('guest-session request boundary', () => {
  it('adds the credential to Agentbook API paths only', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => response(),
    );
    vi.stubGlobal('fetch', fetchMock);
    setGuestSessionToken('unit-test-credential');

    await apiClient.get('/api/notebooks', { cacheTtlMs: 0 });
    await apiClient.get('https://external.example/resource', { cacheTtlMs: 0 });

    const internal = new Headers(fetchMock.mock.calls.at(0)?.[1]?.headers);
    const external = new Headers(fetchMock.mock.calls.at(1)?.[1]?.headers);
    expect(internal.get('Authorization')).toBe('Bearer unit-test-credential');
    expect(external.has('Authorization')).toBe(false);
  });

  it('does not send an old credential during public bootstrap', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => response(),
    );
    vi.stubGlobal('fetch', fetchMock);
    setGuestSessionToken('unit-test-credential');

    await apiClient.post(
      '/api/guest-session',
      undefined,
      { headers: { 'Idempotency-Key': 'A'.repeat(32) } },
    );

    const headers = new Headers(fetchMock.mock.calls.at(0)?.[1]?.headers);
    expect(headers.has('Authorization')).toBe(false);
    expect(headers.get('Idempotency-Key')).toBe('A'.repeat(32));
  });
});
