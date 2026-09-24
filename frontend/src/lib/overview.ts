/** A book as a reader sees it, and what waits on the reader: GET /documents/{id}/overview, GET /inbox.
 * The server derives every state here (docs/specs/ui-ux-overhaul/SPEC.md, decision 3); the UI only renders it. */
import { requestJson } from "./api";
import type { ParseHealthSummary } from "./parseHealth";
import type { PilotQualitySummary } from "./pilotQuality";

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

/** The free two-chapter pilot (services/pilot.py): offered or not, where it stands, what it left. */
export type PilotState =
  | "not_started"
  | "unavailable"
  | "queued"
  | "running"
  | "draining"
  | "paused"
  | "failed"
  | "cancelled"
  | "ready"
  | "book_started"
  | "skipped";

export interface PilotProblems {
  open: number;
  blocking: number;
  /** Issue types (codes; issueTypeCopy gives the words), blocking first. */
  by_type: { type: string; count: number; blocking: number }[];
}

export interface PilotOverview {
  available: boolean;
  unavailable_reason: string | null;
  /** Why this book has no pilot, in words (a web page, a short book). */
  unavailable_text: string | null;
  state: PilotState;
  run_id: string | null;
  chapters: { chapter_id: string; ordinal: number; title: string | null }[];
  /** The pilot's reading copy exists. */
  readable: boolean;
  /** null until the pilot has run. */
  problems: PilotProblems | null;
  /** What the pilot's model calls cost us; the reader is not charged. */
  cost_usd: number;
  finished_at: string | null;
  /** The pilot's layered QA (L1–L7); null before it ran, absent from older servers. */
  quality?: PilotQualitySummary | null;
}

export interface BookOverview {
  document_id: string;
  title: string | null;
  source_type: string;
  source_url: string | null;
  headline: string;
  primary_action: {
    kind: "start" | "pilot" | "start_book" | "resume" | "retry" | "stop" | "read" | "handle" | "none";
    label: string;
    run_id?: string;
  };
  readable: { ready: boolean; formats: string[] };
  translation: {
    state: TranslationState;
    /** Which run this is: the free pilot's, or the whole book's; null before either. */
    scope?: "pilot" | "book" | null;
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
  /** Absent from servers before the pilot existed. */
  pilot?: PilotOverview;
  activity: { exporting: string[] };
  needs_you: NeedsYouItem[];
  quality: { open_issues: number; blocking: number };
  /** The latest parse health report, summarised; null before the first report. */
  parse_health?: ParseHealthSummary | null;
  // null in lists of books (GET /overviews skips the estimate).
  cost: { estimate_usd_range: [number, number] | null; max_charge_usd: number | null; prepaid: boolean; spent_usd: number } | null;
}

export async function getOverview(documentId: string): Promise<BookOverview> {
  return requestJson<BookOverview>(`/documents/${encodeURIComponent(documentId)}/overview`);
}

/** A translation that ended: ready to read, a free pilot ready to review, or failed. */
export interface InboxUpdate {
  kind: "readable" | "pilot_ready" | "failed";
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
