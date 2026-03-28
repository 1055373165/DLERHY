import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";

function installFetchMock() {
  let approved = false;
  const documentPayload = {
    document_id: "doc-123",
    source_type: "epub",
    status: "active",
    title: "Systems Thinking",
    title_src: "Systems Thinking",
    title_tgt: null,
    author: "Jane Doe",
    chapter_count: 12,
    block_count: 0,
    sentence_count: 1200,
    packet_count: 48,
    open_issue_count: 3,
    merged_export_ready: false,
    latest_merged_export_at: null,
    chapter_bilingual_export_count: 0,
    latest_run_id: "run-123",
    latest_run_status: "paused",
    latest_run_current_stage: "translate",
    latest_run_updated_at: "2026-03-28T08:00:00Z",
    chapters: [
      {
        chapter_id: "ch-1",
        ordinal: 1,
        title_src: "Chapter One",
        status: "review_required",
        sentence_count: 120,
        packet_count: 4,
        open_issue_count: 2,
        bilingual_export_ready: false,
      },
    ],
  };

  const runPayload = {
    run_id: "run-123",
    document_id: "doc-123",
    run_type: "translate_full",
    status: "paused",
    priority: 100,
    status_detail_json: {
      pipeline: {
        current_stage: "translate",
        stages: {
          translate: {
            status: "paused",
            total_packet_count: 48,
          },
        },
      },
      control_counters: {
        completed_work_item_count: 20,
      },
    },
    created_at: "2026-03-28T07:00:00Z",
    updated_at: "2026-03-28T08:00:00Z",
    work_items: {
      total_count: 48,
      status_counts: {},
      stage_counts: {
        translate: 48,
      },
    },
    worker_leases: {
      total_count: 1,
      status_counts: {},
      latest_heartbeat_at: "2026-03-28T08:00:00Z",
    },
    events: {
      event_count: 2,
      latest_event_at: "2026-03-28T08:00:00Z",
    },
  };

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
    if (url.includes("/v1/documents/doc-123/exports")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-123",
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
    if (url.includes("/v1/documents/doc-123/chapters/ch-1/worklist")) {
      return new Response(
        JSON.stringify({
          document_id: "doc-123",
          chapter_id: "ch-1",
          ordinal: 1,
          title_src: "Chapter One",
          chapter_status: "review_required",
          packet_count: 4,
          translated_packet_count: 4,
          current_issue_count: 2,
          current_open_issue_count: 2,
          current_triaged_issue_count: 0,
          current_active_blocking_issue_count: 1,
          memory_proposals: {
            proposal_count: 1,
            pending_proposal_count: approved ? 0 : 1,
            counts_by_status: approved
              ? { proposed: 0, committed: 1, rejected: 0 }
              : { proposed: 1, committed: 0, rejected: 0 },
            latest_proposal_updated_at: "2026-03-28T08:05:00Z",
            active_snapshot_version: approved ? 4 : 3,
            pending_proposals: approved
              ? []
              : [
                  {
                    proposal_id: "prop-123",
                    packet_id: "pkt-1",
                    translation_run_id: "run-pkt-1",
                    status: "proposed",
                    base_snapshot_version: 3,
                    committed_snapshot_id: null,
                    created_at: "2026-03-28T08:05:00Z",
                    updated_at: "2026-03-28T08:05:00Z",
                    last_decision: null,
                  },
                ],
            recent_decisions: approved
              ? [
                  {
                    proposal_id: "prop-123",
                    decision: "approved",
                    actor_type: "human",
                    actor_id: "reviewer-ui",
                    note: "Looks good",
                    created_at: "2026-03-28T08:08:00Z",
                  },
                ]
              : [],
          },
          timeline: approved
            ? [
                {
                  event_id: "prop-123",
                  source_kind: "memory_proposal",
                  event_kind: "approved",
                  created_at: "2026-03-28T08:08:00Z",
                  actor_name: "reviewer-ui",
                  note: "Looks good",
                  proposal_id: "prop-123",
                  decision: "approved",
                },
              ]
            : [
                {
                  event_id: "act-1",
                  source_kind: "action",
                  event_kind: "issue_action",
                  created_at: "2026-03-28T08:01:00Z",
                  actor_name: "system",
                  issue_type: "TERM_CONFLICT",
                  action_type: "REBUILD_PACKET_THEN_RERUN",
                  scope_type: "packet",
                  scope_id: "pkt-1",
                  status: "queued",
                },
              ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-123/chapters/ch-1/memory-proposals/prop-123/approve")) {
      approved = true;
      return new Response(
        JSON.stringify({
          document_id: "doc-123",
          chapter_id: "ch-1",
          decision: "approved",
          committed_snapshot_id: "snap-4",
          committed_snapshot_version: 4,
          proposal: {
            proposal_id: "prop-123",
            packet_id: "pkt-1",
            translation_run_id: "run-pkt-1",
            status: "committed",
            base_snapshot_version: 3,
            committed_snapshot_id: "snap-4",
            created_at: "2026-03-28T08:05:00Z",
            updated_at: "2026-03-28T08:08:00Z",
            last_decision: {
              proposal_id: "prop-123",
              decision: "approved",
              actor_type: "human",
              actor_id: "reviewer-ui",
              note: "Looks good",
              created_at: "2026-03-28T08:08:00Z",
            },
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/documents/doc-123")) {
      return new Response(JSON.stringify(documentPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/v1/runs/run-123/events")) {
      return new Response(
        JSON.stringify({
          run_id: "run-123",
          event_count: 0,
          record_count: 0,
          offset: 0,
          limit: 8,
          has_more: false,
          entries: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    if (url.includes("/v1/runs/run-123")) {
      return new Response(JSON.stringify(runPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    throw new Error(`Unhandled fetch: ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Workspace page", () => {
  it("maps a paused run to the continue action", async () => {
    window.localStorage.setItem("book-agent.current-document-id", "doc-123");
    installFetchMock();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByRole("button", { name: "继续当前转换" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "当前书籍" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "章节复核台" })).toBeInTheDocument();
    expect(await screen.findByText("Review / Action Timeline")).toBeInTheDocument();
    expect(screen.getAllByText("Systems Thinking").length).toBeGreaterThan(0);
  });

  it("approves a pending proposal from the reviewer console and refreshes the timeline", async () => {
    window.localStorage.setItem("book-agent.current-document-id", "doc-123");
    const fetchMock = installFetchMock();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByRole("button", { name: "继续当前转换" })).toBeInTheDocument();
    await user.clear(await screen.findByLabelText("操作人"));
    await user.type(screen.getByLabelText("操作人"), "reviewer-ui");
    await user.type(await screen.findByLabelText("备注"), "Looks good");
    await user.click(screen.getByRole("button", { name: "批准写入" }));

    await waitFor(() => {
      expect(screen.getByText(/已批准/)).toBeInTheDocument();
    });
    expect(screen.getByText("Memory Approved")).toBeInTheDocument();
    expect(screen.getByText(/reviewer-ui 对 proposal/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/documents/doc-123/chapters/ch-1/memory-proposals/prop-123/approve"),
      expect.objectContaining({ method: "POST" })
    );
  });
});
