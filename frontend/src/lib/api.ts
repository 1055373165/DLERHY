import type * as Generated from "./api-types.gen";

export type DocumentStatus = Generated.DocumentStatus;

export type RunStatus =
  | "pending"
  | "queued"
  | "running"
  | "draining"
  | "succeeded"
  | "partial"
  | "failed"
  | "paused"
  | "cancelled"
  | string;

export type HealthResponse = Generated.HealthResponse;

export interface ChapterSummary {
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  status: string;
  sentence_count: number;
  packet_count: number;
  open_issue_count: number;
  bilingual_export_ready: boolean;
}

export interface DocumentSummary {
  document_id: string;
  source_type: string;
  status: DocumentStatus;
  title?: string | null;
  title_src?: string | null;
  title_tgt?: string | null;
  author?: string | null;
  chapter_count: number;
  block_count: number;
  sentence_count: number;
  packet_count: number;
  open_issue_count: number;
  merged_export_ready: boolean;
  latest_merged_export_at?: string | null;
  chapter_bilingual_export_count: number;
  latest_run_id?: string | null;
  latest_run_status?: RunStatus | null;
  latest_run_current_stage?: string | null;
  latest_run_updated_at?: string | null;
  source_path?: string | null;
  chapters: ChapterSummary[];
}

export interface DocumentHistoryEntry {
  document_id: string;
  source_type: string;
  status: DocumentStatus;
  title?: string | null;
  title_src?: string | null;
  title_tgt?: string | null;
  author?: string | null;
  source_path?: string | null;
  created_at: string;
  updated_at: string;
  chapter_count: number;
  sentence_count: number;
  packet_count: number;
  merged_export_ready: boolean;
  latest_merged_export_at?: string | null;
  chapter_bilingual_export_count: number;
  latest_run_id?: string | null;
  latest_run_status?: RunStatus | null;
  latest_run_current_stage?: string | null;
  latest_run_completed_work_item_count?: number | null;
  latest_run_total_work_item_count?: number | null;
}

export interface DocumentHistoryPage {
  total_count: number;
  record_count: number;
  offset: number;
  limit?: number | null;
  has_more: boolean;
  entries: DocumentHistoryEntry[];
}

export interface RunStageDetail {
  status?: RunStatus;
  updated_at?: string | null;
  error_message?: string | null;
  total_issue_count?: number | null;
  total_action_count?: number | null;
  chapter_export_count?: number | null;
  total_packet_count?: number | null;
  examined_chapter_count?: number | null;
  skipped_chapter_count?: number | null;
  total_chapter_count?: number | null;
  skipped_chapters?: Array<{
    chapter_id: string;
    reason: string;
    pending_packet_count: number;
    failed_packet_count: number;
  }> | null;
}

export interface RunPipelineDetail {
  current_stage?: string | null;
  stages?: Record<string, RunStageDetail>;
}

export interface RunControlCounters {
  completed_work_item_count?: number | null;
}

export interface RunWorkItemSummary {
  total_count: number;
  status_counts: Record<string, number>;
  stage_counts: Record<string, number>;
}

export interface RunLeaseSummary {
  total_count: number;
  status_counts: Record<string, number>;
  latest_heartbeat_at?: string | null;
}

export type RunEventSummary = Generated.RunEventSummaryResponse;

export interface DocumentRunSummary {
  run_id: string;
  document_id: string;
  run_type: string;
  status: RunStatus;
  backend?: string | null;
  model_name?: string | null;
  requested_by?: string | null;
  priority: number;
  resume_from_run_id?: string | null;
  stop_reason?: string | null;
  status_detail_json: {
    pipeline?: RunPipelineDetail;
    control_counters?: RunControlCounters;
    [key: string]: unknown;
  };
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  work_items: RunWorkItemSummary;
  worker_leases: RunLeaseSummary;
  events: RunEventSummary;
}

export interface RunAuditEvent {
  event_id: string;
  run_id: string;
  work_item_id?: string | null;
  event_type: string;
  actor_type: string;
  actor_id?: string | null;
  payload_json: Record<string, unknown>;
  created_at: string;
}

