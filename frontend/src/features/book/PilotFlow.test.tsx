import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import { decideAutoStart, decideBookStart } from "../../lib/autoStart";
import type { BookOverview, PilotOverview } from "../../lib/overview";
import { json, makeOverview, notFound } from "../../test/fixtures";

const ID = "book-9";
const RUN = { run_id: "run-9", document_id: ID, run_type: "translate_full", status: "running", priority: 100, status_detail_json: {}, created_at: "", updated_at: "" };
const CHAPTERS = [
  { chapter_id: "c1", ordinal: 3, title: "1 Why agents" },
  { chapter_id: "c2", ordinal: 4, title: "2 The loop" },
];

function pilot(overrides: Partial<PilotOverview> = {}): PilotOverview {
  return {
    available: true,
    unavailable_reason: null,
    unavailable_text: null,
    state: "not_started",
    run_id: null,
    chapters: CHAPTERS,
    readable: false,
    problems: null,
    cost_usd: 0,
    finished_at: null,
    ...overrides,
  };
}

const notStarted = (): BookOverview =>
  makeOverview(ID, {
    source_type: "epub",
    headline: "还没开始翻译。可以先免费试译前两章，看过效果再决定是否翻译全书。",
    primary_action: { kind: "pilot", label: "免费试译前两章" },
    pilot: pilot(),
    cost: { estimate_usd_range: [2, 3], max_charge_usd: 3, prepaid: false, spent_usd: 0 },
  });

const ready = (maxCharge = 2.5): BookOverview =>
  makeOverview(ID, {
    source_type: "epub",
    headline: "试译完成：前两章可以阅读了。还有 3 个问题没有解决，列在下面。",
    primary_action: { kind: "start_book", label: "翻译全书" },
    translation: { state: "succeeded", scope: "pilot", run_id: "run-p", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 4, total: 4 } },
    pilot: pilot({
      state: "ready",
      run_id: "run-p",
      readable: true,
      problems: { open: 3, blocking: 1, by_type: [{ type: "OMISSION", count: 2, blocking: 1 }, { type: "STYLE_DRIFT", count: 1, blocking: 0 }] },
    }),
    cost: { estimate_usd_range: [maxCharge / 2, maxCharge], max_charge_usd: maxCharge, prepaid: false, spent_usd: 0 },
  });

function installFetchMock(overview: () => BookOverview) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (url.includes(`/v1/documents/${ID}/overview`)) return json(overview());
      if (url.endsWith(`/v1/documents/${ID}/pilot`) && method === "POST") return json(RUN, 202);
      if (url.endsWith("/v1/runs") && method === "POST") return json(RUN, 201);
      if (/\/v1\/runs\/[^/]+\/(resume|pause|retry)$/.test(url)) return json(RUN);
      if (url.includes("/exports/download")) {
        return new Response("<html><body><p>试译正文</p></body></html>", { status: 200, headers: { "Content-Type": "text/html" } });
      }
      if (url.includes(`/v1/documents/${ID}`) && !url.includes("/overview")) {
        return json({ document_id: ID, title: "Agents", title_src: "Agents", source_type: "epub", status: "active", chapters: [] });
      }
      if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
      if (/\/v1\/approvals\/[^/]+\/(approve|reject)$/.test(url)) return json({ approval_id: "a1", status: "approved" });
      return notFound();
    }),
  );
  return calls;
}

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderBook(path = `/books/${ID}`) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

const posted = (calls: { url: string; method: string }[], suffix: string) =>
  calls.filter((call) => call.method === "POST" && call.url.endsWith(suffix));

