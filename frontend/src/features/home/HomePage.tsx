import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import { bootstrapWebPage, getActiveProvider, listDocumentHistory, uploadDocument } from "../../lib/api";
import { preferredTitle } from "../../lib/workflow";
import s from "./HomePage.module.css";

/** Where a reader starts: paste a link or pick a file, and see the books already here. */
export function HomePage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const provider = useQuery({ queryKey: ["active-provider"], queryFn: getActiveProvider, retry: false });
  const recent = useQuery({ queryKey: ["document-history", "home"], queryFn: () => listDocumentHistory({ limit: 8, offset: 0 }) });
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
  const blocked = busy || providerMissing;

  return (
    <div className={s.layout}>
      <section className={s.hero}>
        <h1 className={s.title}>把英文读成中文</h1>
        <p className={s.subtitle}>粘贴一个英文网页链接，或上传一本 PDF / EPUB。翻译好后直接在这里读。</p>

        {providerMissing && (
          <div className={s.notice} role="alert">
            还没有可用的模型服务商，翻译没法开始。<Link to="/settings">去设置服务商</Link>
          </div>
        )}

        <form
          className={s.urlRow}
          onSubmit={(event) => {
            event.preventDefault();
            if (url.trim()) importing.mutate({ url: url.trim() });
          }}
        >
          <input
            type="url"
            inputMode="url"
            className={s.urlInput}
            placeholder="https://example.com/article"
            aria-label="网页链接"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={blocked || !url.trim()}>
            {busy && "url" in (importing.variables ?? {}) ? "正在抓取…" : "翻译这个网页"}
          </button>
        </form>

        <div className={s.fileRow}>
          <label className={s.fileLabel}>
            <input
              type="file"
              accept=".pdf,.epub"
              className={s.fileInput}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <span>{file ? file.name : "或选择一个 .pdf / .epub 文件"}</span>
          </label>
          <button className="btn" disabled={blocked || !file} onClick={() => file && importing.mutate({ file })}>
            {busy && "file" in (importing.variables ?? {}) ? "正在上传…" : "上传并翻译"}
          </button>
        </div>

        {importing.isError && (
          <div className={s.error} role="alert">
            {importing.error instanceof Error ? importing.error.message : "导入失败"}
          </div>
        )}
        <p className={s.rights}>
          请只翻译你有权使用的内容（自有版权、已获授权，或法律允许的个人学习用途）。译文的发布与传播仍受原作版权约束。
        </p>
      </section>

      <section className={s.recent}>
        <div className={s.recentHead}>
          <h2 className={s.recentTitle}>最近的书</h2>
          <Link to="/library">全部书 →</Link>
        </div>
        {recent.isError ? (
          <div className={s.error}>书单没能加载：{(recent.error as Error).message}</div>
        ) : (recent.data?.entries ?? []).length === 0 ? (
          <div className={s.empty}>{recent.isLoading ? "加载中…" : "还没有书。上面粘贴一个链接试试。"}</div>
        ) : (
          <ul className={s.cards}>
            {(recent.data?.entries ?? []).map((entry) => (
              <li key={entry.document_id}>
                <Link className={s.card} to={`/books/${entry.document_id}`}>
                  <span className={s.cardTitle}>{preferredTitle(entry)}</span>
                  <span className={s.cardMeta}>{entry.merged_export_ready ? "可以阅读" : "还没有中文版"}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
