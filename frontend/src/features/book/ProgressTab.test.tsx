import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import { json, makeOverview, notFound } from "../../test/fixtures";

const ID = "book-progress";
const RUN = "run-1";

function installFetchMock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(`/v1/documents/${ID}/overview`)) {
        return json(
          makeOverview(ID, {
            headline: "翻译已暂停。",
            primary_action: { kind: "resume", label: "继续翻译", run_id: RUN },
            translation: {
              state: "paused",
              run_id: RUN,
              stop_reason: "provider.insufficient_balance",
              stop_reason_text: "模型服务商账户余额不足。",
              progress: { done: 3, total: 7 },
              started_at: "2026-09-20T08:00:00Z",
              finished_at: null,
              steps: [
                { key: "terminology", label: "确定术语", status: "succeeded", at: "2026-09-20T08:01:00Z" },
                { key: "translate", label: "翻译", status: "running", at: "2026-09-20T08:02:00Z" },
                { key: "merged_html", label: "生成中文版", status: "pending", at: null },
              ],
            },
            cost: { estimate_usd_range: null, max_charge_usd: null, prepaid: false, spent_usd: 0.42 },
          }),
        );
      }
      if (url.includes(`/v1/runs/${RUN}/events`)) {
        return json({
          run_id: RUN, event_count: 3, record_count: 3, offset: 0, limit: 100, has_more: false,
          entries: [
            { event_id: "e3", run_id: RUN, event_type: "run.paused", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:02:30Z" },
            { event_id: "e2", run_id: RUN, event_type: "work_item.leased", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:01:30Z" },
            { event_id: "e1", run_id: RUN, event_type: "run.started", actor_type: "system", payload_json: {}, created_at: "2026-09-20T08:00:00Z" },
          ],
        });
      }
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/inbox")) return json({ count: 0, items: [], updates: [] });
      return notFound();
    }),
  );
}

const HEALTH_ID = "book-health";

const report = {
  report_id: "r1",
  document_id: HEALTH_ID,
  trigger: "bootstrap",
  status: "blocking",
  failed: { blocking: 1, major: 1, minor: 0 },
  duration_ms: 5200,
  checked_at: "2026-09-25T08:00:00Z",
  checks: [
    { name: "chapters_vs_outline", severity: "blocking", count: 0, denominator: 14, allowed: 0, passed: true, samples: [], breakdown: {}, threshold_note: null },
    {
      name: "figure_regions", severity: "blocking", count: 4, denominator: 65, allowed: 0, passed: false,
      samples: [{ text: "Figure 5.4 Although sofa and couch have similar meanings", page: 138, block_id: "b1", detail: "no_region" }],
      breakdown: { no_region: 1, cut_path: 2 }, threshold_note: null,
    },
    {
      name: "duplicated_labels", severity: "major", count: 49, denominator: 2315, allowed: 46, passed: false,
      samples: [{ text: "Few-shot learning Zero-shot learning", page: 24, block_id: "b2", detail: "label_as_text" }],
      breakdown: { pages: 29 }, threshold_note: "a label or two kept as text is noise",
    },
  ],
};

function installHealthMock(withReport: boolean) {
  const calls: { url: string; method: string }[] = [];
  let reported = withReport;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.includes(`/v1/documents/${HEALTH_ID}/overview`)) {
        return json(
          makeOverview(HEALTH_ID, {
            source_type: "pdf_text",
            source_url: null,
            parse_health: reported
              ? {
                  status: "blocking",
                  needs_attention: true,
                  checked_at: "2026-09-25T08:00:00Z",
                  check_count: 3,
                  failed: [
                    { name: "figure_regions", severity: "blocking", count: 4, allowed: 0 },
                    { name: "duplicated_labels", severity: "major", count: 49, allowed: 46 },
                  ],
                }
              : null,
          }),
        );
      }
      if (url.includes(`/v1/documents/${HEALTH_ID}/parse-health`)) {
        if (init?.method === "POST") reported = true;
        return reported ? json(report) : notFound();
      }
      if (url.includes("/cost-estimate")) return notFound();
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/inbox")) return json({ count: 0, items: [], updates: [] });
      return notFound();
    }),
  );
  return calls;
}

