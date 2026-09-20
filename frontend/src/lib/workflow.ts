/** Formatting and wording helpers. Book and run state comes from the server (lib/overview.ts), never derived here. */
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

export interface BadgeMeta {
  label: string;
  tone: "active" | "success" | "warning" | "danger" | "muted";
}

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

export function preferredTitle(entity?: {
  title_tgt?: string | null;
  title?: string | null;
  title_src?: string | null;
} | null): string {
  return entity?.title_tgt || entity?.title || entity?.title_src || "未命名书籍";
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

export function describeRunEvent(event: RunAuditEvent): { title: string; detail: string | null } | null {
  switch (event.event_type) {
    case "run.created":
      return { title: "已提交翻译", detail: null };
    case "run.started":
      return { title: "开始翻译", detail: null };
    case "run.resumed":
      return { title: "继续翻译", detail: "从停下的地方接着做，已完成的部分不会重做。" };
    case "run.paused":
      return { title: "翻译已暂停", detail: null };
    case "run.retry_requested":
      return { title: "重新尝试失败的部分", detail: null };
    case "run.cancelled":
      return { title: "翻译已取消", detail: null };
    case "run.succeeded":
      return { title: "翻译完成", detail: "可以阅读中文版了。" };
    case "run.failed":
      return { title: "翻译失败", detail: null };
    case "work_item.retryable_failed":
      return { title: "有一步出错，正在自动重试", detail: null };
    case "work_item.terminal_failed":
      return { title: "有一步失败，已放弃重试", detail: null };
    default:
      return null;
  }
}
