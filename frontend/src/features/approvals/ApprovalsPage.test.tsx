import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111";
const APPROVAL_ID = "22222222-2222-4222-8222-222222222222";

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

function installFetchMock() {
  const posts: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push(url);
    }
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.includes("/v1/documents/history")) {
      return json({ total_count: 0, record_count: 0, offset: 0, limit: 12, has_more: false, entries: [] });
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/approvals`)) {
      return json(
        posts.length
          ? []
          : [
              {
                id: APPROVAL_ID,
                document_id: DOCUMENT_ID,
                turn_id: "t1",
                kind: "lock_term",
                status: "pending",
                payload_json: { tool: "lock_term", arguments: { source_term: "rate of change", target_term: "变化率", term_type: "concept" }, reason: "lock_term changes the book irreversibly" },
                policy_id: "policy.write_irreversible",
                created_at: "2026-09-17T08:00:00Z",
              },
            ],
      );
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/book-guide`)) {
      return json({ document_id: DOCUMENT_ID, markdown: "# BOOK.md\n\n## 术语表\n- RSI => RSI (locked)\n", prompt_guidance: "", locked_term_count: 1, preferred_term_count: 0, decision_count: 0 });
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/agent-turns`)) {
      return json([{ id: "t1", document_id: DOCUMENT_ID, agent_kind: "terminology", status: "awaiting_approval", usage_json: { steps: 1, tool_calls: 4, token_in: 100, token_out: 40 }, started_at: "2026-09-17T08:00:00Z" }]);
    }
    if (url.includes(`/v1/approvals/${APPROVAL_ID}/approve`)) {
      return json({ id: APPROVAL_ID, document_id: DOCUMENT_ID, kind: "lock_term", status: "approved", decided_by: "human:alice" });
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/exports`)) {
      return json({ document_id: DOCUMENT_ID, export_count: 0, successful_export_count: 0, filtered_export_count: 0, record_count: 0, offset: 0, limit: 5, has_more: false, latest_export_at: null, export_counts_by_type: {}, latest_export_ids_by_type: {}, translation_usage_summary: null, issue_hotspots: [], issue_chapter_highlights: {}, records: [] });
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/chapters/worklist`)) {
      return json({ document_id: DOCUMENT_ID, entries: [], summary: {}, owner_workloads: [], highlights: {} , record_count: 0, offset: 0, limit: 50, has_more: false, total_count: 0 });
    }
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/chapters`)) return json([]);
    if (url.includes(`/v1/documents/${DOCUMENT_ID}`)) {
      return json({
        document_id: DOCUMENT_ID,
        title: "Momentum",
        title_src: "Momentum",
        title_tgt: null,
        author: null,
        source_type: "epub",
        status: "active",
        chapter_count: 1,
        sentence_count: 3,
        packet_count: 1,
        translated_sentence_count: 0,
        chapters: [],
        merged_export_ready: false,
        bilingual_export_ready: false,
        latest_run_id: null,
        latest_run_status: null,
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, posts };
}

describe("ApprovalsPage", () => {
  it("lists pending approvals and posts a decision", async () => {
    const { posts } = installFetchMock();
    window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
    render(
      <MemoryRouter initialEntries={["/approvals"]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(/锁定术语：rate of change => 变化率/);
    expect(await screen.findByText(/RSI => RSI \(locked\)/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("审批人"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => expect(posts.some((url) => url.includes(`/v1/approvals/${APPROVAL_ID}/approve`))).toBe(true));
    await screen.findByText(/已批准/);
  });
});
