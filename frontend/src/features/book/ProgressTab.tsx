import { useQuery } from "@tanstack/react-query";

import { getRunEvents } from "../../lib/api";
import { describeRunEvent, formatDate } from "../../lib/workflow";
import { useBook } from "./BookPage";
import s from "./Tabs.module.css";

const STEP_STATUS: Record<string, string> = {
  pending: "等待中",
  queued: "排队中",
  running: "进行中",
  awaiting_approval: "等你审批",
  succeeded: "完成",
  partial: "部分完成",
  failed: "失败",
  paused: "已暂停",
  cancelled: "已取消",
};

/** The steps of the book's translation and what happened, in sentences. */
export function ProgressTab() {
  const book = useBook();
  const runId = book.translation.run_id;
  const events = useQuery({
    queryKey: ["run-events", runId],
    queryFn: () => getRunEvents(runId as string),
    enabled: Boolean(runId),
  });
  if (!runId) {
    return <div className={s.empty}>还没开始翻译。</div>;
  }
  const described = (events.data?.entries ?? [])
    .map((event) => ({ event, text: describeRunEvent(event) }))
    .filter((item) => item.text !== null);
  return (
    <div className={s.columns}>
      <section>
        <h3 className={s.heading}>步骤</h3>
        <ol className={s.steps}>
          {book.translation.steps.map((step) => (
            <li key={step.key} className={s.step} data-status={step.status}>
              <span>{step.label}</span>
              <span className={s.stepStatus}>{STEP_STATUS[step.status] ?? step.status}</span>
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h3 className={s.heading}>最近动态</h3>
        {events.isError ? (
          <div className={s.error}>动态没能加载：{(events.error as Error).message}</div>
        ) : described.length === 0 ? (
          <div className={s.empty}>{events.isLoading ? "加载中…" : "还没有动态。"}</div>
        ) : (
          <ul className={s.events}>
            {described.map(({ event, text }) => (
              <li key={event.event_id} className={s.event}>
                <span className={s.eventTitle}>{text?.title}</span>
                {text?.detail && <span className={s.eventDetail}>{text.detail}</span>}
                <span className={s.eventDate}>{formatDate(event.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
