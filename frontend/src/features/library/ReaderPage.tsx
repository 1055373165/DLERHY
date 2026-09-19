import { useQuery } from "@tanstack/react-query";
import { marked } from "marked";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  downloadDocumentExport,
  getDocument,
  previewDocumentExport,
  type ReadableExportType,
} from "../../lib/api";
import { preferredTitle } from "../../lib/workflow";
import s from "./ReaderPage.module.css";

const FORMATS = [
  { key: "zh", label: "中文版", exportType: "merged_html" },
  { key: "bi", label: "中英对照", exportType: "bilingual_html" },
  { key: "md", label: "Markdown", exportType: "merged_markdown" },
  { key: "pdf", label: "PDF", exportType: "rebuilt_pdf" },
] as const satisfies readonly { key: string; label: string; exportType: ReadableExportType }[];

type FormatKey = (typeof FORMATS)[number]["key"];

const MARKDOWN_PAGE_STYLE = `
  body { max-width: 760px; margin: 0 auto; padding: 32px 24px 64px; font: 17px/1.8 "Noto Serif SC", "Songti SC", serif; color: #1f2328; background: #fffdf8; }
  h1, h2, h3, h4 { font-family: "Noto Sans SC", "PingFang SC", sans-serif; line-height: 1.35; margin: 1.6em 0 0.6em; }
  img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
  pre { background: #f6f3ec; padding: 12px 16px; overflow-x: auto; border-radius: 6px; font-size: 14px; line-height: 1.5; }
  code { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  table { border-collapse: collapse; margin: 1em 0; }
  th, td { border: 1px solid #d8d2c4; padding: 6px 10px; }
  blockquote { margin: 1em 0; padding: 0 1em; color: #57606a; border-left: 3px solid #d8d2c4; }
  details { margin: 0.5em 0; color: #57606a; }
`;

function markdownPage(markdown: string): string {
  const body = marked.parse(markdown, { async: false, gfm: true }) as string;
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>${MARKDOWN_PAGE_STYLE}</style></head><body>${body}</body></html>`;
}

/** Read a book's exports in place: to check a translation without finding the downloaded file. */
export function ReaderPage() {
  const { documentId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const format: FormatKey = FORMATS.some((item) => item.key === params.get("format"))
    ? (params.get("format") as FormatKey)
    : "zh";
  const current = FORMATS.find((item) => item.key === format) ?? FORMATS[0];
  const [showSource, setShowSource] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const document = useQuery({ queryKey: ["document", documentId], queryFn: () => getDocument(documentId), retry: false });
  const preview = useQuery({
    queryKey: ["export-preview", documentId, current.exportType],
    queryFn: () => previewDocumentExport(documentId, current.exportType),
    retry: false,
    staleTime: 5 * 60_000,
  });

  // Served from a blob URL rather than srcdoc: a srcdoc page resolves "#chapter" links against
  // this app's URL and navigates away instead of scrolling, and a whole book is too big for an attribute.
  const frameUrl = useMemo(() => {
    const data = preview.data;
    if (!data) return null;
    if (data.kind === "pdf") return URL.createObjectURL(data.blob);
    const page = data.kind === "markdown" ? markdownPage(data.text) : data.text;
    return URL.createObjectURL(new Blob([page], { type: "text/html;charset=utf-8" }));
  }, [preview.data]);
  useEffect(() => () => {
    if (frameUrl) URL.revokeObjectURL(frameUrl);
  }, [frameUrl]);

  async function handleDownload() {
    setDownloadError(null);
    try {
      await downloadDocumentExport(documentId, current.exportType);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "下载失败");
    }
  }

  const title = document.data ? preferredTitle(document.data) : "";

  return (
    <div className={s.layout}>
      <div className={s.toolbar}>
        <Link to="/library" className={s.back}>
          ← 书库
        </Link>
        <span className={s.title} title={title}>
          {title}
        </span>
        <div className={s.tabs} role="tablist" aria-label="阅读格式">
          {FORMATS.map((item) => (
            <button
              key={item.key}
              role="tab"
              aria-selected={item.key === format}
              className={s.tab}
              data-active={item.key === format}
              onClick={() => {
                setShowSource(false);
                setParams({ format: item.key }, { replace: true });
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        {format === "md" && preview.data?.kind === "markdown" && (
          <button className="btn btn-sm" onClick={() => setShowSource((value) => !value)}>
            {showSource ? "看排版" : "看源码"}
          </button>
        )}
        <button className="btn btn-sm" onClick={() => void handleDownload()}>
          下载
        </button>
      </div>
      {downloadError && <div className={s.error}>{downloadError}</div>}

      <div className={s.viewer}>
        {preview.isPending ? (
          <div className={s.state}>正在准备{current.label}…第一次阅读某个格式时会先生成导出，可能需要一两分钟。</div>
        ) : preview.isError ? (
          <div className={s.state} data-tone="error">
            {current.label}暂时无法阅读：{preview.error instanceof Error ? preview.error.message : "加载失败"}
          </div>
        ) : preview.data.kind === "markdown" && showSource ? (
          <pre className={s.source}>{preview.data.text}</pre>
        ) : !frameUrl ? null : preview.data.kind === "pdf" ? (
          <iframe title={`${title} · PDF`} src={frameUrl} className={s.frame} />
        ) : (
          // No allow-scripts: the book's content is shown, never run. allow-same-origin only lets its
          // table of contents jump within the page (an opaque origin cannot navigate to its own blob
          // URL); without scripts nothing in the page can use that access.
          <iframe
            title={`${title} · ${current.label}`}
            sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
            src={frameUrl}
            className={s.frame}
          />
        )}
      </div>
    </div>
  );
}
