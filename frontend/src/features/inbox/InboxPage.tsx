import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import { decideApproval, resumeRun } from "../../lib/api";
import { getInbox } from "../../lib/overview";
import s from "../book/Tabs.module.css";

/** Everything that waits on you, across books, each with the one thing to do about it. */
export function InboxPage() {
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: getInbox, refetchInterval: 15_000 });
  const settle = async () => {
    await queryClient.invalidateQueries({ queryKey: ["inbox"] });
    await queryClient.invalidateQueries({ queryKey: ["overview"] });
  };
  const decide = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) => decideApproval(id, approved, { decided_by: "human:reader" }),
    onSettled: settle,
  });
  const resume = useMutation({
    mutationFn: (runId: string) => resumeRun(runId, { actor_id: "reader-ui", note: "resumed from the inbox" }),
    onSettled: settle,
  });
  const items = inbox.data?.items ?? [];
  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <h1 className={s.heading} style={{ fontSize: "1.25rem" }}>
        需要你处理
      </h1>
      {inbox.isError ? (
        <div className={s.error}>没能加载：{(inbox.error as Error).message}</div>
      ) : items.length === 0 ? (
        <div className={s.empty}>{inbox.isLoading ? "加载中…" : "没有需要你处理的事。翻译都在自己往前走。"}</div>
      ) : (
        <ul className={s.list}>
          {items.map((item) => (
            <li key={`${item.kind}-${item.id}`} className={s.item}>
              <span>
                <Link to={`/books/${item.document_id}`}>{item.document_title ?? "未命名"}</Link>
                <span> · {item.title}</span>
                {item.detail && <div className={s.meta}>{item.detail}</div>}
              </span>
              <span>
                {item.action.kind === "approve" && (
                  <>
                    <button className="btn btn-sm btn-approve" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: true })}>
                      批准
                    </button>{" "}
                    <button className="btn btn-sm btn-reject" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: false })}>
                      拒绝
                    </button>
                  </>
                )}
                {item.action.kind === "link" && item.action.target && (
                  <Link className="btn btn-sm" to={item.action.target}>
                    {item.action.label ?? "去处理"}
                  </Link>
                )}
                {item.kind === "paused" && (
                  <>
                    {" "}
                    <button className="btn btn-sm" disabled={resume.isPending} onClick={() => resume.mutate(item.id)}>
                      继续翻译
                    </button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
