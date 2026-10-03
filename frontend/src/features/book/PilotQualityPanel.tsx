import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import type { PilotRepairSummary } from "../../lib/overview";
import {
  type PilotQualityFinding,
  type PilotQualityLayer,
  type PilotQualityReport,
  type PilotQualitySummary,
  type QualityScope,
  SEVERITY_LABEL,
  getPilotQuality,
  layerVerdict,
  qualityHeadline,
  runPilotQuality,
} from "../../lib/pilotQuality";
import { formatDate } from "../../lib/workflow";
import s from "./Tabs.module.css";

// A reader wants the few problems that matter, not the whole work list the repair agent gets.
const FINDINGS_SHOWN = 3;

/** Where a finding is: its page and chapter, and a link into the Chinese copy at that chapter. */
function FindingLocation({ documentId, finding, report }: { documentId: string; finding: PilotQualityFinding; report: PilotQualityReport }) {
  const chapter = report.chapters.find((item) => item.chapter_id === finding.location.chapter_id);
  const where = [finding.location.page ? `第 ${finding.location.page} 页` : null, chapter?.title ? `「${chapter.title}」` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className={s.findingWhere}>
      {where && <span className={s.samplePage}>{where}</span>}
      {finding.location.chapter_id && (
        <Link to={`/books/${documentId}/read?format=zh&at=chapter-${encodeURIComponent(finding.location.chapter_id)}`}>在译文中查看</Link>
      )}
    </div>
  );
}

function FindingItem({ documentId, finding, report }: { documentId: string; finding: PilotQualityFinding; report: PilotQualityReport }) {
  const source = typeof finding.evidence.source === "string" ? finding.evidence.source : "";
  const target = typeof finding.evidence.target === "string" ? finding.evidence.target : "";
  return (
    <li className={s.finding} data-severity={finding.severity}>
      <div>
        <span className={s.severityTag} data-severity={finding.severity}>
          {SEVERITY_LABEL[finding.severity]}
        </span>
        {finding.message}
      </div>
      {(source || target) && (
        <div className={s.findingExcerpt}>
          {source && <div lang="en">原文：{source}</div>}
          {target && <div>译文：{target}</div>}
        </div>
      )}
      <FindingLocation documentId={documentId} finding={finding} report={report} />
    </li>
  );
}

