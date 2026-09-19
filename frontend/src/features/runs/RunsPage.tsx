import { useState } from "react";
import { Link } from "react-router-dom";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  awaitingApprovalStage,
  currentStageKey,
  documentBadge,
  describeRunEvent,
  failedPipelineStage,
  formatDate,
  formatNumber,
  getFocusChapters,
  getPrimaryRunAction,
  pipelineMeta,
  pipelineStageLabel,
  pipelineStepsFor,
  preferredTitle,
  readerPath,
  stageStatus,
  statusLabel,
  translateProgress,
} from "../../lib/workflow";
import { stopReasonNote } from "../../lib/runStopReason";
import s from "./RunsPage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

export function RunsPage() {
  const {
    currentDocument,
    currentRun,
    currentRunEvents,
    runActionPending,
    runPrimaryAction,
    refreshCurrentDocument,
    downloadChapterAsset,
  } = useWorkspace();

  const [feedback, setFeedback] = useState<Feedback>(null);

  const action = getPrimaryRunAction(currentDocument, currentRun);
  const badge = documentBadge(currentDocument, currentRun);
  const focusChapters = getFocusChapters(currentDocument);
  const failedStage = failedPipelineStage(currentDocument, currentRun);
  const approvalStage = awaitingApprovalStage(currentDocument, currentRun);
  const currentStage = currentStageKey(currentRun);
  const progress = translateProgress(currentDocument, currentRun);
  const pct = Math.round(progress.ratio * 100);
  const stopNote = stopReasonNote(currentRun?.status, currentRun?.stop_reason);
  const readPath = readerPath(currentDocument);

  async function handleAction() {
    try {
      const run = await runPrimaryAction();
      setFeedback({ tone: "success", text: "已提交，转换开始了" });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "操作失败" });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: filename });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "下载失败" });
    }
  }

  return (
    <div className={s.layout}>
      {/* ── Run Overview ── */}
      <Surface
        eyebrow="运行"
        title="运行总览"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentRun && currentDocument ? (
          <>
            {/* Compact summary row */}
            <div className={s.summaryRow}>
              <span className={s.stat}>
                <span className={s.statLabel}>当前步骤</span>
                <span className={s.statValue}>{pipelineStageLabel(currentStage)}</span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>进度</span>
                <span className={s.statValue}>
                  {formatNumber(progress.completed)}/{formatNumber(progress.total)}
                </span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>状态</span>
                <span className={s.statValue}>{statusLabel(failedStage?.status || (approvalStage ? "awaiting_approval" : currentRun.status))}</span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>更新于</span>
                <span className={s.statValue}>{formatDate(currentRun.updated_at)}</span>
              </span>
            </div>

            {/* Thin progress bar */}
            <div className={s.progressWrap}>
              <div className={s.progressTrack}>
                <span
                  className={s.progressFill}
                  style={{ width: `${progress.total ? Math.max(pct, progress.completed > 0 ? 4 : 0) : 0}%` }}
                />
              </div>
              <span className={s.progressLabel}>{pct}%</span>
            </div>

            {/* Action bar */}
            <div className={s.actionBar}>
              {readPath && (
                <Link className="btn btn-primary" to={readPath}>
                  阅读中文版
                </Link>
              )}
              <button
                className="btn"
                disabled={action.disabled || runActionPending}
                onClick={handleAction}
              >
                {runActionPending ? "提交中…" : action.label}
              </button>
              <button className="btn btn-sm" onClick={() => void refreshCurrentDocument()}>
                刷新
              </button>
            </div>

            {stopNote && (
              <div className={s.feedback} data-tone="error" role="status">
                {stopNote.text}
                {stopNote.link && (
                  <>
                    {" "}
                    <Link to={stopNote.link.to}>{stopNote.link.label}</Link>
                  </>
                )}
              </div>
            )}

            {approvalStage && (
              <div className={s.feedback} data-tone="warning" role="status">
                「{approvalStage.label}」在等你审批，处理完运行会自动继续。{" "}
                <Link to="/approvals">去「审批」页</Link>
              </div>
            )}

            {feedback && (
              <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
            )}

            {/* Pipeline steps */}
            <div className={s.stepList}>
              {pipelineStepsFor(currentDocument).map((step, idx) => {
                const state = stageStatus(currentDocument, currentRun, step.key);
                return (
                  <div
                    key={step.key}
                    className={s.stepRow}
                    data-current={currentStage === step.key}
                    data-state={state}
                  >
                    <span className={s.stepIndex}>{idx + 1}</span>
                    <span className={s.stepTitle}>{step.label}</span>
                    <span className={s.stepDesc}>{step.description}</span>
                    <span className={s.stepMeta}>{pipelineMeta(currentDocument, currentRun, step.key)}</span>
                    <StatusBadge
                      tone={
                        state === "failed" ? "danger"
                        : state === "succeeded" ? "success"
                        : state === "partial" ? "warning"
                        : state === "paused" || state === "awaiting_approval" ? "warning"
                        : state === "running" || state === "queued" || state === "draining" ? "active"
                        : "muted"
                      }
                      label={statusLabel(state)}
                    />
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div className={s.emptyHero}>
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={s.illustration}>
              <circle cx="32" cy="32" r="26" />
              <polygon points="26,20 26,44 46,32" />
            </svg>
            <span className={s.emptyTitle}>还没有运行</span>
            <span className={s.emptySubtitle}>先在工作台上传一本书，再开始翻译。</span>
          </div>
        )}
      </Surface>

      {/* ── Split: Chapters + Events ── */}
      <div className={s.splitGrid}>
        <div className={s.section}>
          <h4 className={s.sectionTitle}>需要关注的章节</h4>
          {focusChapters.length ? (
            <div className={s.chapterList}>
              {focusChapters.map((ch) => (
                <div key={ch.chapter_id} className={s.chapterRow}>
                  <span className={s.chOrd}>第 {ch.ordinal} 章</span>
                  <StatusBadge
                    tone={ch.open_issue_count > 0 ? "warning" : "success"}
                    label={statusLabel(ch.status)}
                  />
                  <span className={s.chTitle}>{ch.title_src || "未命名"}</span>
                  <span className={s.chMeta}>
                    {formatNumber(ch.open_issue_count)} 个问题 &middot; {formatNumber(ch.packet_count)} 包
                  </span>
                  <button
                    className="btn btn-sm"
                    disabled={!ch.bilingual_export_ready}
                    onClick={() => void handleChapterDownload(ch.chapter_id)}
                  >
                    {ch.bilingual_export_ready ? "下载" : "—"}
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className={s.emptyState}>
              {currentDocument
                ? "没有需要关注的章节。"
                : "载入一本书后，这里显示需要关注的章节。"}
            </div>
          )}
        </div>

        <div className={s.section}>
          <h4 className={s.sectionTitle}>最近动态</h4>
          {currentRunEvents.length ? (
            <div className={s.eventList}>
              {currentRunEvents.map((ev) => {
                const described = describeRunEvent(ev);
                if (!described) return null;
                return (
                  <div key={ev.event_id} className={s.eventRow}>
                    <span className={s.eventKind}>{described.title}</span>
                    <span className={s.eventText}>{described.detail ?? ""}</span>
                    <span className={s.eventDate}>{formatDate(ev.created_at)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className={s.emptyState}>还没有运行事件。</div>
          )}
        </div>
      </div>
    </div>
  );
}
