import type {
  ChapterSummary,
  DocumentExportDashboard,
  DocumentHistoryEntry,
  DocumentRunSummary,
  DocumentSummary,
  RunStageDetail,
  RunStatus,
} from "./api";

export interface PipelineStep {
  key: string;
  label: string;
  description: string;
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
    key: "translate",
    label: "全文翻译",
    description: "逐 packet 产出中文译文并写回进度。",
  },
  {
    key: "review",
    label: "自动复核",
    description: "处理导出前必须解决的 blocker。",
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
    title: "Review Package",
    label: "诊断包",
    buttonText: "下载 Review Package",
  },
];

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  queued: "已排队",
  running: "进行中",
  draining: "收尾中",
  succeeded: "已完成",
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
  return labels[stage] ?? stage;
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
  return run?.status_detail_json?.pipeline ?? {};
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
  if (detail?.status) {
    return detail.status;
  }
  if (!run) {
    return "pending";
  }
  if (run.status === "succeeded" && ["translate", "review", "bilingual_html", "merged_html"].includes(stepKey)) {
    return "succeeded";
  }
  return "pending";
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
  const completed = Number(run?.status_detail_json?.control_counters?.completed_work_item_count ?? 0);
  return {
    total,
    completed,
    ratio: total > 0 ? Math.max(0, Math.min(1, completed / total)) : 0,
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
      ? "仍有 blocker 未清理，系统不会继续整书导出。"
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
    return `已准备 ${formatNumber(document.packet_count)} 个 packet`;
  }
  const detail = stageDetail(run, stepKey);
  if (!detail) {
    return "等待进入该阶段";
  }
  if (stepKey === "translate") {
    const progress = translateProgress(document, run);
    return `已完成 ${formatNumber(progress.completed)} / ${formatNumber(progress.total)} 个 packet`;
  }
  if (stepKey === "review") {
    return `issues ${formatNumber(detail.total_issue_count)} · actions ${formatNumber(detail.total_action_count)}`;
  }
  if (stepKey === "bilingual_html" || stepKey === "merged_html") {
    return detail.chapter_export_count
      ? `导出记录 ${formatNumber(detail.chapter_export_count)} 个章节结果`
      : "等待生成导出资产";
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
    return `全文翻译阶段 · 已完成 ${formatNumber(completed)} / ${formatNumber(total)} 个 packet`;
  }
  if (entry.latest_run_current_stage === "review") {
    return "自动复核阶段 · 等待清理 blocker 后再进入导出。";
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
    return "当 review 诊断产物存在时，这里会开放下载。";
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
    return `当前仍有 ${formatNumber(blockers)} 个 blocking issue，系统不会放行整书导出。`;
  }
  return nextMilestoneText(document, run, exportsDashboard);
}

export function eventTitle(eventType: string): string {
  const mapping: Record<string, string> = {
    "run.created": "创建 run",
    "run.started": "启动 run",
    "run.resumed": "继续 run",
    "run.paused": "暂停 run",
    "run.retry_requested": "请求重试",
    "run.cancelled": "取消 run",
    "run.succeeded": "run 完成",
    "run.failed": "run 失败",
  };
  return mapping[eventType] ?? eventType;
}
