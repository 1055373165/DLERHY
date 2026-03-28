import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/v1/health")) {
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/v1/documents/history")) {
      return new Response(
        JSON.stringify({
          total_count: 1,
          record_count: 1,
          offset: 0,
          limit: 12,
          has_more: false,
          entries: [
            {
              document_id: "doc-open",
              source_type: "epub",
              status: "active",
              title: "Open the Desk",
              title_src: "Open the Desk",
              title_tgt: null,
              author: "Riley North",
              source_path: null,
              created_at: "2026-03-28T06:00:00Z",
              updated_at: "2026-03-28T08:00:00Z",
              chapter_count: 10,
              sentence_count: 900,
              packet_count: 30,
              merged_export_ready: false,
              latest_merged_export_at: null,
              chapter_bilingual_export_count: 0,
              latest_run_id: null,
              latest_run_status: null,
              latest_run_current_stage: null,
              latest_run_completed_work_item_count: null,
              latest_run_total_work_item_count: null,
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-open/exports")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-open",
          export_count: 0,
          successful_export_count: 0,
          filtered_export_count: 0,
          record_count: 0,
          offset: 0,
          limit: 5,
          has_more: false,
          latest_export_at: null,
          export_counts_by_type: {},
          latest_export_ids_by_type: {},
          translation_usage_summary: null,
          issue_hotspots: [],
          issue_chapter_highlights: {},
          records: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-open")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-open",
          source_type: "epub",
          status: "active",
          title: "Open the Desk",
          title_src: "Open the Desk",
          title_tgt: null,
          author: "Riley North",
          chapter_count: 10,
          block_count: 0,
          sentence_count: 900,
          packet_count: 30,
          open_issue_count: 0,
          merged_export_ready: false,
          latest_merged_export_at: null,
          chapter_bilingual_export_count: 0,
          latest_run_id: null,
          latest_run_status: null,
          latest_run_current_stage: null,
          latest_run_updated_at: "2026-03-28T08:00:00Z",
          chapters: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    throw new Error(`Unhandled fetch: ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
}

describe("Library page", () => {
  it("opens a history entry into the workspace", async () => {
    installFetchMock();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/library"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByText("Open the Desk")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开这本书" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "当前书籍" })).toBeInTheDocument();
    });
    expect(screen.getAllByText("Open the Desk").length).toBeGreaterThan(0);
  });
});
