import { useState } from "react";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  DELIVERY_ASSETS,
  assetAvailabilityText,
  blockingIssueCount,
  deliverableBlockerReason,
  documentBadge,
  downloadReady,
  formatDate,
  formatNumber,
  preferredTitle,
  statusLabel,
} from "../../lib/workflow";
import styles from "./DeliverablesPage.module.css";

export function DeliverablesPage() {
  const { currentDocument, currentRun, currentExports, downloadAsset } = useWorkspace();
  const [message, setMessage] = useState<string | null>(null);

  const badge = documentBadge(currentDocument, currentRun);

  async function handleDownload(
    exportType: "merged_html" | "bilingual_html" | "review_package"
  ) {
    try {
      const filename = await downloadAsset(exportType);
      setMessage(`已开始下载 ${filename}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "下载失败。");
    }
  }

  return (
    <div className={styles.layout}>
      <div className={styles.heroGrid}>
        <Surface
          eyebrow="Deliverables"
          title="交付资产"
          description="只展示现在能下载什么，以及每个资产离可交付还有多远。"
          aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
        >
          {currentDocument ? (
            <>
              <div className={styles.assetGrid}>
                {DELIVERY_ASSETS.map((asset) => (
                  <article key={asset.key} className={styles.assetCard}>
                    <div className={styles.assetLabel}>{asset.label}</div>
                    <div className={styles.assetTitle}>{asset.title}</div>
                    <div className={styles.assetCopy}>
                      {assetAvailabilityText(asset.key, currentDocument, currentRun, currentExports)}
                    </div>
                    <button
                      className={styles.button}
                      type="button"
                      disabled={!downloadReady(currentDocument, currentExports, asset.key)}
                      onClick={() => void handleDownload(asset.key)}
                    >
                      {asset.buttonText}
                    </button>
                  </article>
                ))}
              </div>
              {message ? <div className={styles.message}>{message}</div> : null}
            </>
          ) : (
            <div className={styles.emptyState}>先载入当前书籍，这里才会展示可下载资产。</div>
          )}
        </Surface>

        <Surface
          eyebrow="Gate"
          title="为什么现在能否交付"
          description="把阻塞解释成用户可以直接判断的语言，不再堆底层诊断字段。"
          aside={
            currentDocument ? (
              <StatusBadge
                tone={currentDocument.merged_export_ready ? "success" : blockingIssueCount(currentExports) > 0 ? "danger" : "warning"}
                label={
                  currentDocument.merged_export_ready
                    ? "可交付"
                    : blockingIssueCount(currentExports) > 0
                      ? "存在 blocker"
                      : "等待后续阶段"
                }
              />
            ) : null
          }
        >
          {currentDocument ? (
            <>
              <div className={styles.reason}>
                <div className={styles.reasonLabel}>Delivery status</div>
                <p className={styles.reasonText}>
                  {deliverableBlockerReason(currentDocument, currentRun, currentExports)}
                </p>
              </div>
              {currentExports?.issue_hotspots?.length ? (
                <div className={styles.hotspots}>
                  {currentExports.issue_hotspots.slice(0, 3).map((entry) => (
                    <article key={`${entry.issue_type}-${entry.root_cause_layer || "unknown"}`} className={styles.hotspotCard}>
                      <h3 className={styles.hotspotTitle}>
                        {entry.issue_type} · {entry.root_cause_layer || "unknown"}
                      </h3>
                      <p className={styles.hotspotCopy}>
                        issue {formatNumber(entry.issue_count)} · open {formatNumber(entry.open_issue_count)} · blocking{" "}
                        {formatNumber(entry.blocking_issue_count)}
                      </p>
                    </article>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <div className={styles.emptyState}>当前还没有可解释的交付状态。</div>
          )}
        </Surface>
      </div>

      <Surface
        eyebrow="Recent Exports"
        title="最近导出记录"
        description="如果需要追查最近一次交付，这里保留最短路径。"
        aside={currentDocument ? preferredTitle(currentDocument) : null}
      >
        {currentExports?.records?.length ? (
          <div className={styles.records}>
            {currentExports.records.map((record) => (
              <article key={record.export_id} className={styles.recordRow}>
                <div className={styles.recordTop}>
                  <div className={styles.recordMeta}>
                    <h3 className={styles.recordTitle}>{record.export_type}</h3>
                    <p className={styles.recordCopy}>
                      {statusLabel(record.status)} · {formatDate(record.created_at)}
                    </p>
                  </div>
                  <StatusBadge
                    tone={record.status === "succeeded" ? "success" : record.status === "failed" ? "danger" : "warning"}
                    label={statusLabel(record.status)}
                  />
                </div>
                <p className={styles.recordCopy}>
                  {currentDocument ? preferredTitle(currentDocument) : "当前书籍"} · 最近总导出{" "}
                  {formatNumber(currentExports.export_count)}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <div className={styles.emptyState}>还没有导出记录。等系统进入导出阶段后，这里会自动出现。</div>
        )}
      </Surface>
    </div>
  );
}
