import { useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import type { ChapterWorklistTimelineEntry, ExecuteActionResponse } from "../../lib/api";
import {
  documentBadge,
  formatDate,
  formatNumber,
  getPrimaryRunAction,
  preferredTitle,
  sourceLabel,
  statusLabel,
} from "../../lib/workflow";
import s from "./WorkspacePage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

export function WorkspacePage() {
  const {
    currentDocument,
    currentRun,
    currentExports,
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
    uploadPending,
    runActionPending,
    reviewDecisionPending,
    actionExecutionPending,
    approveMemoryProposal,
    rejectMemoryProposal,
    executeChapterAction,
    uploadFile,
    runPrimaryAction,
    downloadChapterAsset,
  } = useWorkspace();

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const doc = currentDocument;
  const action = getPrimaryRunAction(doc, currentRun);
  const badge = documentBadge(doc, currentRun);
  const queueEntries = chapterWorklist?.entries ?? [];

  /* ── Handlers ── */

  async function handleUpload() {
    if (!selectedFile) return;
    try {
      const result = await uploadFile(selectedFile);
      setFeedback({ tone: "success", text: `[OK] Loaded: ${result.title ?? selectedFile.name}` });
      setSelectedFile(null);
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Upload failed"}` });
    }
  }

  async function handlePrimaryAction() {
    try {
      await runPrimaryAction();
      setFeedback({ tone: "success", text: `[OK] ${action.label}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Action failed"}` });
    }
  }

  async function handleProposal(proposalId: string, accept: boolean) {
    const payload = { actor_name: "reviewer-ui" };
    try {
      if (accept) {
        await approveMemoryProposal(proposalId, payload);
      } else {
        await rejectMemoryProposal(proposalId, payload);
      }
      setFeedback({ tone: "success", text: `[OK] Proposal ${accept ? "approved" : "rejected"}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Decision failed"}` });
    }
  }

  async function handleAction(actionId: string) {
    try {
      const result: ExecuteActionResponse = await executeChapterAction(actionId, true);
      const ok = result.status === "executed" || result.issue_resolved;
      setFeedback({
        tone: ok ? "success" : "error",
        text: `[${ok ? "OK" : "ERR"}] action ${result.action_id.slice(0, 8)} :: ${result.status}`,
      });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Execute failed"}` });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: `[OK] Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Download failed"}` });
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
      {/* ══════════════ INGEST PANEL ══════════════ */}
      <Surface eyebrow="INGEST" title="载入书稿">
        <div className={s.uploadZone}>
          <label className={s.fileLabel}>
            <input
              type="file"
              accept=".pdf,.epub"
              className={s.fileInput}
              onChange={(e) => {
                setSelectedFile(e.target.files?.[0] ?? null);
                setFeedback(null);
              }}
            />
            <span className={s.fileText}>
              {selectedFile ? `> ${selectedFile.name}` : "[ SELECT .PDF / .EPUB ]"}
            </span>
          </label>
          <button
            className={s.btnPrimary}
            disabled={!selectedFile || uploadPending}
            onClick={handleUpload}
          >
            {uploadPending ? "UPLOADING..." : "BOOTSTRAP"}
          </button>
        </div>
        {feedback && (
          <div className={s.feedback} data-tone={feedback.tone}>
            {feedback.text}
          </div>
        )}
      </Surface>

      {/* ══════════════ DOCUMENT HERO ══════════════ */}
      {doc && (
        <Surface
          eyebrow="ACTIVE DOCUMENT"
          title={preferredTitle(doc)}
          aside={<StatusBadge label={badge.label} tone={badge.tone} />}
        >
          <div className={s.statGrid}>
            <Stat label="STATUS" value={statusLabel(doc.status)} />
            <Stat label="SOURCE" value={sourceLabel(doc.source_type)} />
            <Stat label="CHAPTERS" value={formatNumber(doc.chapter_count)} />
            <Stat label="PACKETS" value={formatNumber(doc.packet_count)} />
            <Stat label="SENTENCES" value={formatNumber(doc.sentence_count)} />
          </div>

          {action.mode !== "disabled" && (
            <div className={s.actionBar}>
              <button
                className={s.btnAction}
                disabled={action.disabled || runActionPending}
                onClick={handlePrimaryAction}
              >
                {runActionPending ? "EXECUTING..." : `$ ${action.label}`}
              </button>
            </div>
          )}
        </Surface>
      )}

      {/* ══════════════ CHAPTER QUEUE + DETAIL ══════════════ */}
      {doc && (
        <div className={s.workbench}>
          {/* ── Queue Rail ── */}
          <div className={s.queueRail}>
            <div className={s.queueHeader}>
              <h3 className={s.queueTitle}>CHAPTER QUEUE</h3>
              <span className={s.queueCount}>{queueEntries.length}</span>
            </div>

            <div className={s.queueFilters}>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.queuePriority}
                onChange={(e) => setChapterQueuePriorityFilter(e.target.value as "all" | "immediate" | "high" | "medium")}
              >
                <option value="all">ALL PRI</option>
                <option value="immediate">IMMEDIATE</option>
                <option value="high">HIGH</option>
                <option value="medium">MEDIUM</option>
              </select>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.assignment}
                onChange={(e) => setChapterAssignmentFilter(e.target.value as "all" | "assigned" | "unassigned")}
              >
                <option value="all">ALL ASSIGN</option>
                <option value="assigned">ASSIGNED</option>
                <option value="unassigned">UNASSIGNED</option>
              </select>
              {(chapterWorklistFilters.queuePriority !== "all" ||
                chapterWorklistFilters.assignment !== "all") && (
                <button className={s.filterClear} onClick={clearChapterWorklistFilters}>
                  RESET
                </button>
              )}
            </div>

            <div className={s.queueList}>
              {chapterWorklistLoading && <div className={s.loading}>SCANNING...</div>}
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
              {!chapterWorklistLoading && queueEntries.length === 0 && (
                <div className={s.emptyQueue}>NO CHAPTERS IN QUEUE</div>
              )}
            </div>
          </div>

          {/* ── Detail Panel ── */}
          <div className={s.detailPanel}>
            {!selectedReviewChapterId && (
              <div className={s.detailEmpty}>
                <span className={s.prompt}>$</span> SELECT A CHAPTER FROM THE QUEUE
                <span className={s.cursor} />
              </div>
            )}

            {selectedReviewChapterId && currentChapterReviewLoading && (
              <div className={s.detailEmpty}>LOADING CHAPTER DATA...</div>
            )}

            {selectedReviewChapterId && detail && (
              <>
                {/* Chapter Header */}
                <div className={s.chapterHeader}>
                  <h3 className={s.chapterTitle}>
                    CH.{selectedChapterEntry?.ordinal ?? detail.ordinal} :: {detail.title_src}
                  </h3>
                  <div className={s.chapterMeta}>
                    <Stat label="ISSUES" value={String(detail.current_open_issue_count ?? 0)} inline />
                    <Stat label="PROPOSALS" value={String(proposals.length)} inline />
                    <Stat label="ACTIONS" value={String(actions.length)} inline />
                  </div>
                  <button
                    className={s.btnSmall}
                    onClick={() => handleChapterDownload(selectedReviewChapterId)}
                  >
                    DOWNLOAD BILINGUAL
                  </button>
                </div>

                {/* Memory Proposals */}
                {proposals.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>MEMORY PROPOSALS</h4>
                    {proposals.map((p) => (
                      <div key={p.proposal_id} className={s.proposalCard}>
                        <div className={s.proposalType}>{p.status}</div>
                        <div className={s.proposalContent}>
                          <span className={s.proposalLabel}>id:</span> {p.proposal_id.slice(0, 12)}
                        </div>
                        <div className={s.proposalContent}>
                          <span className={s.proposalLabel}>packet:</span> {p.packet_id.slice(0, 12)}
                        </div>
                        <div className={s.proposalActions}>
                          <button
                            className={s.btnApprove}
                            disabled={reviewDecisionPending}
                            onClick={() => handleProposal(p.proposal_id, true)}
                          >
                            APPROVE
                          </button>
                          <button
                            className={s.btnReject}
                            disabled={reviewDecisionPending}
                            onClick={() => handleProposal(p.proposal_id, false)}
                          >
                            REJECT
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Issues */}
                {issues.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>RECENT ISSUES</h4>
                    {issues.map((issue) => (
                      <div key={issue.issue_id} className={s.issueCard}>
                        <div className={s.issueTop}>
                          <StatusBadge
                            label={issue.status}
                            tone={
                              issue.status === "resolved"
                                ? "success"
                                : issue.severity === "blocking"
                                  ? "danger"
                                  : "warning"
                            }
                          />
                          <span className={s.issueType}>
                            {issue.issue_type} :: {issue.root_cause_layer}
                          </span>
                        </div>
                        {issue.suggested_action && (
                          <div className={s.issueDetail}>{issue.suggested_action}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Actions */}
                {actions.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>PENDING ACTIONS</h4>
                    {actions.map((act) => (
                      <div key={act.action_id} className={s.issueCard}>
                        <div className={s.issueTop}>
                          <StatusBadge
                            label={act.status}
                            tone={act.status === "executed" ? "success" : "warning"}
                          />
                          <span className={s.issueType}>{act.action_type}</span>
                        </div>
                        <button
                          className={s.btnSmall}
                          disabled={actionExecutionPending}
                          onClick={() => handleAction(act.action_id)}
                        >
                          {`> EXECUTE ${act.action_type}`}
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Timeline */}
                {timeline.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>TIMELINE</h4>
                    <div className={s.timelineList}>
                      {timeline.slice(0, 20).map((entry: ChapterWorklistTimelineEntry) => (
                        <div key={entry.event_id} className={s.timelineEntry}>
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

/* ── Stat Helper ── */
function Stat({ label, value, inline }: { label: string; value: string; inline?: boolean }) {
  return (
    <div className={s.stat} data-inline={inline ?? false}>
      <span className={s.statLabel}>{label}</span>
      <span className={s.statValue}>{value}</span>
    </div>
  );
}
