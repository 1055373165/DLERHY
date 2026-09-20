import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getRunEvents, listAgentTurns } from "../../lib/api";
import { agentKindLabel, turnStatusLabel } from "../../lib/copy";
import { formatDate } from "../../lib/workflow";
import { useBook } from "./BookPage";
import s from "./Tabs.module.css";

/** For the technically curious: every event of the translation run and each agent turn, as recorded. */
export function LogTab() {
  const book = useBook();
  const runId = book.translation.run_id;
  // Same as the 进度 tab: the raw tail loads when someone unfolds it.
  const [rawOpen, setRawOpen] = useState(false);
  const events = useQuery({
    queryKey: ["run-events", runId, "all"],
    queryFn: () => getRunEvents(runId as string, { all: true, limit: 100 }),
    enabled: Boolean(runId) && rawOpen,
  });
  const turns = useQuery({ queryKey: ["agent-turns", book.document_id], queryFn: () => listAgentTurns(book.document_id) });
  return (
    <div className={s.columns}>
      <section>
        <h2 className={s.heading}>代理回合</h2>
        <ul className={s.list}>
          {(turns.data ?? []).map((turn) => {
            const usage = (turn.usage_json ?? {}) as Record<string, unknown>;
            return (
              <li key={turn.id} className={s.item}>
                <span>
                  {agentKindLabel(turn.agent_kind)} · {turnStatusLabel(turn.status)}
                  <span className={s.meta}>
                    {" "}
                    · {String(usage.steps ?? 0)} 步 · {String(usage.tool_calls ?? 0)} 次工具
                  </span>
                </span>
                <span className={s.meta}>{formatDate(turn.started_at ?? null)}</span>
              </li>
            );
          })}
        </ul>
        {turns.data?.length === 0 && <div className={s.empty}>没有代理回合。</div>}
      </section>
      <section>
        <h2 className={s.heading}>运行事件</h2>
        {!runId ? (
          <div className={s.empty}>还没开始翻译。</div>
        ) : (
          <details className={s.rawEvents} onToggle={(event) => setRawOpen(event.currentTarget.open)}>
            <summary>全部事件（技术细节{rawOpen ? `，${events.data?.entries?.length ?? 0} 条` : ""}）</summary>
            <ul className={s.list}>
              {(events.data?.entries ?? []).map((event) => (
                <li key={event.event_id} className={s.item}>
                  <code>{event.event_type}</code>
                  <span className={s.meta}>{formatDate(event.created_at)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
    </div>
  );
}
