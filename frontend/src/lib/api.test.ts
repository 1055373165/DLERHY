import { afterEach, describe, expect, it, vi } from "vitest";

import { blogPublishProgressText, downloadDocumentExport, previewDocumentExport, publishToBlog } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("downloadDocumentExport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("enqueues an export run when no export exists, then lets the browser download it", async () => {
    let downloadAttempts = 0;
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/exports/download")) {
        downloadAttempts += 1;
        if (downloadAttempts === 1) {
          return jsonResponse({ detail: "No successful merged_markdown exports are available for download." }, 404);
        }
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/documents/doc-1/export")) {
        expect(JSON.parse(String(init?.body))).toEqual({ export_type: "merged_markdown" });
        return jsonResponse({ run_id: "run-1", status: "running" }, 202);
      }
      if (url.endsWith("/runs/run-1")) {
        return jsonResponse({ run_id: "run-1", status: "succeeded" });
      }
      throw new Error(`Unhandled fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.href);
    });

    await downloadDocumentExport("doc-1", "merged_markdown", { pollIntervalMs: 1, packaging: "bilingual" });

    // The page only checks; the file itself is the browser's download (a download manager can take it).
    expect(calls.map((call) => call.replace(/^(\w+) .*\/v1/, "$1 "))).toEqual([
      "GET /documents/doc-1/exports/download?export_type=merged_markdown&package=bilingual&check=true",
      "POST /documents/doc-1/export",
      "GET /runs/run-1",
      "GET /documents/doc-1/exports/download?export_type=merged_markdown&package=bilingual&check=true",
    ]);
    expect(clicked).toHaveLength(1);
    expect(clicked[0]).toMatch(/\/v1\/documents\/doc-1\/exports\/download\?export_type=merged_markdown&package=bilingual$/);
  });

  it("reports a failed export run instead of downloading", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/exports/download")) {
          return jsonResponse({ detail: "missing" }, 404);
        }
        if (url.endsWith("/export")) {
          return jsonResponse({ run_id: "run-2", status: "running" }, 202);
        }
        return jsonResponse({ run_id: "run-2", status: "failed", stop_reason: "export_gate" });
      })
    );

    await expect(downloadDocumentExport("doc-1", "merged_html", { pollIntervalMs: 1 })).rejects.toThrow("export_gate");
  });

  it("waits while the book is busy producing the format instead of failing", async () => {
    let downloads = 0;
    const exportPosts: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/exports/download")) {
          downloads += 1;
          return downloads < 3 ? jsonResponse({ detail: "none yet" }, 404) : new Response("<p>中文</p>", { status: 200 });
        }
        if (url.endsWith("/documents/doc-1/export") && init?.method === "POST") {
          exportPosts.push(downloads);
          return jsonResponse({ detail: "这本书还有一项任务在进行（翻译或导出），结束后再试。" }, 409);
        }
        throw new Error(`Unhandled fetch: ${url}`);
      }),
    );
    const page = await previewDocumentExport("doc-1", "web_html", { pollIntervalMs: 1 });
    expect(page).toEqual({ kind: "html", text: "<p>中文</p>" });
    expect(exportPosts).toEqual([1, 2]);
  });
});

describe("publishToBlog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("checks the Markdown export is current, then saves the article on the blog", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url.replace(/^.*\/v1/, "")}`);
        if (url.includes("/exports/download")) {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/blog-publish")) {
          expect(JSON.parse(String(init?.body))).toEqual({ edition: "bilingual" });
          return jsonResponse({ ...job, stage: "login", done: 0, total: 3 }, 202);
        }
        polls += 1;
        return jsonResponse(
          polls === 1
            ? { ...job, stage: "images", done: 2, total: 3 }
            : { ...job, status: "succeeded", stage: "done", done: 3, total: 3, result }
        );
      })
    );
    const job = { job_id: "j1", document_id: "doc-1", edition: "bilingual", status: "running", result: null, error: null };
    const result = {
      article_id: 7,
      action: "created",
      title: "书（中英对照）",
      edit_url: "http://blog/admin/articles/7/edit",
      images_uploaded: 3,
      images_reused: 0,
      images_skipped: [],
    };
    let polls = 0;
    const seen: string[] = [];

    const published = await publishToBlog("doc-1", "bilingual", {
      pollIntervalMs: 1,
      jobPollIntervalMs: 1,
      onProgress: (state) => seen.push(blogPublishProgressText(state)),
    });

    expect(published.article_id).toBe(7);
    expect(seen).toEqual(["正在登录博客……", "正在上传图片 2/3（66%）", "已完成"]);
    expect(calls).toEqual([
      "GET /documents/doc-1/exports/download?export_type=merged_markdown&package=bilingual&check=true",
      "POST /documents/doc-1/blog-publish",
      "GET /blog/publish-jobs/j1",
      "GET /blog/publish-jobs/j1",
    ]);
  });
});
