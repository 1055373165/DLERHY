import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import { bootstrapWebPage, getActiveProvider, listDocumentHistory, uploadDocument } from "../../lib/api";
import { getOverviews } from "../../lib/overview";
import { preferredTitle, sourceLabel } from "../../lib/workflow";
import s from "./HomePage.module.css";

/** Where a reader starts: paste a link or pick a file, and see the books already here. */
export function HomePage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [dragging, setDragging] = useState(false);
  const provider = useQuery({ queryKey: ["active-provider"], queryFn: getActiveProvider, retry: false });
  const recent = useQuery({ queryKey: ["document-history", "home"], queryFn: () => listDocumentHistory({ limit: 6, offset: 0 }) });
  const ids = (recent.data?.entries ?? []).map((entry) => entry.document_id);
  // Each card says where its book stands, in the server's words (GET /overviews), refreshed while any moves.
  const overviews = useQuery({
    queryKey: ["overviews", ids],
    queryFn: () => getOverviews(ids),
    enabled: ids.length > 0,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((item) => ["queued", "running", "draining"].includes(item.translation.state)) ? 10_000 : false,
  });
  const headlines = new Map((overviews.data ?? []).map((item) => [item.document_id, item]));
  const active = provider.data;
  // No provider, or one that cannot be called: importing would only lead to a run that stops at once.
  const providerMissing =
    provider.isSuccess && (active === null || (active?.provider_kind === "openai_compatible" && !active.api_key_preview && !active.shared));

  const importing = useMutation({
    mutationFn: async (source: { url: string } | { file: File }) =>
      "url" in source ? bootstrapWebPage(source.url) : uploadDocument(source.file),
    onSuccess: async (document) => {
      await queryClient.invalidateQueries({ queryKey: ["document-history"] });
      // justImported: the book page may start translating by itself (within the reader's cost cap).
      navigate(`/books/${document.document_id}`, { state: { justImported: true } });
    },
  });
  const busy = importing.isPending;
  const file = importing.isPending && importing.variables && "file" in importing.variables ? importing.variables.file.name : null;
  const blocked = busy || providerMissing;

  return (
    <div className={s.layout}>
      <section className={s.hero}>
        <div className={s.intro}>
          <h1 className={s.title}>把英文<br className={s.titleBreak} />读成中文<span aria-hidden="true">。</span></h1>
          <p className={s.subtitle}>导入英文书稿或网页，在书库阅读中文版。</p>
          <p className={s.rights}>
            请只翻译你有权使用的内容。译文的发布与传播仍受原作版权约束。
          </p>
        </div>

        <div className={s.importPanel}>
          <h2>新建翻译</h2>

          {providerMissing && (
            <div className={s.notice} role="alert">
              还没有可用的模型服务商，翻译没法开始。<Link to="/settings">去设置服务商</Link>
            </div>
          )}

          <label
            className={s.drop}
            data-dragging={dragging || undefined}
            data-disabled={blocked || undefined}
            onDragOver={(event) => {
              event.preventDefault();
              if (!blocked) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const dropped = event.dataTransfer.files?.[0];
              if (dropped && !blocked) importing.mutate({ file: dropped });
            }}
          >
            <input
              type="file"
              accept=".pdf,.epub"
              className={s.fileInput}
              aria-label="选择书稿文件"
              disabled={blocked}
              onChange={(event) => {
                const chosen = event.target.files?.[0];
                // Choosing the file is the decision: no second button to press.
                if (chosen) importing.mutate({ file: chosen });
              }}
            />
            <span className={s.uploadIcon} aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4" />
              </svg>
            </span>
            <span className={s.dropTitle}>{busy && file ? `正在导入 ${file}…` : "选择或拖入书稿"}</span>
            <span className={s.dropHint}>PDF、EPUB · 扫描版 PDF 需要 OCR 组件</span>
          </label>

          <form
            className={s.urlRow}
            onSubmit={(event) => {
              event.preventDefault();
              if (!blocked && url.trim()) importing.mutate({ url: url.trim() });
            }}
          >
            <label className={s.urlLabel} htmlFor="translation-url">或翻译网页</label>
            <div className={s.urlControls}>
              <input
                id="translation-url"
                type="url"
                inputMode="url"
                className={s.urlInput}
                placeholder="粘贴网页链接"
                aria-label="网页链接"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
              />
              <button type="submit" className="btn btn-primary" disabled={blocked || !url.trim()}>
                {busy && !file ? "正在抓取…" : "翻译这个网页"}
              </button>
            </div>
          </form>

          {importing.isError && (
            <div className={s.error} role="alert">
              {importing.error instanceof Error ? importing.error.message : "导入失败"}
            </div>
          )}
        </div>
      </section>

      <section className={s.recent}>
        <div className={s.recentHead}>
          <h2 className={s.recentTitle}>最近的书</h2>
          <Link className={s.libraryLink} to="/library">查看书库 <span aria-hidden="true">→</span></Link>
        </div>
        {recent.isError ? (
          <div className={s.error}>书单没能加载：{(recent.error as Error).message}</div>
        ) : (recent.data?.entries ?? []).length === 0 ? (
          <div className={s.empty}>{recent.isLoading ? "正在加载书目…" : "导入的书稿和网页会显示在这里。"}</div>
        ) : (
          <ul className={s.cards}>
            {(recent.data?.entries ?? []).map((entry) => (
              <li key={entry.document_id}>
                <Link className={s.card} to={`/books/${entry.document_id}`}>
                  <span className={s.format} aria-hidden="true">{entry.source_type?.startsWith("pdf") ? "PDF" : sourceLabel(entry.source_type)}</span>
                  <span className={s.cardText}>
                    <span className={s.cardTitle}>{preferredTitle(entry)}</span>
                    <span className={s.cardMeta} data-needs-you={(headlines.get(entry.document_id)?.needs_you.length ?? 0) > 0 || undefined}>
                      {headlines.get(entry.document_id)?.headline ?? (overviews.isError ? "状态没能加载" : "正在读取状态…")}
                    </span>
                  </span>
                  <span className={s.cardArrow} aria-hidden="true">↗</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
