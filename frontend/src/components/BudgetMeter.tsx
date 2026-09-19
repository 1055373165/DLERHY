import { useQuery } from "@tanstack/react-query";

import { downloadUsageStatement, getCurrentOrgBudget, getCurrentOrgCredit } from "../lib/api";
import s from "./BudgetMeter.module.css";

function usd(value: number): string {
  return `$${value.toFixed(value >= 100 ? 0 : 2)}`;
}

/** This month's model spend of the caller's organisation; hidden when the server has no budget endpoint. */
export function BudgetMeter() {
  const query = useQuery({
    queryKey: ["org-budget"],
    queryFn: getCurrentOrgBudget,
    refetchInterval: 60_000,
    retry: false,
  });
  const credit = useQuery({
    queryKey: ["org-credit"],
    queryFn: getCurrentOrgCredit,
    refetchInterval: 60_000,
    retry: false,
  }).data;
  const budget = query.data;
  if (!budget) return null;
  const cap = budget.monthly_budget_usd;
  const ratio = cap ? Math.min(1, budget.spent_usd / cap) : 0;
  const tone = budget.exhausted ? "danger" : ratio >= 0.8 ? "warning" : "ok";
  return (
    <div className={s.meter} data-tone={tone} title={`${budget.org_name} · 自 ${budget.period_start.slice(0, 10)} 起`}>
      <div className={s.row}>
        <span className={s.label}>本月花费</span>
        <span className={s.value}>
          {usd(budget.spent_usd)}
          {cap !== null ? ` / ${usd(cap)}` : " · 不限额"}
        </span>
      </div>
      {cap !== null ? (
        <div className={s.track} role="meter" aria-label="本月预算用量" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(ratio * 100)}>
          <div className={s.fill} style={{ width: `${Math.round(ratio * 100)}%` }} />
        </div>
      ) : null}
      {budget.exhausted ? <div className={s.warning}>预算已用完：新运行会被拒绝，运行中的会暂停。</div> : null}
      {credit?.prepaid ? (
        <div className={s.row} data-tone={credit.exhausted ? "danger" : undefined}>
          <span className={s.label}>预付余额</span>
          <span className={s.value}>{usd(credit.balance_usd)}</span>
        </div>
      ) : null}
      {credit?.exhausted ? <div className={s.warning}>余额已用完：充值后运行才能继续。</div> : null}
      <button type="button" className={s.statement} onClick={() =>
          void downloadUsageStatement().catch((err) => window.alert(err instanceof Error ? err.message : "下载失败"))
        }>
        下载本月用量明细（CSV）
      </button>
    </div>
  );
}
