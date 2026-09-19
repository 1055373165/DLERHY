import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadDocumentExport } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("downloadDocumentExport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("enqueues an export run when no export exists, then downloads it", async () => {
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
        return new Response("# 译文", {
          status: 200,
          headers: { "Content-Disposition": 'attachment; filename="book.md"' },
        });
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
    Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: vi.fn(() => "blob:1") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: vi.fn() });

    const filename = await downloadDocumentExport("doc-1", "merged_markdown", { pollIntervalMs: 1 });

    expect(filename).toBe("book.md");
    expect(calls.map((call) => call.replace(/^(\w+) .*\/v1/, "$1 "))).toEqual([
      "GET /documents/doc-1/exports/download?export_type=merged_markdown&package=single",
      "POST /documents/doc-1/export",
      "GET /runs/run-1",
      "GET /documents/doc-1/exports/download?export_type=merged_markdown&package=single",
    ]);
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
});
