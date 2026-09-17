import { afterEach, describe, expect, it, vi } from "vitest";

import { getHealth, runStreamUrl, setStoredApiKey } from "./api";

describe("API key handling", () => {
  afterEach(() => {
    setStoredApiKey("");
    vi.unstubAllGlobals();
  });

  it("sends the stored key as a bearer token and on the stream URL", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await getHealth();
    expect(new Headers((fetchMock.mock.calls[0] as unknown[])[1] as HeadersInit | undefined).get("Authorization")).toBeNull();
    expect(runStreamUrl("r1")).not.toContain("access_token");

    setStoredApiKey("bak_secret");
    await getHealth();
    const init = (fetchMock.mock.calls[1] as unknown[])[1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer bak_secret");
    expect(runStreamUrl("r1")).toContain("/runs/r1/stream?access_token=bak_secret");
  });
});
