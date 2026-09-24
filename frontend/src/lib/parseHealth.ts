/** The parse health report: rule checks on a parsed book, run after import (GET/POST /documents/{id}/parse-health). */
import { requestJson } from "./api";

export type ParseHealthStatus = "passed" | "warnings" | "blocking";
export type ParseHealthSeverity = "blocking" | "major" | "minor";

export interface ParseHealthSample {
  text: string;
  page?: number;
  block_id?: string;
  chapter_id?: string;
  detail?: string;
}

export interface ParseHealthCheck {
  name: string;
  severity: ParseHealthSeverity;
  count: number;
  denominator: number;
  allowed: number;
  passed: boolean;
  samples: ParseHealthSample[];
  breakdown: Record<string, number>;
  threshold_note: string | null;
}

export interface ParseHealthReport {
  report_id: string;
  document_id: string;
  trigger: string;
  status: ParseHealthStatus;
  failed: Record<ParseHealthSeverity, number>;
  checks: ParseHealthCheck[];
  duration_ms: number;
  checked_at: string | null;
}

/** What the book overview carries: enough for a banner note and the tab's headline. */
export interface ParseHealthSummary {
  status: ParseHealthStatus;
  needs_attention: boolean;
  checked_at: string | null;
  failed: { name: string; severity: ParseHealthSeverity; count: number; allowed: number }[];
  check_count: number;
}

export async function getParseHealth(documentId: string): Promise<ParseHealthReport> {
  return requestJson<ParseHealthReport>(`/documents/${encodeURIComponent(documentId)}/parse-health`);
}

export async function runParseHealth(documentId: string): Promise<ParseHealthReport> {
  return requestJson<ParseHealthReport>(`/documents/${encodeURIComponent(documentId)}/parse-health`, { method: "POST" });
}
