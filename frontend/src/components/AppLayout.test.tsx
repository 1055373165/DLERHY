import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";
import { json, notFound } from "../test/fixtures";

const DOCUMENT_ID = "doc-wait";

function installFetchMock(count: number) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/v1/inbox")) {
      const items = Array.from({ length: count }, (_, index) => ({
        kind: "approval", id: `ap-${index}`, title: "锁定术语：harness → 驾驭框架", detail: null, action: { kind: "approve" },
        document_id: DOCUMENT_ID, document_title: "Harness",
      }));
      return json({ count, items });
    }
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.includes("/v1/documents/history")) return json({ total_count: 0, offset: 0, limit: 12, has_more: false, entries: [] });
    return notFound();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path: string) {
  window.localStorage.setItem("book-agent.current-document-id", DOCUMENT_ID);
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("What waits on you, in the shell", () => {
  it("shows the count on the nav, in the tab title and as a notice that links to the inbox", async () => {
    installFetchMock(2);
    renderAt("/library");
    expect(await screen.findByLabelText("2 件事等你处理")).toBeInTheDocument();
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("有 2 件事等你处理");
    expect(notice).toHaveAttribute("href", "/inbox");
    await waitFor(() => expect(document.title).toBe("(2) 等你处理 · Book Agent"));
  });

  it("does not load the chapter workbench on pages that do not show it", async () => {
    const fetchMock = installFetchMock(0);
    renderAt("/library");
    await screen.findByText("服务在线");
    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(calls.filter((url) => url.includes("/worklist"))).toEqual([]);
  });

  it("stays quiet when nothing waits", async () => {
    installFetchMock(0);
    renderAt("/library");
    await screen.findByText("服务在线");
    expect(screen.queryByLabelText(/件事等你处理/)).not.toBeInTheDocument();
    expect(document.title).toBe("Book Agent");
  });
});
