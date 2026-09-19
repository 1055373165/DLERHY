import { useQuery } from "@tanstack/react-query";

import { getCostEstimate, getCurrentOrgCredit } from "../../lib/api";
import s from "./WorkspacePage.module.css";

function tokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(Math.round(tokens));
}

function dollars(amount: number): string {
  return amount > 0 && amount < 0.01 ? "<$0.01" : `$${amount.toFixed(2)}`;
}

/** Shown before a translation starts: what the run will take, from the book's size and the provider's prices. */
export function CostEstimateNote({ documentId }: { documentId: string }) {
  const query = useQuery({
    queryKey: ["cost-estimate", documentId],
    queryFn: () => getCostEstimate(documentId),
    retry: false,
    staleTime: 60_000,
  });
  const credit = useQuery({ queryKey: ["org-credit"], queryFn: getCurrentOrgCredit, retry: false }).data;
  const estimate = query.data;
  if (!estimate || estimate.packet_count === 0) return null;
  // Generated types model the (low, high) tuples as unknown[].
  const pair = (value: unknown[] | null | undefined): [number, number] | null =>
    Array.isArray(value) && value.length === 2 ? [Number(value[0]), Number(value[1])] : null;
  const [inLow, inHigh] = pair(estimate.token_in_range) ?? [estimate.token_in, estimate.token_in];
  const [outLow, outHigh] = pair(estimate.token_out_range) ?? [estimate.token_out, estimate.token_out];
  const range = pair(estimate.cost_usd_range);
  // A prepaid organisation is charged the provider cost times its price multiplier.
  const chargedHigh = range && credit?.prepaid ? range[1] * credit.price_multiplier : null;
  const shortOfCredit = chargedHigh !== null && credit !== undefined && chargedHigh > credit.balance_usd;
  return (
    <div className={s.costEstimate} role="note" aria-label="费用预估" title={(estimate.notes ?? []).join(" ")}>
      预计 token：输入 {tokenCount(inLow)}–{tokenCount(inHigh)}，输出 {tokenCount(outLow)}–{tokenCount(outHigh)}
      {range ? `，约 ${dollars(range[0])}–${dollars(range[1])}` : "（服务商未设单价，无法估算金额）"}
      {shortOfCredit && credit && (
        <div className={s.costEstimateWarning}>
          预付余额 {dollars(credit.balance_usd)}，这本书最多可能扣 {dollars(chargedHigh ?? 0)}；余额不够时运行会暂停，充值后可接着翻译。
        </div>
      )}
      {estimate.thinking_may_inflate_output && (
        <div className={s.costEstimateWarning}>
          按关闭思考模式估算；当前服务商未关闭思考，实际输出可能是估算的 2–3 倍。可在「服务商」页关闭思考模式。
        </div>
      )}
    </div>
  );
}
