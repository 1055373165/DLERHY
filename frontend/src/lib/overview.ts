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
  };
  activity: { exporting: string[] };
  needs_you: NeedsYouItem[];
  quality: { open_issues: number; blocking: number };
  cost: { estimate_usd_range: [number, number] | null; max_charge_usd: number | null; prepaid: boolean; spent_usd: number };
}

export async function getOverview(documentId: string): Promise<BookOverview> {
  return requestJson<BookOverview>(`/documents/${encodeURIComponent(documentId)}/overview`);
}

export async function getInbox(): Promise<{ count: number; items: InboxItem[] }> {
  return requestJson<{ count: number; items: InboxItem[] }>("/inbox");
}

export function usd(amount: number): string {
  return amount > 0 && amount < 0.01 ? "<$0.01" : `$${amount.toFixed(2)}`;
}