describe("Parse health on the progress tab", () => {
  it("says what the checks found, worst first, with the pages, and asks for a look before translating", async () => {
    installHealthMock(true);
    render(
      <MemoryRouter initialEntries={[`/books/${HEALTH_ID}/progress`]}>
        <App />
      </MemoryRouter>,
    );

    const panel = await screen.findByRole("region", { name: "解析体检" });
    expect(within(panel).getByRole("alert")).toHaveTextContent("有 1 项严重问题、1 项一般问题，建议先处理再翻译");
    await within(panel).findAllByRole("listitem");
    // One row per check (the samples inside a row are list items too).
    const labels = [...panel.querySelectorAll("li[data-passed]")].map((row) => row.textContent ?? "");
    // Failed checks come first; a passed one is still listed.
    expect(labels[0]).toMatch(/图片区域.*严重.*4 处.*未通过/);
    expect(labels[1]).toMatch(/图中文字重复.*一般.*49 处（可接受 46 处以内）.*未通过/);
    expect(labels.some((text) => /章节与书签一致.*通过/.test(text))).toBe(true);
    expect(within(panel).getByText(/整张图没有识别出来/)).toBeInTheDocument();
    expect(within(panel).getByText("第 138 页")).toBeInTheDocument();

    // Not a gate: the start button is still there, with the finding next to it.
    expect(screen.getByRole("button", { name: /开始翻译/ })).toBeEnabled();
    expect(screen.getByRole("link", { name: "看看体检报告" })).toHaveAttribute("href", `/books/${HEALTH_ID}/progress`);
  });

  it("offers to run the checks on a book imported before they existed", async () => {
    const calls = installHealthMock(false);
    render(
      <MemoryRouter initialEntries={[`/books/${HEALTH_ID}/progress`]}>
        <App />
      </MemoryRouter>,
    );

    const panel = await screen.findByRole("region", { name: "解析体检" });
    expect(within(panel).getByText(/还没有体检报告/)).toBeInTheDocument();
    // Nothing to fetch until there is a report.
    expect(calls.some((call) => call.url.includes("/parse-health"))).toBe(false);
    await userEvent.click(within(panel).getByRole("button", { name: "立即体检" }));
    expect(calls.some((call) => call.url.includes("/parse-health") && call.method === "POST")).toBe(true);
    expect(await within(panel).findByText(/图片区域/)).toBeInTheDocument();
  });
});

describe("Progress tab", () => {
  it("is the run record: steps with their time, what happened, how long, and why it stopped", async () => {
    installFetchMock();
    render(
      <MemoryRouter initialEntries={[`/books/${ID}/progress`]}>
        <App />
      </MemoryRouter>,
    );

    const summary = await screen.findByRole("region", { name: "本次运行" });
    expect(summary).toHaveTextContent(/已翻译\s*3\s*\/\s*7\s*段/);
    expect(summary).toHaveTextContent(/已花费\s*\$0\.42/);

    expect(screen.getByRole("note")).toHaveTextContent("模型服务商账户余额不足。");

    const steps = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(steps.some((text) => text.includes("确定术语") && text.includes("完成"))).toBe(true);
    expect(steps.some((text) => text.includes("翻译") && text.includes("进行中"))).toBe(true);

    // Events in words, with the per-packet chatter only under the technical details.
    expect(await screen.findByText("翻译已暂停")).toBeInTheDocument();
    expect(screen.getByText("开始翻译")).toBeInTheDocument();
    // The raw tail is folded away and not fetched until someone asks for it.
    const fold = screen.getByText(/全部事件（技术细节）/);
    expect(screen.queryByText("work_item.leased")).toBeNull();
    await userEvent.click(fold);
    expect(await screen.findByText("work_item.leased")).toBeInTheDocument();
    expect(screen.getByText(/全部事件（技术细节，3 条）/)).toBeInTheDocument();
  });
});
