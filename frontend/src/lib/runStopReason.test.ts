import { describe, expect, it } from "vitest";

import { stopReasonNote } from "./runStopReason";

describe("stopReasonNote", () => {
  it("explains a pause the user can fix and points to where", () => {
    const note = stopReasonNote("paused", "provider.not_configured");
    expect(note?.text).toContain("服务商");
    expect(note?.link?.to).toBe("/providers");
    expect(stopReasonNote("paused", "provider.insufficient_balance")?.text).toContain("余额不足");
  });

  it("stays quiet for running or finished runs and names unknown reasons", () => {
    expect(stopReasonNote("running", "provider.not_configured")).toBeNull();
    expect(stopReasonNote("succeeded", null)).toBeNull();
    expect(stopReasonNote("failed", "something.new")?.text).toContain("something.new");
  });
});
