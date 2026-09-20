/** A book as a reader sees it, and what waits on the reader: GET /documents/{id}/overview, GET /inbox.
 * The server derives every state here (docs/specs/ui-ux-overhaul/SPEC.md, decision 3); the UI only renders it. */
import { requestJson } from "./api";

export type TranslationState =
  | "never_started"
  | "queued"
  | "running"
  | "paused"
  | "draining"
  | "succeeded"
  | "succeeded_with_warnings"
  | "failed"
  | "cancelled";

export interface OverviewStep {
  key: string;
  label: string;
  status: string;
  /** When the step last changed. */
  at?: string | null;
}

export interface NeedsYouItem {
  kind: "approval" | "paused";
  id: string;
  title: string;
  detail: string | null;
  action: { kind: "approve" | "resume" | "link"; target?: string; label?: string };
}

export interface InboxItem extends NeedsYouItem {
  document_id: string;
  document_title: string | null;
}

export interface BookOverview {
  document_id: string;
  title: string | null;
  source_type: string;
  source_url: string | null;
  headline: string;
  primary_action: { kind: "start" | "resume" | "retry" | "stop" | "read" | "handle" | "none"; label: string; run_id?: string };
  readable: { ready: boolean; formats: string[] };
  translation: {
    state: TranslationState;
    run_id: string | null;
    stop_reason: string | null;
    stop_reason_text: string | null;
    steps: OverviewStep[];
    progress: { done: number; total: number };
    fast_reading?: boolean;
    auto_started?: boolean;
    started_at?: string | null;
    finished_at?: string | null;
  };
  activity: { exporting: string[] };
  needs_you: NeedsYouItem[];
  quality: { open_issues: number; blocking: number };
  // null in lists of books (GET /overviews skips the estimate).
  cost: { estimate_usd_range: [number, number] | null; max_charge_usd: number | null; prepaid: boolean; spent_usd: number } | null;
}

export async function getOverview(documentId: string): Promise<BookOverview> {
  return requestJson<BookOverview>(`/documents/${encodeURIComponent(documentId)}/overview`);
}

/** A full translation that ended: ready to read, or failed. */
export interface InboxUpdate {
  kind: "readable" | "failed";
  run_id: string;
  document_id: string;
  document_title: string | null;
  text: string;
  at: string;
}

export interface Inbox {
  count: number;
  items: InboxItem[];
  updates?: InboxUpdate[];
}

export async function getInbox(since?: string): Promise<Inbox> {
  return requestJson<Inbox>(since ? `/inbox?since=${encodeURIComponent(since)}` : "/inbox");
}

/** Overviews of several books at once, without cost (for lists of books). */
export async function getOverviews(ids: string[]): Promise<BookOverview[]> {
  if (!ids.length) return [];
  const page = await requestJson<{ overviews: BookOverview[] }>(`/overviews?ids=${ids.map(encodeURIComponent).join(",")}`);
  return page.overviews;
}

export function usd(amount: number): string {
  return amount > 0 && amount < 0.01 ? "<$0.01" : `$${amount.toFixed(2)}`;
}
