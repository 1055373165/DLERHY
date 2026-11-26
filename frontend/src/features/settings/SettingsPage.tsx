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
import s from "./SettingsPage.module.css";

/** Reading preferences (per browser) and the model provider. */
export function SettingsPage() {
  const [cap, setCap] = useState(() => String(autoStartCapUsd()));
  const parsed = Number(cap);
  const valid = cap.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  return (
    <div className={s.layout}>
      <header className="page-header"><h1>设置</h1></header>
      <div className={s.columns}>
        <Surface title="阅读偏好">
          <div className={s.preferences}>
            <label className={s.preference}>
              <strong>自动开始上限（美元）</strong>
              <span>导入后，预计花费不超过上限时自动开始翻译。设为 0，每次都先确认。默认 {DEFAULT_AUTO_START_CAP_USD} 美元。</span>
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
              />
              {!valid && <span className={s.error}>请输入 0 或更大的数。</span>}
            </label>
            <FastReadingToggle />
            <ThemeToggle />
            <div className={s.preference}>
              <strong>浏览器通知</strong>
              <span>翻译完成、失败或需要你处理时提醒你。</span>
              <NotifyMe label="开启通知" />
            </div>
          </div>
        </Surface>

        <Surface title="花费与接入">
          <BudgetMeter statement />
          <ApiKeySetting />
          <div className={s.links}>
            <a href={SERVICE_LINKS.docs} target="_blank" rel="noopener">接口文档</a>
            <a href={SERVICE_LINKS.openapi} target="_blank" rel="noopener">OpenAPI</a>
          </div>
        </Surface>
      </div>
      <div className={s.providers} id="providers">
        <ProvidersPage />
      </div>
    </div>
  );
}
