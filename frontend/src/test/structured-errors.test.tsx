import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiClient } from '../api';
import {
  EmptyStateAction,
  ErrorNotice,
  InlineFieldError,
  RetryableErrorCard,
} from '../components';
import { StudyActionsPage } from '../pages/StudyActionsPage';


describe('structured API errors', () => {
  beforeEach(() => apiClient.invalidate());
  afterEach(() => vi.unstubAllGlobals());

  it('parses the standard envelope and keeps the safe request ID', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(
        JSON.stringify({
          error: {
            code: 'AI_PROVIDER_RATE_LIMITED',
            title: 'AI provider rate limit reached',
            reason: 'The configured provider is temporarily refusing requests.',
            next_action: 'Wait briefly and try again.',
            retryable: true,
            request_id: 'a'.repeat(32),
            details: null,
            message: 'Provider rate limited.',
          },
        }),
        {
          status: 429,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': 'a'.repeat(32),
          },
        },
      )),
    );

    let caught: unknown;
    try {
      await apiClient.get('/api/test/structured-error', { cacheTtlMs: 0 });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const apiError = caught as ApiError;
    expect(apiError.status).toBe(429);
    expect(apiError.code).toBe('AI_PROVIDER_RATE_LIMITED');
    expect(apiError.title).toBe('AI provider rate limit reached');
    expect(apiError.reason).toContain('temporarily refusing');
    expect(apiError.nextAction).toBe('Wait briefly and try again.');
    expect(apiError.retryable).toBe(true);
    expect(apiError.requestId).toBe('a'.repeat(32));
  });

  it('supports legacy FastAPI detail responses during transition', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(
        JSON.stringify({ detail: 'Legacy endpoint rejected the request.' }),
        {
          status: 422,
          headers: { 'Content-Type': 'application/json' },
        },
      )),
    );

    await expect(
      apiClient.get('/api/test/legacy-error', { cacheTtlMs: 0 }),
    ).rejects.toMatchObject({
      status: 422,
      reason: 'Legacy endpoint rejected the request.',
      retryable: false,
    });
  });
});


describe('structured error presentation', () => {
  const error = new ApiError('Provider rate limited.', {
    status: 429,
    code: 'AI_PROVIDER_RATE_LIMITED',
    title: 'AI provider rate limit reached',
    reason: 'The selected model is temporarily rate-limited.',
    nextAction: 'Wait briefly and try again.',
    retryable: true,
    requestId: 'b'.repeat(32),
  });

  it('shows title, reason, next action, support ID, and retries once', async () => {
    const retry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorNotice error={error} onRetry={retry} />);

    expect(screen.getByText('AI provider rate limit reached')).toBeTruthy();
    expect(screen.getByText('The selected model is temporarily rate-limited.')).toBeTruthy();
    expect(screen.getByText('Wait briefly and try again.')).toBeTruthy();
    expect(screen.getByText('Support details')).toBeTruthy();
    expect(screen.getByText('b'.repeat(32))).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('renders card, inline, and empty-state variants without raw details', () => {
    const nonRetryable = new ApiError('Invalid input.', {
      status: 422,
      code: 'VALIDATION_ERROR',
      title: 'Check the submitted information',
      reason: 'Available minutes must be between 10 and 240.',
      nextAction: 'Correct the value and submit again.',
      retryable: false,
      details: { raw_output: 'private model output' },
    });

    render(
      <>
        <RetryableErrorCard error={nonRetryable} />
        <InlineFieldError error={nonRetryable} />
        <EmptyStateAction
          title="No coaching items available"
          reason="Complete a quiz or study session first."
        />
      </>,
    );

    expect(screen.getAllByText('Check the submitted information').length).toBeGreaterThan(0);
    expect(screen.getByText('No coaching items available')).toBeTruthy();
    expect(screen.queryByText('private model output')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});


describe('Coaching structured error integration', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows the provider action and retries the same coaching request', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe('POST');
      return new Response(
        JSON.stringify({
          error: {
            code: 'AI_PROVIDER_RATE_LIMITED',
            title: 'AI provider rate limit reached',
            reason: 'The selected model is temporarily rate-limited.',
            next_action: 'Wait briefly and try again.',
            retryable: true,
            request_id: 'c'.repeat(32),
            details: null,
            message: 'Provider rate limited.',
          },
        }),
        {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/study-actions?view=coaching']}>
        <Routes>
          <Route path="/study-actions" element={<StudyActionsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: 'Generate coaching plan' }));
    expect(await screen.findByText('AI provider rate limit reached')).toBeTruthy();
    expect(screen.getByText('Wait briefly and try again.')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const call of fetchMock.mock.calls) {
      expect(String(call[0])).toContain('/api/study/actions/coaching-plan');
    }
  });
});
