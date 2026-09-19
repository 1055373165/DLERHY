import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { queryClient } from "../../app/queryClient";
import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  type Approval,
  decideApproval,
  getBookGuide,
  listAgentTurns,
  listApprovals,
  listTerms,
  updateTerm,
  type GlossaryTerm,
} from "../../lib/api";
import { formatDate, statusLabel } from "../../lib/workflow";
import s from "./ApprovalsPage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

const REVIEWER_KEY = "book-agent.reviewer-name";

function loadReviewerName(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

function describeApproval(approval: Approval): { title: string; detail: string } {
  const payload = (approval.payload_json ?? {}) as Record<string, unknown>;
  if (approval.kind === "budget_extension") {
    return {
      title: "预算扩展",
      detail: `代理用尽预算（${String(payload.reason ?? "")}），批准后继续执行。`,
    };
  }
  const args = (payload.arguments ?? {}) as Record<string, unknown>;
  if (approval.kind === "lock_term") {
    return {
      title: `锁定术语：${String(args.source_term ?? "")} => ${String(args.target_term ?? "")}`,
      detail: `类型 ${String(args.term_type ?? "concept")}。锁定后审校会阻断任何不一致的译法。${payload.reason ? ` ${String(payload.reason)}` : ""}`,
    };
  }
  return {
    title: `${String(payload.tool ?? approval.kind)}`,
    detail: JSON.stringify(args),
  };
}

export function ApprovalsPage() {
  const { currentDocument } = useWorkspace();
  const documentId = currentDocument?.document_id ?? null;
  const [reviewer, setReviewer] = useState<string>(loadReviewerName);
  const [note, setNote] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);

  const approvalsQuery = useQuery({
    queryKey: ["approvals", documentId],
    queryFn: () => listApprovals(documentId as string, "pending"),
    enabled: Boolean(documentId),
    refetchInterval: 5000,
  });
  const guideQuery = useQuery({
    queryKey: ["book-guide", documentId],
    queryFn: () => getBookGuide(documentId as string),
    enabled: Boolean(documentId),
    refetchInterval: 10000,
  });
  const turnsQuery = useQuery({
    queryKey: ["agent-turns", documentId],
    queryFn: () => listAgentTurns(documentId as string),
    enabled: Boolean(documentId),
    refetchInterval: 5000,
  });

  const decision = useMutation({
    mutationFn: async ({ approval, approved }: { approval: Approval; approved: boolean }) => {
      const name = reviewer.trim() || "reviewer-ui";
      try {
        window.localStorage.setItem(REVIEWER_KEY, name);
      } catch {
        /* per-viewer convenience only */
      }
      return decideApproval(approval.id, approved, { decided_by: `human:${name}`, note: note.trim() || null });
    },
    onSuccess: async (result) => {
      setNote("");
      setFeedback({ tone: "success", text: `已${result.status === "approved" ? "批准" : "拒绝"}：${result.kind}` });
      await queryClient.invalidateQueries({ queryKey: ["approvals", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["book-guide", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["agent-turns", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["run"] });
    },
    onError: (err) => {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "操作失败" });
    },
  });

  const pending = approvalsQuery.data ?? [];
  const turns = turnsQuery.data ?? [];

  return (
    <div className={s.layout}>
      <Surface
        eyebrow="审批"
        title="审批收件箱"
        aside={documentId ? <StatusBadge tone={pending.length ? "warning" : "success"} label={pending.length ? `${pending.length} 待审` : "无待审"} /> : null}
      >
        {!documentId ? (
          <div className={s.emptyState}>先在工作台或书库中选择一本书。</div>
        ) : (
          <>
            <div className={s.reviewerRow}>
              <label className={s.fieldLabel} htmlFor="reviewer-name">审批人</label>
              <input
                id="reviewer-name"
                className={s.input}
                value={reviewer}
                onChange={(event) => setReviewer(event.target.value)}
                placeholder="你的名字"
              />
              <label className={s.fieldLabel} htmlFor="approval-note">备注</label>
              <input
                id="approval-note"
                className={s.input}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="可选，会写进审计"
              />
            </div>
            {approvalsQuery.isError ? (
              <div className={s.feedback} data-tone="error">无法加载审批：{String((approvalsQuery.error as Error).message)}</div>
            ) : null}
            {pending.length === 0 ? (
              <div className={s.emptyState}>{approvalsQuery.isLoading ? "加载中…" : "没有等待审批的项目。"}</div>
            ) : (
              <div className={s.approvalList}>
                {pending.map((approval) => {
                  const { title, detail } = describeApproval(approval);
                  return (
                    <div key={approval.id} className={s.approvalRow}>
                      <div className={s.approvalMain}>
                        <span className={s.approvalTitle}>{title}</span>
                        <span className={s.approvalDetail}>{detail}</span>
                        <span className={s.approvalMeta}>
                          {approval.policy_id ?? approval.kind} · {formatDate(approval.created_at ?? null)}
                        </span>
                      </div>
                      <div className={s.approvalActions}>
                        <button
                          className="btn btn-sm btn-approve"
                          disabled={decision.isPending}
                          onClick={() => decision.mutate({ approval, approved: true })}
                        >
                          批准
                        </button>
                        <button
                          className="btn btn-sm btn-reject"
                          disabled={decision.isPending}
                          onClick={() => decision.mutate({ approval, approved: false })}
                        >
                          拒绝
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {feedback ? <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div> : null}
          </>
        )}
      </Surface>

      {documentId ? <GlossaryPanel documentId={documentId} reviewer={reviewer} /> : null}

      <Surface eyebrow="代理" title="代理回合" aside={documentId ? `${turns.length} 回合` : null}>
        {turns.length === 0 ? (
          <div className={s.emptyState}>还没有代理回合。</div>
        ) : (
          <div className={s.turnList}>
            {turns.map((turn) => {
              const usage = (turn.usage_json ?? {}) as Record<string, unknown>;
              return (
                <div key={turn.id} className={s.turnRow}>
                  <span className={s.turnKind}>{turn.agent_kind}</span>
                  <StatusBadge
                    tone={turn.status === "succeeded" ? "success" : turn.status === "failed" ? "danger" : turn.status === "running" ? "active" : "warning"}
                    label={statusLabel(turn.status)}
                  />
                  <span className={s.turnMeta}>
                    {String(usage.steps ?? 0)} 步 · {String(usage.tool_calls ?? 0)} 次工具 · {String(usage.token_in ?? 0)}/{String(usage.token_out ?? 0)} tokens
                    {turn.stop_reason ? ` · ${turn.stop_reason}` : ""}
                  </span>
                  <span className={s.turnDate}>{formatDate(turn.started_at ?? null)}</span>
                </div>
              );
            })}
          </div>
        )}
      </Surface>

      <Surface
        eyebrow="BOOK.MD"
        title="书级指南"
        aside={
          guideQuery.data
            ? `${guideQuery.data.decision_count} 决策 · ${guideQuery.data.locked_term_count} 锁定 · ${guideQuery.data.preferred_term_count} 首选`
            : null
        }
      >
        {guideQuery.data ? (
          <pre className={s.markdown}>{guideQuery.data.markdown}</pre>
        ) : (
          <div className={s.emptyState}>{documentId ? "加载中…" : "选择一本书后显示。"}</div>
        )}
      </Surface>
    </div>
  );
}

function lockedByLabel(lockedBy: string | null | undefined): string {
  if (!lockedBy) return "";
  if (lockedBy === "auto.fast_reading_mode") return "快速阅读模式自动锁定";
  if (lockedBy.startsWith("auto.")) return "按规则自动锁定";
  if (lockedBy.startsWith("human:")) return `${lockedBy.slice("human:".length)} 确认`;
  return lockedBy;
}

/** The book's glossary: what was locked (also without asking, in fast reading mode) and a way to change it. */
function GlossaryPanel({ documentId, reviewer }: { documentId: string; reviewer: string }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const terms = useQuery({ queryKey: ["terms", documentId], queryFn: () => listTerms(documentId), refetchInterval: 15_000 });
  const save = useMutation({
    mutationFn: (payload: { term: GlossaryTerm; lock: boolean; target?: string }) =>
      updateTerm(documentId, {
        source_term: payload.term.source_term,
        target_term: payload.target,
        lock: payload.lock,
        decided_by: `human:${reviewer.trim() || "reviewer-ui"}`,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["terms", documentId], updated);
      void queryClient.invalidateQueries({ queryKey: ["book-guide", documentId] });
      setEditing(null);
      setError(null);
    },
    onError: (err) => setError(err instanceof Error ? err.message : "保存失败"),
  });
  const list = terms.data ?? [];
  const locked = list.filter((term) => term.lock_level === "locked");

  return (
    <Surface eyebrow="术语" title="术语表" aside={`${locked.length} 个锁定`}>
      <p className={s.approvalDetail}>改动对之后的翻译生效；已经翻译的内容会在下次复核时按新译法检查。</p>
      {terms.isError ? (
        <div className={s.feedback} data-tone="error">无法加载术语表：{String((terms.error as Error).message)}</div>
      ) : locked.length === 0 ? (
        <div className={s.emptyState}>{terms.isLoading ? "加载中…" : "还没有锁定的术语。"}</div>
      ) : (
        <div className={s.approvalList}>
          {locked.map((term) => (
            <div key={term.source_term} className={s.approvalRow}>
              <div className={s.approvalMain}>
                {editing === term.source_term ? (
                  <label className={s.approvalTitle}>
                    {term.source_term} =&gt;{" "}
                    <input
                      className={s.input}
                      aria-label={`${term.source_term} 的译法`}
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                    />
                  </label>
                ) : (
                  <span className={s.approvalTitle}>
                    {term.source_term} =&gt; {term.target_term}
                  </span>
                )}
                <span className={s.approvalMeta}>{lockedByLabel(term.locked_by)}</span>
              </div>
              <div className={s.approvalActions}>
                {editing === term.source_term ? (
                  <>
                    <button
                      className="btn btn-sm btn-approve"
                      disabled={save.isPending || !draft.trim()}
                      onClick={() => save.mutate({ term, lock: true, target: draft.trim() })}
                    >
                      保存
                    </button>
                    <button className="btn btn-sm" onClick={() => setEditing(null)}>
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="btn btn-sm"
                      onClick={() => {
                        setEditing(term.source_term);
                        setDraft(term.target_term);
                      }}
                    >
                      修改
                    </button>
                    <button
                      className="btn btn-sm btn-reject"
                      disabled={save.isPending}
                      onClick={() => save.mutate({ term, lock: false })}
                    >
                      解锁
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {error ? <div className={s.feedback} data-tone="error">{error}</div> : null}
    </Surface>
  );
}

