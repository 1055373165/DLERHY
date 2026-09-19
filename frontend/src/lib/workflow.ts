import { stopReasonNote } from "./runStopReason";
import type {
  ChapterSummary,
  DocumentExportDashboard,
  DocumentHistoryEntry,
  DocumentRunSummary,
  DocumentSummary,
  RunAuditEvent,
  RunStageDetail,
  RunStatus,
} from "./api";

export interface PipelineStep {
  key: string;
  label: string;
  description: string;
  /** Only documents of these source types have this step; omitted means every document. */
  sourceTypes?: string[];
}

export function pipelineStepsFor(document: DocumentSummary | null | undefined): PipelineStep[] {
  return PIPELINE_STEPS.filter((step) => !step.sourceTypes || step.sourceTypes.includes(document?.source_type ?? ""));
}

export interface BadgeMeta {
  label: string;
  tone: "active" | "success" | "warning" | "danger" | "muted";
}

export interface PrimaryRunAction {
  mode: "disabled" | "create" | "resume" | "retry" | "recover";
  label: string;
  disabled: boolean;
  runId?: string;
  failedStage?: string;
}

export const PIPELINE_STEPS: PipelineStep[] = [
  {
    key: "bootstrap",
    label: "文档解析",
    description: "导入源文件并完成章节切分。",
  },
  {
    key: "structure_review",
    label: "结构审查",
    description: "（可选）结构代理查看版面可疑的 PDF 页面图像，把块类型、标题、合并拆分等问题记为建议。",
  },
  {
    key: "terminology",
    label: "术语与 BOOK.md",
    description: "术语代理确定全书术语表与翻译决策，例外项等待审批。",
  },
  {
    key: "translate",
    label: "全文翻译",
    description: "逐段翻译成中文。",
  },
  {
    key: "model_review",
    label: "模型审校",
    description: "审校代理抽样阅读译文，把误译、漏译、术语与文风问题记入问题清单。",
  },
  {
    key: "review",
    label: "自动复核",
    description: "导出前自动检查并修复阻断问题。",
  },
  {
    key: "repair",
    label: "修复代理",
    description: "（可选）规则修复后仍阻断的问题交给修复代理：重译、最小改写或申请人工判定不修复。",
  },
  {
    key: "bilingual_html",
    label: "双语导出",
    description: "生成逐章双语结果供精校使用。",
  },
  {
    key: "merged_html",
    label: "中文阅读稿",
    description: "输出最终整书阅读包。",
  },
  {
    key: "web_html",
    label: "中文网页",
    description: "（仅网页）按原网页的排版输出中文网页。",
    sourceTypes: ["web"],
  },
  {
    key: "export_review",
    label: "导出审读",
    description: "（可选）导出审读代理查看渲染后的页面截图，把缺图、表格错乱、漏译、乱码等问题记为建议。",
  },
];

export const DELIVERY_ASSETS = [
  {
    key: "merged_html" as const,
    title: "中文阅读包",
    label: "主交付",
    buttonText: "下载中文阅读包",
  },
  {
    key: "bilingual_html" as const,
    title: "双语章节包",
    label: "精校包",
    buttonText: "下载双语章节包",
  },
  {
    key: "review_package" as const,
    title: "复核包",
    label: "诊断包",
    buttonText: "下载复核包",
  },
];

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  skipped: "未启用",
  queued: "已排队",
  running: "进行中",
  draining: "收尾中",
  succeeded: "已完成",
  succeeded_with_warnings: "已完成（有警告）",
  awaiting_approval: "等待审批",
  partial: "部分完成",
  failed: "失败",
  retryable_failed: "待重试",
  paused: "已暂停",
  cancelled: "已取消",
  active: "已入库",
  partially_exported: "部分可下载",
  exported: "可下载",
};

const STALE_FAILED_STAGE_RETRY_MS = 3 * 60 * 1000;

export function isRunActive(status?: RunStatus | null): boolean {
  return Boolean(status && ["queued", "running", "draining", "paused"].includes(status));
}

export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "0";
  }
  return new Intl.NumberFormat("zh-CN").format(Number(value));
}

