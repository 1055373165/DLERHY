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
      setFeedback({ tone: "success", text: `Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Download failed" });
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
        {/* ── Filters (single row) ── */}
        <div className={s.filterRow}>
          <input
            className={s.searchInput}
            type="search"
            placeholder="Search title, author, path, or ID..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className={s.filterSelect} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Status</option>
            <option value="active">Active</option>
            <option value="partially_exported">Partial</option>
            <option value="exported">Exported</option>
            <option value="failed">Failed</option>
          </select>
          <select className={s.filterSelect} value={runStatus} onChange={(e) => setRunStatus(e.target.value)}>
            <option value="">Run</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="paused">Paused</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="succeeded">Succeeded</option>
          </select>
          <select className={s.filterSelect} value={mergedReady} onChange={(e) => setMergedReady(e.target.value as "" | "true" | "false")}>
            <option value="">Delivery</option>
            <option value="true">Ready</option>
            <option value="false">Not Ready</option>
          </select>
        </div>

        {feedback && (
          <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
        )}

        {/* ── Results ── */}
        {historyQuery.isLoading ? (
          <div className={s.loading}>Loading...</div>
        ) : historyQuery.data?.entries.length ? (
          <div className={s.bookList}>
            {historyQuery.data.entries.map((entry) => {
              const badge = historyBadge(entry);
              return (
                <div key={entry.document_id} className={s.bookRow}>
                  <div className={s.bookInfo}>
                    <span className={s.bookTitle}>{preferredTitle(entry)}</span>
                    <span className={s.bookMeta}>
                      <span className={s.tag}>{entry.author || "—"}</span>
                      <span className={s.dot}>&middot;</span>
                      <span className={s.tag}>{sourceLabel(entry.source_type)}</span>
                      <span className={s.dot}>&middot;</span>
                      <span className={s.tag}>{statusLabel(entry.status)}</span>
                      <span className={s.dot}>&middot;</span>
                      <span className={s.tag}>{formatDate(entry.updated_at)}</span>
                    </span>
                  </div>
                  <span className={s.bookProgress}>{historyProgress(entry)}</span>
                  <StatusBadge tone={badge.tone} label={badge.label} />
                  <div className={s.bookActions}>
                    <button className="btn btn-sm" onClick={() => void handleOpen(entry.document_id)}>
                      Open
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={!entry.merged_export_ready}
                      onClick={() => void handleDownload(entry.document_id)}
                    >
                      {entry.merged_export_ready ? "Download" : "—"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className={s.emptyState}>No results matching current filters.</div>
        )}
      </Surface>
    </div>
  );
}
