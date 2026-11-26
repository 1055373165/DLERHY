import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import { NotifyMe } from "../../components/NotifyMe";
import { decideApproval, resumeRun } from "../../lib/api";
import { getInbox } from "../../lib/overview";
import { EvidenceImages } from "../book/BookPage";
import s from "./InboxPage.module.css";

/** Everything that waits on you, across books, each with the one thing to do about it. */
export function InboxPage() {
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => getInbox(), refetchInterval: 15_000 });
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
    <div className={s.layout}>
      <header className="page-header">
        <div>
          <h1>需要你处理</h1>
          {inbox.data && <p>{inbox.data.count} 件待办</p>}
        </div>
        <NotifyMe label="有事等我处理时通知我" />
      </header>
      <section className={s.panel} aria-label="待办列表">
        {inbox.isError ? (
          <div className={s.error}>没能加载：{(inbox.error as Error).message}</div>
        ) : items.length === 0 ? (
          <div className={s.empty}>{inbox.isLoading ? "加载中…" : "没有需要你处理的事。翻译都在自己往前走。"}</div>
        ) : (
          <ul className={s.list}>
            {items.map((item) => (
              <li key={`${item.kind}-${item.id}`} className={s.item}>
                <div className={s.itemBody}>
                  <Link to={`/books/${item.document_id}`}>{item.document_title ?? "未命名"}</Link>
                  <h2>{item.title}</h2>
                  {item.detail && <div className={s.meta}>{item.detail}</div>}
                  {(item.images ?? []).length > 0 && <EvidenceImages images={item.images ?? []} />}
                </div>
                <div className={s.actions}>
                  {item.action.kind === "approve" && (
                    <>
                      <button className="btn btn-sm btn-approve" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: true })}>
                        {item.action.approve_label ?? "批准"}
                      </button>{" "}
                      <button className="btn btn-sm btn-reject" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: false })}>
                        {item.action.reject_label ?? "拒绝"}
                      </button>
                    </>
                  )}
                  {item.kind === "question" && item.action.target && (
                    <>
                      {" "}
                      <Link className="btn btn-sm" to={item.action.target}>
                        {item.action.label ?? "看质检报告"}
                      </Link>
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
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