export function formatDate(value?: string | null): string {
  if (!value) {
    return "—";
  }
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function shorten(value?: string | null, keep = 6): string {
  if (!value) {
    return "—";
  }
  return value.length <= keep * 2 + 1
    ? value
    : `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

export function statusLabel(status?: string | null): string {
  if (!status) {
    return "未开始";
  }
  return STATUS_LABELS[status] ?? status;
}

export function sourceLabel(sourceType?: string | null): string {
  const labels: Record<string, string> = {
    epub: "EPUB",
    pdf_text: "PDF（文本）",
    pdf_scan: "PDF（扫描）",
    pdf_mixed: "PDF（混合）",
    web: "网页",
  };
  if (!sourceType) {
    return "未识别";
  }
  return labels[sourceType] ?? sourceType;
}

export function pipelineStageLabel(stage?: string | null): string {
  const labels: Record<string, string> = {
    bootstrap: "文档解析",
    translate: "全文翻译",
    review: "自动复核",
    bilingual_html: "双语导出",
    merged_html: "中文阅读稿",
    completed: "全部完成",
  };
  if (!stage) {
    return "等待开始";
  }
  return labels[stage] ?? PIPELINE_STEPS.find((step) => step.key === stage)?.label ?? stage;
}

export function preferredTitle(entity?: {
  title_tgt?: string | null;
  title?: string | null;
  title_src?: string | null;
} | null): string {
  return entity?.title_tgt || entity?.title || entity?.title_src || "未命名书籍";
}

export function currentPipelineDetail(run?: DocumentRunSummary | null): {
  current_stage?: string | null;
  stages?: Record<string, RunStageDetail>;
} {
  const pipeline = run?.status_detail_json?.pipeline ?? {};
  // The server writes `_cached_pipeline_stages`; runs from before that rename still carry `stages`.
  return { current_stage: pipeline.current_stage, stages: pipeline._cached_pipeline_stages ?? pipeline.stages };
}

export function currentStageKey(run?: DocumentRunSummary | null): string | null {
  return currentPipelineDetail(run).current_stage ?? null;
}

function stageDetail(run: DocumentRunSummary | null | undefined, stageKey: string): RunStageDetail | null {
  return currentPipelineDetail(run).stages?.[stageKey] ?? null;
}

export function stageStatus(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined,
  stepKey: string
): string {
  if (stepKey === "bootstrap") {
    if (!document) {
      return "pending";
    }
    return document.status === "failed" ? "failed" : "succeeded";
  }
  const detail = stageDetail(run, stepKey);
  if (detail?.status === "running" && detail.turn_status === "awaiting_approval") {
    return "awaiting_approval";
  }
  if (detail?.status) {
    return detail.status;
  }
  if (!run) {
    return "pending";
  }
  if (run.status === "succeeded" && ["translate", "review", "bilingual_html", "merged_html"].includes(stepKey)) {
    return "succeeded";
  }
  // A finished run never reached this stage: it was not part of this run (optional, or skipped).
  if (["succeeded", "succeeded_with_warnings"].includes(run.status)) {
    return "skipped";
  }
  return "pending";
}

/** The pipeline step whose agent is waiting for a human decision, if any. */
export function awaitingApprovalStage(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined
): PipelineStep | null {
  return PIPELINE_STEPS.find((step) => stageStatus(document, run, step.key) === "awaiting_approval") ?? null;
}

export function failedPipelineStage(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined
): { key: string; status: string; detail: RunStageDetail | null } | null {
  for (const step of PIPELINE_STEPS) {
    const status = stageStatus(document, run, step.key);
    if (status === "failed" || status === "cancelled") {
      return { key: step.key, status, detail: stageDetail(run, step.key) };
    }
  }
  return null;
}

function failedStageRetryEligible(run?: DocumentRunSummary | null, failedStageKey?: string | null): boolean {
  if (!run || !failedStageKey) {
    return false;
  }
  if (["failed", "cancelled", "paused"].includes(run.status)) {
    return true;
  }
  const freshestSignal = run.worker_leases.latest_heartbeat_at || run.updated_at;
  if (!freshestSignal) {
    return false;
  }
  const freshestAt = new Date(freshestSignal).getTime();
  if (Number.isNaN(freshestAt)) {
    return false;
  }
  return Date.now() - freshestAt >= STALE_FAILED_STAGE_RETRY_MS;
}

export function translateProgress(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined
): { total: number; completed: number; ratio: number } {
  const detail = stageDetail(run, "translate");
  const total = Number(
    detail?.total_packet_count ??
      document?.packet_count ??
      run?.work_items.stage_counts.translate ??
      0
  );
  const rawCompleted = Number(run?.status_detail_json?.control_counters?.completed_work_item_count ?? 0);
  const runStatus = run?.status;
  const translateStageStatus = detail?.status;
  const isTerminalSuccess =
    runStatus === "succeeded" ||
    translateStageStatus === "succeeded" ||
    Boolean(document?.merged_export_ready);
  if (isTerminalSuccess && total > 0) {
    return { total, completed: total, ratio: 1 };
  }
  return {
    total,
    completed: rawCompleted,
    ratio: total > 0 ? Math.max(0, Math.min(1, rawCompleted / total)) : 0,
  };
}

export function nextMilestoneText(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined,
  exportsDashboard: DocumentExportDashboard | null | undefined
): string {
  if (!document) {
    return "先上传一本英文书，系统才会给出下一步。";
  }
  if (document.merged_export_ready) {
    return "中文阅读包已完成，可以直接下载交付。";
  }
  const stage = currentStageKey(run);
  if (!document.latest_run_id) {
    return "解析完成，下一步是启动整书转换。";
  }
  if (stage === "translate") {
    return "当前仍在全文翻译阶段，先等待进入复核。";
  }
  if (stage === "review") {
    const blockers = blockingIssueCount(exportsDashboard);
    return blockers > 0
      ? "还有阻断问题没处理，暂时不会导出整本书。"
      : "复核接近完成，系统即将进入导出。";
  }
  if (stage === "bilingual_html") {
    return "双语章节包正在生成，整书阅读包还要再推进一步。";
  }
  if (stage === "merged_html") {
    return "整书中文阅读稿正在导出，接近最终完成。";
  }
  if (document.latest_run_status === "failed" || document.latest_run_status === "cancelled") {
    return "上一次运行已中断，可以直接重试。";
  }
  if (document.latest_run_status === "paused") {
    return "上一次运行已暂停，可以继续推进。";
  }
  return "当前还没有可下载结果，系统会继续推进后续阶段。";
}

/** Where to read the Chinese version, once there is one to read. */
export function readerPath(document: DocumentSummary | null | undefined): string | null {
  if (!document?.merged_export_ready) return null;
  return `/library/${encodeURIComponent(document.document_id)}/read`;
}

export function documentBadge(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined
): BadgeMeta {
  if (!document) {
    return { tone: "muted", label: "等待书稿" };
  }
  if (document.merged_export_ready) {
    return { tone: "success", label: "可交付" };
  }
  const runStatus = run?.status || document.latest_run_status;
  const stage = currentStageKey(run);
  if (stage === "translate" && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "翻译已暂停" : runStatus === "queued" ? "待翻译" : "翻译中",
    };
  }
  if (stage === "review" && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "复核已暂停" : runStatus === "queued" ? "待复核" : "复核中",
    };
  }
  if (["bilingual_html", "merged_html"].includes(stage || "") && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "导出已暂停" : runStatus === "queued" ? "待导出" : "导出中",
    };
  }
  if (runStatus === "failed" || runStatus === "cancelled") {
    return { tone: "danger", label: statusLabel(runStatus) };
  }
  if (!document.latest_run_id) {
    return { tone: "active", label: "已解析" };
  }
  return { tone: "active", label: statusLabel(document.status) };
}

export function getPrimaryRunAction(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined
): PrimaryRunAction {
  const latestStatus = run?.status || document?.latest_run_status || null;
  const runId = run?.run_id || document?.latest_run_id || undefined;
  const failedStage = failedPipelineStage(document, run);
  if (!document) {
    return { mode: "disabled", label: "先上传并解析书稿", disabled: true };
  }
  if (failedStage && runId) {
    if (["failed", "cancelled", "paused"].includes(latestStatus || "") || failedStageRetryEligible(run, failedStage.key)) {
      return {
        mode: "retry",
        label: "重试上次转换",
        disabled: false,
        runId,
        failedStage: failedStage.key,
      };
    }
    return {
      mode: "recover",
      label: "刷新并准备重试",
      disabled: false,
      runId,
      failedStage: failedStage.key,
    };
  }
  if (latestStatus === "running" || latestStatus === "draining") {
    return { mode: "disabled", label: "整书转换进行中", disabled: true, runId };
  }
  if ((latestStatus === "queued" || latestStatus === "paused") && runId) {
    return { mode: "resume", label: "继续当前转换", disabled: false, runId };
  }
  if ((latestStatus === "failed" || latestStatus === "cancelled") && runId) {
    return { mode: "retry", label: "重试上次转换", disabled: false, runId };
  }
  return {
    mode: "create",
    label: latestStatus === "succeeded" ? "重新运行整书转换" : "开始整书转换",
    disabled: false,
  };
}

export function pipelineMeta(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined,
  stepKey: string
): string {
  if (stepKey === "bootstrap" && document) {
    return `已切分为 ${formatNumber(document.packet_count)} 段`;
  }
  const detail = stageDetail(run, stepKey);
  if (!detail) {
    const status = stageStatus(document, run, stepKey);
    if (status === "succeeded") return "已完成";
    if (status === "skipped") return "这次转换没有这一步";
    return "等待进入该阶段";
  }
  if (stepKey === "translate") {
    const progress = translateProgress(document, run);
    return `已翻译 ${formatNumber(progress.completed)} / ${formatNumber(progress.total)} 段`;
  }
  if (stepKey === "review") {
    const examined = Number(detail.examined_chapter_count ?? 0);
    const skipped = Number(detail.skipped_chapter_count ?? 0);
    const total = Number(detail.total_chapter_count ?? 0);
    const coverage = total > 0
      ? `已检查 ${formatNumber(examined)}/${formatNumber(total)}${skipped > 0 ? ` · 跳过 ${formatNumber(skipped)}` : ""}`
      : "";
    const core = `issues ${formatNumber(detail.total_issue_count)} · actions ${formatNumber(detail.total_action_count)}`;
    return coverage ? `${coverage} · ${core}` : core;
  }
  if (stepKey === "bilingual_html" || stepKey === "merged_html" || stepKey === "web_html") {
    return detail.chapter_export_count
      ? `导出记录 ${formatNumber(detail.chapter_export_count)} 个章节结果`
      : detail.status === "succeeded"
        ? "已生成"
        : "等待生成";
  }
  return detail.updated_at ? `最近更新 ${formatDate(detail.updated_at)}` : "等待进入该阶段";
}

export function blockingIssueCount(exportsDashboard?: DocumentExportDashboard | null): number {
  return (exportsDashboard?.issue_hotspots || []).reduce(
    (total, entry) => total + Number(entry.blocking_issue_count || 0),
    0
  );
}

export function getFocusChapters(document?: DocumentSummary | null): ChapterSummary[] {
  return [...(document?.chapters || [])]
    .sort((left, right) => {
      const issueDelta = Number(right.open_issue_count || 0) - Number(left.open_issue_count || 0);
      if (issueDelta !== 0) {
        return issueDelta;
      }
      return Number(left.ordinal || 0) - Number(right.ordinal || 0);
    })
    .slice(0, 6);
}

export function historyBadge(entry: DocumentHistoryEntry): BadgeMeta {
  if (entry.merged_export_ready) {
    return { tone: "success", label: "可下载" };
  }
  const runStatus = entry.latest_run_status || null;
  const stage = entry.latest_run_current_stage || null;
  if (stage === "translate" && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "翻译已暂停" : runStatus === "queued" ? "待翻译" : "翻译中",
    };
  }
  if (stage === "review" && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "复核已暂停" : runStatus === "queued" ? "待复核" : "复核中",
    };
  }
  if (["bilingual_html", "merged_html"].includes(stage || "") && ["queued", "running", "draining", "paused"].includes(runStatus || "")) {
    return {
      tone: runStatus === "paused" ? "warning" : "active",
      label: runStatus === "paused" ? "导出已暂停" : runStatus === "queued" ? "待导出" : "导出中",
    };
  }
  if (entry.chapter_bilingual_export_count > 0) {
    return { tone: "success", label: "部分可下载" };
  }
  if (runStatus === "failed" || runStatus === "cancelled") {
    return { tone: "danger", label: statusLabel(runStatus) };
  }
  return { tone: "active", label: statusLabel(entry.status) };
}

export function historyProgress(entry: DocumentHistoryEntry): string {
  const total = Number(entry.latest_run_total_work_item_count || entry.packet_count || 0);
  const completed = Number(entry.latest_run_completed_work_item_count || 0);
  if (entry.merged_export_ready) {
    return "中文阅读包已经可直接下载。";
  }
  if (entry.latest_run_current_stage === "translate" && total > 0) {
    return `正在翻译 · 已完成 ${formatNumber(completed)} / ${formatNumber(total)} 段`;
  }
  if (entry.latest_run_current_stage === "review") {
    return "正在自动复核 · 处理完阻断问题后开始导出。";
  }
  if (entry.latest_run_current_stage === "bilingual_html") {
    return "双语章节包正在生成，整书中文阅读包尚未完成。";
  }
  if (entry.latest_run_current_stage === "merged_html") {
    return "整书中文阅读稿正在导出，接近最终完成。";
  }
  if (entry.chapter_bilingual_export_count > 0) {
    return "双语章节包已可用，中文阅读包仍待生成。";
  }
  if (!entry.latest_run_id) {
    return "书籍已入库，尚未启动整书转换。";
  }
  if (entry.latest_run_status === "failed" || entry.latest_run_status === "cancelled") {
    return "上次运行中断，可以打开这本书后继续处理或重试。";
  }
  if (entry.latest_run_status === "paused") {
    return "上次运行已暂停，打开这本书后可以继续。";
  }
  return "当前仍在处理中。";
}

export function downloadReady(
  document: DocumentSummary | null | undefined,
  exportsDashboard: DocumentExportDashboard | null | undefined,
  key: "merged_html" | "bilingual_html" | "review_package"
): boolean {
  if (!document) {
    return false;
  }
  if (key === "merged_html") {
    return Boolean(document.merged_export_ready);
  }
  if (key === "bilingual_html") {
    return Number(document.chapter_bilingual_export_count || 0) > 0;
  }
  return Boolean(exportsDashboard?.records?.some((record) => record.export_type === key && record.status === "succeeded"));
}

export function assetAvailabilityText(
  key: "merged_html" | "bilingual_html" | "review_package",
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined,
  exportsDashboard: DocumentExportDashboard | null | undefined
): string {
  if (!document) {
    return "请先载入当前书籍。";
  }
  if (downloadReady(document, exportsDashboard, key)) {
    return "已可直接下载";
  }
  const stage = currentStageKey(run);
  if (key === "merged_html") {
    if (stage === "translate") {
      return "全文翻译尚未完成，整书阅读包不会提前生成。";
    }
    if (stage === "review") {
      return "复核尚未通过，整书阅读包仍被 gate 挡住。";
    }
    if (stage === "merged_html") {
      return "整书阅读包正在导出。";
    }
  }
  if (key === "bilingual_html") {
    if (stage === "translate" || stage === "review") {
      return "双语章节包会在复核之后生成。";
    }
    if (stage === "bilingual_html") {
      return "双语章节包正在导出。";
    }
  }
  if (key === "review_package") {
    return "复核报告生成后，这里可以下载。";
  }
  return "尚未生成";
}

export function deliverableBlockerReason(
  document: DocumentSummary | null | undefined,
  run: DocumentRunSummary | null | undefined,
  exportsDashboard: DocumentExportDashboard | null | undefined
): string {
  if (!document) {
    return "先载入一本书，系统才能解释为什么能否交付。";
  }
  if (document.merged_export_ready) {
    return "整书交付已完成，目前没有阻塞。";
  }
  if (document.latest_run_status === "failed" || document.latest_run_status === "cancelled") {
    return "上一次整书运行已经中断，导出不会继续推进。";
  }
  const blockers = blockingIssueCount(exportsDashboard);
  if (blockers > 0) {
    return `还有 ${formatNumber(blockers)} 个阻断问题没处理，暂时不会导出整本书。`;
  }
  return nextMilestoneText(document, run, exportsDashboard);
}

/** A run event as a sentence for the reader, or null when it is internal bookkeeping. */
export function describeRunEvent(event: RunAuditEvent): { title: string; detail: string | null } | null {
  const payload = event.payload_json ?? {};
  const note = typeof payload.note === "string" ? payload.note : null;
  switch (event.event_type) {
    case "run.created":
      return { title: "已提交转换", detail: null };
    case "run.started":
      return { title: "开始转换", detail: null };
    case "run.resumed":
      return { title: "继续转换", detail: "从停下的地方接着做，已完成的部分不会重做。" };
    case "run.paused":
      return { title: "转换已暂停", detail: note ? (stopReasonNote("paused", note)?.text ?? null) : null };
    case "run.retry_requested":
      return { title: "重新尝试失败的部分", detail: null };
    case "run.cancelled":
      return { title: "转换已取消", detail: null };
    case "run.succeeded":
      return { title: "转换完成", detail: "可以阅读中文版了。" };
    case "run.failed":
      return { title: "转换失败", detail: note ? (stopReasonNote("failed", note)?.text ?? null) : null };
    case "work_item.retryable_failed":
      return { title: "有一步出错，正在自动重试", detail: null };
    case "work_item.terminal_failed":
      return { title: "有一步失败，已放弃重试", detail: null };
    default:
      return null;
  }
}
