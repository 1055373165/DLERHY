import { useEffect, useDeferredValue, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { StatusBadge } from "../../components/StatusBadge";
import {
  type BlogEdition,
  blogPublishProgressText,
  blogPublishResultText,
  deleteDocument,
  downloadDocumentExport,
  getBlogTarget,
  listDocumentHistory,
  publishToBlog,
} from "../../lib/api";
import {
  formatDate,
  historyBadge,
  historyProgress,
  preferredTitle,
  sourceLabel,
} from "../../lib/workflow";
import s from "./LibraryPage.module.css";

type Feedback = {
  tone: "success" | "error";
  text: string;
  link?: { href: string; label: string };
  // A publish in progress: pictures uploaded of all.
  progress?: { done: number; total: number };
} | null;

const BLOG_OPTIONS: { edition: BlogEdition; label: string }[] = [
  { edition: "zh", label: "发布到博客 · 中文版" },
  { edition: "bilingual", label: "发布到博客 · 中英对照版" },
];

const DOWNLOAD_OPTIONS = [
  { key: "zh-web", label: "中文网页 · 原排版", exportType: "web_html", packaging: "single", enabled: true },
  { key: "zh-html", label: "中文版 · HTML", exportType: "merged_html", packaging: "single", enabled: true },
  { key: "zh-epub", label: "中文版 · EPUB", exportType: "merged_html", packaging: "epub", enabled: true },
  { key: "zh-md", label: "中文版 · Markdown", exportType: "merged_markdown", packaging: "single", enabled: true },
  { key: "zh-pdf", label: "中文版 · PDF", exportType: "rebuilt_pdf", packaging: "single", enabled: true },
  { key: "bi-html", label: "中英文对照版 · HTML", exportType: "bilingual_html", packaging: "single", enabled: true },
  { key: "bi-md", label: "中英文对照版 · Markdown", exportType: "merged_markdown", packaging: "bilingual", enabled: true },
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
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [mergedReady, setMergedReady] = useState<"" | "true" | "false">("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(12);
  const [jumpValue, setJumpValue] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
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
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        menuRef.current?.querySelector("button")?.focus();
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
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
    await navigate(`/books/${documentId}`);
  }

  type DownloadKey = typeof DOWNLOAD_OPTIONS[number]["key"];
  async function handleDownload(documentId: string, key: DownloadKey) {
    setOpenMenu(null);
    const opt = DOWNLOAD_OPTIONS.find((o) => o.key === key);
    if (!opt) return;
    setFeedback({ tone: "success", text: `正在准备 ${opt.label}...` });
    try {
      await downloadDocumentExport(documentId, opt.exportType, { packaging: opt.packaging });
      setFeedback({ tone: "success", text: `已开始下载 ${opt.label}，进度见浏览器的下载列表。` });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "下载失败" });
    }
  }

  const blogTarget = useQuery({ queryKey: ["blog-target"], queryFn: getBlogTarget, staleTime: 5 * 60 * 1000 });
  const [publishing, setPublishing] = useState(false);

  async function handlePublish(documentId: string, edition: BlogEdition, label: string) {
    setOpenMenu(null);
    setPublishing(true);
    const action = label.replace(" · ", "（") + "）";
    setFeedback({ tone: "success", text: `正在准备${action}……` });
    try {
      const result = await publishToBlog(documentId, edition, {
        onProgress: (job) =>
          setFeedback({
            tone: "success",
            text: `${action}：${blogPublishProgressText(job)}`,
            progress: job.stage === "images" && job.total > 0 ? { done: job.done, total: job.total } : undefined,
          }),
      });
      setFeedback({
        tone: "success",
        text: blogPublishResultText(result),
        link: result.edit_url ? { href: result.edit_url, label: "去博客后台查看" } : undefined,
      });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "发布到博客失败" });
    } finally {
      setPublishing(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteDocument(pendingDelete.id);
      setFeedback({ tone: "success", text: `已删除：${pendingDelete.title}` });
      setPendingDelete(null);
      await queryClient.invalidateQueries({ queryKey: ["document-history"] });
    } catch (err) {
      setFeedback({ tone: "error", text: err instanceof Error ? err.message : "删除失败" });
    } finally {
      setDeleting(false);
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
      <header className="page-header">
        <div>
          <h1>书库</h1>
          {historyQuery.data && <p>{hasFilters ? "找到" : "共"} {total} 本</p>}
        </div>
        <Link className="btn btn-primary" to="/">新建翻译</Link>
      </header>
      <section className={s.collection} aria-label="全部书">
        <div className={s.filterRow}>
          <div className={s.searchWrap}>
            <svg className={s.searchIcon} aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
              <circle cx="11" cy="11" r="6" />
              <path d="m16 16 4 4" />
            </svg>
            <input
              className={s.searchInput}
              aria-label="搜索书名、作者或文件名"
              type="search"
              placeholder="搜索书名、作者或文件名…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button
                type="button"
                className={s.searchClear}
                onClick={() => setQuery("")}
                aria-label="清空搜索"
              >
                ×
              </button>
            )}
          </div>
          <select aria-label="按书的状态筛选" className={s.filterSelect} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="active">已导入</option>
            <option value="partially_exported">部分可读</option>
            <option value="exported">可阅读</option>
            <option value="failed">失败</option>
          </select>
          <select aria-label="按转换状态筛选" className={s.filterSelect} value={runStatus} onChange={(e) => setRunStatus(e.target.value)}>
            <option value="">全部进度</option>
            <option value="queued">排队中</option>
            <option value="running">进行中</option>
            <option value="paused">已暂停</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
            <option value="succeeded">已完成</option>
          </select>
          <select
            aria-label="按是否可读筛选"
            className={s.filterSelect}
            value={mergedReady}
            onChange={(e) => setMergedReady(e.target.value as "" | "true" | "false")}
          >
            <option value="">全部阅读状态</option>
            <option value="true">可阅读</option>
            <option value="false">未完成</option>
          </select>
          {hasFilters && (
            <button type="button" className={s.resetBtn} onClick={resetFilters}>
              清除筛选
            </button>
          )}
        </div>

        {feedback && (
          <div className={s.feedback} data-tone={feedback.tone} role="status" aria-live="polite">
            {feedback.text}
            {feedback.progress && (
              <progress
                className={s.feedbackProgress}
                value={feedback.progress.done}
                max={feedback.progress.total}
                aria-label="图片上传进度"
              />
            )}
            {feedback.link && (
              <>
                {" "}
                <a href={feedback.link.href} target="_blank" rel="noreferrer">{feedback.link.label}</a>
              </>
            )}
          </div>
        )}

        {historyQuery.isError ? (
          <div className={s.feedback} data-tone="error" role="alert">
            书库加载失败。 <button className="btn btn-sm" onClick={() => void historyQuery.refetch()}>重新加载</button>
          </div>
        ) : historyQuery.isLoading ? (
          <div className={s.loading}>正在加载书目…</div>
        ) : historyQuery.data?.entries.length ? (
          <>
            <ul className={s.bookList}>
              {historyQuery.data.entries.map((entry) => {
                const badge = historyBadge(entry);
                return (
                  <li key={entry.document_id} className={s.bookRow}>
                    <div className={s.bookInfo}>
                      <span className={s.bookTitle}>{preferredTitle(entry)}</span>
                      <span className={s.bookMeta}>
                        <span className={s.source}>{sourceLabel(entry.source_type)}</span>
                        {entry.author && <span>{entry.author}</span>}
                        <span>{formatDate(entry.updated_at)}</span>
                      </span>
                    </div>
                    <div className={s.bookState}>
                      <StatusBadge tone={badge.tone} label={badge.label} />
                      {!entry.merged_export_ready && <span className={s.bookProgress}>{historyProgress(entry)}</span>}
                    </div>
                    <div className={s.bookActions}>
                      <button className="btn btn-sm" onClick={() => void handleOpen(entry.document_id)}>
                        打开
                      </button>
                      {entry.merged_export_ready ? (
                        <Link className="btn btn-sm btn-primary" to={`/books/${entry.document_id}/read`}>
                          阅读
                        </Link>
                      ) : null}
                      {/* Nothing to download yet means no button, rather than a dash that does nothing. */}
                      {entry.merged_export_ready ? (
                      <div className={s.dlWrap} ref={openMenu === entry.document_id ? menuRef : undefined}>
                        <button
                          className="btn btn-sm"
                          aria-expanded={openMenu === entry.document_id}
                          onClick={() => setOpenMenu(openMenu === entry.document_id ? null : entry.document_id)}
                        >
                          下载 ▾
                        </button>
                        {openMenu === entry.document_id && (
                          <div className={s.dlMenu}>
                            {DOWNLOAD_OPTIONS.filter(
                              (opt) => opt.exportType !== "web_html" || entry.source_type === "web"
                            ).map((opt) => (
                              <button
                                key={opt.key}
                                className={s.dlOption}
                                disabled={!opt.enabled}
                                onClick={() => void handleDownload(entry.document_id, opt.key)}
                              >
                                {opt.label}
                              </button>
                            ))}
                            {blogTarget.data?.configured &&
                              BLOG_OPTIONS.map((opt) => (
                                <button
                                  key={opt.edition}
                                  className={s.dlOption}
                                  disabled={publishing}
                                  onClick={() => void handlePublish(entry.document_id, opt.edition, opt.label)}
                                >
                                  {opt.label}
                                </button>
                              ))}
                          </div>
                        )}
                      </div>
                      ) : null}
                      <button
                        className={`btn btn-sm ${s.deleteBtn}`}
                        onClick={() => setPendingDelete({ id: entry.document_id, title: preferredTitle(entry) })}
                        aria-label={`删除 ${preferredTitle(entry)}`}
                        title="删除"
                      >
                        删除
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>

            <div className={s.paginationBar}>
              <div className={s.paginationInfo}>
                第 <strong>{rangeStart}</strong>–<strong>{rangeEnd}</strong> 本，共 <strong>{total}</strong> 本
              </div>
              <div className={s.paginationControls}>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page <= 1}
                  onClick={() => goTo(1)}
                  aria-label="第一页"
                >
                  «
                </button>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page <= 1}
                  onClick={() => goTo(page - 1)}
                  aria-label="上一页"
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
                  aria-label="下一页"
                >
                  ›
                </button>
                <button
                  type="button"
                  className={s.pageBtn}
                  disabled={page >= pageCount}
                  onClick={() => goTo(pageCount)}
                  aria-label="最后一页"
                >
                  »
                </button>
              </div>
              <form className={s.jumpForm} onSubmit={handleJump}>
                <label className={s.jumpLabel}>
                  跳到
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
                每页
                <select
                  aria-label="每页显示多少本"
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
            <h2 className={s.emptyTitle}>{hasFilters ? "没有找到匹配的书" : "书库暂无书目"}</h2>
            <span className={s.emptySubtitle}>
              {hasFilters
                ? "没有符合当前条件的书。换个关键词，或清除筛选。"
                : "书库还是空的。上传一本书或粘贴一个网页链接开始。"}
            </span>
            {hasFilters && (
              <button type="button" className="btn btn-sm" onClick={resetFilters}>
                清除筛选
              </button>
            )}
          </div>
        )}
      </section>

      {pendingDelete && (
        <div
          className={s.modalBackdrop}
          onClick={() => !deleting && setPendingDelete(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-modal-title"
        >
          <div className={s.modal} onClick={(e) => e.stopPropagation()}>
            <h3 id="delete-modal-title" className={s.modalTitle}>删除这本书?</h3>
            <p className={s.modalBody}>
              将永久删除 <strong>{pendingDelete.title}</strong> 及其所有章节、翻译、审阅结果与导出记录。
              此操作<strong>不可撤销</strong>。
            </p>
            <div className={s.modalActions}>
              <button
                type="button"
                className="btn btn-sm"
                disabled={deleting}
                onClick={() => setPendingDelete(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={`btn btn-sm ${s.dangerBtn}`}
                disabled={deleting}
                onClick={() => void confirmDelete()}
              >
                {deleting ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
