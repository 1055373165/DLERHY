import { render, screen } from "@testing-library/react";
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
    expect(screen.getByText(/全部事件（技术细节，3 条）/)).toBeInTheDocument();
    expect(screen.getByText("work_item.leased")).toBeInTheDocument();
  });
});
