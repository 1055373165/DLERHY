import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import type { ChapterWorklistTimelineEntry, ExecuteActionResponse } from "../../lib/api";
import { listDocumentChapters } from "../../lib/api";
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

  // Fetch all chapters so users can browse even when worklist is empty
  const allChaptersQuery = useQuery({
    queryKey: ["document-chapters", doc?.document_id],
    queryFn: () => listDocumentChapters(doc!.document_id),
    enabled: !!doc,
  });
  const allChapters = allChaptersQuery.data ?? [];

  /* ── Handlers ── */

  async function handleUpload() {
    if (!selectedFile) return;
    try {
      const result = await uploadFile(selectedFile);
      setFeedback({ tone: "success", text: `Loaded: ${result.title ?? selectedFile.name}` });
      setSelectedFile(null);
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Upload failed" });
    }
  }

  async function handlePrimaryAction() {
    try {
      await runPrimaryAction();
      setFeedback({ tone: "success", text: action.label });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Action failed" });
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
      setFeedback({ tone: "success", text: `Proposal ${accept ? "approved" : "rejected"}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Decision failed" });
    }
  }

  async function handleAction(actionId: string) {
    try {
      const result: ExecuteActionResponse = await executeChapterAction(actionId, true);
      const ok = result.status === "executed" || result.issue_resolved;
      setFeedback({
        tone: ok ? "success" : "error",
        text: `Action ${result.action_id.slice(0, 8)} :: ${result.status}`,
      });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Execute failed" });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: `Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Download failed" });
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
      {/* ── Upload Hero (when no document loaded) ── */}
      {!doc && (
        <div className={s.uploadHero}>
          <div className={s.uploadIllustration}>
            <svg width="80" height="80" viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="16" y="8" width="48" height="64" rx="3" />
              <path d="M28 8V4a2 2 0 012-2h20a2 2 0 012 2v4" />
              <line x1="28" y1="28" x2="52" y2="28" />
              <line x1="28" y1="36" x2="52" y2="36" />
              <line x1="28" y1="44" x2="44" y2="44" />
              <path d="M40 56l6-6 6 6" />
              <line x1="46" y1="50" x2="46" y2="64" />
            </svg>
          </div>
          <h2 className={s.uploadTitle}>载入书稿</h2>
          <p className={s.uploadSubtitle}>Upload a PDF or EPUB to begin translation</p>
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
              {selectedFile ? selectedFile.name : "Click to select .pdf / .epub file"}
            </span>
          </label>
          <div className={s.uploadActions}>
            <button
              className="btn btn-primary"
              disabled={!selectedFile || uploadPending}
              onClick={handleUpload}
            >
              {uploadPending ? "Uploading..." : "Bootstrap"}
            </button>
          </div>
          {feedback && (
            <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
          )}
        </div>
      )}

      {/* ── Compact upload (when document already loaded) ── */}
      {doc && (
        <div className={s.compactUpload}>
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
            <span className={s.fileTextSmall}>
              {selectedFile ? selectedFile.name : "Select new file..."}
            </span>
          </label>
          <button
            className="btn btn-sm"
            disabled={!selectedFile || uploadPending}
            onClick={handleUpload}
          >
            {uploadPending ? "..." : "Bootstrap"}
          </button>
          {feedback && (
            <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
          )}
        </div>
      )}

      {/* ── Document Header (dense row) ── */}
      {doc && (
        <div className={s.docHeader}>
          <div className={s.docHeaderTop}>
            <h1 className={s.docTitle}>{preferredTitle(doc)}</h1>
            <StatusBadge label={badge.label} tone={badge.tone} />
          </div>
          <div className={s.docStats}>
            <Stat label="Status" value={statusLabel(doc.status)} />
            <Stat label="Source" value={sourceLabel(doc.source_type)} />
            <Stat label="Ch" value={formatNumber(doc.chapter_count)} />
            <Stat label="Pkt" value={formatNumber(doc.packet_count)} />
            <Stat label="Sent" value={formatNumber(doc.sentence_count)} />
            {action.mode !== "disabled" && (
              <button
                className="btn btn-sm"
                disabled={action.disabled || runActionPending}
                onClick={handlePrimaryAction}
              >
                {runActionPending ? "..." : action.label}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Chapter Workbench ── */}
      {doc && (
        <div className={s.workbench}>
          {/* Queue Rail */}
          <aside className={s.queueRail}>
            <div className={s.queueHeader}>
              <h3 className={s.queueTitle}>Chapters</h3>
              <span className={s.queueCount}>{queueEntries.length || allChapters.length}</span>
            </div>

            <div className={s.queueFilters}>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.queuePriority}
                onChange={(e) => setChapterQueuePriorityFilter(e.target.value as "all" | "immediate" | "high" | "medium")}
              >
                <option value="all">All priority</option>
                <option value="immediate">Immediate</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
              </select>
              <select
                className={s.filterSelect}
                value={chapterWorklistFilters.assignment}
                onChange={(e) => setChapterAssignmentFilter(e.target.value as "all" | "assigned" | "unassigned")}
              >
                <option value="all">All assign</option>
                <option value="assigned">Assigned</option>
                <option value="unassigned">Unassigned</option>
              </select>
              {(chapterWorklistFilters.queuePriority !== "all" ||
                chapterWorklistFilters.assignment !== "all") && (
                <button className={s.filterClear} onClick={clearChapterWorklistFilters}>
                  Reset
                </button>
              )}
            </div>

            <div className={s.queueList}>
              {chapterWorklistLoading && <div className={s.loading}>Loading...</div>}
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
                <div className={s.emptyQueue}>No chapters found.</div>
              )}
            </div>
          </aside>

          {/* Detail Panel */}
          <div className={s.detailPanel}>
            {!selectedReviewChapterId && (
              <div className={s.detailEmpty}>Select a chapter from the queue.</div>
            )}

            {selectedReviewChapterId && currentChapterReviewLoading && (
              <div className={s.detailEmpty}>Loading chapter data...</div>
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
                      Download bilingual
                    </button>
                  </div>
                  <div className={s.chapterStats}>
                    <Stat label="Issues" value={String(detail.current_open_issue_count ?? 0)} />
                    <Stat label="Proposals" value={String(proposals.length)} />
                    <Stat label="Actions" value={String(actions.length)} />
                  </div>
                </div>

                {/* Memory Proposals */}
                {proposals.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>MEMORY PROPOSALS</h4>
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
                              Approve
                            </button>
                            <button
                              className="btn btn-sm btn-reject"
                              disabled={reviewDecisionPending}
                              onClick={() => handleProposal(p.proposal_id, false)}
                            >
                              Reject
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
                    <h4 className={s.sectionTitle}>RECENT ISSUES</h4>
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
                    <h4 className={s.sectionTitle}>PENDING ACTIONS</h4>
                    <div className={s.actionList}>
                      {actions.map((act) => (
                        <div key={act.action_id} className={s.actionRow}>
                          <StatusBadge
                            label={act.status}
                            tone={act.status === "executed" ? "success" : "warning"}
                          />
                          <span className={s.actionType}>{act.action_type}</span>
                          <button
                            className="btn btn-sm"
                            disabled={actionExecutionPending}
                            onClick={() => handleAction(act.action_id)}
                          >
                            Execute
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Timeline */}
                {timeline.length > 0 && (
                  <div className={s.section}>
                    <h4 className={s.sectionTitle}>TIMELINE</h4>
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
