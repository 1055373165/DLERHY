import { useEffect, useDeferredValue, useMemo, useRef, useState } from "react";
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

const DOWNLOAD_OPTIONS = [
  { label: "中文版 · HTML", exportType: "merged_html", enabled: true },
  { label: "中文版 · Markdown", exportType: "merged_markdown", enabled: true },
  { label: "中英文对照版 · HTML", exportType: "bilingual_html", enabled: true },
  { label: "中英文对照版 · Markdown", exportType: "bilingual_markdown", enabled: true },
] as const;

const PAGE_SIZE_OPTIONS = [12, 24, 50, 100] as const;
type PageSize = typeof PAGE_SIZE_OPTIONS[number];

function buildPageWindow(current: number, total: number): (number | "…")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const items: (number | "…")[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  if (left > 2) items.push("…");
  for (let i = left; i <= right; i++) items.push(i);
  if (right < total - 1) items.push("…");
  items.push(total);
  return items;
}

export function LibraryPage() {
  const navigate = useNavigate();
  const { selectDocument } = useWorkspace();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [mergedReady, setMergedReady] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(12);
  const [jumpValue, setJumpValue] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const deferredQuery = useDeferredValue(query.trim());

  useEffect(() => {
    setPage(1);
  }, [deferredQuery, status, runStatus, mergedReady, pageSize]);

  useEffect(() => {
    if (!openMenu) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [openMenu]);

  const offset = (page - 1) * pageSize;
  const historyQuery = useQuery({
    queryKey: ["document-history", "library", deferredQuery, status, runStatus, mergedReady, pageSize, offset],
    queryFn: () =>
      listDocumentHistory({
        query: deferredQuery || undefined,
        status: status || undefined,
        latest_run_status: runStatus || undefined,
        merged_export_ready: mergedReady,
        limit: pageSize,
        offset,
      }),
    placeholderData: (prev) => prev,
  });

  const total = historyQuery.data?.total_count ?? 0;
  const shown = historyQuery.data?.record_count ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const pageWindow = useMemo(() => buildPageWindow(page, pageCount), [page, pageCount]);
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + shown;

  async function handleOpen(documentId: string) {
    selectDocument(documentId);
    await navigate("/");
  }

  type ExportKey = typeof DOWNLOAD_OPTIONS[number]["exportType"];
  async function handleDownload(documentId: string, exportType: ExportKey) {
    setOpenMenu(null);
    const opt = DOWNLOAD_OPTIONS.find((o) => o.exportType === exportType);
    setFeedback({ tone: "success", text: `正在生成 ${opt?.label ?? exportType}...` });
    try {
      const filename = await downloadDocumentExport(documentId, exportType);
      setFeedback({ tone: "success", text: `Downloaded: ${filename}` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "Download failed" });
    }
  }

  function goTo(n: number) {
    const clamped = Math.max(1, Math.min(pageCount, n));
    if (clamped !== page) setPage(clamped);
  }

  function handleJump(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const n = parseInt(jumpValue, 10);
    if (!Number.isNaN(n)) goTo(n);
    setJumpValue("");
  }

  function resetFilters() {
    setQuery("");
    setStatus("");
    setRunStatus("");
    setMergedReady("");
  }

  const hasFilters = Boolean(query || status || runStatus || mergedReady);

  return (
    <div className={s.layout}>
      <Surface
        eyebrow="LIB"
        title="书库"
        aside={
          historyQuery.data
            ? `${total} total / ${shown} shown${total ? ` · ${rangeStart}-${rangeEnd}` : ""}`
            : null
        }
      >
        <div className={s.filterRow}>
          <div className={s.searchWrap}>
            <input
              className={s.searchInput}
              type="search"
              placeholder="Search title, author, path, or ID..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button
                type="button"
                className={s.searchClear}
                onClick={() => setQuery("")}
                aria-label="Clear search"
              >
                ×
              </button>
            )}
          </div>
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
          <select
            className={s.filterSelect}
            value={mergedReady}
            onChange={(e) => setMergedReady(e.target.value as "" | "true" | "false")}
          >
            <option value="">Delivery</option>
            <option value="true">Ready</option>
            <option value="false">Not Ready</option>
          </select>
          {hasFilters && (
            <button type="button" className={s.resetBtn} onClick={resetFilters}>
              Reset
            </button>
          )}
        </div>

        {feedback && (
          <div className={s.feedback} data-tone={feedback.tone}>{feedback.text}</div>
        )}

        {historyQuery.isLoading ? (
          <div className={s.loading}>Loading...</div>
        ) : historyQuery.data?.entries.length ? (
          <>
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
                      <div className={s.dlWrap} ref={openMenu === entry.document_id ? menuRef : undefined}>
                        <button
                          className="btn btn-sm"
                          disabled={!entry.merged_export_ready}
                          onClick={() => setOpenMenu(openMenu === entry.document_id ? null : entry.document_id)}
                        >
                          {entry.merged_export_ready ? "Download ▾" : "—"}
                        </button>
                        {openMenu === entry.document_id && (
                          <div className={s.dlMenu}>
                            {DOWNLOAD_OPTIONS.map((opt) => (
                              <button
                                key={opt.exportType}
                                className={s.dlOption}
                                disabled={!opt.enabled}
                                onClick={() => void handleDownload(entry.document_id, opt.exportType)}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className={s.paginationBar}>
              <div className={s.paginationInfo}>
                Showing <strong>{rangeStart}</strong>–<strong>{rangeEnd}</strong> of{" "}
                <strong>{total}</strong>
              </div>
              <div className={s.paginationControls}>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page <= 1}
                  onClick={() => goTo(1)}
                  aria-label="First page"
                >
                  «
                </button>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page <= 1}
                  onClick={() => goTo(page - 1)}
                  aria-label="Previous page"
                >
                  ‹
                </button>
                {pageWindow.map((item, idx) =>
                  item === "…" ? (
                    <span key={`ellipsis-${idx}`} className={s.pageEllipsis}>…</span>
                  ) : (
                    <button
                      key={item}
                      type="button"
                      className={s.pageBtn}
                      data-active={item === page}
                      onClick={() => goTo(item)}
                      aria-current={item === page ? "page" : undefined}
                    >
                      {item}
                    </button>
                  )
                )}
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page >= pageCount}
                  onClick={() => goTo(page + 1)}
                  aria-label="Next page"
                >
                  ›
                </button>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page >= pageCount}
                  onClick={() => goTo(pageCount)}
                  aria-label="Last page"
                >
                  »
                </button>
              </div>
              <form className={s.jumpForm} onSubmit={handleJump}>
                <label className={s.jumpLabel}>
                  Jump to
                  <input
                    className={s.jumpInput}
                    type="number"
                    min={1}
                    max={pageCount}
                    value={jumpValue}
                    placeholder={String(page)}
                    onChange={(e) => setJumpValue(e.target.value)}
                  />
                </label>
                <span className={s.jumpTotal}>/ {pageCount}</span>
              </form>
              <label className={s.pageSizeLabel}>
                Per page
                <select
                  className={s.filterSelect}
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value) as PageSize)}
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </label>
            </div>
          </>
        ) : (
          <div className={s.emptyHero}>
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={s.illustration}>
              <rect x="8" y="4" width="16" height="56" rx="2" />
              <rect x="24" y="8" width="16" height="52" rx="2" />
              <rect x="40" y="4" width="16" height="56" rx="2" />
              <line x1="13" y1="12" x2="19" y2="12" />
              <line x1="13" y1="16" x2="19" y2="16" />
              <line x1="45" y1="12" x2="51" y2="12" />
              <line x1="45" y1="16" x2="51" y2="16" />
            </svg>
            <span className={s.emptyTitle}>No results</span>
            <span className={s.emptySubtitle}>
              {hasFilters
                ? "No documents matching current filters. Try adjusting your search or reset filters."
                : "No documents yet. Upload a book to get started."}
            </span>
            {hasFilters && (
              <button type="button" className="btn btn-sm" onClick={resetFilters}>
                Reset filters
              </button>
            )}
          </div>
        )}
      </Surface>
    </div>
  );
}
