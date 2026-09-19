import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import type { ChapterWorklistTimelineEntry, ExecuteActionResponse } from "../../lib/api";
import { listDocumentChapters } from "../../lib/api";
import { formatDate, formatNumber, statusLabel } from "../../lib/workflow";
import s from "./WorkspacePage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

/** The book page's 逐章审校 tab: chapter queue, memory proposals, issues and repairs (for reviewers). */
export function WorkspacePage() {
  const {
    currentDocument,
    chapterWorklist,
    chapterWorklistLoading,
    chapterWorklistFilters,
    setChapterQueuePriorityFilter,
    setChapterAssignmentFilter,
    clearChapterWorklistFilters,
    selectedReviewChapterId,
    selectReviewChapter,
    currentChapterReviewDetail,
    currentChapterReviewLoading,
    reviewDecisionPending,
    actionExecutionPending,
    approveMemoryProposal,
    rejectMemoryProposal,
    executeChapterAction,
    downloadChapterAsset,
  } = useWorkspace();

  const [feedback, setFeedback] = useState<Feedback>(null);

  const doc = currentDocument;
  const queueEntries = chapterWorklist?.entries ?? [];

  // Fetch all chapters so users can browse even when worklist is empty
  const allChaptersQuery = useQuery({
    queryKey: ["document-chapters", doc?.document_id],
    queryFn: () => listDocumentChapters(doc!.document_id),
    enabled: !!doc,
  });
  const allChapters = allChaptersQuery.data ?? [];

  /* ── Handlers ── */

  async function handleProposal(proposalId: string, accept: boolean) {
    const payload = { actor_name: "reviewer-ui" };
    try {
      if (accept) {
        await approveMemoryProposal(proposalId, payload);
      } else {
        await rejectMemoryProposal(proposalId, payload);
      }
      setFeedback({ tone: "success", text: `Proposal ${accept ? "approved" : "rejected"}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "提交失败" });
    }
  }

  async function handleAction(actionId: string) {
    try {
      const result: ExecuteActionResponse = await executeChapterAction(actionId, true);
      // The API answers "completed" once the action ran; the issue itself may still be open.
      const ran = result.status === "completed";
      setFeedback({
        tone: ran ? "success" : "error",
        text: !ran
          ? "修复没有执行成功，请稍后重试。"
          : result.issue_resolved
            ? "修复已执行，问题已解决。"
            : "修复已执行，问题还在，复核后会再次检查。",
      });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "执行失败" });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: `已下载：${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "下载失败" });
    }
  }

  /* ── Derived ── */
  const detail = currentChapterReviewDetail;
  const proposals = detail?.memory_proposals?.pending_proposals ?? [];
  const timeline = detail?.timeline ?? [];
  const issues = detail?.recent_issues ?? [];
  const actions = detail?.recent_actions ?? [];
  const selectedChapterEntry = queueEntries.find((e) => e.chapter_id === selectedReviewChapterId);

  return (
    <div className={s.layout}>
      {feedback && (
        <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
      )}

      {/* ── Chapter Workbench ── */}
      {doc && (
        <div className={s.workbench}>
          {/* Queue Rail */}
          <aside className={s.queueRail}>
            <div className={s.queueHeader}>
              <h3 className={s.queueTitle}>章节</h3>
              <span className={s.queueCount}>{queueEntries.length || allChapters.length}</span>
            </div>

            <div className={s.queueFilters}>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.queuePriority}
                onChange={(e) => setChapterQueuePriorityFilter(e.target.value as "all" | "immediate" | "high" | "medium")}
              >
                <option value="all">全部优先级</option>
                <option value="immediate">立即</option>
                <option value="high">高</option>
                <option value="medium">中</option>
              </select>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.assignment}
                onChange={(e) => setChapterAssignmentFilter(e.target.value as "all" | "assigned" | "unassigned")}
              >
                <option value="all">全部指派</option>
                <option value="assigned">已指派</option>
                <option value="unassigned">未指派</option>
              </select>
              {(chapterWorklistFilters.queuePriority !== "all" ||
                chapterWorklistFilters.assignment !== "all") && (
                <button className={s.filterClear} onClick={clearChapterWorklistFilters}>
                  清除
                </button>
              )}
            </div>

            <div className={s.queueList}>
              {chapterWorklistLoading && <div className={s.loading}>加载中…</div>}
              {queueEntries.map((entry) => (
                <button
                  key={entry.chapter_id}
                  className={s.queueCard}
                  data-active={entry.chapter_id === selectedReviewChapterId}
                  onClick={() => selectReviewChapter(entry.chapter_id)}
                >
                  <div className={s.queueCardTop}>
                    <span className={s.queueOrdinal}>CH.{entry.ordinal}</span>
                    <span className={s.queuePriority} data-pri={entry.queue_priority}>
                      {entry.queue_priority}
                    </span>
                  </div>
                  <div className={s.queueCardTitle}>{entry.title_src}</div>
                  <div className={s.queueCardMeta}>
                    <span>{entry.open_issue_count} issues</span>
                    <span>{entry.memory_proposals.pending_proposal_count} proposals</span>
                  </div>
                </button>
              ))}
              {!chapterWorklistLoading && queueEntries.length === 0 && allChapters.length > 0 && (
                allChapters.map((ch) => (
                  <button
                    key={ch.chapter_id}
                    className={s.queueCard}
                    data-active={ch.chapter_id === selectedReviewChapterId}
                    onClick={() => selectReviewChapter(ch.chapter_id)}
                  >
                    <div className={s.queueCardTop}>
                      <span className={s.queueOrdinal}>CH.{ch.ordinal}</span>
                    </div>
                    <div className={s.queueCardTitle}>{ch.title_tgt || ch.title_src || `Chapter ${ch.ordinal}`}</div>
                  </button>
                ))
              )}
              {!chapterWorklistLoading && queueEntries.length === 0 && allChapters.length === 0 && (
                <div className={s.emptyQueue}>没有章节。</div>
              )}
            </div>
          </aside>

          {/* Detail Panel */}
          <div className={s.detailPanel}>
            {!selectedReviewChapterId && (
              <div className={s.detailEmpty}>从左侧选择一个章节。</div>
            )}

            {selectedReviewChapterId && currentChapterReviewLoading && (
              <div className={s.detailEmpty}>正在加载章节…</div>
            )}

            {selectedReviewChapterId && detail && (
              <>
                {/* Chapter Header */}
                <div className={s.chapterHeader}>
                  <div className={s.chapterHeaderTop}>
                    <h3 className={s.chapterTitle}>
                      CH.{selectedChapterEntry?.ordinal ?? detail.ordinal} {detail.title_src}
                    </h3>
                    <button
                      className="btn btn-sm"
                      onClick={() => handleChapterDownload(selectedReviewChapterId)}
                    >
                      下载本章对照版
                    </button>
                  </div>
                  <div className={s.chapterStats}>
                    <Stat label="问题" value={String(detail.current_open_issue_count ?? 0)} />
                    <Stat label="记忆" value={String(proposals.length)} />
                    <Stat label="修复" value={String(actions.length)} />
                  </div>
                </div>

                {/* Memory Proposals */}
                {proposals.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>待确认的记忆</h4>
                    <div className={s.proposalList}>
                      {proposals.map((p) => (
                        <div key={p.proposal_id} className={s.proposalRow}>
                          <div className={s.proposalInfo}>
                            <span className={s.proposalId}>{p.proposal_id.slice(0, 10)}</span>
                            <span className={s.proposalMeta}>pkt {p.packet_id.slice(0, 10)}</span>
                          </div>
                          <div className={s.proposalActions}>
                            <button
                              className="btn btn-sm btn-approve"
                              disabled={reviewDecisionPending}
                              onClick={() => handleProposal(p.proposal_id, true)}
                            >
                              批准
                            </button>
                            <button
                              className="btn btn-sm btn-reject"
                              disabled={reviewDecisionPending}
                              onClick={() => handleProposal(p.proposal_id, false)}
                            >
                              拒绝
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Issues */}
                {issues.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>最近的问题</h4>
                    <div className={s.issueList}>
                      {issues.map((issue) => (
                        <div key={issue.issue_id} className={s.issueRow}>
                          <StatusBadge
                            label={issue.status}
                            tone={
                              issue.status === "resolved" ? "success"
                              : issue.severity === "blocking" ? "danger"
                              : "warning"
                            }
                          />
                          <span className={s.issueType}>
                            {issue.issue_type} :: {issue.root_cause_layer}
                          </span>
                          {issue.suggested_action && (
                            <span className={s.issueSuggestion}>{issue.suggested_action}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Actions */}
                {actions.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>待执行的修复</h4>
                    <div className={s.actionList}>
                      {actions.map((act) => (
                        <div key={act.action_id} className={s.actionRow}>
                          <StatusBadge
                            label={act.status}
                            tone={act.status === "completed" ? "success" : "warning"}
                          />
                          <span className={s.actionType}>{act.action_type}</span>
                          <button
                            className="btn btn-sm"
                            disabled={actionExecutionPending}
                            onClick={() => handleAction(act.action_id)}
                          >
                            执行
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Timeline */}
                {timeline.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>时间线</h4>
                    <div className={s.timelineList}>
                      {timeline.slice(0, 20).map((entry: ChapterWorklistTimelineEntry) => (
                        <div key={entry.event_id} className={s.timelineRow}>
                          <span className={s.timelineKind}>{entry.source_kind}</span>
                          <span className={s.timelineText}>
                            {entry.event_kind}
                            {entry.note ? ` — ${entry.note}` : ""}
                          </span>
                          <span className={s.timelineDate}>{formatDate(entry.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Inline Stat ── */
function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className={s.stat}>
      <span className={s.statLabel}>{label}</span>
      <span className={s.statValue}>{value}</span>
    </span>
  );
}