export interface RunAuditEventPage {
  run_id: string;
  event_count: number;
  record_count: number;
  offset: number;
  limit?: number | null;
  has_more: boolean;
  entries: RunAuditEvent[];
}

export type TranslationUsageSummary = Generated.TranslationUsageSummaryResponse;

export type IssueHotspotEntry = Generated.IssueHotspotEntryResponse;

export interface IssueChapterHighlightEntry {
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  issue_count: number;
  open_issue_count: number;
  blocking_issue_count: number;
}

export type IssueChapterHighlights = Generated.IssueChapterHighlightsResponse;

export type ExportRecordSummary = Generated.ExportRecordSummaryResponse;

export interface DocumentExportDashboard {
  document_id: string;
  export_count: number;
  successful_export_count: number;
  filtered_export_count: number;
  record_count: number;
  offset: number;
  limit?: number | null;
  has_more: boolean;
  latest_export_at?: string | null;
  export_counts_by_type: Record<string, number>;
  latest_export_ids_by_type: Record<string, string>;
  translation_usage_summary?: TranslationUsageSummary | null;
  issue_hotspots: IssueHotspotEntry[];
  issue_chapter_highlights: IssueChapterHighlights;
  records: ExportRecordSummary[];
}

export type ChapterMemoryProposalDecisionAudit = Generated.ChapterMemoryProposalDecisionAuditResponse;

export type ChapterMemoryProposal = Generated.ChapterMemoryProposalResponse;

export interface ChapterMemoryProposalSurface {
  proposal_count: number;
  pending_proposal_count: number;
  counts_by_status: Record<string, number>;
  latest_proposal_updated_at?: string | null;
  active_snapshot_version?: number | null;
  pending_proposals: ChapterMemoryProposal[];
  recent_decisions: ChapterMemoryProposalDecisionAudit[];
}

export type ChapterWorklistTimelineEntry = Generated.ChapterWorklistTimelineEntryResponse;

export interface ChapterMemoryProposalQueueSummary {
  proposal_count: number;
  pending_proposal_count: number;
  counts_by_status: Record<string, number>;
  latest_proposal_updated_at?: string | null;
  active_snapshot_version?: number | null;
}

export interface IssueChapterQueueEntry {
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  chapter_status: string;
  issue_count: number;
  open_issue_count: number;
  triaged_issue_count: number;
  blocking_issue_count: number;
  active_blocking_issue_count: number;
  issue_family_count: number;
  dominant_issue_type?: string | null;
  dominant_root_cause_layer?: string | null;
  dominant_issue_count: number;
  latest_issue_at?: string | null;
  heat_score: number;
  heat_level: string;
  queue_rank: number;
  queue_priority: string;
  queue_driver: string;
  needs_immediate_attention: boolean;
  oldest_active_issue_at?: string | null;
  age_hours?: number | null;
  age_bucket: string;
  sla_target_hours?: number | null;
  sla_status: string;
  owner_ready: boolean;
  owner_ready_reason: string;
  is_assigned: boolean;
  assigned_owner_name?: string | null;
  assigned_at?: string | null;
  latest_activity_bucket_start?: string | null;
  latest_created_issue_count: number;
  latest_resolved_issue_count: number;
  latest_net_issue_delta: number;
  regression_hint: string;
  flapping_hint: boolean;
  memory_proposals: ChapterMemoryProposalQueueSummary;
}

export interface ChapterOwnerWorkload {
  owner_name: string;
  assigned_chapter_count: number;
  immediate_count: number;
  high_count: number;
  medium_count: number;
  breached_count: number;
  due_soon_count: number;
  on_track_count: number;
  owner_ready_count: number;
  total_open_issue_count: number;
  total_active_blocking_issue_count: number;
  oldest_active_issue_at?: string | null;
  latest_issue_at?: string | null;
}

