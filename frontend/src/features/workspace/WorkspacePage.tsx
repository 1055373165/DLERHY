import { useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  documentBadge,
  formatDate,
  formatNumber,
  getPrimaryRunAction,
  nextMilestoneText,
  preferredTitle,
  shorten,
  sourceLabel,
  statusLabel,
} from "../../lib/workflow";
import styles from "./WorkspacePage.module.css";

type MessageTone = "success" | "error";

export function WorkspacePage() {
  const {
    currentDocument,
    currentRun,
    currentExports,
    currentChapterReviewDetail,
    currentChapterReviewError,
    currentChapterReviewLoading,
    currentDocumentError,
    selectedReviewChapterId,
    selectReviewChapter,
    uploadPending,
    runActionPending,
    reviewDecisionPending,
    approveMemoryProposal,
    rejectMemoryProposal,
    uploadFile,
    runPrimaryAction,
    refreshCurrentDocument,
  } = useWorkspace();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [actionMessage, setActionMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [reviewMessage, setReviewMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [reviewerName, setReviewerName] = useState("reviewer-ui");
  const [reviewerNote, setReviewerNote] = useState("");

  const action = getPrimaryRunAction(currentDocument, currentRun);
  const badge = documentBadge(currentDocument, currentRun);

  async function handleUpload() {
    if (!selectedFile) {
      setUploadMessage({ tone: "error", text: "请先选择一本 EPUB 或 PDF 书稿。" });
      return;
    }
    try {
      const document = await uploadFile(selectedFile);
      setActionMessage(null);
      setUploadMessage({
        tone: "success",
        text: `已完成《${preferredTitle(document)}》的解析。现在可以启动整书转换。`,
      });
    } catch (error) {
      setActionMessage(null);
      setUploadMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "上传失败，请稍后重试。",
      });
    }
  }

  async function handlePrimaryAction() {
    try {
      const run = await runPrimaryAction();
      setUploadMessage(null);
      setActionMessage({
        tone: "success",
        text: `后台整书转换已推进，当前 run：${run.run_id.slice(0, 6)}。页面会自动更新。`,
      });
    } catch (error) {
      setUploadMessage(null);
      setActionMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "运行操作失败，请稍后再试。",
      });
    }
  }

  async function handleProposalDecision(proposalId: string, decision: "approved" | "rejected") {
    try {
      const payload = {
        actor_name: reviewerName.trim() || undefined,
        note: reviewerNote.trim() || undefined,
      };
      const result =
        decision === "approved"
          ? await approveMemoryProposal(proposalId, payload)
          : await rejectMemoryProposal(proposalId, payload);
      setReviewMessage({
        tone: "success",
        text:
          decision === "approved"
            ? `已批准 ${shorten(result.proposal.proposal_id, 5)}，snapshot v${result.committed_snapshot_version ?? "—"} 已生效。`
            : `已驳回 ${shorten(result.proposal.proposal_id, 5)}，等待新的 rerun proposal。`,
      });
      setReviewerNote("");
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "审批操作失败，请稍后重试。",
      });
    }
  }

  return (
    <div className={styles.grid}>
      <div className={styles.summaryStack}>
        <Surface
          eyebrow="Current Book"
          title="当前书籍"
          description="当前书稿、主操作和下一步都集中在这里，不再把判断拆散到多个区域。"
          aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
        >
          {currentDocument ? (
            <>
              <div className={styles.hero}>
                <div className={styles.documentMeta}>
                  <h3 className={styles.documentTitle}>{preferredTitle(currentDocument)}</h3>
                  <div className={styles.metaStrip}>
                    <span>{currentDocument.author || "作者待识别"}</span>
                    <span>{sourceLabel(currentDocument.source_type)}</span>
                    <span>文档状态 {statusLabel(currentDocument.status)}</span>
                  </div>
                  <p className={styles.documentCopy}>
                    最近运行 {statusLabel(currentDocument.latest_run_status)} · 更新时间{" "}
                    {formatDate(currentDocument.latest_run_updated_at || currentRun?.updated_at)}
                  </p>
                </div>
                <div className={styles.heroAside}>
                  <div className={styles.nextStep}>
                    <div className={styles.nextStepLabel}>Next</div>
                    <p className={styles.nextStepText}>
                      {nextMilestoneText(currentDocument, currentRun, currentExports)}
                    </p>
                  </div>
                  <div className={styles.buttonRow}>
                    <button
                      className={styles.button}
                      type="button"
                      onClick={handlePrimaryAction}
                      disabled={action.disabled || runActionPending}
                    >
                      {runActionPending ? "处理中…" : action.label}
                    </button>
                    <button className={styles.ghostButton} type="button" onClick={() => void refreshCurrentDocument()}>
                      刷新状态
                    </button>
                  </div>
                  {actionMessage ? (
                    <div
                      className={`${styles.message} ${
                        actionMessage.tone === "success" ? styles.messageSuccess : styles.messageError
                      }`}
                    >
                      {actionMessage.text}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className={styles.statGrid}>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>章节</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.chapter_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>Packet</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.packet_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>Open Issues</div>
                  <div className={styles.statValue}>{formatNumber(currentDocument.open_issue_count)}</div>
                </div>
                <div className={styles.statCard}>
                  <div className={styles.statLabel}>最近交付</div>
                  <div className={styles.statValue}>
                    {currentDocument.merged_export_ready ? "已就绪" : "未生成"}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className={styles.emptyState}>
              <strong>当前还没有载入书籍。</strong>
              <span>先上传一本英文书。解析完成后，这里只会展示对你下一步有帮助的信息。</span>
            </div>
          )}
          {currentDocumentError ? <div className={`${styles.message} ${styles.messageError}`}>{currentDocumentError}</div> : null}
        </Surface>
      </div>

      <div className={styles.uploadStack}>
        <Surface
          eyebrow="Ingest"
          title="上传入口"
          description="上传 EPUB 或 PDF 后，这本书会立即成为当前工作对象。"
        >
          <label className={styles.fileField}>
            <span className={styles.fileLabel}>选择源文件</span>
            <input
              aria-label="选择书稿文件"
              className={styles.fileInput}
              type="file"
              accept=".epub,.pdf,application/epub+zip,application/pdf"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
            <span className={styles.fileNote}>
              {selectedFile
                ? `已选择 ${selectedFile.name} · ${formatNumber(Math.round(selectedFile.size / 1024))} KB`
                : "只保留一个上传入口。解析成功后，工作台会自动切到这本书。"}
            </span>
          </label>
          <div className={styles.buttonRow}>
            <button className={styles.button} type="button" onClick={handleUpload} disabled={uploadPending}>
              {uploadPending ? "上传中…" : "上传并解析"}
            </button>
          </div>
          {uploadMessage ? (
            <div
              className={`${styles.message} ${
                uploadMessage.tone === "success" ? styles.messageSuccess : styles.messageError
              }`}
            >
              {uploadMessage.text}
            </div>
          ) : null}
        </Surface>
      </div>

      <div className={styles.reviewStack}>
        <Surface
          eyebrow="Reviewer Console"
          title="章节复核台"
          description="同一块面板里处理待审批 memory proposal，并把 review/action/operator 决策串成可追踪时间线。"
          aside={
            currentChapterReviewDetail
              ? `Chapter ${currentChapterReviewDetail.ordinal} · ${currentChapterReviewDetail.timeline.length} 条事件`
              : null
          }
        >
          {!currentDocument ? (
            <div className={styles.emptyState}>
              <strong>先载入一本书，再进入章节复核台。</strong>
              <span>这里会显示当前章节的待审批 proposal、当前 blocker 和操作时间线。</span>
            </div>
          ) : (
            <>
              <div className={styles.reviewHeader}>
                <label className={styles.reviewSelectField}>
                  <span className={styles.fileLabel}>当前章节</span>
                  <select
                    className={styles.reviewSelect}
                    value={selectedReviewChapterId ?? ""}
                    onChange={(event) => {
                      setReviewMessage(null);
                      selectReviewChapter(event.target.value || null);
                    }}
                  >
                    {(currentDocument.chapters || []).map((chapter) => (
                      <option key={chapter.chapter_id} value={chapter.chapter_id}>
                        第 {chapter.ordinal} 章 · {chapter.title_src || `Chapter ${chapter.ordinal}`}
                      </option>
                    ))}
                  </select>
                </label>
                <div className={styles.reviewMetricGrid}>
                  <div className={styles.reviewMetric}>
                    <span className={styles.reviewMetricLabel}>Pending</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(currentChapterReviewDetail?.memory_proposals.pending_proposal_count)}
                    </strong>
                  </div>
                  <div className={styles.reviewMetric}>
                    <span className={styles.reviewMetricLabel}>Blockers</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(currentChapterReviewDetail?.current_active_blocking_issue_count)}
                    </strong>
                  </div>
                  <div className={styles.reviewMetric}>
                    <span className={styles.reviewMetricLabel}>Snapshot</span>
                    <strong className={styles.reviewMetricValue}>
                      v{currentChapterReviewDetail?.memory_proposals.active_snapshot_version ?? "—"}
                    </strong>
                  </div>
                  <div className={styles.reviewMetric}>
                    <span className={styles.reviewMetricLabel}>Timeline</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(currentChapterReviewDetail?.timeline.length)}
                    </strong>
                  </div>
                </div>
              </div>

              <div className={styles.reviewControlBar}>
                <label className={styles.inlineField}>
                  <span className={styles.inlineFieldLabel}>操作人</span>
                  <input
                    className={styles.inlineInput}
                    type="text"
                    value={reviewerName}
                    onChange={(event) => setReviewerName(event.target.value)}
                    placeholder="reviewer-ui"
                  />
                </label>
                <label className={`${styles.inlineField} ${styles.inlineFieldWide}`}>
                  <span className={styles.inlineFieldLabel}>备注</span>
                  <input
                    className={styles.inlineInput}
                    type="text"
                    value={reviewerNote}
                    onChange={(event) => setReviewerNote(event.target.value)}
                    placeholder="这次 override 的理由或审校结论"
                  />
                </label>
              </div>

              {reviewMessage ? (
                <div
                  className={`${styles.message} ${
                    reviewMessage.tone === "success" ? styles.messageSuccess : styles.messageError
                  }`}
                >
                  {reviewMessage.text}
                </div>
              ) : null}
              {currentChapterReviewError ? (
                <div className={`${styles.message} ${styles.messageError}`}>{currentChapterReviewError}</div>
              ) : null}

              {currentChapterReviewLoading ? (
                <div className={styles.emptyState}>正在加载章节复核数据…</div>
              ) : currentChapterReviewDetail ? (
                <div className={styles.reviewLayout}>
                  <div className={styles.reviewColumn}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <h3 className={styles.reviewSectionTitle}>待审批 Proposal</h3>
                        <p className={styles.reviewSectionCopy}>
                          当前章节共有 {formatNumber(currentChapterReviewDetail.memory_proposals.proposal_count)} 条 proposal，
                          其中待决策 {formatNumber(currentChapterReviewDetail.memory_proposals.pending_proposal_count)} 条。
                        </p>
                      </div>
                    </div>
                    {currentChapterReviewDetail.memory_proposals.pending_proposals.length ? (
                      <div className={styles.proposalList}>
                        {currentChapterReviewDetail.memory_proposals.pending_proposals.map((proposal) => (
                          <article key={proposal.proposal_id} className={styles.proposalCard}>
                            <div className={styles.proposalTop}>
                              <div>
                                <div className={styles.proposalEyebrow}>Packet {shorten(proposal.packet_id, 5)}</div>
                                <h4 className={styles.proposalTitle}>Proposal {shorten(proposal.proposal_id, 6)}</h4>
                              </div>
                              <span className={styles.proposalStatus}>{proposal.status}</span>
                            </div>
                            <p className={styles.proposalMeta}>
                              Translation run {shorten(proposal.translation_run_id, 6)} · base snapshot v
                              {proposal.base_snapshot_version ?? "—"} · 提交于 {formatDate(proposal.updated_at)}
                            </p>
                            <div className={styles.proposalActions}>
                              <button
                                className={styles.button}
                                type="button"
                                disabled={reviewDecisionPending}
                                onClick={() => void handleProposalDecision(proposal.proposal_id, "approved")}
                              >
                                {reviewDecisionPending ? "处理中…" : "批准写入"}
                              </button>
                              <button
                                className={styles.ghostButton}
                                type="button"
                                disabled={reviewDecisionPending}
                                onClick={() => void handleProposalDecision(proposal.proposal_id, "rejected")}
                              >
                                驳回并等待新 proposal
                              </button>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className={styles.reviewEmpty}>
                        当前章节没有待审批 proposal。review pass 后提交的 snapshot 和被驳回的 proposal 都会留在右侧时间线。
                      </div>
                    )}
                  </div>

                  <div className={styles.reviewColumn}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <h3 className={styles.reviewSectionTitle}>Review / Action Timeline</h3>
                        <p className={styles.reviewSectionCopy}>
                          同时查看 action、assignment 和 memory override 的最近动作，避免 reviewer 在多个列表之间来回跳。
                        </p>
                      </div>
                    </div>
                    {currentChapterReviewDetail.timeline.length ? (
                      <div className={styles.timelineList}>
                        {currentChapterReviewDetail.timeline.map((entry) => (
                          <article key={`${entry.source_kind}-${entry.event_id}-${entry.created_at}`} className={styles.timelineCard}>
                            <div className={styles.timelineTop}>
                              <span className={styles.timelineTag}>{timelineLabel(entry)}</span>
                              <span className={styles.timelineDate}>{formatDate(entry.created_at)}</span>
                            </div>
                            <h4 className={styles.timelineTitle}>{timelineHeadline(entry)}</h4>
                            <p className={styles.timelineDetail}>{timelineDetail(entry)}</p>
                            {entry.note ? <p className={styles.timelineNote}>备注：{entry.note}</p> : null}
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className={styles.reviewEmpty}>当前章节还没有可展示的时间线事件。</div>
                    )}
                  </div>
                </div>
              ) : (
                <div className={styles.emptyState}>当前章节尚未生成可复核的上下文。</div>
              )}
            </>
          )}
        </Surface>
      </div>
    </div>
  );
}

function timelineLabel(entry: {
  source_kind: string;
  event_kind: string;
  action_type?: string | null;
  decision?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return entry.decision === "approved" ? "Memory Approved" : "Memory Rejected";
  }
  if (entry.source_kind === "assignment") {
    return entry.event_kind === "cleared" ? "Assignment Cleared" : "Assignment Set";
  }
  return entry.action_type ? `Action · ${entry.action_type}` : "Issue Action";
}

function timelineHeadline(entry: {
  source_kind: string;
  issue_type?: string | null;
  owner_name?: string | null;
  actor_name?: string | null;
  proposal_id?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return `${entry.actor_name || "system"} 对 proposal ${shorten(entry.proposal_id, 5)} 做出决策`;
  }
  if (entry.source_kind === "assignment") {
    return entry.owner_name ? `章节已分派给 ${entry.owner_name}` : "章节重新回到共享队列";
  }
  return entry.issue_type ? `${entry.issue_type} 触发 follow-up 动作` : "Issue action 已更新";
}

function timelineDetail(entry: {
  source_kind: string;
  decision?: string | null;
  issue_type?: string | null;
  action_type?: string | null;
  scope_type?: string | null;
  scope_id?: string | null;
  actor_name?: string | null;
}) {
  if (entry.source_kind === "memory_proposal") {
    return `${entry.actor_name || "system"} 将 memory proposal 标记为 ${entry.decision === "approved" ? "approved" : "rejected"}。`;
  }
  if (entry.source_kind === "assignment") {
    return entry.actor_name ? `${entry.actor_name} 更新了章节处理人。` : "章节所有权发生变化。";
  }
  return `${entry.issue_type || "Issue"} -> ${entry.action_type || "action"} · ${entry.scope_type || "scope"} ${entry.scope_id ? shorten(entry.scope_id, 5) : ""}`.trim();
}
