import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { queryClient } from "../../app/queryClient";
import { StatusBadge } from "../../components/StatusBadge";
import { Surface } from "../../components/Surface";
import {
  type Provider,
  type ProviderCreate,
  type ProviderUpdate,
  activateProvider,
  createProvider,
  deleteProvider,
  listProviders,
  testProvider,
  updateProvider,
} from "../../lib/api";
import s from "./ProvidersPage.module.css";

const THINKING_OFF = { thinking: { type: "disabled" } };

type Draft = {
  name: string;
  provider_kind: "openai_compatible" | "echo";
  base_url: string;
  model_name: string;
  api_key: string;
  max_output_tokens: string;
  input_cost: string;
  cache_hit_cost: string;
  output_cost: string;
  peak_multiplier: string;
  off_peak_window: string;
  peak_schedule: string;
  thinking_off: boolean;
  overrides: string;
};

const EMPTY: Draft = {
  name: "",
  provider_kind: "openai_compatible",
  base_url: "https://api.deepseek.com/v1",
  model_name: "",
  api_key: "",
  max_output_tokens: "8192",
  input_cost: "",
  cache_hit_cost: "",
  output_cost: "",
  peak_multiplier: "",
  off_peak_window: "",
  peak_schedule: "",
  thinking_off: false,
  overrides: "",
};

function isThinkingOff(overrides: Record<string, unknown> | undefined): boolean {
  const thinking = overrides?.thinking as { type?: string } | undefined;
  return thinking?.type === "disabled";
}

function draftFrom(provider: Provider): Draft {
  const overrides = { ...(provider.request_overrides ?? {}) } as Record<string, unknown>;
  const thinkingOff = isThinkingOff(overrides);
  if (thinkingOff) delete overrides.thinking;
  const price = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));
  return {
    name: provider.name,
    provider_kind: provider.provider_kind,
    base_url: provider.base_url,
    model_name: provider.model_name,
    api_key: "",
    max_output_tokens: String(provider.max_output_tokens ?? 8192),
    input_cost: price(provider.input_cost_per_1m_tokens),
    cache_hit_cost: price(provider.input_cache_hit_cost_per_1m_tokens),
    output_cost: price(provider.output_cost_per_1m_tokens),
    peak_multiplier: price(provider.peak_price_multiplier),
    off_peak_window: provider.off_peak_window_utc ?? "",
    peak_schedule: provider.peak_schedule_utc ?? "",
    thinking_off: thinkingOff,
    overrides: Object.keys(overrides).length ? JSON.stringify(overrides, null, 2) : "",
  };
}

/** Parse the form; throws a user-facing message on bad input. */
function payloadFrom(draft: Draft): Omit<ProviderCreate, "activate"> {
  const price = (label: string, value: string): number | null => {
    if (!value.trim()) return null;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) throw new Error(`${label}必须是不小于 0 的数字`);
    return number;
  };
  let overrides: Record<string, unknown> = {};
  if (draft.overrides.trim()) {
    try {
      overrides = JSON.parse(draft.overrides);
    } catch {
      throw new Error("高级请求参数不是合法的 JSON");
    }
    if (typeof overrides !== "object" || overrides === null || Array.isArray(overrides)) {
      throw new Error("高级请求参数必须是 JSON 对象");
    }
  }
  if (draft.thinking_off) overrides = { ...overrides, ...THINKING_OFF };
  const maxTokens = Number(draft.max_output_tokens);
  if (!Number.isInteger(maxTokens) || maxTokens < 1) throw new Error("最大输出 token 必须是正整数");
  return {
    name: draft.name.trim(),
    provider_kind: draft.provider_kind,
    base_url: draft.base_url.trim(),
    model_name: draft.model_name.trim(),
    api_key: draft.api_key.trim() || null,
    max_output_tokens: maxTokens,
    input_cost_per_1m_tokens: price("输入单价", draft.input_cost),
    input_cache_hit_cost_per_1m_tokens: price("缓存命中单价", draft.cache_hit_cost),
    output_cost_per_1m_tokens: price("输出单价", draft.output_cost),
    peak_price_multiplier: price("高峰倍数", draft.peak_multiplier),
    off_peak_window_utc: offPeakWindow(draft.off_peak_window),
    peak_schedule_utc: draft.peak_schedule.trim() || null,
    request_overrides: overrides,
  };
}

