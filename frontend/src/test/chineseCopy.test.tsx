import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";
import { json, makeOverview } from "./fixtures";

/** Brand, format and file-type names that stay as they are in a Chinese interface. */
const ALLOWED = new Set([
  "Book", "Agent", "API", "OpenAPI", "PDF", "EPUB", "Markdown", "HTML", "CSV", "key", "token", "pdf", "epub",
  "BOOK", "md", "Deep", "Work", "Cal", "Newport", "Momentum", "harness", "RSI",
]);

const DOCUMENT_ID = "doc-copy";
const RUN_ID = "run-copy";
const CHAPTER_ID = "ch-1";
const ISSUE_ID = "issue-1";

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
  quality: { open_issues: 1, blocking: 1 },
});

const issue = {
  id: ISSUE_ID,
  document_id: DOCUMENT_ID,
  chapter_id: CHAPTER_ID,
  issue_type: "LOW_CONFIDENCE",
  root_cause_layer: "translation",
  severity: "medium",
  blocking: false,
  detector: "rule",
  confidence: 0.4,
  status: "open",
  version: 1,
  reopen_count: 0,
  evidence_json: { reason: "translation_marked_review_required" },
  created_at: "2026-09-20T08:00:00Z",
};

const issueDetail = {
  issue,
  events: [{ id: "e1", issue_id: ISSUE_ID, version: 1, kind: "opened", to_status: "open", actor_kind: "rule", actor_id: "rule", created_at: "2026-09-20T08:00:00Z" }],
  actions: [{ id: "a1", issue_id: ISSUE_ID, action_type: "RERUN_PACKET", scope_type: "packet", status: "planned", created_by: "system" }],
  source_text: "Momentum persists.",
  target_text: "动量会持续。",
  chapter_title: "第一章",
};

const events = {
  run_id: RUN_ID, event_count: 2, record_count: 2, offset: 0, limit: 8, has_more: false,
  entries: [
    { event_id: "e2", run_id: RUN_ID, event_type: "work_item.retryable_failed", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:00:30Z" },
    { event_id: "e1", run_id: RUN_ID, event_type: "run.started", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:00:00Z" },
  ],
};

const worklist = {
  document_id: DOCUMENT_ID, record_count: 1, offset: 0, limit: 50, has_more: false, total_count: 1,
  summary: {}, owner_workloads: [], highlights: {},
  entries: [{
    chapter_id: CHAPTER_ID, ordinal: 1, title_src: "Momentum", title_tgt: "动量", status: "packet_built",
    queue_priority: "high", open_issue_count: 1, packet_count: 4, bilingual_export_ready: false,
    memory_proposals: { pending_proposal_count: 0, pending_proposals: [] }, assignment: null, assignment_history: [],
  }],
};

const worklistDetail = {
  document_id: DOCUMENT_ID, chapter_id: CHAPTER_ID, ordinal: 1, title_src: "Momentum", title_tgt: "动量",
  current_open_issue_count: 1, memory_proposals: { pending_proposal_count: 0, pending_proposals: [] },
  recent_issues: [issue], recent_actions: [issueDetail.actions[0]], timeline: [], assignment: null, assignment_history: [],
};

function installFetchMock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const has = (part: string) => url.includes(part);
      if (has("/v1/health")) return json({ status: "ok" });
      if (has("/v1/inbox")) {
        return json({
          count: 1,
          items: [{ kind: "paused", id: RUN_ID, title: "翻译暂停了，需要你处理", detail: "还没有可用的模型服务商。", action: { kind: "link", target: "/settings", label: "去设置" }, document_id: DOCUMENT_ID, document_title: "Deep Work" }],
          updates: [],
        });
      }
      if (has("/v1/overviews")) return json({ overviews: [overview] });
      if (has("/overview")) return json(overview);
      if (has("/v1/documents/history")) {
        return json({
          total_count: 1, offset: 0, limit: 12, has_more: false,
          entries: [{ document_id: DOCUMENT_ID, title: "Deep Work", title_src: "Deep Work", title_tgt: null, source_type: "epub", status: "exported", author: "Cal Newport", source_path: "/books/deep-work.epub", created_at: "2026-09-20T07:00:00Z", updated_at: "2026-09-20T08:00:00Z", chapter_count: 2, sentence_count: 40, translated_sentence_count: 10, merged_export_ready: true, latest_run_id: RUN_ID, latest_run_status: "paused", latest_run_current_stage: "translate", latest_run_updated_at: "2026-09-20T08:00:00Z" }],
        });
      }
      if (has("/v1/providers/active")) return json({ id: "p1", name: "echo", provider_kind: "echo", is_active: true, shared: true });
      if (has("/v1/providers")) return json([]);
      if (has(`/v1/issues/${ISSUE_ID}`)) return json(issueDetail);
      if (has("/issues?")) return json({ document_id: DOCUMENT_ID, total_count: 1, record_count: 1, offset: 0, limit: 50, has_more: false, entries: [issue] });
      if (has("/chapters/worklist")) return json(worklist);
      if (has(`/chapters/${CHAPTER_ID}/worklist`)) return json(worklistDetail);
      if (has("/chapters")) return json([{ chapter_id: CHAPTER_ID, ordinal: 1, title_src: "Momentum", title_tgt: "动量", status: "packet_built" }]);
      if (has("/terms")) return json([{ source_term: "harness", target_term: "驾驭框架", term_type: "concept", lock_level: "locked", locked_by: "auto.fast_reading_mode" }]);
      if (has("/book-guide")) return json({ document_id: DOCUMENT_ID, markdown: "# BOOK.md\n\n## 术语表\n- harness => 驾驭框架\n", prompt_guidance: "", locked_term_count: 1, preferred_term_count: 0, decision_count: 1 });
      if (has("/agent-turns")) return json([{ id: "t1", document_id: DOCUMENT_ID, agent_kind: "terminology", status: "succeeded", usage_json: { steps: 3, tool_calls: 5 }, started_at: "2026-09-20T08:00:00Z" }]);
      if (has("/events")) return json(events);
      if (has("/orgs/current/budget")) return json({ org_id: "o", org_name: "默认组织", spent_usd: 0.5, monthly_budget_usd: null, exhausted: false, period_start: "2026-09-01T00:00:00Z" });
      if (has("/orgs/current/credit")) return json({ org_id: "o", org_name: "默认组织", prepaid: false, credited_usd: 0, charged_usd: 0, balance_usd: 0, price_multiplier: 1, exhausted: false });
      if (has("/cost-estimate")) return json({ document_id: DOCUMENT_ID, packet_count: 6, source_tokens: 100, token_in: 1000, token_out: 500, token_in_range: [900, 1100], token_out_range: [400, 600], cost_usd: null, cost_usd_range: null, notes: [], thinking_may_inflate_output: false });
      if (has("/v1/meta")) return json({ app_name: "book-agent", version: "t", environment: "test", api_prefix: "/v1", docs_path: "", capabilities: { pdf_renderer: false } });
      return json({ detail: "not found" }, 404);
    }),
  );
}

