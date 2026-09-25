import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { queryClient } from "../../app/queryClient";
import { rememberBook } from "../../lib/currentBook";
import { NotifyMe } from "../../components/NotifyMe";
import { PilotResult, pilotChapterTitles } from "./PilotResult";
import { StartDialog, type StartMode } from "./StartDialog";
import { apiImageUrl, createRun, decideApproval, pauseRun, resumeRun, retryRun, startPilot } from "../../lib/api";
import { decideAutoStart, decideBookStart } from "../../lib/autoStart";
import { type BookOverview, type NeedsYouItem, getOverview, usd } from "../../lib/overview";
import { autoStartCapUsd, fastReadingPreferred } from "../../lib/preferences";
import { useRunEventStream } from "../../lib/runEvents";
import s from "./BookPage.module.css";

const ACTIVE = new Set(["queued", "running", "draining"]);
const ACTOR = { actor_id: "reader-ui" };

/* What a reader opens a book for comes first and stays on screen at any width.
   The three working tabs live behind 更多, where they are still one click away. */
const TABS = [
  { to: "read", label: "阅读" },
  { to: "progress", label: "进度" },
  { to: "issues", label: "问题" },
] as const;

const MORE_TABS = [
  { to: "terms", label: "术语与决策" },
  { to: "log", label: "运行记录" },
  { to: "chapters", label: "逐章审校" },
] as const;

export function useBookOverview(documentId: string) {
  return useQuery({
    queryKey: ["overview", documentId],
    queryFn: () => getOverview(documentId),
    enabled: Boolean(documentId),
    retry: false,
    // Fast while something moves; the run stream (when there is one) refreshes it too.
    refetchInterval: (query) => {
      const data = query.state.data as BookOverview | undefined;
      if (!data) return false;
      return ACTIVE.has(data.translation.state) || data.activity.exporting.length ? 3000 : false;
    },
  });
}

export function useBook(): BookOverview {
  return useOutletContext<BookOverview>();
}

const PILOT_DECLINED_KEY = "book-agent.pilot-declined.";

/** 暂不翻译 after the pilot: a per-browser note, so the choice card folds away on the next visit too. */
function pilotDeclined(documentId: string): boolean {
  try {
    return window.localStorage.getItem(PILOT_DECLINED_KEY + documentId) === "1";
  } catch {
    return false;
  }
}

function rememberPilotDeclined(documentId: string): void {
  try {
    window.localStorage.setItem(PILOT_DECLINED_KEY + documentId, "1");
  } catch {
    /* folds away for this visit only */
  }
}

async function refresh(documentId: string): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["overview", documentId] });
  await queryClient.invalidateQueries({ queryKey: ["inbox"] });
}

