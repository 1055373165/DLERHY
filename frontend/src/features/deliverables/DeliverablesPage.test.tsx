import { render, screen } from "@testing-library/react";
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
          total_count: 0,
          record_count: 0,
          offset: 0,
          limit: 12,
          has_more: false,
          entries: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-789/exports")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-789",
          export_count: 2,
          successful_export_count: 2,
          filtered_export_count: 2,
          record_count: 2,
          offset: 0,
          limit: 5,
          has_more: false,
          latest_export_at: "2026-03-28T08:00:00Z",
          export_counts_by_type: {
            merged_html: 1,
            review_package: 1,
          },
          latest_export_ids_by_type: {},
          translation_usage_summary: null,
          issue_hotspots: [],
          issue_chapter_highlights: {},
          records: [
            {
              export_id: "exp-1",
              export_type: "merged_html",
              status: "succeeded",
              file_path: "/tmp/merged.zip",
              created_at: "2026-03-28T08:00:00Z",
              updated_at: "2026-03-28T08:00:00Z",
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-789")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-789",
          source_type: "epub",
          status: "exported",
          title: "The Delivery Playbook",
          title_src: "The Delivery Playbook",
          title_tgt: null,
          author: "Alex Example",
          chapter_count: 8,
          block_count: 0,
          sentence_count: 800,
          packet_count: 16,
          open_issue_count: 0,
          merged_export_ready: true,
          latest_merged_export_at: "2026-03-28T08:00:00Z",
          chapter_bilingual_export_count: 8,
          latest_run_id: null,
          latest_run_status: "succeeded",
          latest_run_current_stage: "merged_html",
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

describe("Deliverables page", () => {
  it("enables download for ready assets", async () => {
    window.localStorage.setItem("book-agent.current-document-id", "doc-789");
    installFetchMock();

    render(
      <MemoryRouter initialEntries={["/deliverables"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByRole("button", { name: "下载中文阅读包" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下载双语章节包" })).toBeEnabled();
  });
});
