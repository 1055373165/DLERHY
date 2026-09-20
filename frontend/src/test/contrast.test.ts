/** WCAG AA on the token pairs the app actually puts on screen, in both themes.
 *  The values come from src/styles/tokens.css itself, so a colour edit is checked here. */

import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { describe, expect, it } from "vitest";

// jsdom serves import.meta.url over http, so read from the project root instead.
const CSS = readFileSync(resolvePath(process.cwd(), "src/styles/tokens.css"), "utf8");

/** The declarations inside the block whose selector line starts with `selector`. */
function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  if (start < 0) throw new Error(`tokens.css has no ${selector} block`);
  const open = CSS.indexOf("{", start);
  let depth = 0;
  let end = open;
  for (let i = open; i < CSS.length; i += 1) {
    if (CSS[i] === "{") depth += 1;
    else if (CSS[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const out: Record<string, string> = {};
  for (const [, name, value] of CSS.slice(open, end).matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    out[name] = value.trim();
  }
  return out;
}

const LIGHT = block(":root {");
const DARK = { ...LIGHT, ...block(':root[data-theme="dark"]') };

/** Follow `var(--x)` chains down to a literal colour. */
function resolve(vars: Record<string, string>, name: string): string {
  let value: string | undefined = vars[name];
  for (let hop = 0; hop < 10; hop += 1) {
    if (value === undefined) throw new Error(`tokens.css never defines ${name}`);
    const ref = value.match(/^var\((--[\w-]+)\)$/);
    if (!ref) return value;
    value = vars[ref[1]];
  }
  throw new Error(`${name} loops through var()`);
}

function channel(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const full = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  if (!/^[0-9a-f]{6}$/i.test(full)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = channel(hex).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** [text token, background token, minimum ratio]. 4.5 is AA for body text, 3.0 for large text. */
const PAIRS: [string, string, number][] = [
  // Body text on every surface it lands on.
  ["--text-primary", "--bg-root", 4.5],
  ["--text-primary", "--bg-panel", 4.5],
  ["--text-primary", "--bg-panel-alt", 4.5],
  ["--text-primary", "--bg-hover", 4.5],
  ["--text-primary", "--bg-active", 4.5],
  ["--text-secondary", "--bg-root", 4.5],
  ["--text-secondary", "--bg-panel", 4.5],
  ["--text-secondary", "--bg-panel-alt", 4.5],
  // The unselected tab and other quiet text: the user could not read the old grey.
  ["--text-muted", "--bg-root", 4.5],
  ["--text-muted", "--bg-panel", 4.5],
  ["--text-muted", "--bg-panel-alt", 4.5],
  // Status words.
  ["--blue", "--bg-panel", 4.5],
  ["--blue", "--blue-light", 4.5],
  ["--green", "--bg-panel", 4.5],
  ["--green", "--green-light", 4.5],
  ["--amber", "--bg-panel", 4.5],
  ["--amber", "--amber-light", 4.5],
  ["--red", "--bg-panel", 4.5],
  ["--red", "--red-light", 4.5],
  // Filled controls.
  ["--text-inverse", "--ink", 4.5],
  ["--white", "--red", 4.5],
  // A control's boundary is not text: AA asks 3:1 of it, same as the focus ring.
  ["--border-strong", "--bg-panel", 3],
  ["--border-strong", "--bg-input", 3],
  ["--focus-ring", "--bg-root", 3],
];

describe.each([
  ["浅色", LIGHT],
  ["深色", DARK],
])("%s 主题的对比度", (_name, vars) => {
  it.each(PAIRS)("%s on %s ≥ %s:1", (text, background, min) => {
    const got = ratio(resolve(vars, text), resolve(vars, background));
    expect(Number(got.toFixed(2))).toBeGreaterThanOrEqual(min);
  });
});

it("深色主题覆盖了每个表面与文字色", () => {
  const overridden = Object.keys(block(':root[data-theme="dark"]'));
  for (const name of ["--bg-root", "--bg-panel", "--text-primary", "--text-secondary", "--text-muted", "--border"]) {
    expect(overridden).toContain(name);
  }
});

it("跟随系统的深色与设置里选的深色一致", () => {
  const media = block(':root:not([data-theme="light"])');
  const chosen = block(':root[data-theme="dark"]');
  expect(media).toEqual(chosen);
});