export interface DocumentChapterWorklist {
  document_id: string;
  worklist_count: number;
  filtered_worklist_count: number;
  entry_count: number;
  offset: number;
  limit?: number | null;
  has_more: boolean;
  applied_queue_priority_filter?: string | null;
  applied_sla_status_filter?: string | null;
  applied_owner_ready_filter?: boolean | null;
  applied_needs_immediate_attention_filter?: boolean | null;
  applied_assigned_filter?: boolean | null;
  applied_assigned_owner_filter?: string | null;
  queue_priority_counts: Record<string, number>;
  sla_status_counts: Record<string, number>;
  immediate_attention_count: number;
  owner_ready_count: number;
  assigned_count: number;
  owner_workload_summary: ChapterOwnerWorkload[];
  owner_workload_highlights: Record<string, ChapterOwnerWorkload | null>;
  highlights: Record<string, IssueChapterQueueEntry | null>;
  entries: IssueChapterQueueEntry[];
}

export interface DocumentChapterWorklistFilters {
  limit?: number;
  offset?: number;
  queuePriority?: "immediate" | "high" | "medium";
  assigned?: boolean;
  ownerReady?: boolean;
  assignedOwnerName?: string;
}

export type ChapterWorklistAssignment = Generated.ChapterWorklistAssignmentResponse;

export interface ChapterWorklistAssignmentRequest {
  owner_name: string;
  assigned_by: string;
  note?: string;
}

export interface ChapterWorklistAssignmentClearRequest {
  cleared_by: string;
  note?: string;
}

export type ChapterWorklistAssignmentClearResponse = Generated.ChapterWorklistAssignmentClearResponse;

export type ChapterWorklistIssue = Generated.ChapterWorklistIssueResponse;

export type ChapterWorklistAction = Generated.ChapterWorklistActionResponse;

export interface ExecuteActionResponse {
  action_id: string;
  status: string;
  invalidation_count: number;
  rerun_scope_type: string;
  rerun_scope_ids: string[];
  followup_executed: boolean;
  rebuild_applied: boolean;
  rebuilt_packet_ids: string[];
  rebuilt_snapshot_ids: string[];
  chapter_brief_version?: number | null;
  termbase_version?: number | null;
  entity_snapshot_version?: number | null;
  rerun_packet_ids: string[];
  rerun_translation_run_ids: string[];
  issue_resolved?: boolean | null;
  recheck_issue_count?: number | null;
}

export type ChapterWorklistAssignmentHistoryEntry = Generated.ChapterWorklistAssignmentHistoryEntryResponse;

export interface DocumentChapterWorklistDetail {
  document_id: string;
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  chapter_status: string;
  packet_count: number;
  translated_packet_count: number;
  current_issue_count: number;
  current_open_issue_count: number;
  current_triaged_issue_count: number;
  current_active_blocking_issue_count: number;
  assignment?: ChapterWorklistAssignment | null;
  queue_entry?: IssueChapterQueueEntry | null;
  recent_issues: ChapterWorklistIssue[];
  recent_actions: ChapterWorklistAction[];
  assignment_history: ChapterWorklistAssignmentHistoryEntry[];
  memory_proposals: ChapterMemoryProposalSurface;
  timeline: ChapterWorklistTimelineEntry[];
}

export interface ChapterMemoryProposalDecisionPayload {
  actor_name?: string;
  note?: string;
}

export type ChapterMemoryProposalDecisionResponse = Generated.ChapterMemoryProposalDecisionResponse;

export interface HistoryFilters {
  query?: string;
  status?: string;
  latest_run_status?: string;
  merged_export_ready?: "true" | "false" | "";
  limit?: number;
  offset?: number;
}

export interface RunControlPayload {
  actor_id: string;
  note?: string;
  detail_json?: Record<string, unknown>;
}

export type Approval = Generated.ApprovalResponse;
export type ApprovalDecision = Generated.ApprovalDecisionRequest;
export type BookGuide = Generated.BookGuideResponse;
export type Decision = Generated.DecisionResponse;
export type AgentTurn = Generated.AgentTurnResponse;
export type Issue = Generated.IssueResponse;
export type IssueDetail = Generated.IssueDetailResponse;
export type IssueList = Generated.IssueListResponse;
export type IssueDecision = Generated.IssueDecisionRequest;
export type IssueTransition = "triage" | "wontfix" | "resolve" | "reopen";
export type OrgBudget = Generated.OrgBudgetResponse;
export type Provider = Generated.ProviderCredentialRead;
export type CostEstimate = Generated.CostEstimateResponse;
export type ProviderCreate = Generated.ProviderCredentialCreate;
export type ProviderUpdate = Generated.ProviderCredentialUpdate;
export type ProviderTestResult = Generated.ProviderTestResult;
export type StructureEditRequest = Generated.StructureEditRequest;
export type StructureEdit = Generated.StructureEditResponse;
export type StructureEditList = Generated.StructureEditListResponse;

