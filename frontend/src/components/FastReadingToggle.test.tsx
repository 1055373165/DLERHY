import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../app/App";
import { json, makeOverview, notFound } from "../test/fixtures";

const DOCUMENT_ID = "doc-fast";
const run = { run_id: "run-new", document_id: DOCUMENT_ID, run_type: "translate_full", status: "running", priority: 100, status_detail_json: {}, created_at: "", updated_at: "" };

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/v1/runs") && init?.method === "POST") return json(run, 201);
    if (url.includes("/v1/runs/run-new/resume")) return json(run);
    if (url.includes(`/v1/documents/${DOCUMENT_ID}/overview`)) return json(makeOverview(DOCUMENT_ID));
    if (url.includes("/v1/health")) return json({ status: "ok" });
    if (url.includes("/v1/inbox")) return json({ count: 0, items: [] });
    return notFound();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function runRequestSent(fetchMock: ReturnType<typeof installFetchMock>) {
  const call = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/v1/runs") && init?.method === "POST");
  return JSON.parse(String(call?.[1]?.body)).status_detail_json.run_request;
}

async function openStartDialog(user: ReturnType<typeof userEvent.setup>) {
  const fetchMock = installFetchMock();
  render(
    <MemoryRouter initialEntries={[`/books/${DOCUMENT_ID}`]}>
      <App />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "开始翻译" }));
  const dialog = await screen.findByRole("dialog", { name: "开始翻译" });
  const toggle = within(dialog).getByRole("checkbox", { name: /快速阅读模式/ });
  return { fetchMock, toggle, dialog };
}

describe("Fast reading mode", () => {
  it("is on by default in the start dialog, says what it costs, and starts the run with it", async () => {
    const user = userEvent.setup();
    const { fetchMock, toggle, dialog } = await openStartDialog(user);
    expect(toggle).toBeChecked();
    expect(within(dialog).getByText(/出现次数少的术语也直接锁定/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "开始翻译" }));
    await waitFor(() => expect(runRequestSent(fetchMock)).toEqual({ fast_reading: "on" }));
  });

  it("can be turned off in the dialog, and stays off for the next book", async () => {
    const user = userEvent.setup();
    const { fetchMock, toggle, dialog } = await openStartDialog(user);
    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "开始翻译" }));
    expect(window.localStorage.getItem("book-agent.fast-reading")).toBe("off");
    await waitFor(() => expect(runRequestSent(fetchMock)).toEqual({ fast_reading: "off" }));
  });
});
