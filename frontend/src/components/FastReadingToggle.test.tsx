import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";

const DOCUMENT_ID = "doc-fast";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const run = {
  run_id: "run-new", document_id: DOCUMENT_ID, run_type: "translate_full", status: "running", priority: 100,
  status_detail_json: {}, work_items: { total_count: 0, status_counts: {}, stage_counts: {} },
  worker_leases: { total_count: 0, status_counts: {} }, events: { event_count: 0 },
  created_at: "2026-09-20T08:00:00Z", updated_at: "2026-09-20T08:00:00Z",
};

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/v1/runs") && init?.method === "POST") return json(run, 201);
    if (url.includes("/v1/runs/run-new/resume")) return json(run);
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.endsWith(`/v1/documents/${DOCUMENT_ID}`)) {
      return json({
        document_id: DOCUMENT_ID, source_type: "web", status: "active", title: "Harness", title_src: "Harness",
        chapter_count: 1, sentence_count: 10, packet_count: 4, chapters: [], merged_export_ready: false,
        latest_run_id: null, latest_run_status: null,
      });
    }
    return json({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function runRequestSent(fetchMock: ReturnType<typeof installFetchMock>) {
  const call = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/v1/runs") && init?.method === "POST");
  return JSON.parse(String(call?.[1]?.body)).status_detail_json.run_request;
}

async function openBook() {
  window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
  const fetchMock = installFetchMock();
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  const toggle = await screen.findByRole("checkbox", { name: /快速阅读模式/ });
  return { fetchMock, toggle };
}

describe("Fast reading mode", () => {
  it("is on by default, says what it costs, and starts the run with it", async () => {
    const user = userEvent.setup();
    const { fetchMock, toggle } = await openBook();
    expect(toggle).toBeChecked();
    expect(screen.getByText(/术语直接锁定，不再停下来等你确认/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "开始整书转换" }));
    await waitFor(() => expect(runRequestSent(fetchMock)).toEqual({ fast_reading: "on" }));
  });

  it("can be turned off, and stays off for the next book", async () => {
    const user = userEvent.setup();
    const { fetchMock, toggle } = await openBook();
    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(window.localStorage.getItem("book-agent.fast-reading")).toBe("off");
    await user.click(screen.getByRole("button", { name: "开始整书转换" }));
    await waitFor(() => expect(runRequestSent(fetchMock)).toEqual({ fast_reading: "off" }));
  });
});
