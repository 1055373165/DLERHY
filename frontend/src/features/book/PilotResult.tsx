import { Link } from "react-router-dom";

import { type BookOverview, type PilotOverview, usd } from "../../lib/overview";
import { type PilotQualitySummary, SEVERITY_LABEL } from "../../lib/pilotQuality";
import s from "./BookPage.module.css";
import { LayerChips } from "./PilotQualityPanel";

/** The pilot's chapter titles, in reading order. */
export function pilotChapterTitles(book: BookOverview): string[] {
  return (book.pilot?.chapters ?? []).map((chapter) => chapter.title ?? `第 ${chapter.ordinal} 部分`);
}

/** One sentence for the pilot as it is now: after the repair, by the latest QA. */
export function pilotVerdict(quality: PilotQualitySummary | null | undefined, pilot: PilotOverview): string {
  if (quality && quality.status !== "error") {
    if (quality.status === "blocking") {
      const layers = quality.layers.filter((layer) => layer.status === "blocking").map((layer) => layer.name);
      return `还有 ${quality.counts.blocking} 处${SEVERITY_LABEL.blocking}问题（${layers.join("、")}），试译照常可以读。`;
    }
    if (quality.status === "warnings") return `没有严重问题；还有 ${quality.counts.major} 处一般问题，不影响阅读。`;
    return `七层质检都通过了${quality.counts.minor ? `，只有 ${quality.counts.minor} 处轻微瑕疵` : ""}。`;
  }
  const problems = pilot.problems;
  if (problems && problems.blocking > 0) return `还有 ${problems.blocking} 个严重问题，试译照常可以读。`;
  if (problems && problems.open > 0) return `没有严重问题；还有 ${problems.open} 个问题，不影响阅读。`;
  return "自动复核和模型审校没有留下未解决的问题。";
}

/** What the pilot left, in one place: the verdict, each layer, what was repaired, what waits on the reader. */
function PilotSummary({ book }: { book: BookOverview }) {
  const pilot = book.pilot as PilotOverview;
  const quality = pilot.quality;
  const repair = pilot.repair;
  const problems = pilot.problems;
  const status = quality && quality.status !== "error" ? quality.status : problems?.blocking ? "blocking" : problems?.open ? "warnings" : "passed";
  const repairing = repair?.status === "running";
  const repaired = repair && repair.status === "finished" && repair.repaired > 0;
  const kinds = (repair?.repaired_kinds ?? []).slice(0, 3);
  const questions = repair?.questions ?? 0;
  return (
    <section className={s.pilotSummary} data-status={status} aria-label="试译质量">
      <p className={s.pilotVerdict}>{pilotVerdict(quality, pilot)}</p>
      {quality && quality.status !== "error" && <LayerChips summary={quality} />}
      {(repairing || repaired) && (
        <p className={s.pilotRepair}>
          {repairing ? (
            "正在自动修复质检发现的问题…"
          ) : (
            <>
              自动修复了 <strong>{repair.repaired}</strong> 个问题
              {kinds.length > 0 && <span className={s.pilotRepairKinds}>：{kinds.join("、")}</span>}
            </>
          )}
        </p>
      )}
      {questions > 0 && (
        <p className={s.pilotAsk}>
          有 {questions} 个问题自动修复没能解决，需要你回答：<a href="#needs-you">去回答</a>
        </p>
      )}
      <nav className={s.pilotLinks} aria-label="试译详情">
        <Link to={`/books/${book.document_id}/progress#pilot-quality`}>看质检报告</Link>
        {problems && problems.open > 0 && (
          <Link to={`/books/${book.document_id}/issues`}>
            审校意见 {problems.open} 条{problems.blocking > 0 ? `（${problems.blocking} 条严重）` : ""}
          </Link>
        )}
        {book.parse_health?.needs_attention && <Link to={`/books/${book.document_id}/progress`}>解析体检有严重问题</Link>}
      </nav>
    </section>
  );
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
      <PilotSummary book={book} />
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
