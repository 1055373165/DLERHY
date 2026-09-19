import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";
import { makeOverview } from "./fixtures";

// Names, formats and technical nouns that stay as they are in Chinese UI.
const ALLOWED = new Set(["Book", "Agent", "API", "OpenAPI", "PDF", "EPUB", "Markdown", "HTML", "CSV", "key", "Momentum", "Deep", "Work", "Cal", "Newport", "BOOK", "md", "token", "pdf", "epub"]);

const DOCUMENT_ID = "doc-copy";
const RUN_ID = "run-copy-1234";
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const overview = makeOverview(DOCUMENT_ID, {
  title: "Deep Work",
  source_type: "epub",
  source_url: null,
  headline: "有 1 件事等你处理，处理后翻译会继续。",
  primary_action: { kind: "handle", label: "去处理" },
  translation: {
    state: "paused",
    run_id: RUN_ID,
    stop_reason: "provider.not_configured",
    stop_reason_text: "还没有可用的模型服务商。",
    steps: [
      { key: "terminology", label: "确定术语", status: "succeeded" },
      { key: "translate", label: "翻译", status: "pending" },
    ],
    progress: { done: 2, total: 6 },
  },
});

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
      if (url.includes("/v1/inbox")) return json({ count: 1, items: [{ kind: "paused", id: RUN_ID, title: "翻译暂停了，需要你处理", detail: "还没有可用的模型服务商。", action: { kind: "link", target: "/settings", label: "去设置" }, document_id: DOCUMENT_ID, document_title: "Deep Work" }] });
      if (url.includes("/v1/providers/active")) return json({ id: "p1", name: "echo", provider_kind: "echo", is_active: true, shared: true });
      if (url.includes(`/v1/documents/${DOCUMENT_ID}/overview`)) return json(overview);
      return new Response(JSON.stringify({ detail: "not found" }), { status: 404, headers: { "Content-Type": "application/json" } });
    }),
  );
}

// An example URL is not English copy.
function withoutUrls(text: string | null | undefined): string {
  return (text ?? "").replace(/https?:\/\/\S+/g, "");
}

function englishWords(): string[] {
  // Text node by text node: textContent would glue neighbouring labels into fake words.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const words = new Set<string>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const word of withoutUrls(node.textContent).match(/[A-Za-z]{2,}/g) ?? []) words.add(word);
  }
  // Screen readers read these aloud, and tooltips show them.
  for (const element of document.body.querySelectorAll("[aria-label], [title], [placeholder]")) {
    for (const name of ["aria-label", "title", "placeholder"]) {
      for (const word of withoutUrls(element.getAttribute(name)).match(/[A-Za-z]{2,}/g) ?? []) words.add(word);
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
  it("home", async () => {
    await renderAt("/", "把英文读成中文");
    expect(englishWords()).toEqual([]);
  });

  it("library", async () => {
    await renderAt("/library", "Deep Work");
    expect(englishWords()).toEqual([]);
  });

  it("book progress, with events told as sentences", async () => {
    await renderAt(`/books/${DOCUMENT_ID}/progress`, "翻译已暂停");
    expect(screen.getByText("有一步出错，正在自动重试")).toBeInTheDocument();
    expect(screen.queryByText(/work_item/)).not.toBeInTheDocument();
    expect(englishWords()).toEqual([]);
  });

  it("inbox", async () => {
    await renderAt("/inbox", /翻译暂停了，需要你处理/);
    expect(englishWords()).toEqual([]);
  });
});
