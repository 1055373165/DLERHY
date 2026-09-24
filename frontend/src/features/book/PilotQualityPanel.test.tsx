import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import { queryClient } from "../../app/queryClient";
import type { PilotQualityReport, PilotQualitySummary } from "../../lib/pilotQuality";
import { json, makeOverview, notFound } from "../../test/fixtures";
import { ReaderPage } from "../library/ReaderPage";

const ID = "book-qa";
const C1 = "c1";
const C2 = "c2";

const counts = (blocking: number, major: number, minor: number) => ({ blocking, major, minor });

const layers: PilotQualityReport["layers"] = [
  {
    layer: "L1",
    name: "结构",
    status: "blocking",
    passed: false,
    counts: counts(1, 2, 0),
    checks: [
      { check_id: "L1.export_chapter_missing", title: "中文版里这一章没有单独成章", severity: "blocking", count: 1, passed: false },
      { check_id: "L1.running_header_as_text", title: "页眉被当成了正文或脚注", severity: "major", count: 2, passed: false },
      { check_id: "L1.chapter_folded", title: "一章里混进了另一章", severity: "blocking", count: 0, passed: true },
    ],
    findings: [
      {
        check_id: "L1.export_chapter_missing",
        layer: "L1",
        severity: "blocking",
        key: `L1.export_chapter_missing:chapter:${C2}`,
        target: { kind: "chapter", id: C2 },
        repair: "export_code",
        location: { chapter_id: C2, page: 37 },
        message: "中文版里「2 RAG 系统及其设计」没有自己的一章，内容并进了前一章，目录里找不到它",
        evidence: { source: "2 RAG systems and their design" },
      },
      {
        check_id: "L1.running_header_as_text",
        layer: "L1",
        severity: "major",
        key: "L1.running_header_as_text:block:b1",
        target: { kind: "block", id: "b1" },
        repair: "structure_edit",
        location: { chapter_id: C1, block_id: "b1", page: 31 },
        message: "页眉「11 The novelty of RAG」被当成了脚注",
        evidence: { source: "11 The novelty of RAG", target: "11 RAG 的新颖之处" },
      },
    ],
  },
  { layer: "L2", name: "图表", status: "passed", passed: true, counts: counts(0, 0, 0), checks: [], findings: [] },
  { layer: "L3", name: "表格", status: "passed", passed: true, counts: counts(0, 0, 0), checks: [], findings: [] },
  { layer: "L4", name: "代码与格式", status: "passed", passed: true, counts: counts(0, 0, 0), checks: [], findings: [] },
  {
    layer: "L5",
    name: "术语",
    status: "warnings",
    passed: true,
    counts: counts(0, 1, 0),
    checks: [{ check_id: "L5.label_rendering", title: "图、表、代码清单的编号叫法前后不一", severity: "major", count: 1, passed: false }],
    findings: [
      {
        check_id: "L5.label_rendering",
        layer: "L5",
        severity: "major",
        key: "L5.label_rendering:term:label:listing",
        target: { kind: "term", id: "label:listing" },
        repair: "glossary",
        location: { chapter_id: C2, page: 45 },
        message: "「Listing N」译成了 「代码清单」16 处、「清单」1 处",
        evidence: {},
      },
    ],
  },
  { layer: "L6", name: "忠实度", status: "passed", passed: true, counts: counts(0, 0, 0), checks: [], findings: [] },
  { layer: "L7", name: "排版与纯净", status: "passed", passed: true, counts: counts(0, 0, 2), checks: [], findings: [] },
];

const report: PilotQualityReport = {
  report_id: "q1",
  document_id: ID,
  run_id: "run-p",
  trigger: "pilot_run",
  status: "blocking",
  counts: counts(1, 3, 2),
  chapters: [
    { chapter_id: C1, ordinal: 10, title: "1 大语言模型与 RAG 的必要性" },
    { chapter_id: C2, ordinal: 11, title: "2 RAG 系统及其设计" },
  ],
  layers,
  export_source: "file",
  error: null,
  duration_ms: 2800,
  checked_at: "2026-09-25T08:00:00Z",
};

const summary: PilotQualitySummary = {
  report_id: "q1",
  status: "blocking",
  checked_at: "2026-09-25T08:00:00Z",
  counts: counts(1, 3, 2),
  layers: layers.map(({ layer, name, status, counts }) => ({ layer, name, status, counts })),
};