export interface IssueFilter {
  status?: "active" | "all" | "open" | "triaged" | "resolved" | "wontfix";
  blocking?: boolean;
  detector?: "rule" | "model" | "human";
  issueType?: string;
  chapterId?: string;
  offset?: number;
  limit?: number;
}

declare global {
  interface Window {
    __BOOK_AGENT_CONFIG__?: { apiBaseUrl?: string };
  }
}

// Runtime config (served by the API in production) wins over the build-time value.
const RUNTIME_API_BASE_URL =
  typeof window !== "undefined" ? window.__BOOK_AGENT_CONFIG__?.apiBaseUrl : undefined;
const API_BASE_URL = (RUNTIME_API_BASE_URL ?? import.meta.env.VITE_API_BASE_URL ?? "/v1").replace(/\/$/, "");

export const SERVICE_LINKS = {
  docs: `${API_BASE_URL}/docs`,
  openapi: `${API_BASE_URL}/openapi.json`,
  health: `${API_BASE_URL}/health`,
};

export function runStreamUrl(runId: string): string {
  // EventSource cannot send headers; the stream route accepts the key as a query parameter.
  const apiKey = getStoredApiKey();
  const query = apiKey ? `?access_token=${encodeURIComponent(apiKey)}` : "";
  return withApiBase(`/runs/${encodeURIComponent(runId)}/stream${query}`);
}

function withApiBase(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  if (path.startsWith("/")) {
    return `${API_BASE_URL}${path}`;
  }
  return `${API_BASE_URL}/${path}`;
}

function readErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") {
    return fallback;
  }
  const candidate = payload as { detail?: unknown; message?: unknown };
  if (typeof candidate.detail === "string") {
    return candidate.detail;
  }
  if (typeof candidate.message === "string") {
    return candidate.message;
  }
  return fallback;
}

async function parseError(response: Response): Promise<Error> {
  try {
    const payload = await response.json();
    return new Error(readErrorMessage(payload, `Request failed: ${response.status}`));
  } catch {
    return new Error(`Request failed: ${response.status}`);
  }
}

const API_KEY_STORAGE = "book-agent.api-key";

/** The API key this browser sends (deployments with BOOK_AGENT_AUTH_MODE=api_key). */
export function getStoredApiKey(): string {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setStoredApiKey(value: string): void {
  try {
    if (value.trim()) {
      window.localStorage.setItem(API_KEY_STORAGE, value.trim());
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE);
    }
  } catch {
    /* storage unavailable: the key lasts for this page only */
  }
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const apiKey = getStoredApiKey();
  if (!apiKey) {
    return fetch(withApiBase(path), init);
  }
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${apiKey}`);
  return fetch(withApiBase(path), { ...init, headers });
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

async function requestBinary(path: string): Promise<Response> {
  const response = await apiFetch(path);
  if (!response.ok) {
    throw await parseError(response);
  }
  return response;
}

export async function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/health");
}

export async function uploadDocument(file: File): Promise<DocumentSummary> {
  const formData = new FormData();
  formData.append("source_file", file);
  return requestJson<DocumentSummary>("/documents/bootstrap-upload", {
    method: "POST",
    body: formData,
  });
}

export async function listDocumentHistory(filters: HistoryFilters = {}): Promise<DocumentHistoryPage> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 12));
  params.set("offset", String(filters.offset ?? 0));
  if (filters.query) {
    params.set("query", filters.query);
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.latest_run_status) {
    params.set("latest_run_status", filters.latest_run_status);
  }
  if (filters.merged_export_ready) {
    params.set("merged_export_ready", filters.merged_export_ready);
  }
  return requestJson<DocumentHistoryPage>(`/documents/history?${params.toString()}`);
}

export async function getDocument(documentId: string): Promise<DocumentSummary> {
  return requestJson<DocumentSummary>(`/documents/${encodeURIComponent(documentId)}`);
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await apiFetch(`/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw await parseError(response);
  }
}