// An example URL or a file path is not English copy.
function withoutUrls(text: string | null | undefined): string {
  return (text ?? "").replace(/https?:\/\/\S+/g, "").replace(/\/\S+/g, "");
}

function englishWords(): string[] {
  const words = new Set<string>();
  const add = (text: string | null | undefined) => {
    for (const word of withoutUrls(text).match(/[A-Za-z]{2,}/g) ?? []) words.add(word);
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    // Technical details behind a <details> are for developers, not the reader.
    if ((node.parentElement?.closest("details, pre, code") ?? null) === null) add(node.textContent);
  }
  for (const element of document.body.querySelectorAll("[aria-label], [title], [placeholder]")) {
    for (const name of ["aria-label", "title", "placeholder"]) add(element.getAttribute(name));
  }
  return [...words].filter((word) => !ALLOWED.has(word));
}

/** Raw enum values (LOW_CONFIDENCE, RERUN_PACKET…) must never reach the page. */
function shoutedEnums(): string[] {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const found = new Set<string>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement?.closest("details, pre, code")) continue;
    for (const word of node.textContent?.match(/\b[A-Z][A-Z_]{3,}\b/g) ?? []) found.add(word);
  }
  return [...found].filter((word) => !ALLOWED.has(word));
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

const PAGES: [string, string, string | RegExp][] = [
  ["首页", "/", "把英文读成中文"],
  ["书库", "/library", "Deep Work"],
  ["收件箱", "/inbox", /翻译暂停了，需要你处理/],
  ["设置", "/settings", "阅读偏好"],
  ["书页 · 进度", `/books/${DOCUMENT_ID}/progress`, "确定术语"],
  ["书页 · 术语与决策", `/books/${DOCUMENT_ID}/terms`, /术语表/],
  ["书页 · 问题", `/books/${DOCUMENT_ID}/issues`, "把握不大"],
  ["书页 · 运行记录", `/books/${DOCUMENT_ID}/log`, /代理回合/],
  ["书页 · 逐章审校", `/books/${DOCUMENT_ID}/chapters`, /章节/],
];

describe("Every page speaks Chinese", () => {
  for (const [name, path, ready] of PAGES) {
    it(name, async () => {
      await renderAt(path, ready);
      expect({ page: name, english: englishWords() }).toEqual({ page: name, english: [] });
      expect({ page: name, enums: shoutedEnums() }).toEqual({ page: name, enums: [] });
    });
  }

  it("says what an issue means, not its enum value", async () => {
    await renderAt(`/books/${DOCUMENT_ID}/issues`, "把握不大");
    expect(screen.getAllByText(/系统对这句译文没有把握|翻译时模型自己标记了/)[0]).toBeInTheDocument();
    expect(screen.getByText("一般")).toBeInTheDocument(); // severity medium
    expect(screen.getByText("自动检查")).toBeInTheDocument(); // detector rule
  });
});
