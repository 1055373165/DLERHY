import { useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  PIPELINE_STEPS,
  currentStageKey,
  documentBadge,
  eventTitle,
  failedPipelineStage,
  formatDate,
  formatNumber,
  getFocusChapters,
  getPrimaryRunAction,
  pipelineMeta,
  pipelineStageLabel,
  preferredTitle,
  stageStatus,
  statusLabel,
  translateProgress,
} from "../../lib/workflow";
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
  const currentStage = currentStageKey(currentRun);
  const progress = translateProgress(currentDocument, currentRun);
  const pct = Math.round(progress.ratio * 100);

  async function handleAction() {
    try {
      const run = await runPrimaryAction();
      setFeedback({ tone: "success", text: `Run ${run.run_id.slice(0, 6)} pushed` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Action failed" });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: filename });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Download failed" });
    }
  }

  return (
    <div className={s.layout}>
      {/* ── Run Overview ── */}
      <Surface
        eyebrow="RUN"
        title="运行总览"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentRun && currentDocument ? (
          <>
            {/* Compact summary row */}
            <div className={s.summaryRow}>
              <span className={s.stat}>
                <span className={s.statLabel}>Stage</span>
                <span className={s.statValue}>{pipelineStageLabel(currentStage)}</span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>Run</span>
                <span className={s.statValue}>{currentRun.run_id.slice(0, 6)}</span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>Progress</span>
                <span className={s.statValue}>
                  {formatNumber(progress.completed)}/{formatNumber(progress.total)}
                </span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>Status</span>
                <span className={s.statValue}>{statusLabel(failedStage?.status || currentRun.status)}</span>
              </span>
              <span className={s.stat}>
                <span className={s.statLabel}>Updated</span>
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
              <button
                className="btn"
                disabled={action.disabled || runActionPending}
                onClick={handleAction}
              >
                {runActionPending ? "Executing..." : action.label}
              </button>
              <button className="btn btn-sm" onClick={() => void refreshCurrentDocument()}>
                Refresh
              </button>
            </div>

            {feedback && (
              <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
            )}

            {/* Pipeline steps */}
            <div className={s.stepList}>
              {PIPELINE_STEPS.map((step, idx) => {
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
                        : state === "paused" ? "warning"
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
          <div className={s.emptyState}>No active run — bootstrap a document first.</div>
        )}
      </Surface>

      {/* ── Split: Chapters + Events ── */}
      <div className={s.splitGrid}>
        <div className={s.section}>
          <h4 className={s.sectionTitle}>FOCUS CHAPTERS</h4>
          {focusChapters.length ? (
            <div className={s.chapterList}>
              {focusChapters.map((ch) => (
                <div key={ch.chapter_id} className={s.chapterRow}>
                  <span className={s.chOrd}>CH.{ch.ordinal}</span>
                  <StatusBadge
                    tone={ch.open_issue_count > 0 ? "warning" : "success"}
                    label={statusLabel(ch.status)}
                  />
                  <span className={s.chTitle}>{ch.title_src || "Untitled"}</span>
                  <span className={s.chMeta}>
                    {formatNumber(ch.open_issue_count)} issues &middot; {formatNumber(ch.packet_count)} pkt
                  </span>
                  <button
                    className="btn btn-sm"
                    disabled={!ch.bilingual_export_ready}
                    onClick={() => void handleChapterDownload(ch.chapter_id)}
                  >
                    {ch.bilingual_export_ready ? "Download" : "—"}
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className={s.emptyState}>
              {currentDocument
                ? `No focus chapters for ${preferredTitle(currentDocument)}`
                : "Load a document to see focus chapters."}
            </div>
          )}
        </div>

        <div className={s.section}>
          <h4 className={s.sectionTitle}>RECENT EVENTS</h4>
          {currentRunEvents.length ? (
            <div className={s.eventList}>
              {currentRunEvents.map((ev) => (
                <div key={ev.event_id} className={s.eventRow}>
                  <span className={s.eventKind}>{eventTitle(ev.event_type)}</span>
                  <span className={s.eventText}>
                    {ev.event_type}
                    {ev.work_item_id ? ` :: ${ev.work_item_id.slice(0, 6)}` : ""}
                  </span>
                  <span className={s.eventDate}>{formatDate(ev.created_at)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className={s.emptyState}>No run events yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
