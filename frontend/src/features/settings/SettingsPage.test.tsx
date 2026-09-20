import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../../app/App";
import { autoStartCapUsd } from "../../lib/preferences";
import { json, notFound } from "../../test/fixtures";

describe("Settings", () => {
  it("keeps the auto-start cap (default $1, 0 means always ask) and the provider list in one place", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/providers")) return json([]);
        if (url.includes("/v1/health")) return json({ status: "ok" });
        if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
        return notFound();
      }),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/providers"]}>
        <App />
      </MemoryRouter>,
    );
    const cap = await screen.findByRole("spinbutton", { name: "自动开始上限" });
    expect(cap).toHaveValue(1);
    await user.clear(cap);
    await user.type(cap, "0");
    expect(autoStartCapUsd()).toBe(0);
    expect(screen.getByRole("heading", { name: "模型服务商" })).toBeInTheDocument();
  });
});
