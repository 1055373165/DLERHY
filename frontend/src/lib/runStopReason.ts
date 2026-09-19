/** Why a run stopped, in words a user can act on. */
export interface StopReasonNote {
  text: string;
  /** Where the fix is made, when it is made in the app. */
  link?: { to: string; label: string };
}

const PROVIDERS = { to: "/providers", label: "去「服务商」页" };

const NOTES: Record<string, StopReasonNote> = {
  "provider.not_configured": {
    text: "还没有可用的模型服务商。添加并启用一个服务商后点「继续当前转换」，会从停下的地方接着翻译。",
    link: PROVIDERS,
  },
  "provider.insufficient_balance": {
    text: "模型服务商账户余额不足。充值后点「继续当前转换」，会从停下的地方接着翻译，已完成的部分不会重做。",
  },
  "provider.authentication_failed": {
    text: "模型服务商拒绝了 API key（无效、过期或没有权限）。更新 key 后点「继续当前转换」。",
    link: PROVIDERS,
  },
  "provider.reasoning_exhausted_output": {
    text: "思考模型把输出额度都用在了思考上，没有写出译文。在服务商设置里关闭思考模式或调大最大输出 token，然后点「继续当前转换」。",
    link: PROVIDERS,
  },
  "billing.credit_exhausted": {
    text: "预付余额已用完，运行已暂停。充值后点「继续当前转换」，会从停下的地方接着翻译，已完成的部分不会重复扣费。",
  },
  "budget.org_monthly_exhausted": {
    text: "本组织本月的模型花费已达上限。提高月度预算或等到下个月后点「继续当前转换」。",
  },
  "budget.cost_exceeded": { text: "这次运行的花费达到了设定的上限。调高上限后点「继续当前转换」。" },
  "budget.token_in_exceeded": { text: "这次运行的输入 token 达到了设定的上限。调高上限后点「继续当前转换」。" },
  "budget.token_out_exceeded": { text: "这次运行的输出 token 达到了设定的上限。调高上限后点「继续当前转换」。" },
  "budget.wall_clock_exceeded": { text: "这次运行超过了设定的最长时长。点「继续当前转换」接着跑。" },
  "budget.no_progress_exceeded": {
    text: "运行长时间没有进展，已自动暂停。通常是模型服务暂时不可用；稍后点「继续当前转换」重试。",
  },
  "budget.consecutive_failures_exceeded": {
    text: "连续多次调用失败，已自动暂停，避免继续花钱。检查服务商状态后点「继续当前转换」。",
    link: PROVIDERS,
  },
  "stage.evidence_failed": {
    text: "有一个必需步骤没有完成（见下方失败的步骤）。点「重试上次转换」只重做失败的部分。",
  },
  "runner.unhandled_exception": { text: "运行遇到了内部错误。点「重试上次转换」；如果反复出现，请联系管理员。" },
};

export function stopReasonNote(status: string | null | undefined, stopReason: string | null | undefined): StopReasonNote | null {
  if (!stopReason || !status || !["paused", "failed"].includes(status)) return null;
  return NOTES[stopReason] ?? { text: `运行已${status === "paused" ? "暂停" : "停止"}（${stopReason}）。` };
}