export async function getDocumentExports(documentId: string): Promise<DocumentExportDashboard> {
  return requestJson<DocumentExportDashboard>(
    `/documents/${encodeURIComponent(documentId)}/exports?limit=5&offset=0`
  );
}

export async function getDocumentChapterWorklistDetail(
  documentId: string,
  chapterId: string
): Promise<DocumentChapterWorklistDetail> {
  return requestJson<DocumentChapterWorklistDetail>(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(chapterId)}/worklist`
  );
}

export interface ChapterListItem {
  chapter_id: string;
  ordinal: number;
  title_src: string | null;
  title_tgt: string | null;
  status: string;
}

export async function listDocumentChapters(
  documentId: string
): Promise<ChapterListItem[]> {
  return requestJson<ChapterListItem[]>(
    `/documents/${encodeURIComponent(documentId)}/chapters`
  );
}

export async function getDocumentChapterWorklist(
  documentId: string,
  filters: DocumentChapterWorklistFilters = {}
): Promise<DocumentChapterWorklist> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 50));
  params.set("offset", String(filters.offset ?? 0));
  if (filters.queuePriority) {
    params.set("queue_priority", filters.queuePriority);
  }
  if (filters.assigned !== undefined) {
    params.set("assigned", String(filters.assigned));
  }
  if (filters.ownerReady !== undefined) {
    params.set("owner_ready", String(filters.ownerReady));
  }
  if (filters.assignedOwnerName) {
    params.set("assigned_owner_name", filters.assignedOwnerName);
  }
  return requestJson<DocumentChapterWorklist>(
    `/documents/${encodeURIComponent(documentId)}/chapters/worklist?${params.toString()}`
  );
}

export async function assignDocumentChapterWorklistOwner(
  documentId: string,
  chapterId: string,
  payload: ChapterWorklistAssignmentRequest
): Promise<ChapterWorklistAssignment> {
  return requestJson<ChapterWorklistAssignment>(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(chapterId)}/worklist/assignment`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );
}

export async function clearDocumentChapterWorklistAssignment(
  documentId: string,
  chapterId: string,
  payload: ChapterWorklistAssignmentClearRequest
): Promise<ChapterWorklistAssignmentClearResponse> {
  return requestJson<ChapterWorklistAssignmentClearResponse>(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(chapterId)}/worklist/assignment/clear`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );
}

export async function executeIssueAction(
  actionId: string,
  options: { runFollowup?: boolean } = {}
): Promise<ExecuteActionResponse> {
  const params = new URLSearchParams();
  if (options.runFollowup) {
    params.set("run_followup", "true");
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<ExecuteActionResponse>(
    `/actions/${encodeURIComponent(actionId)}/execute${suffix}`,
    {
      method: "POST",
    }
  );
}

export async function approveChapterMemoryProposal(
  documentId: string,
  chapterId: string,
  proposalId: string,
  payload: ChapterMemoryProposalDecisionPayload
): Promise<ChapterMemoryProposalDecisionResponse> {
  return requestJson<ChapterMemoryProposalDecisionResponse>(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(chapterId)}/memory-proposals/${encodeURIComponent(proposalId)}/approve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );
}

export async function rejectChapterMemoryProposal(
  documentId: string,
  chapterId: string,
  proposalId: string,
  payload: ChapterMemoryProposalDecisionPayload
): Promise<ChapterMemoryProposalDecisionResponse> {
  return requestJson<ChapterMemoryProposalDecisionResponse>(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(chapterId)}/memory-proposals/${encodeURIComponent(proposalId)}/reject`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }
  );
}

export async function getRun(runId: string): Promise<DocumentRunSummary> {
  return requestJson<DocumentRunSummary>(`/runs/${encodeURIComponent(runId)}`);
}

export async function getRunEvents(runId: string): Promise<RunAuditEventPage> {
  return requestJson<RunAuditEventPage>(
    `/runs/${encodeURIComponent(runId)}/events?limit=8&offset=0`
  );
}

