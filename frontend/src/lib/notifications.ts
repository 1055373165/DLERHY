/** Browser notifications for a reader who is not looking at the page: the book is ready, it needs
 * you, or it failed. Driven by GET /inbox (polled every 30 s); permission is asked only when the
 * reader clicks something like 「完成时通知我」, never on page load. */
import type { Inbox } from "./overview";

const PREFERENCE_KEY = "book-agent.notify";
const SEEN_KEY = "book-agent.notified";

export interface PlannedNotification {
  /** Stable per event, so a reload or a second poll does not repeat it. */
  key: string;
  title: string;
  body: string;
  /** Where clicking the notification takes the reader. */
  path: string;
}

/** What the latest inbox says that has not been announced yet. */
export function pickNotifications(inbox: Inbox, seen: ReadonlySet<string>): PlannedNotification[] {
  const planned: PlannedNotification[] = [];
  for (const update of inbox.updates ?? []) {
    const title = update.document_title ?? "一本书";
    planned.push(
      update.kind === "readable"
        ? { key: `readable:${update.run_id}`, title: `可以阅读了：${title}`, body: update.text, path: `/books/${update.document_id}/read` }
        : update.kind === "pilot_ready"
          ? { key: `pilot:${update.run_id}`, title: `试译好了：${title}`, body: update.text, path: `/books/${update.document_id}/read` }
          : { key: `failed:${update.run_id}`, title: `翻译失败：${title}`, body: update.text, path: `/books/${update.document_id}` },
    );
  }
  for (const item of inbox.items) {
    planned.push({
      key: `needs:${item.kind}:${item.id}`,
      title: `需要你处理：${item.document_title ?? "一本书"}`,
      body: item.title,
      path: `/books/${item.document_id}`,
    });
  }
  return planned.filter((item) => !seen.has(item.key));
}

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

/** The reader asked for notifications and the browser allows them. */
export function notificationsOn(): boolean {
  if (!notificationsSupported() || window.Notification.permission !== "granted") return false;
  try {
    return window.localStorage.getItem(PREFERENCE_KEY) === "on";
  } catch {
    return false;
  }
}

/** Call from a click: asks the browser once, remembers the answer. */
export async function enableNotifications(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  const permission =
    window.Notification.permission === "granted" ? "granted" : await window.Notification.requestPermission();
  const on = permission === "granted";
  try {
    window.localStorage.setItem(PREFERENCE_KEY, on ? "on" : "off");
  } catch {
    /* asked again next time */
  }
  return on;
}

export function disableNotifications(): void {
  try {
    window.localStorage.setItem(PREFERENCE_KEY, "off");
  } catch {
    /* nothing to undo */
  }
}

export function loadSeen(): Set<string> {
  try {
    return new Set(JSON.parse(window.sessionStorage.getItem(SEEN_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function saveSeen(seen: Set<string>): void {
  try {
    window.sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-500)));
  } catch {
    /* worst case: a repeat after reload */
  }
}

/** Show what is new; returns the keys shown. */
export function announce(inbox: Inbox, seen: Set<string>, open: (path: string) => void): string[] {
  const planned = pickNotifications(inbox, seen);
  for (const item of planned) {
    seen.add(item.key);
    if (!notificationsOn()) continue;
    const notification = new window.Notification(item.title, { body: item.body, tag: item.key });
    notification.onclick = () => {
      window.focus();
      open(item.path);
      notification.close();
    };
  }
  saveSeen(seen);
  return planned.map((item) => item.key);
}
