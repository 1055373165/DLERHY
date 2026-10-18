/** Every colour token a stylesheet uses is defined in tokens.css, so it follows the theme. A missing
 *  token falls back to its literal: `var(--surface-2, #f7f8fa)` drew the book page's re-import box
 *  light grey in dark mode, its grey text unreadable. A fallback that reads in both themes
 *  (transparent, a translucent grey) is allowed. */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(process.cwd(), "src");
const TOKENS = readFileSync(join(SRC, "styles/tokens.css"), "utf8");
const DEFINED = new Set([...TOKENS.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
const THEME_NEUTRAL = /^(transparent|inherit|currentColor|rgba\(\s*127,\s*127,\s*127,\s*[\d.]+\))$/i;

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return cssFiles(path);
    return name.endsWith(".css") ? [path] : [];
  });
}

describe("theme tokens", () => {
  it("are defined wherever a stylesheet relies on them for colour", () => {
    const problems: string[] = [];
    for (const file of cssFiles(SRC)) {
      const css = readFileSync(file, "utf8");
      for (const [, name, fallback] of css.matchAll(/var\((--[\w-]+)\s*(?:,\s*([^)]+\)?))?\)/g)) {
        if (DEFINED.has(name)) continue;
        const value = (fallback ?? "").trim();
        if (value && (THEME_NEUTRAL.test(value) || !/#|rgb|hsl/i.test(value))) continue;
        problems.push(`${file.slice(SRC.length + 1)}: ${name}${value ? ` (falls back to ${value})` : ""}`);
      }
    }
    expect(problems).toEqual([]);
  });
});
