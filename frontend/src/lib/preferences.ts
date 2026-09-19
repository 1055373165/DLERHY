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

export function setFastReadingPreferred(on: boolean): void {
  try {
    window.localStorage.setItem(FAST_READING_KEY, on ? "on" : "off");
  } catch {
    /* the default applies next time */
  }
}
