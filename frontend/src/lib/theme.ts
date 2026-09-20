/** Light or dark. `system` follows the operating system; the other two override it.
 *  The choice is written to <html data-theme>, which is what tokens.css reads. */

export type Theme = "system" | "light" | "dark";

const THEME_KEY = "book-agent.theme";

export function loadTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

/** Put the choice on <html>. `system` removes the attribute, so the media query decides. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function setTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    if (theme === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* the choice holds for this page only */
  }
}

/** Which theme is actually on screen right now. */
export function resolvedTheme(theme: Theme = loadTheme()): "light" | "dark" {
  if (theme !== "system") return theme;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}