function installFetchMock() {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.includes(`/v1/documents/${ID}/overview`)) {
        return json(
          makeOverview(ID, {
            source_type: "pdf_text",
            headline: "试译完成：前两章可以阅读了。",
            primary_action: { kind: "start_book", label: "翻译全书" },
            translation: { state: "succeeded", scope: "pilot", run_id: "run-p", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 4, total: 4 } },
            pilot: {
              available: true,
              unavailable_reason: null,
              unavailable_text: null,
              state: "ready",
              run_id: "run-p",
              chapters: report.chapters,
              readable: true,
              problems: { open: 0, blocking: 0, by_type: [] },
              cost_usd: 0.12,
              finished_at: "2026-09-25T08:00:00Z",
              quality: summary,
            },
          }),
        );
      }
      if (url.includes(`/v1/documents/${ID}/pilot-quality`)) return json(init?.method === "POST" ? { ...report, report_id: "q2" } : report);
      if (url.includes("/parse-health")) return notFound();
      if (url.includes("/v1/runs/")) return json({ run_id: "run-p", event_count: 0, record_count: 0, offset: 0, limit: 50, has_more: false, entries: [] });
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/inbox")) return json({ count: 0, items: [], updates: [] });
      return notFound();
    }),
  );
  return calls;
}

describe("Pilot quality report", () => {
  it("shows each layer's verdict, opens the blocking one, and links each finding into the Chinese copy", async () => {
    const calls = installFetchMock();
    render(
      <MemoryRouter initialEntries={[`/books/${ID}/progress`]}>
        <App />
      </MemoryRouter>,
    );

    const panel = await screen.findByRole("region", { name: "试译质检" });
    expect(within(panel).getByRole("alert")).toHaveTextContent("试译质检发现 1 处严重问题（结构）。试译照常给你看，问题都列在这里。");
    await within(panel).findByText(/中文版里「2 RAG 系统及其设计」没有自己的一章/);
    const rows = [...panel.querySelectorAll("li[data-passed]")].map((row) => row.textContent ?? "");
    expect(rows).toHaveLength(7);
    expect(rows[0]).toMatch(/L1 结构.*1 处严重.*中文版里这一章没有单独成章 1 处；页眉被当成了正文或脚注 2 处/);
    expect(rows[4]).toMatch(/L5 术语.*1 处一般/);
    expect(rows[6]).toMatch(/L7 排版与纯净.*通过（2 处轻微）/);

    // The blocking layer is open; each finding says where it is and opens the reading copy there.
    expect(within(panel).getByText("第 37 页 · 「2 RAG 系统及其设计」")).toBeInTheDocument();
    const links = within(panel).getAllByRole("link", { name: "在译文中查看" });
    expect(links[0]).toHaveAttribute("href", `/books/${ID}/read?format=zh&at=chapter-${C2}`);
    expect(within(panel).getByText("原文：11 The novelty of RAG")).toBeInTheDocument();

    await userEvent.click(within(panel).getByRole("button", { name: "重新质检" }));
    expect(calls.some((call) => call.url.includes("/pilot-quality") && call.method === "POST")).toBe(true);
  });

  it("puts a line per layer into the pilot's result, with the way to the report", async () => {
    installFetchMock();
    render(
      <MemoryRouter initialEntries={[`/books/${ID}/progress`]}>
        <App />
      </MemoryRouter>,
    );
    const strip = await screen.findByLabelText("试译质检结果");
    expect([...strip.querySelectorAll("li")].map((chip) => chip.textContent)).toEqual([
      "结构严重 1",
      "图表通过",
      "表格通过",
      "代码与格式通过",
      "术语一般 1",
      "忠实度通过",
      "排版与纯净通过",
    ]);
    expect(within(strip).getByRole("link", { name: "看质检报告" })).toHaveAttribute("href", `/books/${ID}/progress#pilot-quality`);
  });

  it("opens the reading copy at the finding's chapter", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/meta")) return json({ app_name: "book-agent", version: "t", environment: "test", api_prefix: "/v1", docs_path: "", capabilities: {} });
        if (url.includes("/exports/download")) return new Response("<html><body><section id='chapter-c2'>正文</section></body></html>", { status: 200, headers: { "Content-Type": "text/html" } });
        if (url.includes(`/v1/documents/${ID}`)) return json({ document_id: ID, title: "RAG", title_src: "RAG", source_type: "pdf_text", status: "exported", chapters: [] });
        return notFound();
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/read/${ID}?format=zh&at=chapter-${C2}`]}>
          <Routes>
            <Route path="/read/:documentId" element={<ReaderPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const frame = await screen.findByTitle(/RAG · 中文版/);
    expect(frame.getAttribute("src")).toMatch(new RegExp(`#chapter-${C2}$`));
  });
});
