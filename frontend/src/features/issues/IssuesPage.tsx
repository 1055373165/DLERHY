import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { queryClient } from "../../app/queryClient";
import { useBook } from "../book/BookPage";
import { StatusBadge } from "../../components/StatusBadge";
import {
  actionStatusLabel,
  actionTypeLabel,
  detectorLabel,
  issueEventLabel,
  issueExplanation,
  issueStatusLabel,
  issueTypeCopy,
  rootCauseLabel,
  scopeLabel,
  severityLabel,
} from "../../lib/copy";
import { Surface } from "../../components/Surface";
import {
  type Issue,
  type IssueFilter,
  type IssueTransition,
  executeIssueAction,
  getIssueDetail,
  listIssues,
  transitionIssue,
} from "../../lib/api";
import { formatDate } from "../../lib/workflow";
import s from "./IssuesPage.module.css";
import { StructureEditForm } from "./StructureEditForm";

type Feedback = { tone: "success" | "error"; text: string } | null;
type Tone = "active" | "success" | "warning" | "danger" | "muted";

const REVIEWER_KEY = "book-agent.reviewer-name";

// Which human transitions each status allows; mirrors services/issues.py.
const TRANSITIONS: Record<string, IssueTransition[]> = {
  open: ["triage", "wontfix", "resolve"],
  triaged: ["wontfix", "resolve", "reopen"],
  resolved: ["reopen"],
  wontfix: ["reopen"],
};

const TRANSITION_TEXT: Record<IssueTransition, string> = {
  triage: "分诊",
  wontfix: "不修复",
  resolve: "标记已解决",
  reopen: "重新打开",
};

function statusTone(status: string): Tone {
  if (status === "open") return "danger";
  if (status === "triaged") return "warning";
  if (status === "resolved") return "success";
  return "muted";
}

function severityTone(severity: string): Tone {
  if (severity === "critical" || severity === "high") return "danger";
  if (severity === "medium") return "warning";
  return "muted";
}

