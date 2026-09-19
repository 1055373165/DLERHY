import { useState } from "react";
import { Link } from "react-router-dom";
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
  readerPath,
  sourceLabel,
  statusLabel,
} from "../../lib/workflow";
import { CostEstimateNote } from "./CostEstimateNote";
import s from "./WorkspacePage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

export function WorkspacePage() {
  const {
    currentDocument,
    currentRun,
    currentExports,
    returnToHome,
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
    importWebPage,
    runPrimaryAction,
    downloadChapterAsset,
  } = useWorkspace();

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pageUrl, setPageUrl] = useState("");
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
      setFeedback({ tone: "success", text: `已载入：${result.title ?? selectedFile.name}` });
      setSelectedFile(null);
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "上传失败" });
    }
  }

  async function handleImportUrl() {
    const url = pageUrl.trim();
    if (!url) return;
    try {
      const result = await importWebPage(url);
      setFeedback({ tone: "success", text: `已载入网页：${result.title ?? url}` });
      setPageUrl("");
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "网页抓取失败" });
    }
  }

  const urlForm = (compact: boolean) => (
    <form
      className={compact ? s.compactUploadGroup : s.urlForm}
      onSubmit={(e) => {
        e.preventDefault();
        void handleImportUrl();
      }}
    >
      <input
        type="url"
        inputMode="url"
        className={s.urlInput}
        placeholder={compact ? "或粘贴网页链接…" : "https://example.com/article"}
        aria-label="网页链接"
        value={pageUrl}
        onChange={(e) => {
          setPageUrl(e.target.value);
          setFeedback(null);
        }}
      />
      <button
        type="submit"
        className={compact ? "btn btn-sm" : "btn"}
        disabled={!pageUrl.trim() || uploadPending}
      >
        {uploadPending ? "…" : "抓取网页"}
      </button>
    </form>
  );

  async function handlePrimaryAction() {
    try {
      await runPrimaryAction();
      setFeedback({ tone: "success", text: action.label });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "操作失败" });
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
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "提交失败" });
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

  function handleReturnHome() {
    setSelectedFile(null);
    setFeedback(null);
    returnToHome();
  }

  /* ── Derived ── */
  const readPath = readerPath(doc);
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
          <p className={s.uploadSubtitle}>上传英文 PDF 或 EPUB，或粘贴英文网页链接，开始翻译</p>
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
              {selectedFile ? selectedFile.name : "点击选择 .pdf / .epub 文件"}
            </span>
          </label>
          <div className={s.uploadActions}>
            <button
              className="btn btn-primary"
              disabled={!selectedFile || uploadPending}
              onClick={handleUpload}
            >
              {uploadPending ? "上传中…" : "上传并解析"}
            </button>
          </div>
          <div className={s.orDivider}>或翻译一个网页（保留原页面排版，输出中文网页）</div>
          {urlForm(false)}
          <p className={s.rightsNote}>
            请只上传或抓取你有权翻译的书稿与网页（自有版权、已获授权，或法律允许的个人学习用途）。译文的发布与传播仍受原作版权约束。
          </p>
          {feedback && (
            <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
          )}
        </div>
      )}

      {/* ── Compact upload (when document already loaded) ── */}
      {doc && (
        <div className={s.compactUpload}>
          <div className={s.compactUploadGroup}>
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
                {selectedFile ? selectedFile.name : "选择新文件…"}
              </span>
            </label>
            <button
              className="btn btn-sm"
              disabled={!selectedFile || uploadPending}
              onClick={handleUpload}
            >
              {uploadPending ? "…" : "上传并解析"}
            </button>
          </div>
          {urlForm(true)}
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
            <div className={s.docHeaderAside}>
              {readPath && (
                <Link className="btn btn-primary btn-sm" to={readPath}>
                  阅读中文版
                </Link>
              )}
              <StatusBadge label={badge.label} tone={badge.tone} />
              <button className={`btn btn-sm ${s.homeButton}`} onClick={handleReturnHome}>
                回到首页上传新书
              </button>
            </div>
          </div>
          <div className={s.docStats}>
            <Stat label="状态" value={statusLabel(doc.status)} />
            <Stat label="来源" value={sourceLabel(doc.source_type)} />
            <Stat label="章节" value={formatNumber(doc.chapter_count)} />
            <Stat label="翻译段" value={formatNumber(doc.packet_count)} />
            <Stat label="句子" value={formatNumber(doc.sentence_count)} />
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
          {action.mode === "create" ? <CostEstimateNote documentId={doc.document_id} /> : null}
        </div>
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
                            tone={act.status === "executed" ? "success" : "warning"}
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
