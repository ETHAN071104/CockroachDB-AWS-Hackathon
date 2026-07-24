import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiClient } from '../api/client';
import { ChatPage } from '../pages/ChatPage';
import { StudyActionsPage } from '../pages/StudyActionsPage';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return String(input);
}

function renderChat() {
  render(
    <MemoryRouter initialEntries={['/chat']}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/study-actions" element={<StudyActionsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function scopeResponse(url: string): Response | null {
  if (url.includes('/api/notebooks')) return jsonResponse({ items: [], total: 0 });
  if (url.includes('/api/documents')) return jsonResponse({ items: [], total: 0 });
  if (url.includes('/api/topics')) return jsonResponse({ items: [], total: 0 });
  if (url.includes('/api/study/sessions')) return jsonResponse({ items: [], total: 0 });
  return null;
}

describe('AI feature responsibility routing', () => {
  beforeEach(() => apiClient.invalidate());

  afterEach(() => vi.unstubAllGlobals());

  it('renders Chat responsibility and routes a weakness request to Coaching on click', async () => {
    const prompt = 'What are my weaknesses? <script>alert("x")</script>';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(input);
        const scoped = scopeResponse(url);
        if (init?.method === 'GET' && scoped) return scoped;
        if (init?.method === 'POST' && url.endsWith('/api/chat')) {
          return jsonResponse({
            session_id: "1",
            interaction_id: "2",
            answer: 'Coaching uses performance history.',
            sources: [],
            memory_proposal: null,
            type: 'feature_redirect',
            intent: 'weakness_analysis',
            evidence_status: 'personal_performance_request',
            redirect: {
              target: 'coaching',
              title: 'This question needs your learning history',
              message: 'Coaching uses your quiz mistakes, Learning Signals, and Learner Memories to identify what you should review.',
              action_label: 'Open Coaching',
              original_prompt: prompt,
              suggested_prompt: 'What should I review first based on my recent mistakes?',
            },
          });
        }
        throw new Error(`Unexpected request: ${init?.method} ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderChat();

    expect(
      await screen.findByText('Ask questions about your uploaded study materials. For weakness analysis, use Coaching.'),
    ).toBeTruthy();
    expect(screen.getByText('Active source: All indexed documents')).toBeTruthy();
    await user.type(screen.getByLabelText('Your question'), prompt);
    await user.click(screen.getByRole('button', { name: 'Send question' }));

    expect(await screen.findByText('This question needs your learning history')).toBeTruthy();
    const action = screen.getByRole('link', { name: 'Open Coaching' });
    expect(action.getAttribute('href')).toContain('view=coaching');
    expect(action.getAttribute('href')).not.toContain('<script>');
    await user.click(action);

    expect(await screen.findByRole('heading', { name: 'Grounded coaching' })).toBeTruthy();
    expect(
      screen.getByText('Uses your quiz mistakes, Learning Signals, and Learner Memories to decide what you should review.'),
    ).toBeTruthy();
    expect(screen.getByText(prompt)).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
  });

  it('routes an explicit planning request to Study Plan without automatic navigation', async () => {
    const prompt = 'Build a schedule for me.';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(input);
        const scoped = scopeResponse(url);
        if (init?.method === 'GET' && scoped) return scoped;
        if (init?.method === 'POST' && url.endsWith('/api/chat')) {
          return jsonResponse({
            session_id: "1",
            interaction_id: "3",
            answer: 'Study Plan can organize this request.',
            sources: [],
            memory_proposal: null,
            type: 'feature_redirect',
            intent: 'study_plan_request',
            evidence_status: 'planning_request',
            redirect: {
              target: 'study-plan',
              title: 'This question is better suited to Study Plan',
              message: 'Study Plan can organize your weaknesses and available materials into a learning order and time budget.',
              action_label: 'Create Study Plan',
              original_prompt: prompt,
              suggested_prompt: null,
            },
          });
        }
        throw new Error(`Unexpected request: ${init?.method} ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderChat();

    await user.type(await screen.findByLabelText('Your question'), prompt);
    await user.click(screen.getByRole('button', { name: 'Send question' }));
    expect(await screen.findByRole('heading', { name: 'Study chat' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Adaptive study plan' })).toBeNull();

    await user.click(await screen.findByRole('link', { name: 'Create Study Plan' }));
    expect(await screen.findByRole('heading', { name: 'Adaptive study plan' })).toBeTruthy();
    expect(
      screen.getByText('Organizes what to learn next, in what order, and how much time to spend.'),
    ).toBeTruthy();
    expect(screen.getByText(prompt)).toBeTruthy();
  });

  it('keeps a grounded document question in Chat with validated citation UI', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = requestUrl(input);
        const scoped = scopeResponse(url);
        if (init?.method === 'GET' && scoped) return scoped;
        if (init?.method === 'POST' && url.endsWith('/api/chat')) {
          return jsonResponse({
            session_id: "1",
            interaction_id: "4",
            answer: 'Mitochondria convert stored energy for cells [1].',
            sources: [
              {
                index: 1,
                document_id: "9",
                notebook_id: null,
                filename: 'lesson.pdf',
                mime_type: 'application/pdf',
                page_number: 2,
                slide_number: null,
                chunk_index: 0,
                distance: 0.1,
                excerpt: 'Mitochondria convert stored energy for cells.',
              },
            ],
            memory_proposal: null,
            type: 'answer',
            intent: 'document_question',
            evidence_status: 'grounded',
            redirect: null,
          });
        }
        throw new Error(`Unexpected request: ${init?.method} ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderChat();

    await user.type(await screen.findByLabelText('Your question'), 'What do mitochondria do?');
    await user.click(screen.getByRole('button', { name: 'Send question' }));

    expect(await screen.findByText('Mitochondria convert stored energy for cells [1].')).toBeTruthy();
    expect(screen.getByText('1 cited source')).toBeTruthy();
    expect(screen.queryByText('Better study tool')).toBeNull();
  });
});
