import { Link } from "react-router-dom";

import { issueTypeCopy } from "../../lib/copy";
import { type BookOverview, usd } from "../../lib/overview";
import s from "./BookPage.module.css";
import { PilotQualityStrip } from "./PilotQualityPanel";

/** The pilot's chapter titles, in reading order. */
export function pilotChapterTitles(book: BookOverview): string[] {
  return (book.pilot?.chapters ?? []).map((chapter) => chapter.title ?? `第 ${chapter.ordinal} 部分`);
}

/** After the free pilot: what was translated, what it left, and the choice to pay for the whole book.
 *  Everything here comes from the overview (book_overview.py); nothing is derived on this side. */
export function PilotResult({
  book,
  onReader,
  pending,
  declined,
  onStartBook,
  onDecline,
}: {
  book: BookOverview;
  onReader: boolean;
  pending: boolean;
  declined: boolean;
  onStartBook: () => void;
  onDecline: () => void;
}) {
  const pilot = book.pilot;
  if (!pilot) return null;
  const estimate = book.cost?.max_charge_usd ?? null;
  const startLabel = estimate !== null ? `翻译全书（约 ${usd(estimate)}）` : "翻译全书";
  const problems = pilot.problems;
  const titles = pilotChapterTitles(book);

  if (declined) {
    return (
      <div className={s.row}>
        <span className={s.note}>暂不翻译全书。想好了随时可以开始，试译过的两章不再收费。</span>
        <button className="btn btn-sm btn-primary" disabled={pending} onClick={onStartBook}>
          {startLabel}
        </button>
      </div>
    );
  }

  return (
    <div className={s.pilot} aria-label="试译结果" role="group">
      <p className={s.pilotLine}>
        <span className={s.free}>免费试译</span>
        {titles.length ? `：${titles.map((title) => `「${title}」`).join("、")}` : ""}
        。费用由我们承担，不从你的余额里扣。
      </p>
      {problems && problems.open > 0 ? (
        <div className={s.pilotProblems}>
          <div className={s.pilotProblemsTitle}>
            还有 {problems.open} 个问题{problems.blocking > 0 ? `，其中 ${problems.blocking} 个严重` : ""}：
          </div>
          <ul className={s.pilotProblemList}>
            {problems.by_type.map((entry) => (
              <li key={entry.type}>
                {issueTypeCopy(entry.type).label} {entry.count} 处
                {entry.blocking > 0 ? `（${entry.blocking} 处严重）` : ""}
              </li>
            ))}
          </ul>
          <Link to={`/books/${book.document_id}/issues`} className={s.note}>
            逐条看这些问题
          </Link>
        </div>
      ) : (
        <p className={s.note}>自动复核和模型审校没有留下未解决的问题。</p>
      )}
      {pilot.quality && <PilotQualityStrip documentId={book.document_id} summary={pilot.quality} />}
      {book.parse_health?.needs_attention && (
        <div className={s.attention}>
          解析体检发现了严重问题，全书会带着这些问题。可以先 <Link to={`/books/${book.document_id}/progress`}>看看体检报告</Link>。
        </div>
      )}
      <div className={s.row}>
        <button className="btn btn-primary" disabled={pending} onClick={onStartBook}>
          {startLabel}
        </button>
        {!onReader && pilot.readable && (
          <Link className="btn" to={`/books/${book.document_id}/read`}>
            阅读试译
          </Link>
        )}
        <button className="btn" disabled={pending} onClick={onDecline}>
          暂不翻译
        </button>
      </div>
    </div>
  );
}