function offPeakWindow(value: string): string | null {
  const window = value.trim();
  if (!window) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(window)) {
    throw new Error("优惠时段要写成 16:30-00:30 这样的格式（UTC）");
  }
  return window;
}

function priceLine(provider: Provider): string {
  const input = provider.input_cost_per_1m_tokens;
  const output = provider.output_cost_per_1m_tokens;
  if (input === null || input === undefined || output === null || output === undefined) return "未设置单价，花费无法计算";
  const line = `输入 $${input} / 输出 $${output} 每百万 token`;
  const multiplier = provider.peak_price_multiplier;
  if (multiplier && provider.peak_schedule_utc) return `${line}（高峰时段 UTC ${provider.peak_schedule_utc} ×${multiplier}，其余时段为此价）`;
  if (!multiplier || !provider.off_peak_window_utc) return line;
  return `${line}（优惠时段 UTC ${provider.off_peak_window_utc}，其余时段 ×${multiplier}）`;
}

export function ProvidersPage() {
  const query = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["providers"] });
  const fail = (error: unknown) => setFeedback({ tone: "error", text: error instanceof Error ? error.message : String(error) });

  const save = useMutation({
    mutationFn: async () => {
      const payload = payloadFrom(draft);
      if (editing === "new") return createProvider({ ...payload, activate: !(query.data ?? []).some((p) => p.is_active) });
      const update: ProviderUpdate = { ...payload };
      if (!draft.api_key.trim()) delete update.api_key; // blank keeps the stored key
      return updateProvider(editing as string, update);
    },
    onSuccess: async (provider) => {
      setFeedback({ tone: "success", text: `已保存「${provider.name}」。` });
      setEditing(null);
      await refresh();
    },
    onError: fail,
  });
  const activate = useMutation({
    mutationFn: activateProvider,
    onSuccess: async (provider) => {
      setFeedback({ tone: "success", text: `之后的翻译使用「${provider.name}」。` });
      await refresh();
    },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: testProvider,
    onSuccess: async (result) => {
      setFeedback({
        tone: result.status === "ok" ? "success" : "error",
        text: `${result.status === "ok" ? "连接正常" : "连接失败"}：${result.message ?? ""}${result.elapsed_ms ? `（${result.elapsed_ms} ms）` : ""}`,
      });
      await refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: deleteProvider,
    onSuccess: async () => {
      setFeedback({ tone: "success", text: "已删除。" });
      await refresh();
    },
    onError: fail,
  });

  const providers = query.data ?? [];
  const field = (key: keyof Draft) => ({
    value: draft[key] as string,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setDraft({ ...draft, [key]: event.target.value }),
  });

  return (
    <div className={s.layout} data-editing={editing ? true : undefined}>
      <Surface
        eyebrow="服务商"
        title="模型服务商"
        aside={
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setDraft(EMPTY);
              setEditing("new");
              setFeedback(null);
            }}
          >
            添加服务商
          </button>
        }
      >
        {query.isError ? <div className={s.empty}>无法加载服务商（可能需要管理员 API key）。</div> : null}
        {providers.length === 0 && !query.isLoading && !query.isError ? (
          <div className={s.empty}>还没有服务商。添加一个 OpenAI 兼容的服务商后才能翻译。</div>
        ) : null}
        <ul className={s.list}>
          {providers.map((provider) => (
            <li key={provider.id} className={s.row}>
              <div className={s.rowHead}>
                <span className={s.name}>{provider.name}</span>
                {provider.is_active ? <StatusBadge tone="success" label="当前使用" /> : null}
                {provider.shared === false ? null : <span className={s.meta}>共享</span>}
              </div>
              <div className={s.meta}>
                {provider.model_name} · {provider.base_url}
                {provider.api_key_preview ? ` · ${provider.api_key_preview}` : ""}
              </div>
              <div className={s.meta}>
                {priceLine(provider)}
                {isThinkingOff(provider.request_overrides as Record<string, unknown>) ? " · 已关闭思考模式" : ""}
              </div>
              <div className={s.actions}>
                {!provider.is_active ? (
                  <button type="button" className="btn btn-sm" onClick={() => activate.mutate(provider.id)} disabled={activate.isPending}>
                    设为当前
                  </button>
                ) : null}
                <button type="button" className="btn btn-sm" onClick={() => test.mutate(provider.id)} disabled={test.isPending}>
                  {test.isPending && test.variables === provider.id ? "测试中…" : "测试连接"}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => {
                    setDraft(draftFrom(provider));
                    setEditing(provider.id);
                    setFeedback(null);
                  }}
                >
                  编辑
                </button>
                {!provider.is_active ? (
                  <button type="button" className="btn btn-sm" onClick={() => remove.mutate(provider.id)} disabled={remove.isPending}>
                    删除
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
        {feedback ? (
          <div className={s.feedback} data-tone={feedback.tone}>
            {feedback.text}
          </div>
        ) : null}
      </Surface>

      {editing ? (
        <Surface eyebrow={editing === "new" ? "新增" : "编辑"} title={editing === "new" ? "添加服务商" : "编辑服务商"}>
          <form
            className={s.form}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <label htmlFor="provider-name">名称</label>
            <input id="provider-name" className={s.input} required {...field("name")} />
            <label htmlFor="provider-kind">类型</label>
            <select id="provider-kind" className={s.input} {...field("provider_kind")}>
              <option value="openai_compatible">OpenAI 兼容接口</option>
              <option value="echo">Echo（离线测试，不翻译）</option>
            </select>
            <label htmlFor="provider-base-url">接口地址</label>
            <input id="provider-base-url" className={s.input} required {...field("base_url")} />
            <label htmlFor="provider-model">模型</label>
            <input id="provider-model" className={s.input} required placeholder="deepseek-v4-flash" {...field("model_name")} />
            <label htmlFor="provider-key">API key</label>
            <input
              id="provider-key"
              className={s.input}
              type="password"
              autoComplete="off"
              placeholder={editing === "new" ? "sk-…" : "留空则保留已保存的 key"}
              {...field("api_key")}
            />
            <label htmlFor="provider-max-tokens">最大输出 token</label>
            <input id="provider-max-tokens" className={s.input} inputMode="numeric" {...field("max_output_tokens")} />
            <fieldset className={s.prices}>
              <legend>单价（美元 / 百万 token，用于花费统计与预算；分时段计价时填优惠时段的单价）</legend>
              <label htmlFor="provider-input-cost">输入</label>
              <input id="provider-input-cost" className={s.input} inputMode="decimal" {...field("input_cost")} />
              <label htmlFor="provider-cache-cost">缓存命中</label>
              <input id="provider-cache-cost" className={s.input} inputMode="decimal" {...field("cache_hit_cost")} />
              <label htmlFor="provider-output-cost">输出</label>
              <input id="provider-output-cost" className={s.input} inputMode="decimal" {...field("output_cost")} />
              <label htmlFor="provider-peak-multiplier">高峰倍数</label>
              <input
                id="provider-peak-multiplier"
                className={s.input}
                inputMode="decimal"
                placeholder="不分时段就留空"
                {...field("peak_multiplier")}
              />
              <label htmlFor="provider-off-peak">优惠时段（UTC）</label>
              <input id="provider-off-peak" className={s.input} placeholder="16:30-00:30" {...field("off_peak_window")} />
              <label htmlFor="provider-peak-schedule">高峰时段（UTC，按星期；填了就不看优惠时段）</label>
              <input
                id="provider-peak-schedule"
                className={s.input}
                placeholder="mon-fri 01:00-04:00,06:00-10:00"
                {...field("peak_schedule")}
              />
            </fieldset>
            <label className={s.checkbox}>
              <input
                type="checkbox"
                checked={draft.thinking_off}
                onChange={(event) => setDraft({ ...draft, thinking_off: event.target.checked })}
              />
              关闭思考模式（DeepSeek 等推理模型建议勾选：否则输出预算会被推理用尽，运行会暂停）
            </label>
            <label htmlFor="provider-overrides">高级请求参数（JSON，可选）</label>
            <textarea id="provider-overrides" className={s.input} rows={3} placeholder='{"temperature": 0.3}' {...field("overrides")} />
            <div className={s.actions}>
              <button type="submit" className="btn btn-sm" disabled={save.isPending}>
                {save.isPending ? "保存中…" : "保存"}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setEditing(null)}>
                取消
              </button>
            </div>
          </form>
        </Surface>
      ) : null}
    </div>
  );
}
