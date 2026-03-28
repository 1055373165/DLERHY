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
import styles from "./RunsPage.module.css";

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
  const [message, setMessage] = useState<string | null>(null);

  const primaryAction = getPrimaryRunAction(currentDocument, currentRun);
  const badge = documentBadge(currentDocument, currentRun);
  const focusChapters = getFocusChapters(currentDocument);
  const failedStage = failedPipelineStage(currentDocument, currentRun);
  const currentStage = currentStageKey(currentRun);
  const progress = translateProgress(currentDocument, currentRun);
  const progressPercent = Math.round(progress.ratio * 100);

  async function handlePrimaryAction() {
    try {
      const run = await runPrimaryAction();
      setMessage(`已推进 run ${run.run_id.slice(0, 6)}。页面会自动同步阶段变化。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "运行操作失败。");
    }
  }

  async function handleChapterDownload(chapterId: string) {
    try {
      const filename = await downloadChapterAsset(chapterId);
      setMessage(`已开始下载 ${filename}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "章节下载失败。");
    }
  }

  return (
    <div className={styles.layout}>
      <Surface
        eyebrow="Run Flow"
        title="运行总览"
        description="所有决定都围绕当前 run 的阶段、状态和下一次可执行操作。"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentRun && currentDocument ? (
          <>
            <div className={styles.summaryGrid}>
              <div className={styles.summaryCard}>
                <div className={styles.summaryLabel}>当前阶段</div>
                <div className={styles.summaryValue}>
                  {pipelineStageLabel(currentStageKey(currentRun))}
                </div>
                <div className={styles.summaryNote}>状态 {statusLabel(failedStage?.status || currentRun.status)}</div>
              </div>
              <div className={styles.summaryCard}>
                <div className={styles.summaryLabel}>Run ID</div>
                <div className={styles.summaryValue}>{currentRun.run_id.slice(0, 6)}</div>
                <div className={styles.summaryNote}>更新时间 {formatDate(currentRun.updated_at)}</div>
              </div>
              <div className={styles.summaryCard}>
                <div className={styles.summaryLabel}>翻译进度</div>
                <div className={styles.summaryValue}>
                  {formatNumber(progress.completed)} / {formatNumber(progress.total)}
                </div>
                <div className={styles.summaryNote}>
                  worker leases {formatNumber(currentRun.worker_leases.total_count)}
                </div>
              </div>
            </div>
            <div className={styles.progressPanel}>
              <div className={styles.progressHead}>
                <div className={styles.progressMeta}>
                  <div className={styles.progressLabel}>翻译进度</div>
                  <div className={styles.progressValue}>{progressPercent}%</div>
                </div>
                <p className={styles.progressCopy}>
                  已完成 {formatNumber(progress.completed)} / {formatNumber(progress.total)} 个 packet
                </p>
              </div>
              <div className={styles.progressTrack}>
                <span
                  className={styles.progressFill}
                  style={{
                    width: `${progress.total ? Math.max(progressPercent, progress.completed > 0 ? 6 : 0) : 0}%`,
                  }}
                />
              </div>
            </div>
            <div className={styles.actions}>
              <button
                className={styles.button}
                type="button"
                disabled={primaryAction.disabled || runActionPending}
                onClick={handlePrimaryAction}
              >
                {runActionPending ? "处理中…" : primaryAction.label}
              </button>
              <button className={styles.ghostButton} type="button" onClick={() => void refreshCurrentDocument()}>
                刷新状态
              </button>
            </div>
            {message ? <div className={styles.message}>{message}</div> : null}
            <div className={styles.stepList}>
              {PIPELINE_STEPS.map((step, index) => {
                const state = stageStatus(currentDocument, currentRun, step.key);
                return (
                  <article
                    key={step.key}
                    className={styles.stepCard}
                    data-current={currentStage === step.key}
                    data-state={state}
                  >
                    <div className={styles.stepIndex}>{index + 1}</div>
                    <div className={styles.stepMeta}>
                      <h3 className={styles.stepTitle}>{step.label}</h3>
                      <p className={styles.stepCopy}>{step.description}</p>
                      <p className={styles.stepCopy}>{pipelineMeta(currentDocument, currentRun, step.key)}</p>
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
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <div className={styles.emptyState}>
            <strong>当前还没有活跃 run。</strong>
            <span>先在工作台上传书稿并启动整书转换，这里才会成为你的主监控页。</span>
          </div>
        )}
      </Surface>

      <div className={styles.splitGrid}>
        <div className={styles.listCard}>
          <div className={styles.listHeader}>
            <h3 className={styles.listTitle}>最值得关注的章节</h3>
            <p className={styles.listCopy}>
              只列最有行动价值的章节，优先看 open issue 多、已可直接导出的章节。
            </p>
          </div>
          {focusChapters.length ? (
            <div className={styles.chapterList}>
              {focusChapters.map((chapter) => (
                <article key={chapter.chapter_id} className={styles.chapterRow}>
                  <div className={styles.chapterTop}>
                    <div className={styles.chapterMeta}>
                      <h4 className={styles.chapterTitle}>
                        第 {chapter.ordinal} 章 · {chapter.title_src || "未命名章节"}
                      </h4>
                      <p className={styles.chapterCopy}>
                        状态 {statusLabel(chapter.status)} · open issue {formatNumber(chapter.open_issue_count)} · packet{" "}
                        {formatNumber(chapter.packet_count)}
                      </p>
                    </div>
                    <button
                      className={styles.smallButton}
                      type="button"
                      disabled={!chapter.bilingual_export_ready}
                      onClick={() => void handleChapterDownload(chapter.chapter_id)}
                    >
                      {chapter.bilingual_export_ready ? "下载双语章节" : "等待导出"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className={styles.emptyState}>
              {currentDocument
                ? `《${preferredTitle(currentDocument)}》当前还没有需要额外关注的章节。`
                : "载入当前书籍后，这里会自动聚焦最值得处理的章节。"}
            </div>
          )}
        </div>

        <div className={styles.listCard}>
          <div className={styles.listHeader}>
            <h3 className={styles.listTitle}>最近事件</h3>
            <p className={styles.listCopy}>用最近事件判断系统刚刚做了什么，而不是翻大量内部字段。</p>
          </div>
          {currentRunEvents.length ? (
            <div className={styles.eventList}>
              {currentRunEvents.map((entry) => (
                <article key={entry.event_id} className={styles.eventCard}>
                  <div className={styles.eventTop}>
                    <div className={styles.eventMeta}>
                      <h4 className={styles.eventTitle}>{eventTitle(entry.event_type)}</h4>
                      <p className={styles.eventCopy}>
                        {entry.event_type} · {formatDate(entry.created_at)}
                      </p>
                    </div>
                    <StatusBadge tone="muted" label={entry.actor_type || "system"} />
                  </div>
                  <p className={styles.eventCopy}>
                    event {entry.event_id.slice(0, 6)}
                    {entry.work_item_id ? ` · work item ${entry.work_item_id.slice(0, 6)}` : ""}
                  </p>
                </article>
              ))}
            </div>
          ) : (
            <div className={styles.emptyState}>当前没有可展示的 run 事件。</div>
          )}
        </div>
      </div>
    </div>
  );
}
