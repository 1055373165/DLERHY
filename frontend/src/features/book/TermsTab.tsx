import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { queryClient } from "../../app/queryClient";
import { type GlossaryTerm, getBookGuide, listTerms, updateTerm } from "../../lib/api";
import { useBook } from "./BookPage";
import s from "./Tabs.module.css";

function lockedByLabel(lockedBy: string | null | undefined): string {
  if (!lockedBy) return "";
  if (lockedBy === "auto.fast_reading_mode") return "快速阅读模式自动锁定";
  if (lockedBy.startsWith("auto.")) return "按规则自动锁定";
  if (lockedBy.startsWith("human:")) return `${lockedBy.slice("human:".length)} 确认`;
  return lockedBy;
}

/** The book's glossary (including what fast reading mode locked without asking) and its BOOK.md decisions. */
export function TermsTab() {
  const { document_id: documentId } = useBook();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const terms = useQuery({ queryKey: ["terms", documentId], queryFn: () => listTerms(documentId) });
  const guide = useQuery({ queryKey: ["book-guide", documentId], queryFn: () => getBookGuide(documentId) });
  const save = useMutation({
    mutationFn: (payload: { term: GlossaryTerm; lock: boolean; target?: string }) =>
      updateTerm(documentId, {
        source_term: payload.term.source_term,
        target_term: payload.target,
        lock: payload.lock,
        decided_by: "human:reader",
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["terms", documentId], updated);
      void queryClient.invalidateQueries({ queryKey: ["book-guide", documentId] });
      setEditing(null);
    },
  });
  const locked = (terms.data ?? []).filter((term) => term.lock_level === "locked");
  // Rare terms the terminology agent recorded as preferred instead of stopping the run to ask.
  const preferred = (terms.data ?? []).filter((term) => term.lock_level === "preferred");

  return (
    <div className={s.columns}>
      <section>
        <h2 className={s.heading}>术语表 · {locked.length} 个锁定</h2>
        <p className={s.meta}>改动对之后的翻译生效；已经翻译的内容会在下次复核时按新译法检查。</p>
        {terms.isError ? (
          <div className={s.error}>术语表没能加载：{(terms.error as Error).message}</div>
        ) : locked.length === 0 ? (
          <div className={s.empty}>{terms.isLoading ? "加载中…" : "还没有锁定的术语。"}</div>
        ) : (
          <ul className={s.list}>
            {locked.map((term) => (
              <li key={term.source_term} className={s.item}>
                <span>
                  {editing === term.source_term ? (
                    <label>
                      {term.source_term} →{" "}
                      <input aria-label={`${term.source_term} 的译法`} value={draft} onChange={(event) => setDraft(event.target.value)} />
                    </label>
                  ) : (
                    <>
                      {term.source_term} → {term.target_term}
                    </>
                  )}
                  <span className={s.meta}> · {lockedByLabel(term.locked_by)}</span>
                </span>
                <span>
                  {editing === term.source_term ? (
                    <>
                      <button className="btn btn-sm btn-approve" disabled={save.isPending || !draft.trim()} onClick={() => save.mutate({ term, lock: true, target: draft.trim() })}>
                        保存
                      </button>{" "}
                      <button className="btn btn-sm" onClick={() => setEditing(null)}>
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="btn btn-sm"
                        onClick={() => {
                          setEditing(term.source_term);
                          setDraft(term.target_term);
                        }}
                      >
                        修改
                      </button>{" "}
                      <button className="btn btn-sm btn-reject" disabled={save.isPending} onClick={() => save.mutate({ term, lock: false })}>
                        解锁
                      </button>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {save.isError && <div className={s.error}>{save.error instanceof Error ? save.error.message : "保存失败"}</div>}
        {preferred.length > 0 && (
          <>
            <h2 className={s.heading}>首选译法 · {preferred.length} 个</h2>
            <p className={s.meta}>出现次数少的术语：翻译时优先用这个译法，但不强制，也不会停下来问你。需要全书统一时可以锁定。</p>
            <ul className={s.list}>
              {preferred.map((term) => (
                <li key={term.source_term} className={s.item}>
                  <span>
                    {term.source_term} → {term.target_term}
                  </span>
                  <span>
                    <button className="btn btn-sm" disabled={save.isPending} onClick={() => save.mutate({ term, lock: true, target: term.target_term })}>
                      锁定
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      <section>
        <h2 className={s.heading}>翻译决策（BOOK.md）</h2>
        {guide.data ? <pre className={s.markdown}>{guide.data.markdown}</pre> : <div className={s.empty}>{guide.isError ? "没能加载。" : "加载中…"}</div>}
      </section>
    </div>
  );
}
