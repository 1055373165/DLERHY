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
      setFeedback({ tone: "success", text: `Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Download failed" });
    }
  }

  return (
    <div className={s.layout}>
      {/* ── Assets ── */}
      <Surface
        eyebrow="SHIP"
        title="交付资产"
        aside={currentDocument ? <StatusBadge tone={badge.tone} label={badge.label} /> : null}
      >
        {currentDocument ? (
          <>
            <div className={s.assetList}>
              {DELIVERY_ASSETS.map((asset) => (
                <div key={asset.key} className={s.assetRow}>
                  <span className={s.assetLabel}>{asset.label}</span>
                  <span className={s.assetTitle}>{asset.title}</span>
                  <span className={s.assetAvail}>
                    {assetAvailabilityText(asset.key, currentDocument, currentRun, currentExports)}
                  </span>
                  <button
                    className="btn btn-sm"
                    disabled={!downloadReady(currentDocument, currentExports, asset.key)}
                    onClick={() => void handleDownload(asset.key)}
                  >
                    {asset.buttonText}
                  </button>
                </div>
              ))}
            </div>
            {feedback && (
              <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
            )}
          </>
        ) : (
          <div className={s.emptyState}>Load a document to view deliverables.</div>
        )}
      </Surface>

      {/* ── Blocker Status ── */}
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
            <div className={s.gateRow}>
              <span className={s.gateLabel}>DELIVERY STATUS</span>
              <span className={s.gateText}>
                {deliverableBlockerReason(currentDocument, currentRun, currentExports)}
              </span>
            </div>
            {currentExports?.issue_hotspots?.length ? (
              <div className={s.hotspotList}>
                {currentExports.issue_hotspots.slice(0, 3).map((entry) => (
                  <div
                    key={`${entry.issue_type}-${entry.root_cause_layer || "unknown"}`}
                    className={s.hotspotRow}
                  >
                    <span className={s.hotspotType}>
                      {entry.issue_type} :: {entry.root_cause_layer || "unknown"}
                    </span>
                    <span className={s.hotspotCounts}>
                      {formatNumber(entry.issue_count)} total &middot; {formatNumber(entry.open_issue_count)} open &middot; {formatNumber(entry.blocking_issue_count)} blocking
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

      {/* ── Export Records ── */}
      <Surface
        eyebrow="LOG"
        title="导出记录"
        aside={currentDocument ? preferredTitle(currentDocument) : null}
      >
        {currentExports?.records?.length ? (
          <div className={s.recordList}>
            {currentExports.records.map((rec) => (
              <div key={rec.export_id} className={s.recordRow}>
                <span className={s.recordType}>{rec.export_type}</span>
                <span className={s.recordDate}>{formatDate(rec.created_at)}</span>
                <StatusBadge
                  tone={rec.status === "succeeded" ? "success" : rec.status === "failed" ? "danger" : "warning"}
                  label={statusLabel(rec.status)}
                />
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
