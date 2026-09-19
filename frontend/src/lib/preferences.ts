/** Per-browser reader preferences. Storage can be unavailable (private mode): fall back to defaults. */

const FAST_READING_KEY = "book-agent.fast-reading";

/** Fast reading mode: term locks are approved automatically so the run never waits on a person. On by default. */
export function fastReadingPreferred(): boolean {
  try {
    return window.localStorage.getItem(FAST_READING_KEY) !== "off";
  } catch {
    return true;
  }
}

const AUTO_START_CAP_KEY = "book-agent.auto-start-cap-usd";
/** Start translating right after import when the most it may cost is at most this; 0 means always ask. */
export const DEFAULT_AUTO_START_CAP_USD = 1;

export function autoStartCapUsd(): number {
  try {
    const stored = window.localStorage.getItem(AUTO_START_CAP_KEY);
    const value = stored === null ? NaN : Number(stored);
    return Number.isFinite(value) && value >= 0 ? value : DEFAULT_AUTO_START_CAP_USD;
  } catch {
    return DEFAULT_AUTO_START_CAP_USD;
  }
}

export function setAutoStartCapUsd(value: number): void {
  try {
    window.localStorage.setItem(AUTO_START_CAP_KEY, String(Math.max(0, value)));
  } catch {
    /* the default applies next time */
  }
}

export function setFastReadingPreferred(on: boolean): void {
  try {
    window.localStorage.setItem(FAST_READING_KEY, on ? "on" : "off");
  } catch {
    /* the default applies next time */
  }
}