export async function createRun(documentId: string): Promise<DocumentRunSummary> {
  return requestJson<DocumentRunSummary>("/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      document_id: documentId,
      run_type: "translate_full",
      requested_by: "react-ui",
      status_detail_json: {
        source: "react-ui",
        surface: "translation-workspace-react",
      },
    }),
  });
}

export async function resumeRun(runId: string, payload: RunControlPayload): Promise<DocumentRunSummary> {
  return requestJson<DocumentRunSummary>(`/runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      actor_id: payload.actor_id,
      note: payload.note,
      detail_json: payload.detail_json ?? {},
    }),
  });
}

export async function retryRun(runId: string, payload: RunControlPayload): Promise<DocumentRunSummary> {
  return requestJson<DocumentRunSummary>(`/runs/${encodeURIComponent(runId)}/retry`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      actor_id: payload.actor_id,
      note: payload.note,
      detail_json: payload.detail_json ?? {},
    }),
  });
}

function filenameFromDisposition(headerValue: string | null, fallbackName: string): string {
  if (!headerValue) {
    return fallbackName;
  }
  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const plainMatch = headerValue.match(/filename="?([^"]+)"?/i);
  return plainMatch?.[1] ?? fallbackName;
}

async function saveBinaryResponse(response: Response, fallbackName: string): Promise<string> {
  const blob = await response.blob();
  const filename = filenameFromDisposition(
    response.headers.get("content-disposition"),
    fallbackName
  );
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 500);
  return filename;
}

export type DocumentDownloadType = "merged_html" | "bilingual_html" | "merged_markdown" | "review_package";

const TERMINAL_RUN_STATUSES = new Set(["succeeded", "succeeded_with_warnings", "failed", "paused", "cancelled"]);

async function waitForRunToFinish(
  runId: string,
  { pollIntervalMs, timeoutMs }: { pollIntervalMs: number; timeoutMs: number }
): Promise<DocumentRunSummary> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const run = await getRun(runId);
    if (TERMINAL_RUN_STATUSES.has(run.status)) {
      return run;
    }
    if (Date.now() >= deadline) {
      throw new Error("导出仍在进行，请稍后在交付页下载。");
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
}

/**
 * Download the latest export of a document. Downloads only serve existing
 * exports, so when none exists yet an export run is enqueued and awaited first.
 */
export type DownloadPackage = "single" | "zip" | "epub";

export async function downloadDocumentExport(
  documentId: string,
  exportType: DocumentDownloadType,
  {
    pollIntervalMs = 2000,
    timeoutMs = 10 * 60 * 1000,
    packaging = "single",
  }: { pollIntervalMs?: number; timeoutMs?: number; packaging?: DownloadPackage } = {}
): Promise<string> {
  const downloadPath = `/documents/${encodeURIComponent(documentId)}/exports/download?export_type=${encodeURIComponent(
    exportType
  )}&package=${packaging}`;
  let response = await apiFetch(downloadPath);
  if (response.status === 404) {
    const run = await requestJson<DocumentRunSummary>(`/documents/${encodeURIComponent(documentId)}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ export_type: exportType }),
    });
    const finished = await waitForRunToFinish(run.run_id, { pollIntervalMs, timeoutMs });
    if (finished.status !== "succeeded" && finished.status !== "succeeded_with_warnings") {
      throw new Error(`导出未完成（${finished.stop_reason ?? finished.status}）。`);
    }
    response = await apiFetch(downloadPath);
  }
  if (!response.ok) {
    throw await parseError(response);
  }
  const fallbackExtension: Record<DocumentDownloadType, string> = {
    merged_markdown: ".md",
    merged_html: ".html",
    bilingual_html: ".html",
    review_package: ".zip",
  };
  const extension = packaging === "epub" ? ".epub" : packaging === "zip" ? ".zip" : fallbackExtension[exportType];
  return saveBinaryResponse(response, `book-agent-${exportType}${extension}`);
}

/** This month's model usage of the caller's organisation, per book, as CSV. */
export async function downloadUsageStatement(month?: string): Promise<string> {
  const query = month ? `&month=${encodeURIComponent(month)}` : "";
  const response = await requestBinary(`/orgs/current/usage?format=csv${query}`);
  return saveBinaryResponse(response, `usage-${month ?? "current"}.csv`);
}

