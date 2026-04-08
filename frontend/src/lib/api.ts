export type DocumentStatus =
  | "active"
  | "failed"
  | "partially_exported"
  | "exported"
  | string;

export type RunStatus =
  | "pending"
  | "queued"
  | "running"
  | "draining"
  | "succeeded"
  | "failed"
  | "paused"
  | "cancelled"
  | string;

export interface HealthResponse {
  status: string;
}

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

export interface RunEventSummary {
  event_count: number;
  latest_event_at?: string | null;
}

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

export interface TranslationUsageSummary {
  run_count: number;
  succeeded_run_count: number;
  total_token_in: number;
  total_token_out: number;
  total_cost_usd: number;
}

export interface IssueHotspotEntry {
  issue_type: string;
  root_cause_layer?: string | null;
  issue_count: number;
  open_issue_count: number;
  blocking_issue_count: number;
}

export interface IssueChapterHighlightEntry {
  chapter_id: string;
  ordinal: number;
  title_src?: string | null;
  issue_count: number;
  open_issue_count: number;
  blocking_issue_count: number;
}

export interface IssueChapterHighlights {
  top_open_chapter?: IssueChapterHighlightEntry | null;
  top_blocking_chapter?: IssueChapterHighlightEntry | null;
  top_resolved_chapter?: IssueChapterHighlightEntry | null;
}

export interface ExportRecordSummary {
  export_id: string;
  export_type: string;
  status: string;
  file_path: string;
  manifest_path?: string | null;
  chapter_id?: string | null;
  created_at: string;
  updated_at: string;
}

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

export interface ChapterMemoryProposalDecisionAudit {
  proposal_id: string;
  decision: "approved" | "rejected";
  actor_type: string;
  actor_id?: string | null;
  note?: string | null;
  created_at: string;
}

export interface ChapterMemoryProposal {
  proposal_id: string;
  packet_id: string;
  translation_run_id: string;
  status: string;
  base_snapshot_version?: number | null;
  committed_snapshot_id?: string | null;
  created_at: string;
  updated_at: string;
  last_decision?: ChapterMemoryProposalDecisionAudit | null;
}

export interface ChapterMemoryProposalSurface {
  proposal_count: number;
  pending_proposal_count: number;
  counts_by_status: Record<string, number>;
  latest_proposal_updated_at?: string | null;
  active_snapshot_version?: number | null;
  pending_proposals: ChapterMemoryProposal[];
  recent_decisions: ChapterMemoryProposalDecisionAudit[];
}

export interface ChapterWorklistTimelineEntry {
  event_id: string;
  source_kind: "action" | "assignment" | "memory_proposal" | string;
  event_kind: string;
  created_at: string;
  actor_name?: string | null;
  note?: string | null;
  issue_id?: string | null;
  issue_type?: string | null;
  action_id?: string | null;
  action_type?: string | null;
  scope_type?: string | null;
  scope_id?: string | null;
  status?: string | null;
  proposal_id?: string | null;
  decision?: "approved" | "rejected" | null;
  owner_name?: string | null;
}

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

export interface ChapterWorklistAssignment {
  assignment_id: string;
  document_id: string;
  chapter_id: string;
  owner_name: string;
  assigned_by: string;
  note?: string | null;
  assigned_at: string;
  created_at: string;
  updated_at: string;
}

export interface ChapterWorklistAssignmentRequest {
  owner_name: string;
  assigned_by: string;
  note?: string;
}

export interface ChapterWorklistAssignmentClearRequest {
  cleared_by: string;
  note?: string;
}

export interface ChapterWorklistAssignmentClearResponse {
  document_id: string;
  chapter_id: string;
  cleared: boolean;
  cleared_by: string;
  note?: string | null;
  cleared_assignment_id: string;
}

export interface ChapterWorklistIssue {
  issue_id: string;
  issue_type: string;
  root_cause_layer: string;
  severity: string;
  status: string;
  blocking: boolean;
  detector: string;
  suggested_action?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChapterWorklistAction {
  action_id: string;
  issue_id: string;
  issue_type: string;
  action_type: string;
  scope_type: string;
  scope_id?: string | null;
  status: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

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

export interface ChapterWorklistAssignmentHistoryEntry {
  event_id: string;
  event_type: string;
  owner_name?: string | null;
  performed_by?: string | null;
  note?: string | null;
  created_at: string;
}

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

export interface ChapterMemoryProposalDecisionResponse {
  document_id: string;
  chapter_id: string;
  decision: "approved" | "rejected";
  proposal: ChapterMemoryProposal;
  committed_snapshot_id?: string | null;
  committed_snapshot_version?: number | null;
}

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

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "/v1").replace(/\/$/, "");

export const SERVICE_LINKS = {
  docs: `${API_BASE_URL}/docs`,
  openapi: `${API_BASE_URL}/openapi.json`,
  health: `${API_BASE_URL}/health`,
};

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

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(withApiBase(path), init);
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as T;
}

async function requestBinary(path: string): Promise<Response> {
  const response = await fetch(withApiBase(path));
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

export async function downloadDocumentExport(
  documentId: string,
  exportType: "merged_html" | "bilingual_html" | "merged_markdown" | "bilingual_markdown" | "review_package"
): Promise<string> {
  const response = await requestBinary(
    `/documents/${encodeURIComponent(documentId)}/exports/download?export_type=${encodeURIComponent(
      exportType
    )}`
  );
  const extMap: Record<string, string> = {
    merged_markdown: ".md",
    bilingual_markdown: ".md",
    merged_html: ".html",
    bilingual_html: ".html",
    review_package: ".zip",
  };
  const ext = extMap[exportType] ?? ".zip";
  return saveBinaryResponse(response, `book-agent-${exportType}${ext}`);
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
  return saveBinaryResponse(response, `${chapterId}-bilingual_html.zip`);
}
