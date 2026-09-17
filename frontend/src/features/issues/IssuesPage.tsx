import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { queryClient } from "../../app/queryClient";
import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  type Issue,
  type IssueFilter,
  type IssueTransition,
  getIssueDetail,
  listIssues,
  transitionIssue,
} from "../../lib/api";
import { formatDate } from "../../lib/workflow";
import s from "./IssuesPage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;
type Tone = "active" | "success" | "warning" | "danger" | "muted";

const REVIEWER_KEY = "book-agent.reviewer-name";

const STATUS_TEXT: Record<string, string> = {
  open: "待处理",
  triaged: "已分诊",
  resolved: "已解决",
  wontfix: "不修复",
};

const DETECTOR_TEXT: Record<string, string> = {
  rule: "规则",
  model: "模型",
  human: "人工",
};

const EVENT_TEXT: Record<string, string> = {
  opened: "发现",
  updated: "更新",
  reopened: "重新打开",
  seen_while_closed: "关闭后再次发现",
  resolved: "解决",
  triaged: "分诊",
  wontfix: "标记不修复",
  action_replanned: "重新计划动作",
};

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

function evidenceSummary(issue: Issue): string {
  const evidence = (issue.evidence_json ?? {}) as Record<string, unknown>;
  const explanation = evidence.explanation ?? evidence.reason;
  return explanation ? String(explanation) : "";
}

export function IssuesPage() {
  const { currentDocument } = useWorkspace();
  const documentId = currentDocument?.document_id ?? null;
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
      setFeedback({ tone: "success", text: `${TRANSITION_TEXT[variables.action]}：${issue.issue_type}` });
      await queryClient.invalidateQueries({ queryKey: ["issues", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["issue", issue.id] });
    },
    onError: (err) => {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "操作失败" });
    },
  });

  const detail = detailQuery.data;
  const selected = detail?.issue;

  return (
    <div className={s.layout}>
      <Surface
        eyebrow="ISSUES"
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
                      <span className={s.issueType}>{issue.issue_type}</span>
                      <span className={s.badges}>
                        {issue.blocking ? <StatusBadge tone="danger" label="阻断" /> : null}
                        <StatusBadge tone={severityTone(issue.severity)} label={issue.severity} />
                        <StatusBadge tone={statusTone(issue.status)} label={STATUS_TEXT[issue.status] ?? issue.status} />
                        <StatusBadge tone="muted" label={DETECTOR_TEXT[issue.detector] ?? issue.detector} />
                      </span>
                      <span className={s.issueSummary}>{evidenceSummary(issue)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Surface>

      <Surface eyebrow="DETAIL" title="问题详情" aside={selected ? `v${selected.version}` : null}>
        {!selectedId ? (
          <div className={s.emptyState}>在左侧选择一个问题。</div>
        ) : !detail || !selected ? (
          <div className={s.emptyState}>{detailQuery.isError ? "无法加载详情。" : "加载中…"}</div>
        ) : (
          <div className={s.detail}>
            <div className={s.detailHeader}>
              <span className={s.issueType}>{selected.issue_type}</span>
              <StatusBadge tone={statusTone(selected.status)} label={STATUS_TEXT[selected.status] ?? selected.status} />
              {selected.reopen_count ? <span className={s.meta}>重开 {selected.reopen_count} 次</span> : null}
            </div>
            <div className={s.meta}>
              {detail.chapter_title ? `${detail.chapter_title} · ` : ""}
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
            <div className={s.fieldLabel}>证据</div>
            <pre className={s.evidence}>{JSON.stringify(selected.evidence_json ?? {}, null, 2)}</pre>

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

            <div className={s.fieldLabel}>修复动作</div>
            {(detail.actions ?? []).length === 0 ? (
              <div className={s.emptyState}>没有计划中的动作。</div>
            ) : (
              <ul className={s.plainList}>
                {(detail.actions ?? []).map((action) => (
                  <li key={action.id} className={s.meta}>
                    {action.action_type} · {action.scope_type} · {action.status}
                  </li>
                ))}
              </ul>
            )}

            <div className={s.fieldLabel}>历史</div>
            <ol className={s.timeline}>
              {(detail.events ?? []).map((event) => (
                <li key={event.id} className={s.timelineItem}>
                  <span className={s.timelineKind}>{EVENT_TEXT[event.kind] ?? event.kind}</span>
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
    </div>
  );
}
