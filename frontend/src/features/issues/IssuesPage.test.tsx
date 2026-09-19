import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111";
const ISSUE_ID = "33333333-3333-4333-8333-333333333333";
const ISSUE = {
  id: ISSUE_ID,
  document_id: DOCUMENT_ID,
  issue_type: "MISTRANSLATION_SEMANTIC",
  root_cause_layer: "translation",
  severity: "high",
  blocking: true,
  detector: "model",
  confidence: 0.9,
  status: "open",
  version: 1,
  reopen_count: 0,
  evidence_json: { reason: "model_review", explanation: "意思译反了。" },
  created_at: "2026-09-17T08:00:00Z",
};

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
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/issues`)) {
      const decided = posts.some((item) => item.includes("/wontfix"));
      return json({
        document_id: DOCUMENT_ID,
        total_count: 1,
        offset: 0,
        limit: 50,
        has_more: false,
        entries: [decided ? { ...ISSUE, status: "wontfix", version: 2, decided_by: "human:alice" } : ISSUE],
      });
    }
    if (url.includes("/v1/actions/a1/execute")) {
      return json({ action_id: "a1", status: "completed", invalidation_count: 1, rerun_scope_type: "packet", rerun_packet_ids: ["p1"], issue_resolved: true });
    }
    if (url.includes(`/v1/issues/${ISSUE_ID}/wontfix`)) {
      return json({ ...ISSUE, status: "wontfix", version: 2, decided_by: "human:alice" });
    }
    if (url.includes(`/v1/issues/${ISSUE_ID}`)) {
      const decided = posts.some((item) => item.includes("/wontfix"));
      return json({
        issue: decided ? { ...ISSUE, status: "wontfix", version: 2, decided_by: "human:alice" } : ISSUE,
        events: [
          { id: "e1", issue_id: ISSUE_ID, version: 1, kind: "opened", to_status: "open", actor_kind: "agent", actor_id: "agent:reviewer", created_at: "2026-09-17T08:00:00Z" },
          ...(decided
            ? [{ id: "e2", issue_id: ISSUE_ID, version: 2, kind: "wontfix", from_status: "open", to_status: "wontfix", actor_kind: "human", actor_id: "human:alice", note: "keep", created_at: "2026-09-17T09:00:00Z" }]
            : []),
        ],
        actions: [{ id: "a1", issue_id: ISSUE_ID, action_type: "RERUN_PACKET", scope_type: "packet", status: "planned", created_by: "system" }],
        source_text: "Momentum persists.",
        target_text: "动量消失。",
        chapter_title: "Chapter One",
      });
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

describe("IssuesPage", () => {
  it("filters, shows the source/target pair and records a human decision", async () => {
    const { fetchMock, posts } = installFetchMock();
    window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
    render(
      <MemoryRouter initialEntries={["/issues"]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByRole("button", { name: /MISTRANSLATION_SEMANTIC/ });
    fireEvent.change(screen.getByLabelText("来源"), { target: { value: "model" } });
    fireEvent.click(screen.getByLabelText("只看阻断"));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return url.includes("/issues?") && url.includes("detector=model") && url.includes("blocking=true");
        }),
      ).toBe(true),
    );
    fireEvent.click(await screen.findByRole("button", { name: /MISTRANSLATION_SEMANTIC/ }));
    expect(await screen.findByText("Momentum persists.")).toBeInTheDocument();
    expect(screen.getByText("动量消失。")).toBeInTheDocument();
    expect(screen.getByText(/RERUN_PACKET/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "执行并复核" }));
    await waitFor(() =>
      expect(posts.some((url) => url.includes("/v1/actions/a1/execute") && url.includes("run_followup=true"))).toBe(true),
    );
    await screen.findByText(/问题已解决/);
    expect(screen.queryByRole("button", { name: "重新打开" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("处理人"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "keep" } });
    fireEvent.click(screen.getByRole("button", { name: "不修复" }));
    await waitFor(() => expect(posts.some((url) => url.includes(`/v1/issues/${ISSUE_ID}/wontfix`))).toBe(true));
    await screen.findByText(/标记不修复/);
    expect(await screen.findByRole("button", { name: "重新打开" })).toBeInTheDocument();
  });
});
