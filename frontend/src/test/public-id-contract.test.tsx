import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import { api } from "../api/endpoints";
import { isPublicId } from "../api/publicIds";
import type { PublicId } from "../api/types";
import { DocumentDetailPage } from "../pages/DocumentDetailPage";

const LARGE_DOCUMENT_ID = "3557348663300104065";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("public integer ID contract", () => {
  beforeEach(() => apiClient.invalidate());

  afterEach(() => vi.unstubAllGlobals());

  it("accepts only canonical positive decimal strings", () => {
    expect(isPublicId(LARGE_DOCUMENT_ID)).toBe(true);
    expect(isPublicId("1")).toBe(true);
    expect(isPublicId("0")).toBe(false);
    expect(isPublicId("01")).toBe(false);
    expect(isPublicId("-1")).toBe(false);
    expect(isPublicId("1.5")).toBe(false);
    expect(isPublicId(" 1")).toBe(false);
  });

  it("keeps a 19-digit route ID exact in every document request", async () => {
    const requestedUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        requestedUrls.push(url);
        if (url === `/api/documents/${LARGE_DOCUMENT_ID}`) {
          return jsonResponse({
            id: LARGE_DOCUMENT_ID,
            filename: "large-id.pdf",
            mime_type: "application/pdf",
            chunk_count: 17,
            created_at: "2026-07-24T00:00:00+00:00",
            updated_at: "2026-07-24T00:00:00+00:00",
            notebook_id: null,
          });
        }
        if (url === "/api/notebooks") {
          return jsonResponse({
            items: [],
            total: 0,
            unsorted: {
              id: null,
              name: "Unsorted Documents",
              description: "Documents not assigned to a notebook.",
              document_count: 1,
              created_at: null,
              updated_at: null,
              is_virtual: true,
            },
          });
        }
        if (url === `/api/documents/${LARGE_DOCUMENT_ID}/summary`) {
          return jsonResponse(
            {
              error: {
                code: "SUMMARY_NOT_GENERATED",
                message: "No summary has been generated.",
              },
            },
            { status: 404 },
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={[`/documents/${LARGE_DOCUMENT_ID}`]}>
        <Routes>
          <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "large-id.pdf" }),
    ).toBeTruthy();
    expect(requestedUrls).toContain(`/api/documents/${LARGE_DOCUMENT_ID}`);
    expect(requestedUrls).toContain(
      `/api/documents/${LARGE_DOCUMENT_ID}/summary`,
    );
    expect(requestedUrls.join("\n")).not.toContain("3557348663300104000");
  });

  it("preserves large IDs across library, session, report, and memory routes", async () => {
    const publicId: PublicId = LARGE_DOCUMENT_ID;
    const requestedUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        requestedUrls.push(String(input));
        return jsonResponse({});
      }),
    );

    await api.getNotebook(publicId);
    await api.getStudySession(publicId);
    await api.getSessionReport(publicId);
    await api.getMemory(publicId);

    expect(requestedUrls).toEqual([
      `/api/notebooks/${LARGE_DOCUMENT_ID}`,
      `/api/study/sessions/${LARGE_DOCUMENT_ID}`,
      `/api/reports/study/sessions/${LARGE_DOCUMENT_ID}`,
      `/api/memories/${LARGE_DOCUMENT_ID}`,
    ]);
    expect(requestedUrls.join("\n")).not.toContain("3557348663300104000");
  });

  it("sends Chat, Quiz, Study Plan, and Coaching scope IDs as strings", async () => {
    const requests: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        requests.push({
          url: String(input),
          body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
        });
        return jsonResponse({});
      }),
    );
    const scope = { document_ids: [LARGE_DOCUMENT_ID] };

    await api.sendChat({ question: "Use one exact source.", ...scope });
    await api.generateQuiz({
      topic: "Precision",
      question_count: 1,
      ...scope,
    });
    await api.buildStudyPlan({
      total_minutes: 30,
      max_items: 2,
      scope,
    });
    await api.buildCoachingPlan({
      total_minutes: 30,
      max_items: 2,
      scope,
    });

    expect(requests.map((request) => request.url)).toEqual([
      "/api/chat",
      "/api/study/actions/quizzes/generate",
      "/api/study/actions/plan",
      "/api/study/actions/coaching-plan",
    ]);
    for (const request of requests) {
      const body = request.body as {
        document_ids?: unknown;
        scope?: { document_ids?: unknown };
      };
      expect(
        body.document_ids ?? body.scope?.document_ids,
      ).toEqual([LARGE_DOCUMENT_ID]);
    }
  });
});
