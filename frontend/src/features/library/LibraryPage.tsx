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
import styles from "./LibraryPage.module.css";

export function LibraryPage() {
  const navigate = useNavigate();
  const { selectDocument } = useWorkspace();
  const [message, setMessage] = useState<string | null>(null);
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
      setMessage(`已开始下载 ${filename}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "下载失败。");
    }
  }

  return (
    <div className={styles.layout}>
      <Surface
        eyebrow="Library"
        title="书库"
        description="只保留检索、回看和重新打开。历史书籍不再挤压主工作流。"
        aside={
          historyQuery.data ? `共 ${historyQuery.data.total_count} 本，本页显示 ${historyQuery.data.record_count} 本` : null
        }
      >
        <div className={styles.filters}>
          <label className={styles.searchField}>
            <span className={styles.fieldLabel}>搜索</span>
            <input
              className={styles.textInput}
              type="search"
              placeholder="搜索标题、作者、路径或 document id"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className={styles.filterGrid}>
            <label className={styles.filterField}>
              <span className={styles.fieldLabel}>文档状态</span>
              <select className={styles.selectInput} value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="">全部状态</option>
                <option value="active">已入库</option>
                <option value="partially_exported">部分可下载</option>
                <option value="exported">可下载</option>
                <option value="failed">失败</option>
              </select>
            </label>
            <label className={styles.filterField}>
              <span className={styles.fieldLabel}>最近运行</span>
              <select className={styles.selectInput} value={runStatus} onChange={(event) => setRunStatus(event.target.value)}>
                <option value="">全部运行状态</option>
                <option value="queued">已排队</option>
                <option value="running">进行中</option>
                <option value="paused">已暂停</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
                <option value="succeeded">已完成</option>
              </select>
            </label>
            <label className={styles.filterField}>
              <span className={styles.fieldLabel}>中文阅读稿</span>
              <select className={styles.selectInput} value={mergedReady} onChange={(event) => setMergedReady(event.target.value as "" | "true" | "false")}>
                <option value="">全部</option>
                <option value="true">已生成</option>
                <option value="false">未生成</option>
              </select>
            </label>
          </div>
        </div>

        {message ? <div className={styles.message}>{message}</div> : null}

        {historyQuery.isLoading ? (
          <div className={styles.emptyState}>正在加载书库…</div>
        ) : historyQuery.data?.entries.length ? (
          <div className={styles.libraryList}>
            {historyQuery.data.entries.map((entry) => {
              const badge = historyBadge(entry);
              return (
                <article key={entry.document_id} className={styles.libraryRow}>
                  <div className={styles.libraryTop}>
                    <div className={styles.libraryMeta}>
                      <h3 className={styles.libraryTitle}>{preferredTitle(entry)}</h3>
                      <p className={styles.libraryFacts}>
                        <span>{entry.author || "作者待识别"}</span>
                        <span>{sourceLabel(entry.source_type)}</span>
                        <span>文档状态 {statusLabel(entry.status)}</span>
                        <span>更新于 {formatDate(entry.updated_at)}</span>
                      </p>
                      <p className={styles.libraryProgress}>{historyProgress(entry)}</p>
                    </div>
                    <StatusBadge tone={badge.tone} label={badge.label} />
                  </div>
                  <div className={styles.libraryBottom}>
                    <div className={styles.libraryActions}>
                      <button className={styles.button} type="button" onClick={() => void handleOpen(entry.document_id)}>
                        打开这本书
                      </button>
                      <button
                        className={styles.ghostButton}
                        type="button"
                        disabled={!entry.merged_export_ready}
                        onClick={() => void handleDownload(entry.document_id)}
                      >
                        {entry.merged_export_ready ? "下载中文阅读包" : "等待交付"}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className={styles.emptyState}>当前筛选条件下没有命中的书籍。</div>
        )}
      </Surface>
    </div>
  );
}
