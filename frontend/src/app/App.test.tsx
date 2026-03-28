import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "./App";

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/v1/health")) {
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/v1/documents/history")) {
      return new Response(
        JSON.stringify({
          total_count: 0,
          record_count: 0,
          offset: 0,
          limit: 12,
          has_more: false,
          entries: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
    throw new Error(`Unhandled fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
}

describe("App navigation", () => {
  it("renders the left navigation and switches to the library tab", async () => {
    installFetchMock();
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByText("Book Agent")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /工作台/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /运行/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /交付/i })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: /书库/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "书库" })).toBeInTheDocument();
    });
  });
});