describe("The free pilot", () => {
  beforeEach(() => window.localStorage.clear());

  it("is the first thing offered, and its dialog says it is free and what happens next", async () => {
    const calls = installFetchMock(notStarted);
    renderBook(`/books/${ID}/progress`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "免费试译前两章" }));
    const dialog = await screen.findByRole("dialog", { name: "免费试译" });
    expect(dialog).toHaveTextContent("免费");
    expect(dialog).toHaveTextContent("「1 Why agents」和「2 The loop」");
    expect(dialog).toHaveTextContent("不满意不花钱");
    await user.click(within(dialog).getByRole("button", { name: "开始免费试译" }));
    await waitFor(() => expect(posted(calls, `/v1/documents/${ID}/pilot`)).toHaveLength(1));
    expect(posted(calls, "/v1/runs")).toHaveLength(0);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/progress`));
  });

  it("can be skipped from its dialog, translating the whole book (paid)", async () => {
    const calls = installFetchMock(notStarted);
    renderBook(`/books/${ID}/progress`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "免费试译前两章" }));
    const dialog = await screen.findByRole("dialog", { name: "免费试译" });
    await user.click(within(dialog).getByRole("button", { name: "跳过试译，直接翻译全书（约 $3.00）" }));
    await waitFor(() => expect(posted(calls, "/v1/runs")).toHaveLength(1));
    expect(posted(calls, `/v1/documents/${ID}/pilot`)).toHaveLength(0);
  });

  it("when ready, opens on its two chapters with the problems left and the choice", async () => {
    const calls = installFetchMock(() => ready());
    renderBook();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(`/books/${ID}/read`));
    expect(await screen.findByText(/这是免费试译的前两章/)).toBeInTheDocument();
    // Only the pilot's formats: no whole-book Markdown or web page to ask for.
    expect(screen.getByRole("tab", { name: "中文版" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Markdown" })).not.toBeInTheDocument();
    const result = screen.getByRole("group", { name: "试译结果" });
    // One summary: the verdict, and the review's findings one link away.
    const summary = within(result).getByRole("region", { name: "试译质量" });
    expect(summary).toHaveTextContent("还有 1 个严重问题，试译照常可以读。");
    expect(within(summary).getByRole("link", { name: "审校意见 3 条（1 条严重）" })).toHaveAttribute("href", `/books/${ID}/issues`);
    // Above the auto-start cap ($1), 翻译全书 asks first, with the rest of the book's cost.
    const user = userEvent.setup();
    await user.click(within(result).getByRole("button", { name: "翻译全书（约 $2.50）" }));
    const dialog = await screen.findByRole("dialog", { name: "翻译全书" });
    expect(dialog).toHaveTextContent("试译过的两章不再翻译、不再收费");
    await user.click(within(dialog).getByRole("button", { name: "翻译全书" }));
    await waitFor(() => expect(posted(calls, "/v1/runs")).toHaveLength(1));
  });

  it("says in one place what the repair fixed, the layers, and what waits on the reader", async () => {
    const repaired = (): BookOverview => {
      const book = ready();
      return {
        ...book,
        headline: "试译完成：前两章可以阅读了。自动修复了 7 个问题，还有 3 个需要你看（1 个问题等你回答）。",
        pilot: {
          ...(book.pilot as PilotOverview),
          problems: { open: 41, blocking: 0, by_type: [] },
          quality: {
            report_id: "q2",
            status: "blocking",
            checked_at: null,
            counts: { blocking: 1, major: 2, minor: 4 },
            layers: [
              { layer: "L1", name: "结构", status: "passed", counts: { blocking: 0, major: 0, minor: 0 } },
              { layer: "L2", name: "图表", status: "blocking", counts: { blocking: 1, major: 0, minor: 0 } },
              { layer: "L5", name: "术语", status: "warnings", counts: { blocking: 0, major: 2, minor: 0 } },
            ],
          },
          repair: {
            repair_id: "r1",
            status: "finished",
            outcome: "escalated",
            repaired_kinds: ["同一术语用了几种译法", "中英文之间的空格与全书习惯不一致", "页眉被当成了正文或脚注", "图片裁剪不对"],
            stop_reason: "exhausted",
            rounds: 2,
            repaired: 7,
            remaining: { blocking: 1, major: 2 },
            questions: 1,
            attempts: 9,
            cost_usd: 0.02,
            text: "自动修复了 7 个问题，还有 3 个需要你看（1 个问题等你回答）。",
            finished_at: null,
          },
        },
        parse_health: { report_id: "h1", status: "blocking", needs_attention: true, checked_at: null, counts: { blocking: 1, major: 0, minor: 0 }, checks: [] } as unknown as BookOverview["parse_health"],
        needs_you: [
          {
            kind: "question",
            id: "a1",
            title: "第 12 页的图片自动裁剪不干净，修正了几次没有成功。要把这张图所在的页面区域原样截取、保留成图片吗？",
            detail: "图片裁剪不对：第 12 页，共 1 处，自动修复没有解决。",
            images: [`/documents/${ID}/pilot-repair/evidence/L2-figure_regions-p12.png`],
            action: { kind: "approve", approve_label: "是", reject_label: "否", target: `/books/${ID}/progress#pilot-quality`, label: "看质检报告" },
          },
        ],
      };
    };
    const calls = installFetchMock(repaired);
    renderBook(`/books/${ID}/read`);
    const result = await screen.findByRole("group", { name: "试译结果" });
    const summary = within(result).getByRole("region", { name: "试译质量" });
    // One verdict, one line of layers, what was repaired, what is asked: no separate notice boxes.
    expect(summary).toHaveTextContent("还有 1 处严重问题（图表），试译照常可以读。");
    expect([...within(summary).getByLabelText("试译质检结果").querySelectorAll("li")].map((chip) => chip.textContent)).toEqual([
      "结构通过",
      "图表严重 1",
      "术语一般 2",
    ]);
    expect(summary).toHaveTextContent("自动修复了 7 个问题：同一术语用了几种译法、中英文之间的空格与全书习惯不一致、页眉被当成了正文或脚注");
    expect(within(summary).getByRole("link", { name: "去回答" })).toHaveAttribute("href", "#needs-you");
    expect(within(summary).getByRole("link", { name: "审校意见 41 条" })).toBeInTheDocument();
    expect(within(summary).getByRole("link", { name: "解析体检有严重问题" })).toBeInTheDocument();
    expect(result.querySelectorAll("[class*='attention'], [class*='pilotProblems']")).toHaveLength(0);
    // The pilot does not wait on the question: 翻译全书 is still the choice.
    expect(within(result).getByRole("button", { name: "翻译全书（约 $2.50）" })).toBeEnabled();

    // The question: 是 / 否, with the page it is about.
    const needs = screen.getByRole("region", { name: "需要你处理" });
    expect(needs).toHaveTextContent("要把这张图所在的页面区域原样截取、保留成图片吗？");
    expect(within(needs).getByRole("img", { name: /原书这一页/ })).toHaveAttribute(
      "src",
      `/v1/documents/${ID}/pilot-repair/evidence/L2-figure_regions-p12.png`,
    );
    await userEvent.setup().click(within(needs).getByRole("button", { name: "是" }));
    await waitFor(() => expect(posted(calls, "/v1/approvals/a1/approve")).toHaveLength(1));
    expect(within(needs).getByRole("button", { name: "否" })).toBeInTheDocument();
  });

  it("starts the whole book at once when it costs no more than the auto-start cap", async () => {
    const calls = installFetchMock(() => ready(0.2));
    renderBook(`/books/${ID}/read`);
    const result = await screen.findByRole("group", { name: "试译结果" });
    await userEvent.setup().click(within(result).getByRole("button", { name: "翻译全书（约 $0.20）" }));
    await waitFor(() => expect(posted(calls, "/v1/runs")).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("暂不翻译 folds the choice away, and it stays folded", async () => {
    installFetchMock(() => ready());
    renderBook(`/books/${ID}/read`);
    const result = await screen.findByRole("group", { name: "试译结果" });
    await userEvent.setup().click(within(result).getByRole("button", { name: "暂不翻译" }));
    expect(await screen.findByText(/暂不翻译全书。想好了随时可以开始/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "试译结果" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(`book-agent.pilot-declined.${ID}`)).toBe("1");
  });
});

describe("Starting by itself", () => {
  it("never starts a paid run while a free pilot is on offer, and applies the cap to the whole book after it", () => {
    expect(decideAutoStart({ ...notStarted(), cost: { estimate_usd_range: [0.1, 0.2], max_charge_usd: 0.2, prepaid: false, spent_usd: 0 } }, 1)).toEqual({ auto: false, reason: "" });
    expect(decideBookStart(ready(0.5), 1)).toEqual({ auto: true });
    expect(decideBookStart(ready(5), 1).auto).toBe(false);
  });
});
