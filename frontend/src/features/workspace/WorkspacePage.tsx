import { useEffect, useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import type { ChapterWorklistTimelineEntry, ExecuteActionResponse } from "../../lib/api";
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
type TimelineFocusTarget =
  | {
      eventId: string;
      section: "actions";
      actionId?: string | null;
      label: string;
      helper: string;
    }
  | {
      eventId: string;
      section: "assignment";
      label: string;
      helper: string;
    }
  | {
      eventId: string;
      section: "proposal";
      proposalId?: string | null;
      label: string;
      helper: string;
    };
type OperatorConvergenceSnapshot = {
  pendingProposalCount: number;
  activeSnapshotVersion: number | null;
  ownerName: string;
  actionStatus: string;
};
type RecentOperatorChange = {
  chapterId: string;
  kind: "proposal" | "assignment" | "action";
  title: string;
  body: string;
  highlights: string[];
  before: OperatorConvergenceSnapshot | null;
};

export function WorkspacePage() {
  const {
    currentDocument,
    currentRun,
    currentExports,
    chapterWorklist,
    chapterWorklistLoading,
    chapterWorklistError,
    currentChapterReviewDetail,
    currentChapterReviewError,
    currentChapterReviewLoading,
    currentDocumentError,
    selectedReviewChapterId,
    selectReviewChapter,
    chapterWorklistFilters,
    setChapterQueuePriorityFilter,
    setChapterAssignmentFilter,
    setChapterAssignedOwnerFilter,
    clearChapterWorklistFilters,
    uploadPending,
    runActionPending,
    reviewDecisionPending,
    assignmentPending,
    actionExecutionPending,
    approveMemoryProposal,
    rejectMemoryProposal,
    assignChapterOwner,
    clearChapterAssignment,
    executeChapterAction,
    uploadFile,
    runPrimaryAction,
    refreshCurrentDocument,
  } = useWorkspace();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [actionMessage, setActionMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [reviewMessage, setReviewMessage] = useState<{ tone: MessageTone; text: string } | null>(null);
  const [lastActionExecution, setLastActionExecution] = useState<{
    result: ExecuteActionResponse;
    createdAt: string;
  } | null>(null);
  const [timelineFocus, setTimelineFocus] = useState<TimelineFocusTarget | null>(null);
  const [recentOperatorChange, setRecentOperatorChange] = useState<RecentOperatorChange | null>(null);
  const [reviewerName, setReviewerName] = useState("reviewer-ui");
  const [reviewerNote, setReviewerNote] = useState("");
  const [assignmentOwner, setAssignmentOwner] = useState("");

  const action = getPrimaryRunAction(currentDocument, currentRun);
  const badge = documentBadge(currentDocument, currentRun);
  const queueEntries = chapterWorklist?.entries ?? [];
  const ownerWorkloads = chapterWorklist?.owner_workload_summary ?? [];
  const hasActiveQueueFilters =
    chapterWorklistFilters.queuePriority !== "all" ||
    chapterWorklistFilters.assignment !== "all" ||
    Boolean(chapterWorklistFilters.assignedOwnerName);
  const activeQueueFilters = buildActiveQueueFilters(chapterWorklistFilters);
  const selectedOwnerWorkload =
    ownerWorkloads.find((owner) => owner.owner_name === chapterWorklistFilters.assignedOwnerName) ?? null;
  const selectedQueueEntry =
    queueEntries.find((entry) => entry.chapter_id === selectedReviewChapterId) ?? null;
  const timelineGroups = groupTimelineEntries(currentChapterReviewDetail?.timeline ?? []);
  const selectedChapterRecentChange =
    recentOperatorChange?.chapterId === selectedReviewChapterId ? recentOperatorChange : null;
  const selectedChapterCurrentSnapshot = buildOperatorSnapshot(
    selectedQueueEntry,
    currentChapterReviewDetail
  );
  const selectedChapterConvergenceItems =
    selectedChapterRecentChange && selectedChapterCurrentSnapshot
      ? buildConvergenceItems(selectedChapterRecentChange.before, selectedChapterCurrentSnapshot)
      : [];
  const selectedChapterImpactedTimelineEventId =
    selectedChapterRecentChange && currentChapterReviewDetail
      ? currentChapterReviewDetail.timeline.find((entry) =>
          timelineEntryMatchesRecentChange(entry, selectedChapterRecentChange)
        )?.event_id ?? null
      : null;
  const selectedChapterNextStep = selectedChapterRecentChange
    ? buildRecentChangeNextStep(
        selectedChapterRecentChange,
        selectedChapterCurrentSnapshot,
        currentChapterReviewDetail
      )
    : null;
  const focusedActionEntry =
    timelineFocus?.section === "actions"
      ? currentChapterReviewDetail?.recent_actions.find(
          (entry) => entry.action_id === timelineFocus.actionId
        ) ?? null
      : null;
  const focusedProposal =
    timelineFocus?.section === "proposal"
      ? currentChapterReviewDetail?.memory_proposals.pending_proposals.find(
          (proposal) => proposal.proposal_id === timelineFocus.proposalId
        ) ?? null
      : null;
  const focusedAssignment = timelineFocus?.section === "assignment" ? currentChapterReviewDetail?.assignment : null;

  useEffect(() => {
    setAssignmentOwner(currentChapterReviewDetail?.assignment?.owner_name ?? "");
  }, [currentChapterReviewDetail?.assignment?.owner_name, selectedReviewChapterId]);

  useEffect(() => {
    setLastActionExecution(null);
    setTimelineFocus(null);
  }, [selectedReviewChapterId]);

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
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
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
      setRecentOperatorChange({
        chapterId: selectedReviewChapterId,
        kind: "proposal",
        title: decision === "approved" ? "Memory proposal 已批准" : "Memory proposal 已驳回",
        body:
          decision === "approved"
            ? "这次 override 已回写到 chapter memory 治理链，后续 review 会继续围绕新的 snapshot 收敛。"
            : "旧 proposal 已退出待审批面板，章节会等待新的 rerun proposal 再进入审批。",
        highlights:
          decision === "approved"
            ? [
                `Proposal ${shorten(result.proposal.proposal_id, 5)}`,
                `Snapshot v${result.committed_snapshot_version ?? "—"}`,
              ]
            : [`Proposal ${shorten(result.proposal.proposal_id, 5)}`, "等待新 proposal"],
        before: beforeSnapshot,
      });
      setReviewerNote("");
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "审批操作失败，请稍后重试。",
      });
    }
  }

  async function handleAssignment(mode: "assign" | "clear") {
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    if (mode === "assign" && !assignmentOwner.trim()) {
      setReviewMessage({ tone: "error", text: "请先填写要分派的处理人。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
    try {
      if (mode === "assign") {
        const assignment = await assignChapterOwner(selectedReviewChapterId, {
          owner_name: assignmentOwner.trim(),
          assigned_by: reviewerName.trim() || "reviewer-ui",
          note: reviewerNote.trim() || undefined,
        });
        setReviewMessage({
          tone: "success",
          text: `章节已分派给 ${assignment.owner_name}。后续 review、action 和 memory proposal 会继续收敛到同一条时间线。`,
        });
        setRecentOperatorChange({
          chapterId: selectedReviewChapterId,
          kind: "assignment",
          title: "章节 assignment 已更新",
          body: "队列所有权已经切到新的 operator，后续 follow-up 和 override 会沿着这条 ownership 链继续推进。",
          highlights: [assignment.owner_name, `By ${assignment.assigned_by}`],
          before: beforeSnapshot,
        });
      } else {
        await clearChapterAssignment(selectedReviewChapterId, {
          cleared_by: reviewerName.trim() || "reviewer-ui",
          note: reviewerNote.trim() || undefined,
        });
        setReviewMessage({
          tone: "success",
          text: "章节已回收到共享队列。其他 operator 现在可以继续接手处理。",
        });
        setRecentOperatorChange({
          chapterId: selectedReviewChapterId,
          kind: "assignment",
          title: "章节已回收至共享队列",
          body: "这章不再绑定单一 owner，当前队列里的其他 operator 都可以继续接手。",
          highlights: ["共享队列", `By ${reviewerName.trim() || "reviewer-ui"}`],
          before: beforeSnapshot,
        });
        setAssignmentOwner("");
      }
      setReviewerNote("");
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "章节分派失败，请稍后重试。",
      });
    }
  }

  async function handleExecuteAction(actionId: string) {
    if (!selectedReviewChapterId) {
      setReviewMessage({ tone: "error", text: "请先选择一个章节。" });
      return;
    }
    const beforeSnapshot = buildOperatorSnapshot(selectedQueueEntry, currentChapterReviewDetail);
    try {
      const result = await executeChapterAction(actionId, true);
      setLastActionExecution({
        result,
        createdAt: new Date().toISOString(),
      });
      setReviewMessage({
        tone: "success",
        text: [
          `已执行 ${shorten(result.action_id, 5)}。`,
          result.followup_executed ? "follow-up rerun 已触发。" : "当前没有新的 follow-up rerun。",
          result.issue_resolved === true ? "相关 issue 已收敛。" : null,
          result.recheck_issue_count != null ? `复检 issue 数 ${result.recheck_issue_count}。` : null,
        ]
          .filter(Boolean)
          .join(" "),
      });
      setRecentOperatorChange({
        chapterId: selectedReviewChapterId,
        kind: "action",
        title: "Follow-up action 已执行",
        body: result.followup_executed
          ? "这次操作已经触发新的 rerun/replay，队列会沿着新的 scope 继续收敛。"
          : "这次操作没有触发新的 rerun，但 issue/action 状态已经完成一次显式推进。",
        highlights: [
          `Action ${shorten(result.action_id, 5)}`,
          result.followup_executed ? "已触发 rerun" : "未触发 rerun",
          result.issue_resolved ? "issue 已收敛" : `复检 ${formatNumber(result.recheck_issue_count ?? 0)}`,
        ],
        before: beforeSnapshot,
      });
    } catch (error) {
      setReviewMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "执行 action 失败，请稍后重试。",
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
                    <button
                      className={styles.ghostButton}
                      type="button"
                      onClick={() => void refreshCurrentDocument()}
                    >
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
          {currentDocumentError ? (
            <div className={`${styles.message} ${styles.messageError}`}>{currentDocumentError}</div>
          ) : null}
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
          eyebrow="Chapter Workbench"
          title="章节工作台"
          description="把章节队列、处理人分派、proposal override 和 review/action timeline 收进同一个操作面。"
          aside={
            chapterWorklist
              ? `${chapterWorklist.entry_count} 章在队列中 · ${chapterWorklist.immediate_attention_count} 章需立即处理`
              : null
          }
        >
          {!currentDocument ? (
            <div className={styles.emptyState}>
              <strong>先载入一本书，再进入章节工作台。</strong>
              <span>这里会显示当前待处理章节、负责人、proposal 和 follow-up timeline。</span>
            </div>
          ) : (
            <div className={styles.workbenchShell}>
              <aside className={styles.queueRail}>
                <div className={styles.queueRailHeader}>
                  <div>
                    <div className={styles.fileLabel}>Queue</div>
                    <h3 className={styles.reviewSectionTitle}>待处理章节</h3>
                  </div>
                  <p className={styles.reviewSectionCopy}>
                    先按 immediate / SLA / owner-ready 看清队列，再切换到具体章节。
                  </p>
                </div>

                <div className={styles.queueSummaryGrid}>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Queued</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.entry_count)}
                    </strong>
                  </div>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Immediate</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.immediate_attention_count)}
                    </strong>
                  </div>
                  <div className={styles.queueSummaryCard}>
                    <span className={styles.reviewMetricLabel}>Assigned</span>
                    <strong className={styles.reviewMetricValue}>
                      {formatNumber(chapterWorklist?.assigned_count)}
                    </strong>
                  </div>
                </div>

                <div className={styles.queueFilterPanel}>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>优先级</span>
                    <select
                      aria-label="队列优先级筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.queuePriority}
                      onChange={(event) =>
                        setChapterQueuePriorityFilter(
                          event.target.value as "all" | "immediate" | "high" | "medium"
                        )
                      }
                    >
                      <option value="all">全部优先级</option>
                      <option value="immediate">Immediate</option>
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                    </select>
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>分派状态</span>
                    <select
                      aria-label="章节分派筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.assignment}
                      onChange={(event) =>
                        setChapterAssignmentFilter(
                          event.target.value as "all" | "assigned" | "unassigned"
                        )
                      }
                    >
                      <option value="all">全部章节</option>
                      <option value="assigned">仅已分派</option>
                      <option value="unassigned">仅共享队列</option>
                    </select>
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>Owner 视角</span>
                    <select
                      aria-label="owner 视角筛选"
                      className={styles.reviewSelect}
                      value={chapterWorklistFilters.assignedOwnerName}
                      onChange={(event) => setChapterAssignedOwnerFilter(event.target.value)}
                    >
                      <option value="">全部 owner</option>
                      {ownerWorkloads.map((owner) => (
                        <option key={owner.owner_name} value={owner.owner_name}>
                          {owner.owner_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className={styles.ghostButton}
                    type="button"
                    disabled={!hasActiveQueueFilters}
                    onClick={clearChapterWorklistFilters}
                  >
                    清除筛选
                  </button>
                </div>

                {ownerWorkloads.length ? (
                  <div className={styles.ownerRail}>
                    {ownerWorkloads.map((owner) => {
                      const active = chapterWorklistFilters.assignedOwnerName === owner.owner_name;
                      return (
                        <button
                          key={owner.owner_name}
                          type="button"
                          className={`${styles.ownerCard} ${active ? styles.ownerCardActive : ""}`}
                          onClick={() =>
                            setChapterAssignedOwnerFilter(active ? "" : owner.owner_name)
                          }
                        >
                          <span className={styles.ownerName}>{owner.owner_name}</span>
                          <span className={styles.ownerMeta}>
                            {formatNumber(owner.assigned_chapter_count)} 章 · Immediate{" "}
                            {formatNumber(owner.immediate_count)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                <section className={styles.queueInspector}>
                  <div className={styles.reviewSectionHeader}>
                    <div>
                      <div className={styles.fileLabel}>Active Scope</div>
                      <h4 className={styles.reviewSectionTitle}>当前筛选范围</h4>
                    </div>
                    <p className={styles.reviewSectionCopy}>
                      先确认你现在看到的是整条队列，还是某个 owner / assignment 子集。
                    </p>
                  </div>
                  <div className={styles.queueInspectorGrid}>
                    <div className={styles.queueInspectorCard}>
                      <span className={styles.reviewMetricLabel}>Visible</span>
                      <strong>
                        {formatNumber(chapterWorklist?.filtered_worklist_count ?? chapterWorklist?.entry_count)} /{" "}
                        {formatNumber(chapterWorklist?.worklist_count)}
                      </strong>
                      <p className={styles.timelineDetail}>chapters in current scope</p>
                    </div>
                    <div className={styles.queueInspectorCard}>
                      <span className={styles.reviewMetricLabel}>Filters</span>
                      <strong>{hasActiveQueueFilters ? "已启用" : "未启用"}</strong>
                      <p className={styles.timelineDetail}>
                        {hasActiveQueueFilters
                          ? "当前队列已收窄到更明确的操作范围。"
                          : "当前展示整条 reviewer/operator 队列。"}
                      </p>
                    </div>
                  </div>
                  {activeQueueFilters.length ? (
                    <div className={styles.filterChipRow}>
                      {activeQueueFilters.map((label) => (
                        <span key={label} className={styles.filterChip}>
                          {label}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className={styles.timelineDetail}>当前未启用过滤，适合做整条队列扫描。</p>
                  )}
                  {selectedOwnerWorkload ? (
                    <div className={styles.ownerSnapshotGrid}>
                      <div className={styles.ownerSnapshotCard}>
                        <span className={styles.reviewMetricLabel}>Owner</span>
                        <strong>{selectedOwnerWorkload.owner_name}</strong>
                        <p className={styles.timelineDetail}>
                          {formatNumber(selectedOwnerWorkload.assigned_chapter_count)} 章在此 owner 下
                        </p>
                      </div>
                      <div className={styles.ownerSnapshotCard}>
                        <span className={styles.reviewMetricLabel}>Immediate</span>
                        <strong>{formatNumber(selectedOwnerWorkload.immediate_count)}</strong>
                        <p className={styles.timelineDetail}>
                          blocker {formatNumber(selectedOwnerWorkload.total_active_blocking_issue_count)}
                        </p>
                      </div>
                    </div>
                  ) : null}
                </section>

                {chapterWorklistError ? (
                  <div className={`${styles.message} ${styles.messageError}`}>{chapterWorklistError}</div>
                ) : null}

                {chapterWorklistLoading ? (
                  <div className={styles.reviewEmpty}>正在加载章节队列…</div>
                ) : queueEntries.length ? (
                  <div className={styles.queueList}>
                    {queueEntries.map((entry) => {
                      const active = entry.chapter_id === selectedReviewChapterId;
                      return (
                        <button
                          key={entry.chapter_id}
                          type="button"
                          className={`${styles.queueCard} ${active ? styles.queueCardActive : ""}`}
                          onClick={() => {
                            setReviewMessage(null);
                            selectReviewChapter(entry.chapter_id);
                          }}
                        >
                          <div className={styles.queueCardTop}>
                            <div className={styles.queueRankRow}>
                              <span className={styles.queueRank}>#{entry.queue_rank}</span>
                              {recentOperatorChange?.chapterId === entry.chapter_id ? (
                                <>
                                  <span className={styles.changeBadge}>最新操作</span>
                                  <span className={styles.changeKindBadge}>
                                    {recentChangeKindLabel(recentOperatorChange.kind)}
                                  </span>
                                </>
                              ) : null}
                            </div>
                            <span className={styles.queuePriority}>{queuePriorityLabel(entry.queue_priority)}</span>
                          </div>
                          <h4 className={styles.queueTitle}>
                            第 {entry.ordinal} 章 · {entry.title_src || `Chapter ${entry.ordinal}`}
                          </h4>
                          <p className={styles.queueMeta}>
                            {entry.queue_driver} · SLA {slaStatusLabel(entry.sla_status)} ·{" "}
                            {entry.assigned_owner_name ? `Owner ${entry.assigned_owner_name}` : "未分派"}
                          </p>
                          <div className={styles.queueStatRow}>
                            <span>Blockers {formatNumber(entry.active_blocking_issue_count)}</span>
                            <span>Pending {formatNumber(entry.memory_proposals.pending_proposal_count)}</span>
                          </div>
                          {recentOperatorChange?.chapterId === entry.chapter_id &&
                          selectedReviewChapterId === entry.chapter_id &&
                          selectedChapterConvergenceItems.length ? (
                            <p className={styles.queueDeltaHint}>
                              {selectedChapterConvergenceItems
                                .slice(0, 2)
                                .map((item) => item.value)
                                .join(" · ")}
                            </p>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className={styles.reviewEmpty}>
                    {hasActiveQueueFilters
                      ? "当前过滤条件下没有匹配章节。可以放宽优先级、owner 或分派条件。"
                      : "当前书籍还没有进入 reviewer/operator 队列的章节。"}
                  </div>
                )}
              </aside>

              <div className={styles.workbenchMain}>
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
                      <span className={styles.reviewMetricLabel}>Assignment</span>
                      <strong className={styles.reviewMetricValue}>
                        {currentChapterReviewDetail?.assignment?.owner_name ?? "共享队列"}
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

                <div
                  className={`${styles.assignmentBar} ${
                    timelineFocus?.section === "assignment" ? styles.focusSection : ""
                  }`}
                >
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>操作人</span>
                    <input
                      aria-label="操作人"
                      className={styles.inlineInput}
                      type="text"
                      value={reviewerName}
                      onChange={(event) => setReviewerName(event.target.value)}
                      placeholder="reviewer-ui"
                    />
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineFieldLabel}>指派给</span>
                    <input
                      aria-label="指派给"
                      className={styles.inlineInput}
                      type="text"
                      value={assignmentOwner}
                      onChange={(event) => setAssignmentOwner(event.target.value)}
                      placeholder="operator-name"
                    />
                  </label>
                  <label className={`${styles.inlineField} ${styles.inlineFieldWide}`}>
                    <span className={styles.inlineFieldLabel}>备注</span>
                    <input
                      aria-label="备注"
                      className={styles.inlineInput}
                      type="text"
                      value={reviewerNote}
                      onChange={(event) => setReviewerNote(event.target.value)}
                      placeholder="记录这次审批、assignment 或 override 的理由"
                    />
                  </label>
                  <div className={styles.assignmentActions}>
                    <button
                      className={styles.button}
                      type="button"
                      disabled={assignmentPending}
                      onClick={() => void handleAssignment("assign")}
                    >
                      {assignmentPending ? "处理中…" : "指派章节"}
                    </button>
                    <button
                      className={styles.ghostButton}
                      type="button"
                      disabled={assignmentPending || !currentChapterReviewDetail?.assignment}
                      onClick={() => void handleAssignment("clear")}
                    >
                      归还共享队列
                    </button>
                  </div>
                </div>

                {timelineFocus ? (
                  <div className={styles.focusBanner}>
                    <div>
                      <div className={styles.fileLabel}>Current Focus</div>
                      <strong className={styles.focusTitle}>{timelineFocus.label}</strong>
                      <p className={styles.timelineDetail}>{timelineFocus.helper}</p>
                    </div>
                    <div className={styles.focusActions}>
                      {timelineFocus.section === "actions" && focusedActionEntry ? (
                        <button
                          className={styles.button}
                          type="button"
                          disabled={actionExecutionPending || focusedActionEntry.status === "completed"}
                          onClick={() => void handleExecuteAction(focusedActionEntry.action_id)}
                        >
                          {actionExecutionPending ? "执行中…" : "执行当前 follow-up"}
                        </button>
                      ) : null}
                      {timelineFocus.section === "proposal" && focusedProposal ? (
                        <>
                          <button
                            className={styles.button}
                            type="button"
                            disabled={reviewDecisionPending}
                            onClick={() => void handleProposalDecision(focusedProposal.proposal_id, "approved")}
                          >
                            {reviewDecisionPending ? "处理中…" : "批准 focused proposal"}
                          </button>
                          <button
                            className={styles.ghostButton}
                            type="button"
                            disabled={reviewDecisionPending}
                            onClick={() => void handleProposalDecision(focusedProposal.proposal_id, "rejected")}
                          >
                            驳回 focused proposal
                          </button>
                        </>
                      ) : null}
                      {timelineFocus.section === "assignment" && focusedAssignment ? (
                        <button
                          className={styles.ghostButton}
                          type="button"
                          disabled={assignmentPending}
                          onClick={() => void handleAssignment("clear")}
                        >
                          {assignmentPending ? "处理中…" : "回收当前 assignment"}
                        </button>
                      ) : null}
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => setTimelineFocus(null)}
                      >
                        清除聚焦
                      </button>
                    </div>
                  </div>
                ) : null}

                {selectedChapterRecentChange ? (
                  <div className={styles.changeCard}>
                    <div className={styles.reviewSectionHeader}>
                      <div>
                        <div className={styles.fileLabel}>Latest Change</div>
                        <h4 className={styles.reviewSectionTitle}>{selectedChapterRecentChange.title}</h4>
                      </div>
                      <button
                        className={styles.ghostButton}
                        type="button"
                        onClick={() => setRecentOperatorChange(null)}
                      >
                        清除回写
                      </button>
                    </div>
                    <p className={styles.timelineDetail}>{selectedChapterRecentChange.body}</p>
                    <div className={styles.filterChipRow}>
                      {selectedChapterRecentChange.highlights.map((item) => (
                        <span key={item} className={styles.filterChip}>
                          {item}
                        </span>
                      ))}
                    </div>
                    {selectedChapterConvergenceItems.length ? (
                      <div className={styles.deltaGrid}>
                        {selectedChapterConvergenceItems.map((item) => (
                          <div key={item.label} className={styles.deltaCard}>
                            <span className={styles.deltaLabel}>{item.label}</span>
                            <strong className={styles.deltaValue}>{item.value}</strong>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {selectedChapterNextStep ? (
                      <div className={styles.nextStepCard}>
                        <span className={styles.deltaLabel}>Recommended Next Step</span>
                        <strong className={styles.deltaValue}>{selectedChapterNextStep.title}</strong>
                        <p className={styles.timelineDetail}>{selectedChapterNextStep.body}</p>
                      </div>
                    ) : null}
                  </div>
                ) : null}

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
                  <div className={styles.emptyState}>正在加载章节工作台数据…</div>
                ) : currentChapterReviewDetail ? (
                  <div className={styles.reviewLayout}>
                    <div className={styles.reviewColumn}>
                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Queue Context</h3>
                            <p className={styles.reviewSectionCopy}>
                              用当前 queue driver、SLA 和 assignment 状态快速判断这章应该先做什么。
                            </p>
                          </div>
                        </div>
                        <div className={styles.contextGrid}>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>Driver</span>
                            <strong>{selectedQueueEntry?.queue_driver ?? "—"}</strong>
                            <p className={styles.timelineDetail}>
                              {selectedQueueEntry?.regression_hint || "当前没有明确的回归提示。"}
                            </p>
                          </div>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>SLA</span>
                            <strong>{slaStatusLabel(selectedQueueEntry?.sla_status)}</strong>
                            <p className={styles.timelineDetail}>
                              {selectedQueueEntry?.owner_ready_reason || "尚未生成 owner-ready 说明。"}
                            </p>
                          </div>
                          <div className={styles.contextCard}>
                            <span className={styles.reviewMetricLabel}>Assignment</span>
                            <strong>
                              {currentChapterReviewDetail.assignment?.owner_name ?? "共享队列"}
                            </strong>
                            <p className={styles.timelineDetail}>
                              {currentChapterReviewDetail.assignment
                                ? `Assigned by ${currentChapterReviewDetail.assignment.assigned_by} · ${formatDate(
                                    currentChapterReviewDetail.assignment.assigned_at
                                  )}`
                                : "这章当前没有 owner。"}
                            </p>
                          </div>
                        </div>
                      </section>

                      <section className={styles.infoPanel}>
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
                              <article
                                key={proposal.proposal_id}
                                className={`${styles.proposalCard} ${
                                  timelineFocus?.section === "proposal" &&
                                  timelineFocus.proposalId === proposal.proposal_id
                                    ? styles.focusCard
                                    : ""
                                }`}
                              >
                                <div className={styles.proposalTop}>
                                  <div>
                                    <div className={styles.proposalEyebrow}>
                                      Packet {shorten(proposal.packet_id, 5)}
                                    </div>
                                    <h4 className={styles.proposalTitle}>
                                      Proposal {shorten(proposal.proposal_id, 6)}
                                    </h4>
                                  </div>
                                  <span className={styles.proposalStatus}>{proposal.status}</span>
                                </div>
                                <p className={styles.proposalMeta}>
                                  Translation run {shorten(proposal.translation_run_id, 6)} · base snapshot v
                                  {proposal.base_snapshot_version ?? "—"} · 提交于{" "}
                                  {formatDate(proposal.updated_at)}
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
                            当前章节没有待审批 proposal。review pass 后提交的 snapshot 和被驳回的 proposal 都会继续留在时间线里。
                          </div>
                        )}
                      </section>
                    </div>

                    <div className={styles.reviewColumn}>
                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Issue / Action Summary</h3>
                            <p className={styles.reviewSectionCopy}>
                              先确认 blocker 和 follow-up action，再决定这次是 approve、reject 还是换 owner。
                            </p>
                          </div>
                        </div>
                        <div className={styles.signalGrid}>
                          <div
                            className={`${styles.signalCard} ${
                              timelineFocus?.section === "actions" ? styles.focusSection : ""
                            }`}
                          >
                            <div className={styles.signalHeader}>
                              <span className={styles.reviewMetricLabel}>Recent Issues</span>
                              <strong>{formatNumber(currentChapterReviewDetail.recent_issues.length)}</strong>
                            </div>
                            {currentChapterReviewDetail.recent_issues.length ? (
                              <div className={styles.signalList}>
                                {currentChapterReviewDetail.recent_issues.slice(0, 3).map((issue) => (
                                  <article key={issue.issue_id} className={styles.signalItem}>
                                    <h4 className={styles.signalTitle}>{issue.issue_type}</h4>
                                    <p className={styles.timelineDetail}>
                                      {issue.root_cause_layer} · {issue.severity} ·{" "}
                                      {issue.blocking ? "blocking" : issue.status}
                                    </p>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <div className={styles.reviewEmpty}>当前没有 recent issues。</div>
                            )}
                          </div>

                          <div className={styles.signalCard}>
                            <div className={styles.signalHeader}>
                              <span className={styles.reviewMetricLabel}>Recent Actions</span>
                              <strong>{formatNumber(currentChapterReviewDetail.recent_actions.length)}</strong>
                            </div>
                            {currentChapterReviewDetail.recent_actions.length ? (
                              <div className={styles.signalList}>
                                {currentChapterReviewDetail.recent_actions.slice(0, 3).map((entry) => (
                                  <article
                                    key={entry.action_id}
                                    className={`${styles.signalItem} ${
                                      timelineFocus?.section === "actions" &&
                                      timelineFocus.actionId === entry.action_id
                                        ? styles.focusCard
                                        : ""
                                    }`}
                                  >
                                    <h4 className={styles.signalTitle}>{entry.action_type}</h4>
                                    <p className={styles.timelineDetail}>
                                      {entry.issue_type} · {entry.scope_type}{" "}
                                      {entry.scope_id ? shorten(entry.scope_id, 5) : ""}
                                    </p>
                                    <p className={styles.timelineDetail}>状态 {entry.status}</p>
                                    <div className={styles.signalActions}>
                                      <button
                                        className={styles.ghostButton}
                                        type="button"
                                        disabled={actionExecutionPending || entry.status === "completed"}
                                        onClick={() => void handleExecuteAction(entry.action_id)}
                                      >
                                        {actionExecutionPending ? "执行中…" : "执行 follow-up"}
                                      </button>
                                    </div>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <div className={styles.reviewEmpty}>当前没有 recent actions。</div>
                            )}
                            {lastActionExecution ? (
                              <article className={styles.executionCard}>
                                <div className={styles.signalHeader}>
                                  <span className={styles.reviewMetricLabel}>最近执行结果</span>
                                  <strong>{formatDate(lastActionExecution.createdAt)}</strong>
                                </div>
                                <div className={styles.executionGrid}>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Action</span>
                                    <strong>{shorten(lastActionExecution.result.action_id, 5)}</strong>
                                    <p className={styles.timelineDetail}>
                                      状态 {lastActionExecution.result.status}
                                    </p>
                                  </div>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Follow-up</span>
                                    <strong>
                                      {lastActionExecution.result.followup_executed
                                        ? "已触发 rerun"
                                        : "未触发 rerun"}
                                    </strong>
                                    <p className={styles.timelineDetail}>
                                      {lastActionExecution.result.rerun_scope_type
                                        ? `${lastActionExecution.result.rerun_scope_type} · ${formatNumber(
                                            lastActionExecution.result.rerun_scope_ids.length
                                          )} scopes`
                                        : "当前没有新的 replay scope。"}
                                    </p>
                                  </div>
                                  <div className={styles.executionMetric}>
                                    <span className={styles.reviewMetricLabel}>Issue</span>
                                    <strong>
                                      {lastActionExecution.result.issue_resolved
                                        ? "已收敛"
                                        : "待复检确认"}
                                    </strong>
                                    <p className={styles.timelineDetail}>
                                      复检 issue 数 {formatNumber(lastActionExecution.result.recheck_issue_count ?? 0)}
                                    </p>
                                  </div>
                                </div>
                              </article>
                            ) : null}
                          </div>
                        </div>
                      </section>

                      <section className={styles.infoPanel}>
                        <div className={styles.reviewSectionHeader}>
                          <div>
                            <h3 className={styles.reviewSectionTitle}>Review / Action Timeline</h3>
                            <p className={styles.reviewSectionCopy}>
                              同时查看 action、assignment 和 memory override 的最近动作，避免 reviewer 在多个列表之间来回跳。
                            </p>
                          </div>
                        </div>
                        {timelineGroups.length ? (
                          <div className={styles.timelineGroupList}>
                            {timelineGroups.map((group) => (
                              <section key={group.key} className={styles.timelineGroup}>
                                <div className={styles.timelineGroupHeader}>
                                  <div>
                                    <div className={styles.fileLabel}>Timeline Group</div>
                                    <h4 className={styles.timelineGroupTitle}>{group.title}</h4>
                                  </div>
                                  <span className={styles.timelineGroupCount}>
                                    {formatNumber(group.entries.length)}
                                  </span>
                                </div>
                                <div className={styles.timelineList}>
                                  {group.entries.map((entry) => {
                                    const impacted = selectedChapterImpactedTimelineEventId === entry.event_id;
                                    return (
                                      <button
                                        key={`${entry.source_kind}-${entry.event_id}-${entry.created_at}`}
                                        className={`${styles.timelineCard} ${styles.timelineEventButton} ${
                                          timelineFocus?.eventId === entry.event_id ? styles.timelineCardActive : ""
                                        } ${impacted ? styles.timelineCardRecentChange : ""}`}
                                        type="button"
                                        aria-label={`聚焦 ${timelineHeadline(entry)}`}
                                        aria-pressed={timelineFocus?.eventId === entry.event_id}
                                        onClick={() => setTimelineFocus(buildTimelineFocus(entry))}
                                      >
                                        <div className={styles.timelineTop}>
                                          <span className={styles.timelineTag}>{timelineLabel(entry)}</span>
                                          <div className={styles.timelineTopMeta}>
                                            {impacted ? (
                                              <span className={styles.timelineImpactBadge}>已影响当前状态</span>
                                            ) : null}
                                            <span className={styles.timelineDate}>{formatDate(entry.created_at)}</span>
                                          </div>
                                        </div>
                                        <h4 className={styles.timelineTitle}>{timelineHeadline(entry)}</h4>
                                        <p className={styles.timelineDetail}>{timelineDetail(entry)}</p>
                                        {entry.note ? <p className={styles.timelineNote}>备注：{entry.note}</p> : null}
                                      </button>
                                    );
                                  })}
                                </div>
                              </section>
                            ))}
                          </div>
                        ) : (
                          <div className={styles.reviewEmpty}>当前章节还没有可展示的时间线事件。</div>
                        )}
                      </section>
                    </div>
                  </div>
                ) : (
                  <div className={styles.emptyState}>当前章节尚未生成可复核的上下文。</div>
                )}
              </div>
            </div>
          )}
        </Surface>
      </div>
    </div>
  );
}

function queuePriorityLabel(priority?: string | null) {
  if (!priority) {
    return "未知优先级";
  }
  if (priority === "immediate") {
    return "Immediate";
  }
  if (priority === "high") {
    return "High";
  }
  if (priority === "medium") {
    return "Medium";
  }
  return priority;
}

function slaStatusLabel(status?: string | null) {
  if (!status) {
    return "未知";
  }
  if (status === "breached") {
    return "已超时";
  }
  if (status === "due_soon") {
    return "临近 SLA";
  }
  if (status === "on_track") {
    return "正常";
  }
  return status;
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
    return `${entry.actor_name || "system"} 将 memory proposal 标记为 ${
      entry.decision === "approved" ? "approved" : "rejected"
    }。`;
  }
  if (entry.source_kind === "assignment") {
    return entry.actor_name ? `${entry.actor_name} 更新了章节处理人。` : "章节所有权发生变化。";
  }
  return `${entry.issue_type || "Issue"} -> ${entry.action_type || "action"} · ${
    entry.scope_type || "scope"
  } ${entry.scope_id ? shorten(entry.scope_id, 5) : ""}`.trim();
}

function groupTimelineEntries(entries: ChapterWorklistTimelineEntry[]) {
  const grouped = new Map<string, ChapterWorklistTimelineEntry[]>();
  for (const entry of entries) {
    const key = timelineGroupKey(entry.source_kind);
    const current = grouped.get(key) ?? [];
    current.push(entry);
    grouped.set(key, current);
  }
  return Array.from(grouped.entries()).map(([key, groupEntries]) => ({
    key,
    title: timelineGroupTitle(key),
    entries: groupEntries.sort((left, right) => right.created_at.localeCompare(left.created_at)),
  }));
}

function timelineGroupKey(sourceKind: string) {
  if (sourceKind === "action") {
    return "action";
  }
  if (sourceKind === "assignment") {
    return "assignment";
  }
  if (sourceKind === "memory_proposal") {
    return "memory_proposal";
  }
  return "other";
}

function timelineGroupTitle(groupKey: string) {
  if (groupKey === "action") {
    return "Follow-up Actions";
  }
  if (groupKey === "assignment") {
    return "Assignments";
  }
  if (groupKey === "memory_proposal") {
    return "Memory Overrides";
  }
  return "Other Events";
}

function buildActiveQueueFilters(filters: {
  queuePriority: "all" | "immediate" | "high" | "medium";
  assignment: "all" | "assigned" | "unassigned";
  assignedOwnerName: string;
}) {
  const labels: string[] = [];
  if (filters.queuePriority !== "all") {
    labels.push(`Priority · ${queuePriorityLabel(filters.queuePriority)}`);
  }
  if (filters.assignment === "assigned") {
    labels.push("Assignment · 已分派");
  } else if (filters.assignment === "unassigned") {
    labels.push("Assignment · 共享队列");
  }
  if (filters.assignedOwnerName) {
    labels.push(`Owner · ${filters.assignedOwnerName}`);
  }
  return labels;
}

function buildTimelineFocus(entry: ChapterWorklistTimelineEntry): TimelineFocusTarget {
  if (entry.source_kind === "assignment") {
    return {
      eventId: entry.event_id,
      section: "assignment",
      label: entry.owner_name ? `Assignment · ${entry.owner_name}` : "Assignment · 共享队列",
      helper: "已把焦点切到 assignment 控制区，可以继续指派、回收或补充备注。",
    };
  }
  if (entry.source_kind === "memory_proposal") {
    return {
      eventId: entry.event_id,
      section: "proposal",
      proposalId: entry.proposal_id,
      label: `Memory Override · ${shorten(entry.proposal_id, 5)}`,
      helper: "已把焦点切到 proposal 区。若这条 proposal 仍待审批，会直接高亮对应卡片。",
    };
  }
  return {
    eventId: entry.event_id,
    section: "actions",
    actionId: entry.action_id ?? entry.event_id,
    label: `Follow-up Action · ${entry.action_type || entry.issue_type || shorten(entry.event_id, 5)}`,
    helper: "已把焦点切到 Recent Actions，可以直接执行 follow-up 或核对最近一次执行结果。",
  };
}

function buildOperatorSnapshot(
  queueEntry:
    | {
        memory_proposals?: { pending_proposal_count?: number; active_snapshot_version?: number | null };
        assigned_owner_name?: string | null;
      }
    | null,
  detail:
    | {
        memory_proposals: { pending_proposal_count: number; active_snapshot_version?: number | null };
        assignment?: { owner_name?: string | null } | null;
        recent_actions: Array<{ status: string }>;
      }
    | null
) {
  if (!queueEntry && !detail) {
    return null;
  }
  return {
    pendingProposalCount:
      detail?.memory_proposals.pending_proposal_count ??
      queueEntry?.memory_proposals?.pending_proposal_count ??
      0,
    activeSnapshotVersion:
      detail?.memory_proposals.active_snapshot_version ??
      queueEntry?.memory_proposals?.active_snapshot_version ??
      null,
    ownerName: detail?.assignment?.owner_name ?? queueEntry?.assigned_owner_name ?? "共享队列",
    actionStatus: detail?.recent_actions[0]?.status ?? "unknown",
  } satisfies OperatorConvergenceSnapshot;
}

function buildConvergenceItems(
  before: OperatorConvergenceSnapshot | null,
  after: OperatorConvergenceSnapshot | null
) {
  if (!before || !after) {
    return [];
  }
  const items: Array<{ label: string; value: string }> = [];
  if (before.pendingProposalCount !== after.pendingProposalCount) {
    items.push({
      label: "Pending",
      value: `Pending ${before.pendingProposalCount} -> ${after.pendingProposalCount}`,
    });
  }
  if (before.activeSnapshotVersion !== after.activeSnapshotVersion) {
    items.push({
      label: "Snapshot",
      value: `Snapshot v${before.activeSnapshotVersion ?? "—"} -> v${after.activeSnapshotVersion ?? "—"}`,
    });
  }
  if (before.ownerName !== after.ownerName) {
    items.push({
      label: "Owner",
      value: `Owner ${before.ownerName} -> ${after.ownerName}`,
    });
  }
  if (before.actionStatus !== after.actionStatus) {
    items.push({
      label: "Action",
      value: `Action ${before.actionStatus} -> ${after.actionStatus}`,
    });
  }
  return items;
}

function recentChangeKindLabel(kind: RecentOperatorChange["kind"]) {
  if (kind === "proposal") {
    return "Proposal 回写";
  }
  if (kind === "assignment") {
    return "Assignment 回写";
  }
  return "Action 回写";
}

function timelineEntryMatchesRecentChange(
  entry: ChapterWorklistTimelineEntry,
  change: RecentOperatorChange
) {
  if (change.kind === "proposal") {
    return entry.source_kind === "memory_proposal";
  }
  if (change.kind === "assignment") {
    return entry.source_kind === "assignment";
  }
  return entry.source_kind === "action";
}

function buildRecentChangeNextStep(
  change: RecentOperatorChange,
  snapshot: OperatorConvergenceSnapshot | null,
  detail:
    | {
        current_active_blocking_issue_count: number;
        memory_proposals: { pending_proposal_count: number };
        assignment?: { owner_name?: string | null } | null;
      }
    | null
) {
  if (!snapshot || !detail) {
    return null;
  }
  if (change.kind === "proposal") {
    if (snapshot.pendingProposalCount > 0) {
      return {
        title: "继续清理剩余 proposal",
        body: `当前还有 ${snapshot.pendingProposalCount} 条待审批 proposal，优先继续处理 memory override，避免章节停在半收敛状态。`,
      };
    }
    if (detail.current_active_blocking_issue_count > 0) {
      return {
        title: "转入 blocker / follow-up 处理",
        body: `proposal 已收敛，但当前仍有 ${detail.current_active_blocking_issue_count} 个 blocker，下一步应切回 issue/action 面继续推进。`,
      };
    }
    return {
      title: "切回章节队列确认是否可放行",
      body: "当前没有待审批 proposal，也没有明显 blocker，可以回到章节队列判断这章是否已经达到下一阶段门槛。",
    };
  }
  if (change.kind === "assignment") {
    return detail.assignment?.owner_name
      ? {
          title: "由新 owner 接手 follow-up",
          body: `章节现在已经绑定到 ${detail.assignment.owner_name}，下一步应由当前 owner 继续执行 blocker 处理或 proposal 审批。`,
        }
      : {
          title: "回到共享队列重新分诊",
          body: "章节已回收到共享队列，下一步应由值班 operator 重新确认优先级、owner 和 follow-up 动作。",
        };
  }
  return snapshot.actionStatus === "completed"
    ? {
        title: "复核 rerun / recheck 结果",
        body: "follow-up action 已执行完成，下一步应检查 rerun 后的 issue、timeline 和 queue 状态是否真正收敛。",
      }
    : {
        title: "继续盯 action 执行结果",
        body: "当前 action 还没有进入完成态，下一步应继续观察 recheck / rerun 是否落盘，再决定是否扩大处理范围。",
      };
}
