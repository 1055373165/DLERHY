import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../app/App";
import { json, makeOverview, notFound } from "../test/fixtures";
import { announce, pickNotifications } from "./notifications";
import type { Inbox } from "./overview";

class FakeNotification {
  static permission: NotificationPermission = "default";
  static requestPermission = vi.fn(async () => {
    FakeNotification.permission = "granted";
    return "granted" as NotificationPermission;
  });
  static shown: FakeNotification[] = [];
  onclick: (() => void) | null = null;
  constructor(public title: string, public options: NotificationOptions) {
    FakeNotification.shown.push(this);
  }
  close() {}
}

beforeEach(() => {
  FakeNotification.permission = "default";
  FakeNotification.shown = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal("Notification", FakeNotification);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const readable: Inbox = {
  count: 0,
  items: [],
  updates: [{ kind: "readable", run_id: "r1", document_id: "b1", document_title: "Harness", text: "中文版已就绪，可以阅读了。", at: "" }],
};
const failed: Inbox = {
  count: 0,
  items: [],
  updates: [{ kind: "failed", run_id: "r2", document_id: "b2", document_title: "Deep Work", text: "有一个必需步骤没有完成。", at: "" }],
};
const needsYou: Inbox = {
  count: 2,
  items: [
    { kind: "approval", id: "ap-1", title: "锁定术语：harness → 驾驭框架", detail: null, action: { kind: "approve" }, document_id: "b1", document_title: "Harness" },
    { kind: "paused", id: "r3", title: "翻译暂停了，需要你处理", detail: "还没有可用的模型服务商。", action: { kind: "link", target: "/settings" }, document_id: "b3", document_title: "Momentum" },
  ],
};

function turnOn() {
  FakeNotification.permission = "granted";
  window.localStorage.setItem("book-agent.notify", "on");
}

describe("What gets announced", () => {
  it("a translation that became readable, linking to reading", () => {
    const [planned] = pickNotifications(readable, new Set());
    expect(planned).toMatchObject({ title: "可以阅读了：Harness", path: "/books/b1/read" });
  });

  it("a failed translation, with its reason", () => {
    const [planned] = pickNotifications(failed, new Set());
    expect(planned).toMatchObject({ title: "翻译失败：Deep Work", body: "有一个必需步骤没有完成。", path: "/books/b2" });
  });

  it("each thing that needs you: approvals and provider problems", () => {
    expect(pickNotifications(needsYou, new Set()).map((item) => [item.title, item.body])).toEqual([
      ["需要你处理：Harness", "锁定术语：harness → 驾驭框架"],
      ["需要你处理：Momentum", "翻译暂停了，需要你处理"],
    ]);
  });

  it("only once, and only when the reader turned them on", () => {
    const seen = new Set<string>();
    announce(readable, seen, () => {});
    expect(FakeNotification.shown).toHaveLength(0); // not turned on: nothing shown, but remembered
    turnOn();
    announce({ ...needsYou, updates: readable.updates }, seen, () => {});
    expect(FakeNotification.shown.map((item) => item.title)).toEqual(["需要你处理：Harness", "需要你处理：Momentum"]);
    announce(needsYou, seen, () => {});
    expect(FakeNotification.shown).toHaveLength(2);
  });

  it("clicking one opens the book", () => {
    turnOn();
    vi.spyOn(window, "focus").mockImplementation(() => {});
    const open = vi.fn();
    announce(failed, new Set(), open);
    FakeNotification.shown[0].onclick?.();
    expect(open).toHaveBeenCalledWith("/books/b2");
  });
});

describe("Asking for permission", () => {
  it("never on page load, only when the reader clicks 完成时通知我", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/overview")) {
          return json(makeOverview("b1", { translation: { state: "running", run_id: "r1", stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 1, total: 7 } }, primary_action: { kind: "stop", label: "停止", run_id: "r1" } }));
        }
        if (url.includes("/v1/inbox")) return json({ count: 0, items: [], updates: [] });
        if (url.includes("/v1/health")) return json({ status: "ok" });
        return notFound();
      }),
    );
    render(
      <MemoryRouter initialEntries={["/books/b1/progress"]}>
        <App />
      </MemoryRouter>,
    );
    const button = await screen.findByRole("button", { name: "完成时通知我" });
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    await userEvent.setup().click(button);
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText(/翻译完成、失败或需要你处理时会通知你/)).toBeInTheDocument());
    expect(window.localStorage.getItem("book-agent.notify")).toBe("on");
  });
});
