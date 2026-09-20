import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import type { BookOverview } from "../../lib/overview";
import { json, makeOverview, notFound } from "../../test/fixtures";

const ID = "book-1";
const RUN = { run_id: "run-1", document_id: ID, run_type: "translate_full", status: "running", priority: 100, status_detail_json: {}, created_at: "", updated_at: "" };

function installFetchMock(overview: () => BookOverview) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.includes(`/v1/documents/${ID}/overview`)) return json(overview());
    if (url.endsWith("/v1/runs") && method === "POST") return json(RUN, 201);
    if (/\/v1\/runs\/[^/]+\/(resume|pause|retry)$/.test(url)) return json(RUN);
    if (/\/v1\/approvals\/[^/]+\/approve$/.test(url)) return json({ id: "ap-1", status: "approved" });
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
    if (url.includes("/v1/runs/run-1/events")) return json({ run_id: "run-1", event_count: 0, record_count: 0, offset: 0, limit: 8, has_more: false, entries: [] });
    return notFound();
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderBook(path = `/books/${ID}`, state?: unknown) {
  render(
    <MemoryRouter initialEntries={[state ? { pathname: path, state } : path]}>
      <App />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

const posted = (calls: { url: string; method: string; body: unknown }[], suffix: string) =>
  calls.filter((call) => call.method === "POST" && call.url.endsWith(suffix));

describe("Book page", () => {
  it("opens a readable book on reading, with the headline", async () => {
    installFetchMock(() =>
      makeOverview(ID, {
        headline: "中文版已就绪。",
        primary_action: { kind: "read", label: "阅读中文版" },
        readable: { ready: true, formats: ["web_html", "merged_html"] },
        translation: { state: "succeeded", run_id: "run-1", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 7, total: 7 } },
      }),
    );
    renderBook();
    expect(await screen.findByRole("status")).toHaveTextContent("中文版已就绪。");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/read`));
    // Already reading: no button pointing at the page the reader is on.
    expect(screen.queryByRole("link", { name: "阅读中文版" })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: "进度" }));
    expect(await screen.findByRole("link", { name: "阅读中文版" })).toHaveAttribute("href", `/books/${ID}/read`);
  });

  it("starts by itself right after import when the estimate is within the cap", async () => {
    // Started once the page has posted a run.
    const calls = installFetchMock(() =>
      posted(calls, "/v1/runs").length > 0
        ? makeOverview(ID, { headline: "正在翻译…", primary_action: { kind: "stop", label: "停止", run_id: "run-1" }, translation: { state: "running", run_id: "run-1", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 1, total: 7 }, auto_started: true }, cost: { estimate_usd_range: [0.1, 0.2], max_charge_usd: 0.2, prepaid: false, spent_usd: 0 } })
        : makeOverview(ID, { cost: { estimate_usd_range: [0.1, 0.2], max_charge_usd: 0.2, prepaid: false, spent_usd: 0 } }),
    );
    renderBook(`/books/${ID}`, { justImported: true });
    await waitFor(() => expect(posted(calls, "/v1/runs")).toHaveLength(1));
    const request = (posted(calls, "/v1/runs")[0].body as { status_detail_json: { run_request: Record<string, unknown> } }).status_detail_json.run_request;
    expect(request).toEqual({ fast_reading: "on", auto_started: true });
    expect(await screen.findByRole("button", { name: "停止翻译" })).toBeInTheDocument();
    expect(await screen.findByText(/已自动开始：预计最多 \$0\.20，在你的自动开始上限 \$1\.00 以内/)).toBeInTheDocument();
  });

  it("waits for a click, and says why, when the estimate is above the cap", async () => {
    const calls = installFetchMock(() => makeOverview(ID, { cost: { estimate_usd_range: [2, 3], max_charge_usd: 3, prepaid: false, spent_usd: 0 } }));
    renderBook(`/books/${ID}`, { justImported: true });
    const start = await screen.findByRole("button", { name: "开始翻译（约 $3.00）" });
    expect(screen.getByText(/预计最多 \$3\.00，超过了自动开始上限 \$1\.00/)).toBeInTheDocument();
    expect(posted(calls, "/v1/runs")).toHaveLength(0);
    const user = userEvent.setup();
    await user.click(start);
    // The dialog is where cost, fast reading mode and notifications are settled.
    const dialog = await screen.findByRole("dialog", { name: "开始翻译" });
    expect(dialog).toHaveTextContent("预计最多花费 $3.00");
    await user.click(within(dialog).getByRole("button", { name: "开始翻译" }));
    await waitFor(() => expect(posted(calls, "/v1/runs")).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/progress`));
  });

  it("never starts by itself without prices, or on a later visit", async () => {
    const calls = installFetchMock(() => makeOverview(ID));
    renderBook(`/books/${ID}`, { justImported: true });
    expect(await screen.findByText(/服务商没有设置单价，估算不出费用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始翻译" })).toBeInTheDocument();
    expect(posted(calls, "/v1/runs")).toHaveLength(0);
  });

  it("shows what needs you and approves in place", async () => {
    let approved = false;
    const calls = installFetchMock(() =>
      makeOverview(ID, {
        headline: approved ? "正在翻译…" : "有 1 件事等你处理，处理后翻译会继续。",
        primary_action: approved ? { kind: "stop", label: "停止", run_id: "run-1" } : { kind: "handle", label: "去处理" },
        translation: { state: "running", run_id: "run-1", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 0, total: 7 } },
        needs_you: approved ? [] : [{ kind: "approval", id: "ap-1", title: "锁定术语：harness → 驾驭框架", detail: "批准后翻译会继续。", action: { kind: "approve" } }],
      }),
    );
    renderBook();
    const needs = await screen.findByRole("region", { name: "需要你处理" });
    expect(within(needs).getByText("锁定术语：harness → 驾驭框架")).toBeInTheDocument();
    approved = true;
    await userEvent.setup().click(within(needs).getByRole("button", { name: "批准" }));
    await waitFor(() => expect(posted(calls, "/v1/approvals/ap-1/approve")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("region", { name: "需要你处理" })).not.toBeInTheDocument());
  });

  it("says when the book cannot be opened, with a retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => (String(input).includes("/overview") ? json({ detail: "database unavailable" }, 503) : notFound())),
    );
    renderBook();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("这本书没能打开");
    expect(within(alert).getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

describe("Old addresses", () => {
  it("send the reader to the current book's page, replacing the old entry", async () => {
    installFetchMock(() => makeOverview(ID));
    window.localStorage.setItem("book-agent.current-document-id", ID);
    renderBook("/runs");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/progress`));
  });

  it("go home when there is no current book", async () => {
    installFetchMock(() => makeOverview(ID));
    renderBook("/issues");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
  });

  it("keep old reader links working", async () => {
    installFetchMock(() => makeOverview(ID, { readable: { ready: true, formats: ["merged_html"] } }));
    renderBook(`/library/${ID}/read`);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/read`));
  });
});
