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
import s from "./DeliverablesPage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

export function DeliverablesPage() {
  const { currentDocument, currentRun, currentExports, downloadAsset } = useWorkspace();
  const [feedback, setFeedback] = useState<Feedback>(null);

  const badge = documentBadge(currentDocument, currentRun);

  async function handleDownload(exportType: "merged_html" | "bilingual_html" | "review_package") {
    try {
      const filename = await downloadAsset(exportType);
      setFeedback({ tone: "success", text: `[OK] Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Download failed"}` });
    }
  }

  return (
    <div className={s.layout}>
      {/* ══════════════ ASSETS ══════════════ */}
      <Surface
        eyebrow="SHIP"
        title="交付资产"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentDocument ? (
          <>
            <div className={s.assetGrid}>
              {DELIVERY_ASSETS.map((asset) => (
                <div key={asset.key} className={s.assetCard}>
                  <span className={s.assetLabel}>{asset.label}</span>
                  <span className={s.assetTitle}>{asset.title}</span>
                  <span className={s.assetDesc}>
                    {assetAvailabilityText(asset.key, currentDocument, currentRun, currentExports)}
                  </span>
                  <button
                    className={s.btnAction}
                    disabled={!downloadReady(currentDocument, currentExports, asset.key)}
                    onClick={() => void handleDownload(asset.key)}
                  >
                    {`> ${asset.buttonText}`}
                  </button>
                </div>
              ))}
            </div>
            {feedback && (
              <div className={s.feedback} data-tone={feedback.tone}>
                {feedback.text}
              </div>
            )}
          </>
        ) : (
          <div className={s.emptyState}>
            <span className={s.prompt}>$</span> LOAD A DOCUMENT TO VIEW DELIVERABLES
            <span className={s.cursor} />
          </div>
        )}
      </Surface>

      {/* ══════════════ BLOCKER STATUS ══════════════ */}
      <Surface
        eyebrow="GATE"
        title="交付阻塞"
        aside={
          currentDocument ? (
            <StatusBadge
              tone={
                currentDocument.merged_export_ready
                  ? "success"
                  : blockingIssueCount(currentExports) > 0
                    ? "danger"
                    : "warning"
              }
              label={
                currentDocument.merged_export_ready
                  ? "CLEAR"
                  : blockingIssueCount(currentExports) > 0
                    ? "BLOCKED"
                    : "PENDING"
              }
            />
          ) : null
        }
      >
        {currentDocument ? (
          <>
            <div className={s.reasonBox}>
              <span className={s.label}>DELIVERY STATUS</span>
              <span className={s.reasonText}>
                {deliverableBlockerReason(currentDocument, currentRun, currentExports)}
              </span>
            </div>
            {currentExports?.issue_hotspots?.length ? (
              <div className={s.hotspotList}>
                {currentExports.issue_hotspots.slice(0, 3).map((entry) => (
                  <div
                    key={`${entry.issue_type}-${entry.root_cause_layer || "unknown"}`}
                    className={s.hotspotCard}
                  >
                    <span className={s.hotspotTitle}>
                      {entry.issue_type} :: {entry.root_cause_layer || "unknown"}
                    </span>
                    <span className={s.hotspotMeta}>
                      total {formatNumber(entry.issue_count)} | open{" "}
                      {formatNumber(entry.open_issue_count)} | blocking{" "}
                      {formatNumber(entry.blocking_issue_count)}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : (
          <div className={s.emptyState}>No blocker status available.</div>
        )}
      </Surface>

      {/* ══════════════ EXPORT RECORDS ══════════════ */}
      <Surface
        eyebrow="LOG"
        title="导出记录"
        aside={currentDocument ? preferredTitle(currentDocument) : null}
      >
        {currentExports?.records?.length ? (
          <div className={s.recordList}>
            {currentExports.records.map((rec) => (
              <div key={rec.export_id} className={s.recordRow}>
                <div className={s.recordTop}>
                  <div className={s.recordMeta}>
                    <span className={s.recordType}>{rec.export_type}</span>
                    <span className={s.recordNote}>
                      {statusLabel(rec.status)} :: {formatDate(rec.created_at)}
                    </span>
                  </div>
                  <StatusBadge
                    tone={rec.status === "succeeded" ? "success" : rec.status === "failed" ? "danger" : "warning"}
                    label={statusLabel(rec.status)}
                  />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className={s.emptyState}>No export records yet.</div>
        )}
      </Surface>
    </div>
  );
}
