import { useState } from "react";

import { loadTheme, setTheme, type Theme } from "../lib/theme";

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

/** Appearance: follow the system, or pin light or dark. */
export function ThemeToggle() {
  const [theme, setCurrent] = useState<Theme>(() => loadTheme());
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)", maxWidth: 640 }}>
      <span>
        <strong>界面配色</strong>：默认跟随系统的深浅色设置，也可以在这里固定。
      </span>
      <select
        aria-label="界面配色"
        value={theme}
        onChange={(event) => {
          const next = event.target.value as Theme;
          setCurrent(next);
          setTheme(next);
        }}
        style={{ maxWidth: 200 }}
      >
        {OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