function LayerRow({ documentId, layer, report }: { documentId: string; layer: PilotQualityLayer; report: PilotQualityReport }) {
  const titles = new Map(layer.checks.map((check) => [check.check_id, check.title]));
  const failing = layer.checks.filter((check) => check.count > 0);
  const shown = layer.findings.slice(0, FINDINGS_SHOWN);
  const total = failing.reduce((sum, check) => sum + check.count, 0);
  return (
    <li className={s.check} data-passed={layer.status === "passed"} data-severity={layer.status === "blocking" ? "blocking" : "major"}>
      <div className={s.checkHead}>
        <span className={s.checkLabel}>
          {layer.layer} {layer.name}
        </span>
        <span className={s.checkMeta}>
          <strong>{layerVerdict(layer.status, layer.counts)}</strong>
        </span>
      </div>
      {failing.length > 0 && (
        <>
          <div className={s.checkMeaning}>{failing.map((check) => `${titles.get(check.check_id)} ${check.count} 处`).join("；")}</div>
          {shown.length > 0 && (
            // A layer with a blocking problem opens by itself: that is what the reader should see first.
            <details className={s.samples} open={layer.status === "blocking"}>
              <summary>
                看看在哪里（先看最要紧的 {shown.length} 处{total > shown.length ? `，共 ${total} 处` : ""}）
              </summary>
              <ul className={s.findings}>
                {shown.map((finding) => (
                  <FindingItem key={finding.key} documentId={documentId} finding={finding} report={report} />
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </li>
  );
}

const PANEL_TITLE: Record<QualityScope, string> = { pilot: "试译质检", book: "全书质检" };
const PANEL_ANCHOR: Record<QualityScope, string> = { pilot: "pilot-quality", book: "book-quality" };
const EMPTY_TEXT: Record<QualityScope, string> = {
  pilot: "试译完成后会自动质检：按结构、图表、表格、代码与格式、术语、忠实度、排版七层检查试译的两章，只用规则，不花钱。",
  book: "全书翻译完成后会自动质检：用和试译一样的七层检查查每一章，再自动修复能修的问题。",
};

/** What the repair after the QA did, in one line (the server words it). */
function RepairLine({ repair }: { repair: PilotRepairSummary }) {
  return (
    <p className={s.meta} data-testid="quality-repair">
      {repair.text}
      {repair.status === "finished" && repair.questions > 0 && (
        <>
          {" "}
          <a href="#needs-you">去回答</a>
        </>
      )}
    </p>
  );
}

/** The layered QA: seven layers of rule checks on what the reader gets — of the pilot's two chapters,
 *  or (``scope="book"``, 全书质检) of every chapter after the whole-book run, with its repair. */
export function PilotQualityPanel({
  documentId,
  summary,
  scope = "pilot",
  repair = null,
}: {
  documentId: string;
  summary: PilotQualitySummary | null;
  scope?: QualityScope;
  repair?: PilotRepairSummary | null;
}) {
  const queryKey = scope === "book" ? "book-quality" : "pilot-quality";
  const report = useQuery({
    queryKey: [queryKey, documentId, summary?.report_id ?? null],
    queryFn: () => getPilotQuality(documentId, scope),
    enabled: Boolean(summary),
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => runPilotQuality(documentId, scope),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["overview", documentId] });
      await queryClient.invalidateQueries({ queryKey: [queryKey, documentId] });
    },
  });
  const data = report.data;
  const status = summary?.status ?? "none";
  const title = PANEL_TITLE[scope];
  const anchor = PANEL_ANCHOR[scope];
  // Opened from the pilot result's "看质检报告": bring the report into view.
  const { hash } = useLocation();
  const section = useRef<HTMLElement>(null);
  const loaded = Boolean(data);
  useEffect(() => {
    if (hash === `#${anchor}`) section.current?.scrollIntoView?.({ block: "start" });
  }, [hash, loaded, anchor]);

  return (
    <section ref={section} id={anchor} className={s.health} aria-label={title} data-status={status === "error" ? "blocking" : status}>
      <div className={s.healthHead}>
        <h2 className={s.heading}>{title}</h2>
        <button className="btn btn-sm" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "质检中…" : summary ? "重新质检" : "立即质检"}
        </button>
      </div>
      {!summary ? (
        <div className={s.empty}>{EMPTY_TEXT[scope]}</div>
      ) : (
        <>
          <p className={s.healthStatus} role={summary.status === "blocking" ? "alert" : "status"}>
            {qualityHeadline(summary, scope)}
            {summary.checked_at && <span className={s.meta}> · 质检于 {formatDate(summary.checked_at)}</span>}
          </p>
          {repair && <RepairLine repair={repair} />}
          {data?.error && <div className={s.error}>质检出错：{data.error}</div>}
          {report.isError && <div className={s.error}>质检报告没能加载：{(report.error as Error).message}</div>}
          {data && data.layers.length > 0 && (
            <ul className={s.checks}>
              {data.layers.map((layer) => (
                <LayerRow key={layer.layer} documentId={documentId} layer={layer} report={data} />
              ))}
            </ul>
          )}
        </>
      )}
      {run.isError && <div className={s.error}>质检没能完成：{(run.error as Error).message}</div>}
    </section>
  );
}

/** Each layer's verdict as a chip: the pilot result's line of the QA (the report itself is on 进度). */
export function LayerChips({ summary }: { summary: PilotQualitySummary }) {
  return (
    <ul className={s.layerChips} aria-label="试译质检结果">
      {summary.layers.map((layer) => (
        <li key={layer.layer} className={s.layerChip} data-status={layer.status}>
          {layer.name}
          <span>{layer.status === "passed" ? "通过" : layer.status === "blocking" ? `严重 ${layer.counts.blocking}` : `一般 ${layer.counts.major}`}</span>
        </li>
      ))}
    </ul>
  );
}
