import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, vi } from "vitest";

import { App } from "../../app/App";

const STORAGE_KEY_DOCUMENT = "book-agent.current-document-id";
const STORAGE_KEY_WORKBENCH_MODE = "book-agent.workbench-mode";

type ProposalStatus = "proposed" | "committed" | "rejected" | "none";

interface ChapterState {
  proposalStatus: ProposalStatus;
  actionStatus: "queued" | "completed";
  assignment:
    | {
        owner_name: string;
        assigned_by: string;
        note?: string;
        assigned_at: string;
        assignment_id: string;
      }
    | null;
  recentDecision:
    | {
        proposal_id: string;
        decision: "approved" | "rejected";
        actor_type: string;
        actor_id: string;
        note?: string;
        created_at: string;
      }
    | null;
  assignmentHistory: Array<{
    event_id: string;
    event_type: string;
    owner_name?: string | null;
    performed_by?: string | null;
    note?: string | null;
    created_at: string;
  }>;
}

function installFetchMock() {
  const chapterState: Record<string, ChapterState> = {
    "ch-1": {
      proposalStatus: "proposed",
      actionStatus: "queued",
      assignment: null,
      recentDecision: null,
      assignmentHistory: [],
    },
    "ch-2": {
      proposalStatus: "none",
      actionStatus: "queued",
      assignment: {
        assignment_id: "assign-2",
        owner_name: "night-shift",
        assigned_by: "lead-reviewer",
        note: "Keep momentum",
        assigned_at: "2026-03-28T07:58:00Z",
      },
      recentDecision: null,
      assignmentHistory: [
        {
          event_id: "assign-2",
          event_type: "assigned",
          owner_name: "night-shift",
          performed_by: "lead-reviewer",
          note: "Keep momentum",
          created_at: "2026-03-28T07:58:00Z",
        },
      ],
    },
    "ch-3": {
      proposalStatus: "none",
      actionStatus: "completed",
      assignment: {
        assignment_id: "assign-3",
        owner_name: "night-shift",
        assigned_by: "lead-reviewer",
        note: "Ready for release pass",
        assigned_at: "2026-03-28T08:02:00Z",
      },
      recentDecision: null,
      assignmentHistory: [
        {
          event_id: "assign-3",
          event_type: "assigned",
          owner_name: "night-shift",
          performed_by: "lead-reviewer",
          note: "Ready for release pass",
          created_at: "2026-03-28T08:02:00Z",
        },
      ],
    },
  };

  const chapterBase = {
    "ch-1": {
      ordinal: 1,
      title_src: "Chapter One",
      packet_count: 4,
      translated_packet_count: 4,
      issue_type: "TERM_CONFLICT",
      action_type: "REBUILD_PACKET_THEN_RERUN",
      queue_priority: "immediate",
      queue_driver: "blocking issues",
      sla_status: "breached",
      owner_ready_reason: "Waiting for reviewer decision",
      regression_hint: "Blocking terminology drift keeps returning.",
      needs_immediate_attention: true,
      issue_count: 2,
      open_issue_count: 2,
      active_blocking_issue_count: 1,
      dominant_root_cause_layer: "memory",
      heat_score: 92,
      heat_level: "critical",
      queue_rank: 1,
      age_hours: 4,
      age_bucket: "4h",
    },
    "ch-2": {
      ordinal: 2,
      title_src: "Chapter Two",
      packet_count: 5,
      translated_packet_count: 5,
      issue_type: "STYLE_DRIFT",
      action_type: "REBUILD_CHAPTER_BRIEF",
      queue_priority: "high",
      queue_driver: "owner ready",
      sla_status: "due_soon",
      owner_ready_reason: "Context is stable enough for a named operator.",
      regression_hint: "Style drift is stable but unresolved.",
      needs_immediate_attention: false,
      issue_count: 1,
      open_issue_count: 1,
      active_blocking_issue_count: 0,
      dominant_root_cause_layer: "review",
      heat_score: 64,
      heat_level: "warm",
      queue_rank: 2,
      age_hours: 2,
      age_bucket: "2h",
    },
    "ch-3": {
      ordinal: 3,
      title_src: "Chapter Three",
      packet_count: 3,
      translated_packet_count: 3,
      issue_type: "EXPORT_RECOVERY",
      action_type: "REEXPORT_ONLY",
      queue_priority: "medium",
      queue_driver: "release ready",
      sla_status: "on_track",
      owner_ready_reason: "Everything is green enough for a final release pass.",
      regression_hint: "No fresh blockers remain; only final release confirmation is left.",
      needs_immediate_attention: false,
      issue_count: 0,
      open_issue_count: 0,
      active_blocking_issue_count: 0,
      dominant_root_cause_layer: "export",
      heat_score: 38,
      heat_level: "cool",
      queue_rank: 3,
      age_hours: 1,
      age_bucket: "1h",
    },
  } as const;

  function buildQueueEntry(chapterId: keyof typeof chapterBase) {
    const base = chapterBase[chapterId];
    const state = chapterState[chapterId];
    const pendingProposalCount = state.proposalStatus === "proposed" ? 1 : 0;
    return {
      chapter_id: chapterId,
      ordinal: base.ordinal,
      title_src: base.title_src,
      chapter_status: "review_required",
      issue_count: base.issue_count,
      open_issue_count: base.open_issue_count,
      triaged_issue_count: 0,
      blocking_issue_count: base.active_blocking_issue_count,
      active_blocking_issue_count: base.active_blocking_issue_count,
      issue_family_count: base.issue_count > 0 ? 1 : 0,
      dominant_issue_type: base.issue_type,
      dominant_root_cause_layer: base.dominant_root_cause_layer,
      dominant_issue_count: base.issue_count > 0 ? 1 : 0,
      latest_issue_at: "2026-03-28T08:05:00Z",
      heat_score: base.heat_score,
      heat_level: base.heat_level,
      queue_rank: base.queue_rank,
      queue_priority: base.queue_priority,
      queue_driver: base.queue_driver,
      needs_immediate_attention: base.needs_immediate_attention,
      oldest_active_issue_at: "2026-03-28T06:00:00Z",
      age_hours: base.age_hours,
      age_bucket: base.age_bucket,
      sla_target_hours: 6,
      sla_status: base.sla_status,
      owner_ready: true,
      owner_ready_reason: base.owner_ready_reason,
      is_assigned: Boolean(state.assignment),
      assigned_owner_name: state.assignment?.owner_name ?? null,
      assigned_at: state.assignment?.assigned_at ?? null,
      latest_activity_bucket_start: "2026-03-28T08:00:00Z",
      latest_created_issue_count: chapterId === "ch-1" ? 1 : 0,
      latest_resolved_issue_count: 0,
      latest_net_issue_delta: chapterId === "ch-1" ? 1 : 0,
      regression_hint: base.regression_hint,
      flapping_hint: false,
      memory_proposals: {
        proposal_count: state.proposalStatus === "none" ? 0 : 1,
        pending_proposal_count: pendingProposalCount,
        counts_by_status:
          state.proposalStatus === "proposed"
            ? { proposed: 1, committed: 0, rejected: 0 }
            : state.proposalStatus === "committed"
              ? { proposed: 0, committed: 1, rejected: 0 }
              : state.proposalStatus === "rejected"
                ? { proposed: 0, committed: 0, rejected: 1 }
                : { proposed: 0, committed: 0, rejected: 0 },
        latest_proposal_updated_at: "2026-03-28T08:05:00Z",
        active_snapshot_version: state.proposalStatus === "committed" ? 4 : 3,
      },
    };
  }

  function buildOwnerWorkloadSummary(entries: ReturnType<typeof buildQueueEntry>[]) {
    const grouped = new Map<string, ReturnType<typeof buildQueueEntry>[]>();
    for (const entry of entries) {
      if (!entry.assigned_owner_name) {
        continue;
      }
      const current = grouped.get(entry.assigned_owner_name) ?? [];
      current.push(entry);
      grouped.set(entry.assigned_owner_name, current);
    }
    return Array.from(grouped.entries()).map(([owner_name, ownerEntries]) => ({
      owner_name,
      assigned_chapter_count: ownerEntries.length,
      immediate_count: ownerEntries.filter((entry) => String(entry.queue_priority) === "immediate").length,
      high_count: ownerEntries.filter((entry) => String(entry.queue_priority) === "high").length,
      medium_count: ownerEntries.filter((entry) => String(entry.queue_priority) === "medium").length,
      breached_count: ownerEntries.filter((entry) => String(entry.sla_status) === "breached").length,
      due_soon_count: ownerEntries.filter((entry) => String(entry.sla_status) === "due_soon").length,
      on_track_count: ownerEntries.filter((entry) => String(entry.sla_status) === "on_track").length,
      owner_ready_count: ownerEntries.filter((entry) => entry.owner_ready).length,
      total_open_issue_count: ownerEntries.reduce((sum, entry) => sum + entry.open_issue_count, 0),
      total_active_blocking_issue_count: ownerEntries.reduce(
        (sum, entry) => sum + entry.active_blocking_issue_count,
        0
      ),
      oldest_active_issue_at: "2026-03-28T06:00:00Z",
      latest_issue_at: "2026-03-28T08:05:00Z",
    }));
  }

  function buildTimeline(chapterId: keyof typeof chapterBase) {
    const base = chapterBase[chapterId];
    const state = chapterState[chapterId];
    const assignmentEvents = state.assignmentHistory.map((entry) => ({
      event_id: entry.event_id,
      source_kind: "assignment",
      event_kind: entry.event_type === "cleared" ? "cleared" : "assigned",
      created_at: entry.created_at,
      actor_name: entry.performed_by ?? null,
      note: entry.note ?? null,
      owner_name: entry.owner_name ?? null,
    }));
    const memoryEvents = state.recentDecision
      ? [
          {
            event_id: state.recentDecision.proposal_id,
            source_kind: "memory_proposal",
            event_kind: state.recentDecision.decision,
            created_at: state.recentDecision.created_at,
            actor_name: state.recentDecision.actor_id,
            note: state.recentDecision.note ?? null,
            proposal_id: state.recentDecision.proposal_id,
            decision: state.recentDecision.decision,
          },
        ]
      : [];
    return [
      {
        event_id: `act-${chapterId}`,
        source_kind: "action",
        event_kind: "issue_action",
        created_at: "2026-03-28T08:01:00Z",
        actor_name: "system",
        issue_type: base.issue_type,
        action_type: base.action_type,
        scope_type: chapterId === "ch-1" ? "packet" : "chapter",
        scope_id: chapterId === "ch-1" ? "pkt-1" : chapterId,
        status: "queued",
      },
      ...assignmentEvents,
      ...memoryEvents,
    ].sort((left, right) => right.created_at.localeCompare(left.created_at));
  }

  function buildDetail(chapterId: keyof typeof chapterBase) {
    const base = chapterBase[chapterId];
    const state = chapterState[chapterId];
    return {
      document_id: "doc-123",
      chapter_id: chapterId,
      ordinal: base.ordinal,
      title_src: base.title_src,
      chapter_status: "review_required",
      packet_count: base.packet_count,
      translated_packet_count: base.translated_packet_count,
      current_issue_count: base.issue_count,
      current_open_issue_count: base.open_issue_count,
      current_triaged_issue_count: 0,
      current_active_blocking_issue_count: base.active_blocking_issue_count,
      assignment: state.assignment
        ? {
            assignment_id: state.assignment.assignment_id,
            document_id: "doc-123",
            chapter_id: chapterId,
            owner_name: state.assignment.owner_name,
            assigned_by: state.assignment.assigned_by,
            note: state.assignment.note ?? null,
            assigned_at: state.assignment.assigned_at,
            created_at: state.assignment.assigned_at,
            updated_at: state.assignment.assigned_at,
          }
        : null,
      queue_entry: buildQueueEntry(chapterId),
      recent_issues:
        base.issue_count > 0
          ? [
              {
                issue_id: `iss-${chapterId}`,
                issue_type: base.issue_type,
                root_cause_layer: base.dominant_root_cause_layer,
                severity: chapterId === "ch-1" ? "high" : "medium",
                status: "open",
                blocking: base.active_blocking_issue_count > 0,
                detector: "qa",
                suggested_action: base.action_type,
                created_at: "2026-03-28T08:00:00Z",
                updated_at: "2026-03-28T08:05:00Z",
              },
            ]
          : [],
      recent_actions: [
        {
          action_id: `act-${chapterId}`,
          issue_id: `iss-${chapterId}`,
          issue_type: base.issue_type,
          action_type: base.action_type,
          scope_type: chapterId === "ch-1" ? "packet" : "chapter",
          scope_id: chapterId === "ch-1" ? "pkt-1" : chapterId,
          status: state.actionStatus,
          created_by: "system",
          created_at: "2026-03-28T08:01:00Z",
          updated_at: "2026-03-28T08:01:00Z",
        },
      ],
      assignment_history: state.assignmentHistory,
      memory_proposals: {
        proposal_count: state.proposalStatus === "none" ? 0 : 1,
        pending_proposal_count: state.proposalStatus === "proposed" ? 1 : 0,
        counts_by_status:
          state.proposalStatus === "proposed"
            ? { proposed: 1, committed: 0, rejected: 0 }
            : state.proposalStatus === "committed"
              ? { proposed: 0, committed: 1, rejected: 0 }
              : state.proposalStatus === "rejected"
                ? { proposed: 0, committed: 0, rejected: 1 }
                : { proposed: 0, committed: 0, rejected: 0 },
        latest_proposal_updated_at: "2026-03-28T08:05:00Z",
        active_snapshot_version: state.proposalStatus === "committed" ? 4 : 3,
        pending_proposals:
          state.proposalStatus === "proposed"
            ? [
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
              ]
            : [],
        recent_decisions: state.recentDecision ? [state.recentDecision] : [],
      },
      timeline: buildTimeline(chapterId),
    };
  }

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
      {
        chapter_id: "ch-2",
        ordinal: 2,
        title_src: "Chapter Two",
        status: "review_required",
        sentence_count: 140,
        packet_count: 5,
        open_issue_count: 1,
        bilingual_export_ready: false,
      },
      {
        chapter_id: "ch-3",
        ordinal: 3,
        title_src: "Chapter Three",
        status: "review_required",
        sentence_count: 96,
        packet_count: 3,
        open_issue_count: 0,
        bilingual_export_ready: true,
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

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl = String(input);
    const url = new URL(rawUrl, "http://localhost");
    const path = url.pathname;

    if (path.endsWith("/v1/health")) {
      return jsonResponse({ status: "ok" });
    }
    if (path.endsWith("/v1/documents/history")) {
      return jsonResponse({
        total_count: 0,
        record_count: 0,
        offset: 0,
        limit: 12,
        has_more: false,
        entries: [],
      });
    }
    if (path.endsWith("/v1/documents/doc-123/exports")) {
      return jsonResponse({
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
      });
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/worklist")) {
      const queuePriority = url.searchParams.get("queue_priority");
      const assigned = url.searchParams.get("assigned");
      const assignedOwnerName = url.searchParams.get("assigned_owner_name");
      const allEntries = [buildQueueEntry("ch-1"), buildQueueEntry("ch-2"), buildQueueEntry("ch-3")];
      const filteredEntries = allEntries.filter((entry) => {
        if (queuePriority && entry.queue_priority !== queuePriority) {
          return false;
        }
        if (assigned === "true" && !entry.is_assigned) {
          return false;
        }
        if (assigned === "false" && entry.is_assigned) {
          return false;
        }
        if (assignedOwnerName && entry.assigned_owner_name !== assignedOwnerName) {
          return false;
        }
        return true;
      });
      return jsonResponse({
        document_id: "doc-123",
        worklist_count: allEntries.length,
        filtered_worklist_count: filteredEntries.length,
        entry_count: filteredEntries.length,
        offset: 0,
        limit: 50,
        has_more: false,
        queue_priority_counts: { immediate: 1, high: 1, medium: 1 },
        sla_status_counts: { breached: 1, due_soon: 1, on_track: 1 },
        immediate_attention_count: 1,
        owner_ready_count: 3,
        assigned_count: Object.values(chapterState).filter((entry) => entry.assignment).length,
        applied_queue_priority_filter: queuePriority,
        applied_assigned_filter:
          assigned === null ? null : assigned === "true" ? true : assigned === "false" ? false : null,
        applied_assigned_owner_filter: assignedOwnerName,
        owner_workload_summary: buildOwnerWorkloadSummary(allEntries),
        owner_workload_highlights: {},
        highlights: {},
        entries: filteredEntries,
      });
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-1/worklist")) {
      return jsonResponse(buildDetail("ch-1"));
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-2/worklist")) {
      return jsonResponse(buildDetail("ch-2"));
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-3/worklist")) {
      return jsonResponse(buildDetail("ch-3"));
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-1/memory-proposals/prop-123/approve")) {
      chapterState["ch-1"].proposalStatus = "committed";
      chapterState["ch-1"].recentDecision = {
        proposal_id: "prop-123",
        decision: "approved",
        actor_type: "human",
        actor_id: "reviewer-ui",
        note: "Looks good",
        created_at: "2026-03-28T08:08:00Z",
      };
      return jsonResponse({
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
          last_decision: chapterState["ch-1"].recentDecision,
        },
      });
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-1/memory-proposals/prop-123/reject")) {
      chapterState["ch-1"].proposalStatus = "rejected";
      chapterState["ch-1"].recentDecision = {
        proposal_id: "prop-123",
        decision: "rejected",
        actor_type: "human",
        actor_id: "reviewer-ui",
        note: "Need another pass",
        created_at: "2026-03-28T08:08:00Z",
      };
      return jsonResponse({
        document_id: "doc-123",
        chapter_id: "ch-1",
        decision: "rejected",
        committed_snapshot_id: null,
        committed_snapshot_version: null,
        proposal: {
          proposal_id: "prop-123",
          packet_id: "pkt-1",
          translation_run_id: "run-pkt-1",
          status: "rejected",
          base_snapshot_version: 3,
          committed_snapshot_id: null,
          created_at: "2026-03-28T08:05:00Z",
          updated_at: "2026-03-28T08:08:00Z",
          last_decision: chapterState["ch-1"].recentDecision,
        },
      });
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-1/worklist/assignment") && init?.method === "PUT") {
      const payload = JSON.parse(String(init.body)) as {
        owner_name: string;
        assigned_by: string;
        note?: string;
      };
      chapterState["ch-1"].assignment = {
        assignment_id: "assign-1",
        owner_name: payload.owner_name,
        assigned_by: payload.assigned_by,
        note: payload.note,
        assigned_at: "2026-03-28T08:10:00Z",
      };
      chapterState["ch-1"].assignmentHistory.unshift({
        event_id: "assign-1",
        event_type: "assigned",
        owner_name: payload.owner_name,
        performed_by: payload.assigned_by,
        note: payload.note ?? null,
        created_at: "2026-03-28T08:10:00Z",
      });
      return jsonResponse({
        assignment_id: "assign-1",
        document_id: "doc-123",
        chapter_id: "ch-1",
        owner_name: payload.owner_name,
        assigned_by: payload.assigned_by,
        note: payload.note ?? null,
        assigned_at: "2026-03-28T08:10:00Z",
        created_at: "2026-03-28T08:10:00Z",
        updated_at: "2026-03-28T08:10:00Z",
      });
    }
    if (path.endsWith("/v1/documents/doc-123/chapters/ch-3/worklist/assignment") && init?.method === "PUT") {
      const payload = JSON.parse(String(init.body)) as {
        owner_name: string;
        assigned_by: string;
        note?: string;
      };
      chapterState["ch-3"].assignment = {
        assignment_id: "assign-3b",
        owner_name: payload.owner_name,
        assigned_by: payload.assigned_by,
        note: payload.note,
        assigned_at: "2026-03-28T08:16:00Z",
      };
      chapterState["ch-3"].assignmentHistory.unshift({
        event_id: "assign-3b",
        event_type: "assigned",
        owner_name: payload.owner_name,
        performed_by: payload.assigned_by,
        note: payload.note ?? null,
        created_at: "2026-03-28T08:16:00Z",
      });
      return jsonResponse({
        assignment_id: "assign-3b",
        document_id: "doc-123",
        chapter_id: "ch-3",
        owner_name: payload.owner_name,
        assigned_by: payload.assigned_by,
        note: payload.note ?? null,
        assigned_at: "2026-03-28T08:16:00Z",
        created_at: "2026-03-28T08:16:00Z",
        updated_at: "2026-03-28T08:16:00Z",
      });
    }
    if (path.endsWith("/v1/actions/act-ch-1/execute") && init?.method === "POST") {
      chapterState["ch-1"].actionStatus = "completed";
      return jsonResponse({
        action_id: "act-ch-1",
        status: "completed",
        invalidation_count: 2,
        rerun_scope_type: "packet",
        rerun_scope_ids: ["pkt-1"],
        followup_executed: true,
        rebuild_applied: false,
        rebuilt_packet_ids: [],
        rebuilt_snapshot_ids: [],
        rerun_packet_ids: ["pkt-1"],
        rerun_translation_run_ids: ["run-pkt-2"],
        issue_resolved: true,
        recheck_issue_count: 0,
      });
    }
    if (path.endsWith("/v1/actions/act-ch-2/execute") && init?.method === "POST") {
      chapterState["ch-2"].actionStatus = "completed";
      return jsonResponse({
        action_id: "act-ch-2",
        status: "completed",
        invalidation_count: 1,
        rerun_scope_type: "chapter",
        rerun_scope_ids: ["ch-2"],
        followup_executed: true,
        rebuild_applied: false,
        rebuilt_packet_ids: [],
        rebuilt_snapshot_ids: [],
        rerun_packet_ids: [],
        rerun_translation_run_ids: ["run-ch-2"],
        issue_resolved: false,
        recheck_issue_count: 1,
      });
    }
    if (
      path.endsWith("/v1/documents/doc-123/chapters/ch-1/worklist/assignment/clear") &&
      init?.method === "POST"
    ) {
      const payload = JSON.parse(String(init.body)) as {
        cleared_by: string;
        note?: string;
      };
      chapterState["ch-1"].assignmentHistory.unshift({
        event_id: "assign-1-clear",
        event_type: "cleared",
        owner_name: null,
        performed_by: payload.cleared_by,
        note: payload.note ?? null,
        created_at: "2026-03-28T08:12:00Z",
      });
      chapterState["ch-1"].assignment = null;
      return jsonResponse({
        document_id: "doc-123",
        chapter_id: "ch-1",
        cleared: true,
        cleared_by: payload.cleared_by,
        note: payload.note ?? null,
        cleared_assignment_id: "assign-1",
      });
    }
    if (
      path.endsWith("/v1/documents/doc-123/chapters/ch-2/worklist/assignment/clear") &&
      init?.method === "POST"
    ) {
      const payload = JSON.parse(String(init.body)) as {
        cleared_by: string;
        note?: string;
      };
      chapterState["ch-2"].assignmentHistory.unshift({
        event_id: "assign-2-clear",
        event_type: "cleared",
        owner_name: null,
        performed_by: payload.cleared_by,
        note: payload.note ?? null,
        created_at: "2026-03-28T08:14:00Z",
      });
      chapterState["ch-2"].assignment = null;
      return jsonResponse({
        document_id: "doc-123",
        chapter_id: "ch-2",
        cleared: true,
        cleared_by: payload.cleared_by,
        note: payload.note ?? null,
        cleared_assignment_id: "assign-2",
      });
    }
    if (path.endsWith("/v1/documents/doc-123")) {
      return jsonResponse(documentPayload);
    }
    if (path.endsWith("/v1/runs/run-123/events")) {
      return jsonResponse({
        run_id: "run-123",
        event_count: 0,
        record_count: 0,
        offset: 0,
        limit: 8,
        has_more: false,
        entries: [],
      });
    }
    if (path.endsWith("/v1/runs/run-123")) {
      return jsonResponse(runPayload);
    }
    throw new Error(`Unhandled fetch: ${rawUrl}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.localStorage.clear();
});

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Workspace page", () => {
  it("can return from an opened document to the upload homepage", async () => {
    window.localStorage.setItem(STORAGE_KEY_DOCUMENT, "doc-123");
    installFetchMock();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByRole("button", { name: "回到首页上传新书" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "回到首页上传新书" }));

    expect(await screen.findByRole("heading", { name: "载入书稿" })).toBeInTheDocument();
    expect(screen.getByText("上传英文 PDF 或 EPUB，开始翻译")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "回到首页上传新书" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY_DOCUMENT)).toBeNull();
  });
});
