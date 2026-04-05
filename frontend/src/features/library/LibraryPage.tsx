import { useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { useWorkspace } from "../../app/WorkspaceContext";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import { downloadDocumentExport, listDocumentHistory } from "../../lib/api";
import {
  formatDate,
  historyBadge,
  historyProgress,
  preferredTitle,
  sourceLabel,
  statusLabel,
} from "../../lib/workflow";
import s from "./LibraryPage.module.css";

type Feedback = { tone: "success" | "error"; text: string } | null;

export function LibraryPage() {
  const navigate = useNavigate();
  const { selectDocument } = useWorkspace();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [mergedReady, setMergedReady] = useState<"" | "true" | "false">("");
  const deferredQuery = useDeferredValue(query.trim());

  const historyQuery = useQuery({
    queryKey: ["document-history", "library", deferredQuery, status, runStatus, mergedReady],
    queryFn: () =>
      listDocumentHistory({
        query: deferredQuery || undefined,
        status: status || undefined,
        latest_run_status: runStatus || undefined,
        merged_export_ready: mergedReady,
        limit: 12,
        offset: 0,
      }),
  });

  async function handleOpen(documentId: string) {
    selectDocument(documentId);
    await navigate("/");
  }

  async function handleDownload(documentId: string) {
    try {
      const filename = await downloadDocumentExport(documentId, "merged_html");
      setFeedback({ tone: "success", text: `[OK] Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: `[ERR] ${err instanceof Error ? err.message : "Download failed"}` });
    }
  }

  return (
    <div className={s.layout}>
      <Surface
        eyebrow="LIB"
        title="书库"
        aside={
          historyQuery.data
            ? `${historyQuery.data.total_count} total / ${historyQuery.data.record_count} shown`
            : null
        }
      >
        {/* Filters */}
        <div className={s.filterBar}>
          <input
            className={s.searchInput}
            type="search"
            placeholder="> search title, author, path, or document id..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className={s.filterGrid}>
            <select
              className={s.filterSelect}
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">ALL STATUS</option>
              <option value="active">ACTIVE</option>
              <option value="partially_exported">PARTIAL</option>
              <option value="exported">EXPORTED</option>
              <option value="failed">FAILED</option>
            </select>
            <select
              className={s.filterSelect}
              value={runStatus}
              onChange={(e) => setRunStatus(e.target.value)}
            >
              <option value="">ALL RUNS</option>
              <option value="queued">QUEUED</option>
              <option value="running">RUNNING</option>
              <option value="paused">PAUSED</option>
              <option value="failed">FAILED</option>
              <option value="cancelled">CANCELLED</option>
              <option value="succeeded">SUCCEEDED</option>
            </select>
            <select
              className={s.filterSelect}
              value={mergedReady}
              onChange={(e) => setMergedReady(e.target.value as "" | "true" | "false")}
            >
              <option value="">DELIVERY</option>
              <option value="true">READY</option>
              <option value="false">NOT READY</option>
            </select>
          </div>
        </div>

        {feedback && (
          <div className={s.feedback} data-tone={feedback.tone}>
            {feedback.text}
          </div>
        )}

        {/* Results */}
        {historyQuery.isLoading ? (
          <div className={s.loading}>SCANNING LIBRARY...</div>
        ) : historyQuery.data?.entries.length ? (
          <div className={s.bookList}>
            {historyQuery.data.entries.map((entry) => {
              const badge = historyBadge(entry);
              return (
                <div key={entry.document_id} className={s.bookCard}>
                  <div className={s.bookTop}>
                    <div className={s.bookMeta}>
                      <span className={s.bookTitle}>{preferredTitle(entry)}</span>
                      <div className={s.tagRow}>
                        <span className={s.tag}>{entry.author || "Unknown"}</span>
                        <span className={s.tag}>{sourceLabel(entry.source_type)}</span>
                        <span className={s.tag}>{statusLabel(entry.status)}</span>
                        <span className={s.tag}>{formatDate(entry.updated_at)}</span>
                      </div>
                      <span className={s.bookProgress}>{historyProgress(entry)}</span>
                    </div>
                    <StatusBadge tone={badge.tone} label={badge.label} />
                  </div>
                  <div className={s.bookActions}>
                    <button
                      className={s.btnAction}
                      onClick={() => void handleOpen(entry.document_id)}
                    >
                      {"> OPEN"}
                    </button>
                    <button
                      className={s.btnSmall}
                      disabled={!entry.merged_export_ready}
                      onClick={() => void handleDownload(entry.document_id)}
                    >
                      {entry.merged_export_ready ? "> DOWNLOAD" : "PENDING"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className={s.emptyState}>
            <span className={s.prompt}>$</span> NO RESULTS MATCHING CURRENT FILTERS
            <span className={s.cursor} />
          </div>
        )}
      </Surface>
    </div>
  );
}
