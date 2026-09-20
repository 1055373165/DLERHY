import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { json, notFound } from "../test/fixtures";
import { App } from "./App";

function installFetchMock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/health")) return json({ status: "ok" });
      if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
      if (url.includes("/v1/providers/active")) return json({ id: "p1", provider_kind: "echo", name: "echo", is_active: true });
      if (url.includes("/v1/documents/history")) {
        return json({ total_count: 0, record_count: 0, offset: 0, limit: 12, has_more: false, entries: [] });
      }
      return notFound();
    }),
  );
}

describe("App navigation", () => {
  it("has three places to go and reaches all books from the home page", async () => {
    installFetchMock();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "把英文读成中文" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "主导航" });
    expect([...nav.querySelectorAll("a")].map((link) => link.textContent)).toEqual(["首页", "需要你处理", "设置"]);

    await user.click(screen.getByRole("link", { name: "全部书 →" }));
    // The library's own title is its h1: one per page, which axe checks for.
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "全部书" })).toBeInTheDocument());
  });
});
