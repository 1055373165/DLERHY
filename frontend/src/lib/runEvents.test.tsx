import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RUN_EVENT_KINDS, runPollInterval, useRunEventStream } from "./runEvents";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, Set<() => void>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(kind: string, listener: () => void) {
    if (!this.listeners.has(kind)) this.listeners.set(kind, new Set());
    this.listeners.get(kind)!.add(listener);
  }

  removeEventListener(kind: string, listener: () => void) {
    this.listeners.get(kind)?.delete(listener);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  emit(kind: string) {
    for (const listener of this.listeners.get(kind) ?? []) listener();
  }
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useRunEventStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("goes live, refreshes run queries on events (debounced) and closes on unmount", () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result, unmount } = renderHook(() => useRunEventStream("run-1", true), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain("/runs/run-1/stream");
    expect(result.current).toBe("connecting");
    act(() => source.onopen?.());
    expect(result.current).toBe("live");
    expect(RUN_EVENT_KINDS).toContain("document.reparsed");

    act(() => {
      source.emit("packet.translated");
      source.emit("agent.turn.finished");
    });
    expect(invalidate).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run"] });
    const calls = invalidate.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(invalidate.mock.calls.length).toBe(calls);

    unmount();
    expect(source.closed).toBe(true);
  });

  it("falls back to polling when the stream cannot open", () => {
    const client = new QueryClient();
    const { result } = renderHook(() => useRunEventStream("run-1", true), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];
    act(() => source.onerror?.());
    expect(result.current).toBe("unavailable");
    expect(source.closed).toBe(true);
    expect(runPollInterval(true, result.current)).toBe(2_500);
    expect(runPollInterval(true, "live")).toBe(15_000);
    expect(runPollInterval(false, "live")).toBe(false);
  });

  it("does not connect for inactive runs", () => {
    const client = new QueryClient();
    const { result } = renderHook(() => useRunEventStream("run-1", false), { wrapper: wrapper(client) });
    expect(result.current).toBe("idle");
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
