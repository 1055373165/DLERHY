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
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "下载失败" });
    }
  }

  return (
    <div className={s.layout}>
      {/* ── Assets ── */}
      <Surface
        eyebrow="交付"
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
          <div className={s.emptyHero}>
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={s.illustration}>
              <path d="M8 48l8-32h32l8 32" />
              <rect x="4" y="48" width="56" height="12" rx="2" />
              <path d="M24 20v-8h16v8" />
              <line x1="22" y1="36" x2="42" y2="36" />
              <line x1="26" y1="42" x2="38" y2="42" />
            </svg>
            <span className={s.emptyTitle}>交付</span>
            <span className={s.emptySubtitle}>先在工作台或书库选择一本书，这里会列出可下载的文件。</span>
          </div>
        )}
      </Surface>

      {/* ── Blocker Status ── */}
      <Surface
        eyebrow="阻塞"
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
                  ? "可交付"
                  : blockingIssueCount(currentExports) > 0
                    ? "有阻塞"
                    : "未完成"
              }
            />
          ) : null
        }
      >
        {currentDocument ? (
          <>
            <div className={s.gateRow}>
              <span className={s.gateLabel}>交付状态</span>
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
          <div className={s.emptyState}>暂无阻塞信息。</div>
        )}
      </Surface>

      {/* ── Export Records ── */}
      <Surface
        eyebrow="记录"
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
          <div className={s.emptyState}>还没有导出记录。</div>
        )}
      </Surface>
    </div>
  );
}
