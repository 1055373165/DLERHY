import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { queryClient } from "../../app/queryClient";
import { parseCheckCopy, parseSeverityLabel } from "../../lib/copy";
import {
  type ParseHealthCheck,
  type ParseHealthSummary,
  getParseHealth,
  reimportDocument,
  runParseHealth,
} from "../../lib/parseHealth";
import { formatDate } from "../../lib/workflow";
import s from "./Tabs.module.css";

function headline(summary: ParseHealthSummary): string {
  const blocking = summary.failed.filter((check) => check.severity === "blocking").length;
  const others = summary.failed.length - blocking;
  if (summary.status === "passed") return `${summary.check_count} 项检查都通过了，版面没有发现问题。`;
  if (blocking) {
    return `有 ${blocking} 项严重问题${others ? `、${others} 项一般问题` : ""}，建议先处理再翻译，否则译文会带着这些问题。`;
  }
  return `有 ${others} 项一般问题，不影响开始翻译。`;
}

/** What a figure-region sample found, when it has no text of its own to show. */
const REGION_KINDS: Record<string, string> = {
  cut_path: "裁剪范围切断了图的线条",
  cut_text: "裁剪范围切断了一行文字",
  caption: "裁剪范围框进了图注",
  page_furniture: "裁剪范围压到了页眉或页脚",
  no_region: "这张图没有识别出区域",
};

function sampleText(text: string): string {
  if (!/^[a-z_,]+$/.test(text)) return text;
  return text
    .split(",")
    .map((kind) => REGION_KINDS[kind] ?? kind)
    .join("；");
}

function CheckRow({ check }: { check: ParseHealthCheck }) {
  const copy = parseCheckCopy(check.name);
  const verdict = check.passed ? "通过" : "未通过";
  const counts = check.allowed > 0 ? `${check.count} 处（可接受 ${check.allowed} 处以内）` : `${check.count} 处`;
  return (
    <li className={s.check} data-passed={check.passed} data-severity={check.severity}>
      <div className={s.checkHead}>
        <span className={s.checkLabel}>{copy.label}</span>
        <span className={s.checkMeta}>
          {parseSeverityLabel(check.severity)} · {counts} · <strong>{verdict}</strong>
        </span>
      </div>
      {!check.passed && (
        <>
          <div className={s.checkMeaning}>{copy.meaning}</div>
          {check.samples.length > 0 && (
            <details className={s.samples}>
              <summary>看看在哪里（{check.samples.length} 个例子）</summary>
              <ul>
                {check.samples.map((sample, index) => (
                  <li key={`${check.name}-${index}`}>
                    {sample.page ? <span className={s.samplePage}>第 {sample.page} 页</span> : null}
                    <span>{sampleText(sample.text)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </li>
  );
}

/** The parse health report: before any translation, what the rules found wrong with how the book was read. */
export function ParseHealthPanel({
  documentId,
  summary,
  canReimport = false,
}: {
  documentId: string;
  summary: ParseHealthSummary | null;
  /** From the overview: nothing translated yet, so the book can be imported again with the current parser. */
  canReimport?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const reimport = useMutation({
    mutationFn: () => reimportDocument(documentId),
    onSuccess: async () => {
      setConfirming(false);
      // Chapters, blocks and the report are all new.
      await queryClient.invalidateQueries();
    },
  });
  const report = useQuery({
    queryKey: ["parse-health", documentId, summary?.checked_at ?? null],
    queryFn: () => getParseHealth(documentId),
    enabled: Boolean(summary),
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => runParseHealth(documentId),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["overview", documentId] });
      await queryClient.invalidateQueries({ queryKey: ["parse-health", documentId] });
    },
  });

  // Failed checks first, worst first; passed ones after, in the report's order.
  const checks = [...(report.data?.checks ?? [])].sort((a, b) => Number(a.passed) - Number(b.passed));

  return (
    <section className={s.health} aria-label="解析体检" data-status={summary?.status ?? "none"}>
      <div className={s.healthHead}>
        <h2 className={s.heading}>解析体检</h2>
        <button className="btn btn-sm" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "体检中…" : summary ? "重新体检" : "立即体检"}
        </button>
      </div>
      {!summary ? (
        <div className={s.empty}>这本书还没有体检报告。体检只按规则检查版面（章节、字符、图片），不花钱，几秒钟完成。</div>
      ) : (
        <>
          <p className={s.healthStatus} role={summary.needs_attention ? "alert" : "status"}>
            {headline(summary)}
            {summary.checked_at && <span className={s.meta}> · 体检于 {formatDate(summary.checked_at)}</span>}
          </p>
          {report.isError && <div className={s.error}>体检报告没能加载：{(report.error as Error).message}</div>}
          {canReimport && summary.needs_attention && (
            <div className={s.reimport}>
              <p>
                这些问题出在解析上，不是你的书有问题。解析规则修复后，这本书还没开始翻译，可以按最新的规则重新导入：
                章节、图片和体检都会重新生成，不花钱，大约一分钟。
              </p>
              {confirming ? (
                <div className={s.reimportActions}>
                  <span>现有的解析结果和体检报告会被替换，确定吗？</span>
                  <button className="btn btn-sm btn-primary" disabled={reimport.isPending} onClick={() => reimport.mutate()}>
                    {reimport.isPending ? "重新导入中…" : "确定重新导入"}
                  </button>
                  <button className="btn btn-sm" disabled={reimport.isPending} onClick={() => setConfirming(false)}>
                    取消
                  </button>
                </div>
              ) : (
                <button className="btn btn-sm" onClick={() => setConfirming(true)}>
                  重新导入
                </button>
              )}
              {reimport.isError && <div className={s.error}>重新导入没能完成：{(reimport.error as Error).message}</div>}
            </div>
          )}
          {checks.length > 0 && (
            <ul className={s.checks}>
              {checks.map((check) => (
                <CheckRow key={check.name} check={check} />
              ))}
            </ul>
          )}
        </>
      )}
      {run.isError && <div className={s.error}>体检没能完成：{(run.error as Error).message}</div>}
    </section>
  );
}
