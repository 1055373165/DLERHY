/** The pilot's layered QA (L1–L7): rule checks on the pilot's translation (GET/POST /documents/{id}/pilot-quality). */
import { requestJson } from "./api";

export type PilotQualityStatus = "passed" | "warnings" | "blocking" | "error";
export type PilotQualitySeverity = "blocking" | "major" | "minor";
export type SeverityCounts = Record<PilotQualitySeverity, number>;

export interface PilotQualityFinding {
  check_id: string;
  layer: string;
  severity: PilotQualitySeverity;
  /** Stable across runs: the repair agent's handle on this problem. */
  key: string;
  target: { kind: string; id: string };
  repair: string | null;
  location: { chapter_id?: string; packet_id?: string; block_id?: string; sentence_id?: string; page?: number };
  /** In Chinese, for the reader. */
  message: string;
  evidence: { source?: string; target?: string; [key: string]: unknown };
}

export interface PilotQualityCheck {
  check_id: string;
  title: string;
  severity: PilotQualitySeverity;
  count: number;
  passed: boolean;
}

export interface PilotQualityLayer {
  layer: string;
  name: string;
  status: Exclude<PilotQualityStatus, "error">;
  passed: boolean;
  counts: SeverityCounts;
  checks: PilotQualityCheck[];
  /** Worst first. */
  findings: PilotQualityFinding[];
}

export interface PilotQualityReport {
  report_id: string;
  document_id: string;
  run_id: string | null;
  trigger: string;
  status: PilotQualityStatus;
  counts: SeverityCounts;
  chapters: { chapter_id: string; ordinal: number; title: string | null }[];
  layers: PilotQualityLayer[];
  export_source: string;
  error: string | null;
  duration_ms: number;
  checked_at: string | null;
}

/** What the book overview's pilot block carries: the verdict and each layer's counts. */
export interface PilotQualitySummary {
  report_id: string;
  status: PilotQualityStatus;
  checked_at: string | null;
  counts: SeverityCounts;
  layers: { layer: string; name: string; status: PilotQualityLayer["status"]; counts: SeverityCounts }[];
}

export async function getPilotQuality(documentId: string): Promise<PilotQualityReport> {
  return requestJson<PilotQualityReport>(`/documents/${encodeURIComponent(documentId)}/pilot-quality`);
}

export async function runPilotQuality(documentId: string): Promise<PilotQualityReport> {
  return requestJson<PilotQualityReport>(`/documents/${encodeURIComponent(documentId)}/pilot-quality`, { method: "POST" });
}

export const SEVERITY_LABEL: Record<PilotQualitySeverity, string> = { blocking: "严重", major: "一般", minor: "轻微" };

/** A layer's verdict in words. */
export function layerVerdict(status: PilotQualityLayer["status"], counts: SeverityCounts): string {
  if (status === "blocking") return `${counts.blocking} 处严重`;
  if (status === "warnings") return `${counts.major} 处一般`;
  return counts.minor ? `通过（${counts.minor} 处轻微）` : "通过";
}

/** The headline of a report or its summary. */
export function qualityHeadline(summary: Pick<PilotQualitySummary, "status" | "counts" | "layers">): string {
  const layers = summary.layers.filter((layer) => layer.status === "blocking").map((layer) => layer.name);
  if (summary.status === "error") return "这次试译质检没能完成，可以重新质检。";
  if (summary.status === "blocking") {
    return `试译质检发现 ${summary.counts.blocking} 处严重问题（${layers.join("、")}）。试译照常给你看，问题都列在这里。`;
  }
  if (summary.status === "warnings") {
    return `没有严重问题；有 ${summary.counts.major} 处一般问题，不影响阅读。`;
  }
  return `七层检查都通过了${summary.counts.minor ? `，只有 ${summary.counts.minor} 处轻微瑕疵` : ""}。`;
}
