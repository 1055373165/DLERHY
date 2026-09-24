import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getRunEvents } from "../../lib/api";
import { usd } from "../../lib/overview";
import { describeRunEvent, formatDate } from "../../lib/workflow";
import { useBook } from "./BookPage";
import { ParseHealthPanel } from "./ParseHealthPanel";
import { PilotQualityPanel } from "./PilotQualityPanel";
import s from "./Tabs.module.css";

const STEP_STATUS: Record<string, string> = {
  pending: "等待中",
  queued: "排队中",
  running: "进行中",
  awaiting_approval: "等你审批",
  succeeded: "完成",
  partial: "部分完成",
  failed: "失败",
  skipped: "未启用",
  paused: "已暂停",
  cancelled: "已取消",
};

function elapsed(from: string | null | undefined, to: string | null | undefined): string {
  if (!from) return "";
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

/** This book's progress: how well it was read (parse health), how good the pilot came out (its QA), then its run. */
export function ProgressTab() {
  const book = useBook();
  const quality = book.pilot?.quality ?? null;
  return (
    <div className={s.stack}>
      <ParseHealthPanel documentId={book.document_id} summary={book.parse_health ?? null} />
      {(quality || book.pilot?.state === "ready") && <PilotQualityPanel documentId={book.document_id} summary={quality} />}
      <RunProgress />
    </div>
  );
}

/** This book's run, in one place: the steps, what happened, how long it took, and why it stopped. */
function RunProgress() {
  const book = useBook();
  const runId = book.translation.run_id;
  const active = ["queued", "running", "draining"].includes(book.translation.state);
  // The story of the run (its own milestones, oldest kept) and, separately, the raw tail for developers.
  const milestones = useQuery({
    queryKey: ["run-events", runId, "milestones"],
    queryFn: () => getRunEvents(runId as string, { limit: 50 }),
    enabled: Boolean(runId),
    refetchInterval: active ? 5000 : false,
  });
  // The raw tail is behind a fold: nobody has asked for it until they open it.
  const [rawOpen, setRawOpen] = useState(false);
  const events = useQuery({
    queryKey: ["run-events", runId, "all"],
    queryFn: () => getRunEvents(runId as string, { all: true, limit: 100 }),
    enabled: Boolean(runId) && rawOpen,
    refetchInterval: active && rawOpen ? 15_000 : false,
  });
  if (!runId) {
    return <div className={s.empty}>还没开始翻译。开始后，这里会显示每一步的进展。</div>;
  }
  const entries = events.data?.entries ?? [];
  const told = (milestones.data?.entries ?? [])
    .map((event) => ({ event, text: describeRunEvent(event) }))
    .filter((item) => item.text !== null);
  const { started_at: startedAt, finished_at: finishedAt, progress, stop_reason_text: stopText } = book.translation;
  const running = active;

  return (
    <div className={s.stack}>
      <section className={s.runSummary} aria-label="本次运行">
        <span>
          开始于 <strong>{formatDate(startedAt ?? null)}</strong>
        </span>
        <span>
          {running ? "已运行" : "用时"} <strong>{elapsed(startedAt, finishedAt) || "—"}</strong>
        </span>
        <span>
          已翻译 <strong>{progress.done}/{progress.total}</strong> 段
        </span>
        {book.cost && book.cost.spent_usd > 0 && (
          <span>
            已花费 <strong>{usd(book.cost.spent_usd)}</strong>
          </span>
        )}
      </section>

      {/* The banner above already carries the headline: repeat it here only if it says something else. */}
      {stopText && stopText !== book.headline && (
        <div className={s.stopReason} role="note">
          {stopText}
        </div>
      )}

      <div className={s.columns}>
        <section>
          <h2 className={s.heading}>步骤</h2>
          <ol className={s.steps}>
            {book.translation.steps.map((step) => (
              <li key={step.key} className={s.step} data-status={step.status}>
                <span>{step.label}</span>
                <span className={s.stepStatus}>
                  {STEP_STATUS[step.status] ?? step.status}
                  {step.at ? ` · ${formatDate(step.at)}` : ""}
                </span>
              </li>
            ))}
          </ol>
        </section>

        <section>
          <h2 className={s.heading}>运行记录</h2>
          {milestones.isError ? (
            <div className={s.error}>运行记录没能加载：{(milestones.error as Error).message}</div>
          ) : told.length === 0 ? (
            <div className={s.empty}>{milestones.isLoading ? "加载中…" : "还没有记录。"}</div>
          ) : (
            <ul className={s.events}>
              {told.map(({ event, text }) => (
                <li key={event.event_id} className={s.event}>
                  <span className={s.eventTitle}>{text?.title}</span>
                  {text?.detail && <span className={s.eventDetail}>{text.detail}</span>}
                  <span className={s.eventDate}>{formatDate(event.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
          <details className={s.rawEvents} onToggle={(event) => setRawOpen(event.currentTarget.open)}>
            <summary className={s.fieldLabel}>
              全部事件（技术细节{rawOpen ? `，${entries.length} 条` : ""}）
            </summary>
            {rawOpen && events.isLoading && <div className={s.empty}>加载中…</div>}
            <ul className={s.list}>
              {entries.map((event) => (
                <li key={`raw-${event.event_id}`} className={s.item}>
                  <code>{event.event_type}</code>
                  <span className={s.meta}>{formatDate(event.created_at)}</span>
                </li>
              ))}
            </ul>
          </details>
        </section>
      </div>
    </div>
  );
}
