import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import { json, makeOverview, notFound } from "../../test/fixtures";

const LINK = "https://www.anthropic.com/institute/recursive-self-improvement";

function installFetchMock(provider: unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/v1/providers/active")) return json(provider);
    if (url.includes("/v1/documents/bootstrap-url")) return json({ document_id: "doc-new", title: "When AI builds itself", source_type: "web" }, 201);
    if (url.includes("/v1/documents/doc-new/overview")) return json(makeOverview("doc-new"));
    if (url.includes("/v1/documents/history")) {
      return json({
        total_count: 1, offset: 0, limit: 8, has_more: false,
        entries: [{ document_id: "doc-old", title: "Deep Work", title_src: "Deep Work", source_type: "epub", status: "exported", merged_export_ready: true, latest_run_id: "run-old", latest_run_status: "running" }],
      });
    }
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
    return notFound();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderHome() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

const echo = { id: "p1", name: "echo", provider_kind: "echo", is_active: true, api_key_preview: null, shared: true };

describe("Home page", () => {
  it("turns a pasted link into a book and opens its page", async () => {
    const fetchMock = installFetchMock(echo);
    const user = userEvent.setup();
    renderHome();
    const input = await screen.findByRole("textbox", { name: "网页链接" });
    await user.click(input);
    await user.paste(LINK);
    await user.click(screen.getByRole("button", { name: "翻译这个网页" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/books/doc-new"));
    const call = fetchMock.mock.calls.find(([request]) => String(request).includes("/v1/documents/bootstrap-url"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ url: LINK });
  });

  it("lists recent books, readable ones marked", async () => {
    installFetchMock(echo);
    renderHome();
    const card = await screen.findByRole("link", { name: /Deep Work/ });
    expect(card).toHaveAttribute("href", "/books/doc-old");
    expect(card).toHaveTextContent("可以阅读");
  });

  it("stays home even while a book is translating (no automatic jump into it)", async () => {
    installFetchMock(echo);
    window.localStorage.setItem("book-agent.current-document-id", "doc-old");
    renderHome();
    await screen.findByRole("link", { name: /Deep Work/ });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/);
  });

  it("says a provider is needed before anything is imported", async () => {
    installFetchMock(null);
    const user = userEvent.setup();
    renderHome();
    expect(await screen.findByRole("alert")).toHaveTextContent("还没有可用的模型服务商");
    expect(screen.getByRole("link", { name: "去设置服务商" })).toHaveAttribute("href", "/settings");
    await user.type(screen.getByRole("textbox", { name: "网页链接" }), "https://example.com/a");
    expect(screen.getByRole("button", { name: "翻译这个网页" })).toBeDisabled();
  });

  it("treats a provider without an API key as missing", async () => {
    installFetchMock({ ...echo, provider_kind: "openai_compatible", shared: false, api_key_preview: null });
    renderHome();
    expect(await screen.findByRole("alert")).toHaveTextContent("还没有可用的模型服务商");
  });
});
