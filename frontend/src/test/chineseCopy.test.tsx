import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";

// Names, formats and technical nouns that stay as they are in Chinese UI.
const ALLOWED = new Set(["Book", "Agent", "API", "OpenAPI", "PDF", "EPUB", "Markdown", "HTML", "CSV", "key", "Momentum", "Deep", "Work", "Cal", "Newport", "BOOK", "md", "token"]);

const DOCUMENT_ID = "doc-copy";
const RUN_ID = "run-copy-1234";
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const documentPayload = {
  document_id: DOCUMENT_ID, source_type: "epub", status: "active", title: "Deep Work", title_src: "Deep Work",
  title_tgt: null, author: "Cal Newport", chapter_count: 2, sentence_count: 40, packet_count: 6,
  merged_export_ready: false, latest_run_id: RUN_ID, latest_run_status: "paused",
  chapters: [{ chapter_id: "ch-1", ordinal: 1, title_src: "Momentum", title_tgt: null, open_issue_count: 0, bilingual_export_ready: false }],
};

const run = {
  run_id: RUN_ID, document_id: DOCUMENT_ID, run_type: "translate_full", status: "paused", priority: 100,
  stop_reason: "provider.reasoning_exhausted_output",
  status_detail_json: { pipeline: { current_stage: "terminology", _cached_pipeline_stages: { terminology: { status: "running" } } } },
  work_items: { total_count: 1, status_counts: {}, stage_counts: {} },
  worker_leases: { total_count: 0, status_counts: {} },
  events: { event_count: 3 },
  created_at: "2026-09-20T08:00:00Z", updated_at: "2026-09-20T08:01:00Z",
};

const events = {
  run_id: RUN_ID, event_count: 3, record_count: 3, offset: 0, limit: 8, has_more: false,
  entries: [
    { event_id: "e3", run_id: RUN_ID, event_type: "run.paused", actor_type: "system", payload_json: { note: "provider.reasoning_exhausted_output" }, created_at: "2026-09-20T08:01:00Z" },
    { event_id: "e2", run_id: RUN_ID, event_type: "work_item.retryable_failed", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:00:30Z" },
    { event_id: "e1", run_id: RUN_ID, event_type: "run.started", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:00:00Z" },
  ],
};

const history = {
  total_count: 1, offset: 0, limit: 12, has_more: false,
  entries: [{
    document_id: DOCUMENT_ID, source_type: "epub", status: "active", title: "Deep Work", title_src: "Deep Work", title_tgt: null,
    author: "Cal Newport", source_path: "/books/deep-work.epub", created_at: "2026-09-20T07:00:00Z", updated_at: "2026-09-20T08:00:00Z",
    chapter_count: 2, sentence_count: 40, translated_sentence_count: 0, merged_export_ready: false,
    latest_run_id: RUN_ID, latest_run_status: "paused", latest_run_current_stage: "terminology", latest_run_updated_at: "2026-09-20T08:01:00Z",
  }],
};

function installFetchMock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/documents/history")) return json(history);
      if (url.includes(`/v1/runs/${RUN_ID}/events`)) return json(events);
      if (url.includes(`/v1/runs/${RUN_ID}`)) return json(run);
      if (url.includes("/approvals?status=pending")) return json([]);
      if (url.endsWith(`/v1/documents/${DOCUMENT_ID}`)) return json(documentPayload);
      return new Response(JSON.stringify({ detail: "not found" }), { status: 404, headers: { "Content-Type": "application/json" } });
    }),
  );
}

function englishWords(): string[] {
  // Text node by text node: textContent would glue neighbouring labels into fake words.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const words = new Set<string>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const word of node.textContent?.match(/[A-Za-z]{2,}/g) ?? []) words.add(word);
  }
  // Screen readers read these aloud, and tooltips show them.
  for (const element of document.body.querySelectorAll("[aria-label], [title], [placeholder]")) {
    for (const name of ["aria-label", "title", "placeholder"]) {
      for (const word of element.getAttribute(name)?.match(/[A-Za-z]{2,}/g) ?? []) words.add(word);
    }
  }
  return [...words].filter((word) => !ALLOWED.has(word));
}

async function renderAt(path: string, ready: string | RegExp) {
  window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
  installFetchMock();
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findAllByText(ready);
}

describe("Reader-facing pages speak Chinese", () => {
  it("library", async () => {
    await renderAt("/library", "Deep Work");
    expect(englishWords()).toEqual([]);
  });

  it("run page, with events told as sentences", async () => {
    await renderAt("/runs", "转换已暂停");
    expect(screen.getByText("有一步出错，正在自动重试")).toBeInTheDocument();
    expect(screen.queryByText(/work_item/)).not.toBeInTheDocument();
    expect(englishWords()).toEqual([]);
  });
});
