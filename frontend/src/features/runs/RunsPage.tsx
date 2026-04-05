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
      setFeedback({ tone: "success", text: `[OK] run ${run.run_id.slice(0, 6)} pushed` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Action failed"}` });
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setFeedback({ tone: "success", text: `[OK] ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Download failed"}` });
    }
  }

  return (
    <div className={s.layout}>
      {/* ══════════════ RUN OVERVIEW ══════════════ */}
      <Surface
        eyebrow="RUN"
        title="运行总览"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentRun && currentDocument ? (
          <>
            <div className={s.summaryGrid}>
              <div className={s.summaryCard}>
                <span className={s.label}>STAGE</span>
                <span className={s.value}>{pipelineStageLabel(currentStage)}</span>
                <span className={s.note}>{statusLabel(failedStage?.status || currentRun.status)}</span>
              </div>
              <div className={s.summaryCard}>
                <span className={s.label}>RUN</span>
                <span className={s.value}>{currentRun.run_id.slice(0, 6)}</span>
                <span className={s.note}>{formatDate(currentRun.updated_at)}</span>
              </div>
              <div className={s.summaryCard}>
                <span className={s.label}>PROGRESS</span>
                <span className={s.value}>
                  {formatNumber(progress.completed)}/{formatNumber(progress.total)}
                </span>
                <span className={s.note}>
                  leases {formatNumber(currentRun.worker_leases.total_count)}
                </span>
              </div>
            </div>

            {/* Progress Bar */}
            <div className={s.progressPanel}>
              <div className={s.progressHead}>
                <span className={s.label}>TRANSLATE</span>
                <span className={s.progressPct}>{pct}%</span>
              </div>
              <div className={s.progressTrack}>
                <span
                  className={s.progressFill}
                  style={{
                    width: `${progress.total ? Math.max(pct, progress.completed > 0 ? 6 : 0) : 0}%`,
                  }}
                />
              </div>
              <span className={s.note}>
                {formatNumber(progress.completed)} / {formatNumber(progress.total)} packets
              </span>
            </div>

            {/* Actions */}
            <div className={s.actionBar}>
              <button
                className={s.btnAction}
                disabled={action.disabled || runActionPending}
                onClick={handleAction}
              >
                {runActionPending ? "EXECUTING..." : `$ ${action.label}`}
              </button>
              <button
                className={s.btnSmall}
                onClick={() => void refreshCurrentDocument()}
              >
                REFRESH
              </button>
            </div>

            {feedback && (
              <div className={s.feedback} data-tone={feedback.tone}>
                {feedback.text}
              </div>
            )}

            {/* Pipeline Steps */}
            <div className={s.stepList}>
              {PIPELINE_STEPS.map((step, idx) => {
                const state = stageStatus(currentDocument, currentRun, step.key);
                return (
                  <div
                    key={step.key}
                    className={s.stepCard}
                    data-current={currentStage === step.key}
                    data-state={state}
                  >
                    <span className={s.stepIndex}>{idx + 1}</span>
                    <div className={s.stepBody}>
                      <span className={s.stepTitle}>{step.label}</span>
                      <span className={s.stepDesc}>{step.description}</span>
                      <span className={s.stepMeta}>{pipelineMeta(currentDocument, currentRun, step.key)}</span>
                    </div>
                    <StatusBadge
                      tone={
                        state === "failed"
                          ? "danger"
                          : state === "succeeded"
                            ? "success"
                            : state === "paused"
                              ? "warning"
                              : state === "running" || state === "queued" || state === "draining"
                                ? "active"
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
          <div className={s.emptyState}>
            <span className={s.prompt}>$</span> NO ACTIVE RUN — BOOTSTRAP A DOCUMENT FIRST
            <span className={s.cursor} />
          </div>
        )}
      </Surface>

      {/* ══════════════ SPLIT: CHAPTERS + EVENTS ══════════════ */}
      <div className={s.splitGrid}>
        {/* Focus Chapters */}
        <div className={s.section}>
          <h4 className={s.sectionTitle}>FOCUS CHAPTERS</h4>
          {focusChapters.length ? (
            <div className={s.cardList}>
              {focusChapters.map((ch) => (
                <div key={ch.chapter_id} className={s.card}>
                  <div className={s.cardTop}>
                    <span className={s.cardOrd}>CH.{ch.ordinal}</span>
                    <StatusBadge
                      tone={ch.open_issue_count > 0 ? "warning" : "success"}
                      label={statusLabel(ch.status)}
                    />
                  </div>
                  <span className={s.cardTitle}>{ch.title_src || "Untitled"}</span>
                  <span className={s.cardMeta}>
                    issues {formatNumber(ch.open_issue_count)} | packets {formatNumber(ch.packet_count)}
                  </span>
                  <button
                    className={s.btnSmall}
                    disabled={!ch.bilingual_export_ready}
                    onClick={() => void handleChapterDownload(ch.chapter_id)}
                  >
                    {ch.bilingual_export_ready ? "DOWNLOAD" : "PENDING"}
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

        {/* Recent Events */}
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