function loadReviewerName(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveReviewerName(name: string): void {
  try {
    window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* per-viewer convenience only */
  }
}

function summaryOf(issue: Issue): string {
  return issueExplanation(issue.issue_type, issue.evidence_json as Record<string, unknown> | null);
}

export function IssuesPage() {
  const documentId: string | null = useBook().document_id;
  const [filter, setFilter] = useState<IssueFilter>({ status: "active" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState<string>(loadReviewerName);
  const [note, setNote] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);

  const listQuery = useQuery({
    queryKey: ["issues", documentId, filter],
    queryFn: () => listIssues(documentId as string, filter),
    enabled: Boolean(documentId),
    refetchInterval: 10000,
  });
  const entries = listQuery.data?.entries ?? [];

  useEffect(() => {
    if (selectedId && !entries.some((entry) => entry.id === selectedId) && !listQuery.isFetching) {
      setSelectedId(null);
    }
    // A detail pane asking to be filled is wasted space: open the first issue by itself.
    if (!selectedId && entries.length > 0) {
      setSelectedId(entries[0].id);
    }
  }, [entries, selectedId, listQuery.isFetching]);

  const detailQuery = useQuery({
    queryKey: ["issue", selectedId],
    queryFn: () => getIssueDetail(selectedId as string),
    enabled: Boolean(selectedId),
  });

  const transition = useMutation({
    mutationFn: async ({ issueId, action }: { issueId: string; action: IssueTransition }) => {
      const name = reviewer.trim() || "reviewer-ui";
      saveReviewerName(name);
      return transitionIssue(issueId, action, { actor_id: name, note: note.trim() || null });
    },
    onSuccess: async (issue, variables) => {
      setNote("");
      setFeedback({ tone: "success", text: `已${TRANSITION_TEXT[variables.action]}：${issueTypeCopy(issue.issue_type).label}` });
      await queryClient.invalidateQueries({ queryKey: ["issues", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["issue", issue.id] });
    },
    onError: (err) => {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "操作失败" });
    },
  });

  const runAction = useMutation({
    mutationFn: async (actionId: string) => executeIssueAction(actionId, { runFollowup: true }),
    onSuccess: async (result) => {
      setFeedback({
        tone: result.issue_resolved ? "success" : "error",
        text: result.issue_resolved
          ? `动作已执行，问题已解决（重译 ${result.rerun_packet_ids?.length ?? 0} 段）`
          : `动作已执行，问题仍在（重译 ${result.rerun_packet_ids?.length ?? 0} 段）`,
      });
      await queryClient.invalidateQueries({ queryKey: ["issues", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["issue", selectedId] });
    },
    onError: (err) => {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "执行失败" });
    },
  });

  const detail = detailQuery.data;
  const selected = detail?.issue;

  // With nothing to show on the right, the list takes the whole width.
  const showDetail = entries.length > 0;

  return (
    <div className={s.layout} data-single={showDetail ? undefined : true}>
      <Surface
        eyebrow="问题"
        title="问题清单"
        aside={documentId && listQuery.data ? `${listQuery.data.total_count} 条` : null}
      >
        {!documentId ? (
          <div className={s.emptyState}>先在工作台或书库中选择一本书。</div>
        ) : (
          <>
            <div className={s.filters}>
              <label className={s.fieldLabel} htmlFor="issue-status">状态</label>
              <select
                id="issue-status"
                className={s.input}
                value={filter.status ?? "active"}
                onChange={(event) => setFilter({ ...filter, status: event.target.value as IssueFilter["status"] })}
              >
                <option value="active">待处理与已分诊</option>
                <option value="open">待处理</option>
                <option value="triaged">已分诊</option>
                <option value="resolved">已解决</option>
                <option value="wontfix">不修复</option>
                <option value="all">全部</option>
              </select>
              <label className={s.fieldLabel} htmlFor="issue-detector">来源</label>
              <select
                id="issue-detector"
                className={s.input}
                value={filter.detector ?? ""}
                onChange={(event) =>
                  setFilter({ ...filter, detector: (event.target.value || undefined) as IssueFilter["detector"] })
                }
              >
                <option value="">全部</option>
                <option value="rule">规则</option>
                <option value="model">模型</option>
                <option value="human">人工</option>
              </select>
              <label className={s.checkboxLabel}>
                <input
                  type="checkbox"
                  checked={filter.blocking === true}
                  onChange={(event) => setFilter({ ...filter, blocking: event.target.checked ? true : undefined })}
                />
                只看阻断
              </label>
            </div>
            {listQuery.isError ? (
              <div className={s.feedback} data-tone="error">无法加载问题：{String((listQuery.error as Error).message)}</div>
            ) : null}
            {entries.length === 0 ? (
              <div className={s.emptyState}>{listQuery.isLoading ? "加载中…" : "没有符合条件的问题。"}</div>
            ) : (
              <ul className={s.issueList}>
                {entries.map((issue) => (
                  <li key={issue.id}>
                    <button
                      type="button"
                      className={s.issueRow}
                      data-selected={issue.id === selectedId}
                      onClick={() => setSelectedId(issue.id)}
                    >
                      <span className={s.issueType}>{issueTypeCopy(issue.issue_type).label}</span>
                      <span className={s.badges}>
                        {issue.blocking ? <StatusBadge tone="danger" label="必须处理" /> : null}
                        <StatusBadge tone={severityTone(issue.severity)} label={severityLabel(issue.severity)} />
                        <StatusBadge tone={statusTone(issue.status)} label={issueStatusLabel(issue.status)} />
                        <StatusBadge tone="muted" label={detectorLabel(issue.detector)} />
                      </span>
                      <span className={s.issueSummary}>{summaryOf(issue)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Surface>

      {showDetail && (
      <Surface eyebrow="详情" title="问题详情" aside={selected ? `v${selected.version}` : null}>
        {!selectedId ? (
          <div className={s.emptyState}>在左侧选择一个问题。</div>
        ) : !detail || !selected ? (
          <div className={s.emptyState}>{detailQuery.isError ? "无法加载详情。" : "加载中…"}</div>
        ) : (
          <div className={s.detail}>
            <div className={s.detailHeader}>
              <span className={s.issueType}>{issueTypeCopy(selected.issue_type).label}</span>
              <StatusBadge tone={severityTone(selected.severity)} label={severityLabel(selected.severity)} />
              <StatusBadge tone={statusTone(selected.status)} label={issueStatusLabel(selected.status)} />
              <StatusBadge tone="muted" label={detectorLabel(selected.detector)} />
              {selected.reopen_count ? <span className={s.meta}>重开 {selected.reopen_count} 次</span> : null}
            </div>
            <p className={s.issueSummary}>{summaryOf(selected)}</p>
            <div className={s.meta}>
              {detail.chapter_title ? `${detail.chapter_title} · ` : ""}
              {`出在${rootCauseLabel(selected.root_cause_layer)}环节 · `}
              首次发现 {formatDate(selected.created_at ?? null)}
              {selected.decided_by ? ` · 决定人 ${selected.decided_by}` : ""}
            </div>
            <div className={s.pair}>
              <div>
                <div className={s.fieldLabel}>原文</div>
                <p className={s.pairText}>{detail.source_text ?? "（无对应句子）"}</p>
              </div>
              <div>
                <div className={s.fieldLabel}>当前译文</div>
                <p className={s.pairText}>{detail.target_text ?? "（没有译文）"}</p>
              </div>
            </div>
            {selected.resolution_note ? (
              <div className={s.meta}>备注：{selected.resolution_note}</div>
            ) : null}
            <details>
              <summary className={s.fieldLabel}>技术细节</summary>
              <pre className={s.evidence}>{JSON.stringify(selected.evidence_json ?? {}, null, 2)}</pre>
            </details>

            <div className={s.decisionRow}>
              <label className={s.fieldLabel} htmlFor="issue-reviewer">处理人</label>
              <input
                id="issue-reviewer"
                className={s.input}
                value={reviewer}
                onChange={(event) => setReviewer(event.target.value)}
                placeholder="你的名字"
              />
              <label className={s.fieldLabel} htmlFor="issue-note">备注</label>
              <input
                id="issue-note"
                className={s.input}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="可选，写进问题历史"
              />
            </div>
            <div className={s.actions}>
              {(TRANSITIONS[selected.status] ?? []).map((action) => (
                <button
                  key={action}
                  type="button"
                  className="btn btn-sm"
                  disabled={transition.isPending}
                  onClick={() => transition.mutate({ issueId: selected.id, action })}
                >
                  {TRANSITION_TEXT[action]}
                </button>
              ))}
            </div>
            {feedback ? <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div> : null}

            {selected.issue_type === "STRUCTURE_SUGGESTION" && documentId && (selected.status === "open" || selected.status === "triaged") ? (
              <StructureEditForm key={selected.id} documentId={documentId} issue={selected} />
            ) : null}

            <div className={s.fieldLabel}>修复动作</div>
            {(detail.actions ?? []).length === 0 ? (
              <div className={s.emptyState}>没有计划中的动作。</div>
            ) : (
              <ul className={s.plainList}>
                {(detail.actions ?? []).map((action) => (
                  <li key={action.id} className={s.actionRow}>
                    <span className={s.meta}>
                      {actionTypeLabel(action.action_type)} · 范围：{scopeLabel(action.scope_type)} · {actionStatusLabel(action.status)}
                    </span>
                    {action.status === "planned" && (selected.status === "open" || selected.status === "triaged") ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={runAction.isPending}
                        onClick={() => runAction.mutate(action.id)}
                      >
                        {runAction.isPending ? "执行中…" : "执行并复核"}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}

            <div className={s.fieldLabel}>历史</div>
            <ol className={s.timeline}>
              {(detail.events ?? []).map((event) => (
                <li key={event.id} className={s.timelineItem}>
                  <span className={s.timelineKind}>{issueEventLabel(event.kind)}</span>
                  <span className={s.meta}>
                    v{event.version} · {event.actor_id ?? event.actor_kind} · {formatDate(event.created_at ?? null)}
                  </span>
                  {event.note ? <span className={s.meta}>{event.note}</span> : null}
                </li>
              ))}
            </ol>
          </div>
        )}
      </Surface>
      )}
    </div>
  );
}
