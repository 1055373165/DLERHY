import type { BookOverview } from "../lib/overview";

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const notFound = () => json({ detail: "not found" }, 404);

/** An overview as the server sends it; override the parts a test is about. */
export function makeOverview(documentId: string, overrides: Partial<BookOverview> = {}): BookOverview {
  const base: BookOverview = {
    document_id: documentId,
    title: "Why Momentum Matters",
    source_type: "web",
    source_url: "https://example.com/momentum",
    headline: "还没开始翻译。",
    primary_action: { kind: "start", label: "开始翻译" },
    readable: { ready: false, formats: ["web_html", "merged_html", "bilingual_html", "merged_markdown"] },
    translation: { state: "never_started", run_id: null, stop_reason: null, stop_reason_text: null, steps: [], progress: { done: 0, total: 7 } },
    activity: { exporting: [] },
    needs_you: [],
    quality: { open_issues: 0, blocking: 0 },
    cost: { estimate_usd_range: null, max_charge_usd: null, prepaid: false, spent_usd: 0 },
  };
  return { ...base, ...overrides, translation: { ...base.translation, ...(overrides.translation ?? {}) } };
}
