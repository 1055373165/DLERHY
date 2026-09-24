/** Whether a freshly imported book may start translating by itself (SPEC.md decision 6). */
import type { BookOverview } from "./overview";
import { usd } from "./overview";

export type StartDecision = { auto: true } | { auto: false; reason: string };

export function decideAutoStart(overview: BookOverview, capUsd: number): StartDecision {
  if (overview.translation.state !== "never_started") {
    return { auto: false, reason: "" };
  }
  // With a free pilot on offer, nothing paid starts by itself: the reader tries the pilot first.
  if (overview.pilot?.state === "not_started") {
    return { auto: false, reason: "" };
  }
  return withinCap(overview, capUsd);
}

/** After the free pilot: 翻译全书 starts at once when the most it may cost is within the cap, else asks. */
export function decideBookStart(overview: BookOverview, capUsd: number): StartDecision {
  return withinCap(overview, capUsd);
}

function withinCap(overview: BookOverview, capUsd: number): StartDecision {
  const maxCharge = overview.cost?.max_charge_usd ?? null;
  if (maxCharge === null) {
    return { auto: false, reason: "服务商没有设置单价，估算不出费用，所以没有自动开始。" };
  }
  if (capUsd <= 0) {
    return { auto: false, reason: "你设置了每次都先确认再开始。" };
  }
  if (maxCharge > capUsd) {
    return { auto: false, reason: `预计最多 ${usd(maxCharge)}，超过了自动开始上限 ${usd(capUsd)}，请确认后开始。` };
  }
  return { auto: true };
}
