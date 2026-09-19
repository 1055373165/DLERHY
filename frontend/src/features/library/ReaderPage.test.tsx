import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { queryClient } from "../../app/queryClient";
import { ReaderPage } from "./ReaderPage";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

function installFetchMock(pdfRenderer: boolean) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/v1/meta")) {
      return json({ app_name: "book-agent", version: "t", environment: "test", api_prefix: "/v1", docs_path: "", capabilities: { pdf_renderer: pdfRenderer } });
    }
    if (url.includes("/exports/download")) {
      return new Response("<html><body><p>中文正文</p></body></html>", { status: 200, headers: { "Content-Type": "text/html" } });
    }
    if (url.includes("/v1/documents/doc-1")) {
      return json({ document_id: "doc-1", title: "Momentum", title_src: "Momentum", source_type: "epub", status: "exported", chapters: [] });
    }
    throw new Error(`Unhandled fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function renderReader() {
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/library/doc-1/read"]}>
        <Routes>
          <Route path="/library/:documentId/read" element={<ReaderPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Reader page", () => {
  it("does not offer PDF where this deployment cannot print it", async () => {
    installFetchMock(false);
    renderReader();
    expect(await screen.findByRole("tab", { name: "中文版" })).toBeInTheDocument();
    // Give the capabilities query a chance to land before asserting absence.
    await screen.findByTitle(/Momentum · 中文版/);
    expect(screen.queryByRole("tab", { name: "PDF" })).not.toBeInTheDocument();
  });

  it("offers PDF once the renderer is installed", async () => {
    installFetchMock(true);
    renderReader();
    expect(await screen.findByRole("tab", { name: "PDF" })).toBeInTheDocument();
  });
});
