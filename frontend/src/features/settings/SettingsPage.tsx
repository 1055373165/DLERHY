import { useState } from "react";

import { ApiKeySetting } from "../../components/ApiKeySetting";
import { FastReadingToggle } from "../../components/FastReadingToggle";
import { Surface } from "../../components/Surface";
import { autoStartCapUsd, DEFAULT_AUTO_START_CAP_USD, setAutoStartCapUsd } from "../../lib/preferences";
import { ProvidersPage } from "../providers/ProvidersPage";

/** Reading preferences (per browser) and the model provider. */
export function SettingsPage() {
  const [cap, setCap] = useState(() => String(autoStartCapUsd()));
  const parsed = Number(cap);
  const valid = cap.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-6)" }}>
      <Surface eyebrow="偏好" title="阅读偏好">
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
          <ApiKeySetting />
        </div>
      </Surface>
      <div id="providers">
        <ProvidersPage />
      </div>
    </div>
  );
}