export async function downloadChapterExport(
  documentId: string,
  chapterId: string
): Promise<string> {
  const response = await requestBinary(
    `/documents/${encodeURIComponent(documentId)}/chapters/${encodeURIComponent(
      chapterId
    )}/exports/download?export_type=bilingual_html`
  );
  return saveBinaryResponse(response, `${chapterId}-bilingual_html.html`);
}

export async function listApprovals(documentId: string, status: "pending" | "all" = "pending"): Promise<Approval[]> {
  return requestJson<Approval[]>(`/documents/${documentId}/approvals?status=${status}`);
}

export async function decideApproval(
  approvalId: string,
  approved: boolean,
  payload: ApprovalDecision,
): Promise<Approval> {
  return requestJson<Approval>(`/approvals/${approvalId}/${approved ? "approve" : "reject"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getBookGuide(documentId: string): Promise<BookGuide> {
  return requestJson<BookGuide>(`/documents/${documentId}/book-guide`);
}

export async function listDecisions(documentId: string): Promise<Decision[]> {
  return requestJson<Decision[]>(`/documents/${documentId}/decisions`);
}

export async function listAgentTurns(documentId: string): Promise<AgentTurn[]> {
  return requestJson<AgentTurn[]>(`/documents/${documentId}/agent-turns`);
}

export async function listIssues(documentId: string, filter: IssueFilter = {}): Promise<IssueList> {
  const params = new URLSearchParams();
  params.set("status", filter.status ?? "active");
  if (filter.blocking !== undefined) params.set("blocking", String(filter.blocking));
  if (filter.detector) params.set("detector", filter.detector);
  if (filter.issueType) params.set("issue_type", filter.issueType);
  if (filter.chapterId) params.set("chapter_id", filter.chapterId);
  params.set("offset", String(filter.offset ?? 0));
  params.set("limit", String(filter.limit ?? 50));
  return requestJson<IssueList>(`/documents/${documentId}/issues?${params.toString()}`);
}

export async function getIssueDetail(issueId: string): Promise<IssueDetail> {
  return requestJson<IssueDetail>(`/issues/${issueId}`);
}

export async function transitionIssue(
  issueId: string,
  transition: IssueTransition,
  payload: IssueDecision,
): Promise<Issue> {
  return requestJson<Issue>(`/issues/${issueId}/${transition}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Model spend of the caller's organisation this month and its cap (null: unlimited). */
export async function getCurrentOrgBudget(): Promise<OrgBudget> {
  return requestJson<OrgBudget>("/orgs/current/budget");
}

export async function listStructureEdits(documentId: string): Promise<StructureEditList> {
  return requestJson<StructureEditList>(`/documents/${documentId}/structure-edits`);
}

export async function createStructureEdit(documentId: string, payload: StructureEditRequest): Promise<StructureEdit> {
  return requestJson<StructureEdit>(`/documents/${documentId}/structure-edits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function listProviders(): Promise<Provider[]> {
  return requestJson<Provider[]>("/providers");
}

export async function createProvider(payload: ProviderCreate): Promise<Provider> {
  return requestJson<Provider>("/providers", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(payload) });
}

export async function updateProvider(id: string, payload: ProviderUpdate): Promise<Provider> {
  return requestJson<Provider>(`/providers/${id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(payload) });
}

export async function activateProvider(id: string): Promise<Provider> {
  return requestJson<Provider>(`/providers/${id}/activate`, { method: "POST" });
}

export async function testProvider(id: string): Promise<ProviderTestResult> {
  return requestJson<ProviderTestResult>(`/providers/${id}/test`, { method: "POST" });
}

export async function deleteProvider(id: string): Promise<void> {
  const response = await apiFetch(`/providers/${id}`, { method: "DELETE" });
  if (!response.ok) {
    throw await parseError(response);
  }
}

/** Tokens (and cost, when the provider has prices) a full run of this book will take, as a range. */
export async function getCostEstimate(documentId: string): Promise<CostEstimate> {
  return requestJson<CostEstimate>(`/documents/${encodeURIComponent(documentId)}/cost-estimate`);
}
