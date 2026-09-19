import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";

const DOCUMENT_ID = "doc-wait";
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const APPROVAL = {
  id: "ap-1",
  document_id: DOCUMENT_ID,
  turn_id: "t-1",
  kind: "lock_term",
  status: "pending",
  payload_json: { tool: "lock_term", arguments: { source_term: "harness", target_term: "驾驭框架" } },
  created_at: "2026-09-20T08:00:00Z",
};

function installFetchMock(pending: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/approvals?status=pending")) return json(pending);
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/documents/history")) return json({ total_count: 0, offset: 0, limit: 12, has_more: false, entries: [] });
      if (url.includes(`/v1/documents/${DOCUMENT_ID}`) && !url.includes("/", url.indexOf(DOCUMENT_ID) + DOCUMENT_ID.length)) {
        return json({
          document_id: DOCUMENT_ID, title: "Harness", title_src: "Harness", source_type: "web", status: "active",
          chapter_count: 1, sentence_count: 10, packet_count: 4, chapters: [], merged_export_ready: false,
          latest_run_id: null, latest_run_status: null,
        });
      }
      // Everything else the shell loads (budget, worklists...) is irrelevant here: answer "not found".
      return new Response(JSON.stringify({ detail: "not found" }), { status: 404, headers: { "Content-Type": "application/json" } });
    }),
  );
}

function renderAt(path: string) {
  window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("Pending approvals in the shell", () => {
  it("shows the count on the nav, in the tab title and as a notice that links to the approvals", async () => {
    installFetchMock([APPROVAL, { ...APPROVAL, id: "ap-2" }]);
    renderAt("/library");
    expect(await screen.findByLabelText("2 项待审批")).toBeInTheDocument();
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("有 2 项等你审批");
    expect(notice).toHaveAttribute("href", "/approvals");
    await waitFor(() => expect(document.title).toBe("(2) 等你审批 · Book Agent"));
  });

  it("stays quiet when nothing waits", async () => {
    installFetchMock([]);
    renderAt("/library");
    await screen.findByText("服务在线");
    expect(screen.queryByLabelText(/项待审批/)).not.toBeInTheDocument();
    expect(document.title).toBe("Book Agent");
  });
});
