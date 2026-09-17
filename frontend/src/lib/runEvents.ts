import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { runStreamUrl } from "./api";

// Mirrors book_agent.domain.event_kinds.EVENT_KINDS; tests/test_frontend_event_kinds.py keeps them in sync.
// The stream sends named SSE events, and EventSource has no wildcard listener.
export const RUN_EVENT_KINDS = [
  "run.created",
  "run.paused",
  "run.resumed",
  "run.cancelled",
  "run.completed",
  "chapter.started",
  "chapter.completed",
  "packet.built",
  "packet.leased",
  "packet.translated",
  "packet.failed",
  "translation.output.rejected",
  "translation.segment.edited",
  "document.reparsed",
  "llm.call.started",
  "llm.call.completed",
  "llm.call.failed",
  "glossary.injected",
  "glossary.resolved",
  "glossary.violation",
  "glossary.suppressed",
  "glossary.updated",
  "review.issue.opened",
  "review.issue.closed",
  "action.dispatched",
  "cost.budget.warning",
  "cost.budget.exceeded",
  "agent.trace.started",
  "agent.trace.completed",
  "agent.help.requested",
  "agent.turn.started",
  "agent.turn.finished",
  "agent.tool.called",
  "agent.tool.returned",
  "agent.approval.requested",
  "agent.approval.decided",
] as const;

// Queries whose data a run event can change.
const INVALIDATED_QUERY_PREFIXES = [
  "run",
  "run-events",
  "document",
  "document-exports",
  "chapter-worklist",
  "chapter-worklist-detail",
  "issues",
  "issue",
  "approvals",
  "agent-turns",
  "book-guide",
];

const INVALIDATE_DEBOUNCE_MS = 400;

export type RunStreamState = "idle" | "connecting" | "live" | "unavailable";

/**
 * Subscribe to a run's server-sent events while it is active and refresh the
 * affected queries when something happens. Returns "live" while connected so
 * callers can relax their polling; "unavailable" when the server cannot stream
 * (SQLite deployments answer 501) or the browser lacks EventSource, in which
 * case polling stays the source of updates.
 */
export function useRunEventStream(runId: string | null, active: boolean): RunStreamState {
  const client = useQueryClient();
  const [state, setState] = useState<RunStreamState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!runId || !active) {
      setState("idle");
      return undefined;
    }
    if (typeof window === "undefined" || typeof window.EventSource === "undefined") {
      setState("unavailable");
      return undefined;
    }
    setState("connecting");
    const source = new window.EventSource(runStreamUrl(runId));
    let opened = false;

    const scheduleInvalidate = () => {
      if (timer.current !== null) return;
      timer.current = setTimeout(() => {
        timer.current = null;
        for (const prefix of INVALIDATED_QUERY_PREFIXES) {
          void client.invalidateQueries({ queryKey: [prefix] });
        }
      }, INVALIDATE_DEBOUNCE_MS);
    };

    source.onopen = () => {
      opened = true;
      setState("live");
    };
    source.onerror = () => {
      // A stream that never opened is not supported here (e.g. 501 on SQLite):
      // stop reconnect attempts and let polling carry on.
      if (!opened || source.readyState === 2) {
        source.close();
        setState("unavailable");
      } else {
        setState("connecting");
      }
    };
    for (const kind of RUN_EVENT_KINDS) {
      source.addEventListener(kind, scheduleInvalidate);
    }
    return () => {
      for (const kind of RUN_EVENT_KINDS) {
        source.removeEventListener(kind, scheduleInvalidate);
      }
      source.close();
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [runId, active, client]);

  return state;
}

/** Polling interval for run-bound queries: fast without a live stream, a slow safety net with one. */
export function runPollInterval(active: boolean, stream: RunStreamState): number | false {
  if (!active) return false;
  return stream === "live" ? 15_000 : 2_500;
}
