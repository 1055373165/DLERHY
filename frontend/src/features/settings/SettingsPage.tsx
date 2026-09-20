import { useState } from "react";

import { ApiKeySetting } from "../../components/ApiKeySetting";
import { BudgetMeter } from "../../components/BudgetMeter";
import { FastReadingToggle } from "../../components/FastReadingToggle";
import { NotifyMe } from "../../components/NotifyMe";
import { Surface } from "../../components/Surface";
import { ThemeToggle } from "../../components/ThemeToggle";
import { SERVICE_LINKS } from "../../lib/api";
import { autoStartCapUsd, DEFAULT_AUTO_START_CAP_USD, setAutoStartCapUsd } from "../../lib/preferences";
import { ProvidersPage } from "../providers/ProvidersPage";

/** Reading preferences (per browser) and the model provider. */
export function SettingsPage() {
  const [cap, setCap] = useState(() => String(autoStartCapUsd()));
  const parsed = Number(cap);
  const valid = cap.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-6)" }}>
      <Surface level={1} eyebrow="设置" title="阅读偏好">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", maxWidth: 640 }}>
            <span>
              <strong>自动开始上限（美元）</strong>：导入后，预计最多花费不超过这个数就直接开始翻译，超过就先让你确认。设为 0 表示每次都先确认。默认 {DEFAULT_AUTO_START_CAP_USD}。
            </span>
            <input
              type="number"
              min={0}
              step={0.5}
              aria-label="自动开始上限"
              value={cap}
              onChange={(event) => {
                setCap(event.target.value);
                const value = Number(event.target.value);
                if (event.target.value.trim() !== "" && Number.isFinite(value) && value >= 0) setAutoStartCapUsd(value);
              }}
              style={{ maxWidth: 160 }}
            />
            {!valid && <span style={{ color: "var(--red)" }}>请输入 0 或更大的数。</span>}
          </label>
          <FastReadingToggle />
          <ThemeToggle />
          <div>
            <strong>浏览器通知</strong>：翻译完成、失败或需要你处理时提醒你（每 30 秒检查一次）。 <NotifyMe label="开启通知" />
          </div>
        </div>
      </Surface>

      <Surface eyebrow="账单" title="花费与接入">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)", maxWidth: 640 }}>
          <BudgetMeter statement />
          <ApiKeySetting />
          <div style={{ display: "flex", gap: "var(--sp-4)" }}>
            <a href={SERVICE_LINKS.docs} target="_blank" rel="noopener">
              接口文档
            </a>
            <a href={SERVICE_LINKS.openapi} target="_blank" rel="noopener">
              OpenAPI
            </a>
          </div>
        </div>
      </Surface>
      <div id="providers">
        <ProvidersPage />
      </div>
    </div>
  );
}