/** One book: where it stands, what it needs from you, and its tabs (reading first). */
export function BookPage() {
  const { documentId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const overview = useBookOverview(documentId);
  const data = overview.data;
  useRunEventStream(data?.translation.run_id ?? null, Boolean(data && ACTIVE.has(data.translation.state)));

  // Old addresses (/runs, /issues...) land on the last book opened.
  useEffect(() => {
    rememberBook(documentId);
  }, [documentId]);

  const [dialog, setDialog] = useState<StartMode | null>(null);
  const [declined, setDeclined] = useState(() => pilotDeclined(documentId));
  const act = useMutation({
    mutationFn: async (kind: "start" | "autostart" | "pilot" | "book" | "resume" | "retry" | "stop") => {
      const runId = data?.translation.run_id ?? "";
      if (kind === "pilot") return startPilot(documentId, { fastReading: fastReadingPreferred() });
      if (kind === "start" || kind === "autostart" || kind === "book") {
        const created = await createRun(documentId, { fastReading: fastReadingPreferred(), autoStarted: kind === "autostart" });
        const note = kind === "autostart" ? "auto-started within the cost cap" : kind === "book" ? "whole book after the free pilot" : "started by the reader";
        return resumeRun(created.run_id, { ...ACTOR, note });
      }
      if (kind === "resume") return resumeRun(runId, { ...ACTOR, note: "resumed by the reader" });
      if (kind === "retry") return retryRun(runId, { ...ACTOR, note: "retried by the reader" });
      return pauseRun(runId, { ...ACTOR, note: "stopped by the reader" });
    },
    onSuccess: (_result, kind) => {
      setDialog(null);
      // Straight into this book's progress, where the run tells its story.
      if (kind === "start" || kind === "autostart" || kind === "pilot" || kind === "book") {
        navigate(`/books/${documentId}/progress`, { replace: true });
      }
    },
    onSettled: () => refresh(documentId),
  });

  // Right after import: start by itself when the most it may cost is within the reader's cap.
  const justImported = Boolean((location.state as { justImported?: boolean } | null)?.justImported);
  const autoStartTried = useRef(false);
  const cap = autoStartCapUsd();
  const decision = data ? decideAutoStart(data, cap) : null;
  useEffect(() => {
    if (!justImported || !data || autoStartTried.current) return;
    autoStartTried.current = true;
    // Only a book that has never started qualifies (decideAutoStart), so a reload that keeps the
    // flag, or coming back later, cannot start anything twice.
    if (decision?.auto) act.mutate("autostart");
  }, [justImported, data]); // eslint-disable-line react-hooks/exhaustive-deps

  // When the pilot finishes while the reader is on this book, open its result: the two chapters.
  const pilotState = data?.pilot?.state;
  const previousPilotState = useRef(pilotState);
  useEffect(() => {
    const before = previousPilotState.current;
    previousPilotState.current = pilotState;
    if (pilotState === "ready" && before && ACTIVE.has(before) && data?.pilot?.readable) {
      navigate(`/books/${documentId}/read`, { replace: true });
    }
  }, [pilotState]); // eslint-disable-line react-hooks/exhaustive-deps

  if (overview.isError) {
    return (
      <div className={s.error} role="alert">
        这本书没能打开：{(overview.error as Error).message}
        <div className={s.row}>
          <button className="btn btn-sm" onClick={() => void overview.refetch()}>
            重试
          </button>
          <Link className="btn btn-sm" to="/">
            回到首页
          </Link>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className={s.loading}>正在打开这本书…</div>;
  }

  const state = data.translation.state;
  const progress = data.translation.progress;
  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
  const primary = data.primary_action;
  const estimate = data.cost?.max_charge_usd ?? null;
  const showStartPanel =
    (primary.kind === "start" || primary.kind === "pilot") && !act.isPending && !(justImported && decision?.auto);
  const pilotReady = primary.kind === "start_book";
  const onPilot = data.translation.scope === "pilot";

  // 翻译全书 after the pilot: within the auto-start cap it starts at once, above it the dialog asks.
  function startBook() {
    if (!data) return;
    if (decideBookStart(data, cap).auto) act.mutate("book");
    else setDialog("rest");
  }

  const bookHeader = (
    <header className={s.header}>
      <h1 className={s.title}>{data.title ?? "未命名"}</h1>
      {data.source_url && (
        <a className={s.source} href={data.source_url} target="_blank" rel="noreferrer noopener">
          原网页 ↗
        </a>
      )}
    </header>
  );

  const bookTabs = (
    <nav className={s.tabs} aria-label="这本书">
      {TABS.map((tab) => (
        <NavLink key={tab.to} to={tab.to} className={({ isActive }) => `${s.tab} ${isActive ? s.tabActive : ""}`}>
          {tab.label}
        </NavLink>
      ))}
      <MoreTabs />
    </nav>
  );

  const onReader = location.pathname.endsWith("/read");
  const showProgressBar = (ACTIVE.has(state) || (state === "paused" && progress.total > 0)) && progress.total > 0;
  // What the card would put under the headline. With none of it, the card is a sentence in a box:
  // on the 阅读 tab of a finished book that is a whole phone screen spent saying "it is ready".
  const hasSomethingToDo =
    showStartPanel ||
    pilotReady ||
    showProgressBar ||
    act.isPending ||
    act.isError ||
    primary.kind === "resume" ||
    primary.kind === "retry" ||
    primary.kind === "stop" ||
    (primary.kind === "read" && !onReader) ||
    (data.translation.stop_reason_text && data.translation.stop_reason_text !== data.headline) ||
    (ACTIVE.has(state) && data.translation.auto_started);

  if (!hasSomethingToDo) {
    return (
      <div className={s.layout}>
        {bookHeader}
        <p className={s.quietStatus} role="status">
          {data.headline}
        </p>
        {data.needs_you.length > 0 && <NeedsYou documentId={documentId} items={data.needs_you} />}
        {bookTabs}
        <Outlet context={data} />
      </div>
    );
  }

  return (
    <div className={s.layout}>
      {bookHeader}

      <section className={s.banner} data-state={state} aria-label="状态">
        <div className={s.headline} role="status">
          {data.headline}
        </div>
        {showProgressBar && (
          <div className={s.progress} aria-label={`进度 ${pct}%`}>
            <span className={s.progressFill} style={{ width: `${pct}%` }} />
          </div>
        )}
        {ACTIVE.has(state) && onPilot && (
          <div className={s.note}>试译免费，费用由我们承担。试译好了，这里会打开试译的两章，再由你决定是否翻译全书。</div>
        )}
        {pilotReady && (
          <PilotResult
            book={data}
            onReader={onReader}
            pending={act.isPending}
            declined={declined}
            onStartBook={startBook}
            onDecline={() => {
              rememberPilotDeclined(documentId);
              setDeclined(true);
            }}
          />
        )}
        {ACTIVE.has(state) && data.translation.auto_started && (
          <div className={s.note}>
            已自动开始{estimate !== null ? `：预计最多 ${usd(estimate)}，在你的自动开始上限 ${usd(cap)} 以内` : ""}。不想继续可以停止。
          </div>
        )}
        {/* The headline is often the stop reason itself: say it once. */}
        {data.translation.stop_reason_text && state !== "paused" && data.translation.stop_reason_text !== data.headline && (
          <div className={s.note}>{data.translation.stop_reason_text}</div>
        )}
        <div className={s.row}>
          {primary.kind === "read" && !onReader && (
            <Link className="btn btn-primary" to={`/books/${documentId}/read`}>
              {primary.label}
            </Link>
          )}
          {(primary.kind === "resume" || primary.kind === "retry" || primary.kind === "stop") && (
            <button className={primary.kind === "stop" ? "btn" : "btn btn-primary"} disabled={act.isPending} onClick={() => act.mutate(primary.kind as "resume" | "retry" | "stop")}>
              {primary.kind === "stop" ? (onPilot ? "停止试译" : "停止翻译") : primary.label}
            </button>
          )}
          {primary.kind === "read" && ACTIVE.has(state) && (
            <button className="btn" disabled={act.isPending} onClick={() => act.mutate("stop")}>
              停止翻译
            </button>
          )}
          {(ACTIVE.has(state) || act.isPending) && <NotifyMe />}
          {act.isError && <span className={s.errorText}>{act.error instanceof Error ? act.error.message : "操作失败"}</span>}
        </div>

        {showStartPanel && primary.kind === "pilot" && (
          <div className={s.start}>
            <button className="btn btn-primary" disabled={act.isPending} onClick={() => setDialog("pilot")}>
              免费试译前两章
            </button>
            <div className={s.note}>
              先免费翻译前两章{pilotChapterTitles(data).length ? `（${pilotChapterTitles(data).map((title) => `「${title}」`).join("、")}）` : ""}
              ，走完整流程，看过效果再决定是否翻译全书。
            </div>
            {data.parse_health?.needs_attention && (
              <div className={s.attention}>
                解析体检发现了严重问题，译文会带着这些问题。建议先 <Link to={`/books/${documentId}/progress`}>看看体检报告</Link> 再试译。
              </div>
            )}
          </div>
        )}

        {showStartPanel && primary.kind === "start" && (
          <div className={s.start}>
            <button className="btn btn-primary" disabled={act.isPending} onClick={() => setDialog("book")}>
              {estimate !== null ? `开始翻译（约 ${usd(estimate)}）` : "开始翻译"}
            </button>
            {data.pilot?.unavailable_text && <div className={s.note}>{data.pilot.unavailable_text}</div>}
            {decision && !decision.auto && decision.reason && (
              <div className={s.note}>
                {decision.reason} <Link to="/settings">修改自动开始上限</Link>
              </div>
            )}
            {/* Not a gate yet: the reader may still start, but should know first. */}
            {data.parse_health?.needs_attention && (
              <div className={s.attention}>
                解析体检发现了严重问题，译文会带着这些问题。建议先 <Link to={`/books/${documentId}/progress`}>看看体检报告</Link> 再开始翻译。
              </div>
            )}
          </div>
        )}
      </section>

      {dialog && (
        <StartDialog
          documentId={documentId}
          mode={dialog}
          maxChargeUsd={estimate}
          pending={act.isPending}
          pilotChapters={pilotChapterTitles(data)}
          onCancel={() => setDialog(null)}
          onStart={() => act.mutate(dialog === "pilot" ? "pilot" : dialog === "rest" ? "book" : "start")}
          onSkipPilot={dialog === "pilot" ? () => act.mutate("start") : undefined}
        />
      )}

      {data.needs_you.length > 0 && <NeedsYou documentId={documentId} items={data.needs_you} />}

      {bookTabs}

      <Outlet context={data} />
    </div>
  );
}

/** 更多: the tabs a reader rarely needs, one click away and out of the way of the ones they do. */
function MoreTabs() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const active = MORE_TABS.find((tab) => location.pathname.endsWith(`/${tab.to}`));

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    const onClick = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <div className={s.more} ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        className={`${s.tab} ${active ? s.tabActive : ""}`}
        aria-expanded={open}
        aria-controls="more-tabs"
        onClick={() => setOpen((was) => !was)}
      >
        {active ? `更多 · ${active.label}` : "更多"}
        <span aria-hidden="true"> ▾</span>
      </button>
      {open && (
        <div className={s.moreMenu} id="more-tabs">
          {MORE_TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) => `${s.moreItem} ${isActive ? s.moreItemActive : ""}`}
              onClick={() => setOpen(false)}
            >
              {tab.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

function NeedsYou({ documentId, items }: { documentId: string; items: NeedsYouItem[] }) {
  const decide = useMutation({
    mutationFn: ({ id, approved }: { id: string; approved: boolean }) => decideApproval(id, approved, { decided_by: "human:reader" }),
    onSettled: () => refresh(documentId),
  });
  const resume = useMutation({
    mutationFn: (runId: string) => resumeRun(runId, { ...ACTOR, note: "resumed after fixing the cause" }),
    onSettled: () => refresh(documentId),
  });
  return (
    <section className={s.needs} aria-label="需要你处理" id="needs-you">
      <h2 className={s.needsTitle}>需要你处理</h2>
      {items.map((item) => (
        <div key={item.id} className={s.card}>
          <div className={s.cardBody}>
            <div className={s.cardTitle}>{item.title}</div>
            {item.detail && <div className={s.note}>{item.detail}</div>}
            {(item.images ?? []).length > 0 && <EvidenceImages images={item.images ?? []} />}
          </div>
          <div className={s.row}>
            {item.action.kind === "approve" && (
              <>
                <button className="btn btn-sm btn-approve" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: true })}>
                  {item.action.approve_label ?? "批准"}
                </button>
                <button className="btn btn-sm btn-reject" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, approved: false })}>
                  {item.action.reject_label ?? "拒绝"}
                </button>
              </>
            )}
            {item.kind === "question" && item.action.target && (
              <Link className="btn btn-sm" to={item.action.target}>
                {item.action.label ?? "看质检报告"}
              </Link>
            )}
            {item.action.kind === "link" && item.action.target && (
              <Link className="btn btn-sm" to={item.action.target}>
                {item.action.label ?? "去处理"}
              </Link>
            )}
            {item.kind === "paused" && (
              <button className="btn btn-sm" disabled={resume.isPending} onClick={() => resume.mutate(item.id)}>
                继续翻译
              </button>
            )}
          </div>
        </div>
      ))}
      {(decide.isError || resume.isError) && <div className={s.errorText}>操作失败，请重试。</div>}
    </section>
  );
}

/** The page a question is about, with the problem outlined: a small picture that opens full size. */
export function EvidenceImages({ images }: { images: string[] }) {
  return (
    <div className={s.evidence}>
      {images.map((path) => (
        <a key={path} href={apiImageUrl(path)} target="_blank" rel="noreferrer" className={s.evidenceLink}>
          <img src={apiImageUrl(path)} alt="原书这一页，问题处用红框标出" className={s.evidenceImage} loading="lazy" />
        </a>
      ))}
    </div>
  );
}

/** /books/:id: a readable book opens on reading (a finished pilot on its two chapters), anything else on its progress. */
export function BookIndex() {
  const data = useBook();
  const pilotToRead = data.pilot?.state === "ready" && data.pilot.readable;
  return <Navigate to={data.readable.ready || pilotToRead ? "read" : "progress"} replace />;
}
