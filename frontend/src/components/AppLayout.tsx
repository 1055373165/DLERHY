import { BudgetMeter } from "./BudgetMeter";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { getHealth } from "../lib/api";
import { announce, loadSeen } from "../lib/notifications";
import { getInbox } from "../lib/overview";
import s from "./AppLayout.module.css";

/* ── Inline SVG Icons (16×16) ── */
const Icons = {
  work: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="1.5" y="1.5" width="5" height="5" rx="1" />
      <rect x="9.5" y="1.5" width="5" height="5" rx="1" />
      <rect x="1.5" y="9.5" width="5" height="5" rx="1" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
    </svg>
  ),
  lib: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="1.5" width="12" height="3" rx="1" />
      <rect x="3" y="6.5" width="10" height="3" rx="1" />
      <rect x="4" y="11.5" width="8" height="3" rx="1" />
    </svg>
  ),
  settings: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="2.5" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
    </svg>
  ),
  approve: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6.5" />
      <path d="M5 8.5l2 2 4-4.5" />
    </svg>
  ),
} as const;

const NAV_ITEMS = [
  { to: "/", label: "首页", icon: Icons.work },
  { to: "/library", label: "书库", icon: Icons.lib },
  { to: "/inbox", label: "需要你处理", icon: Icons.approve },
  { to: "/settings", label: "设置", icon: Icons.settings },
] as const;

const bookMark = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 6C9 4 6 4 3 5v13c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1v13" />
  </svg>
);

const APP_TITLE = "Book Agent";

export function AppLayout() {
  const location = useLocation();
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 30_000, retry: false });
  const health = healthQuery.data;
  const healthLoading = healthQuery.isLoading;
  // Across every book: approvals and pauses only a person can fix (GET /inbox).
  // Endings before this page opened are not news.
  const [openedAt] = useState(() => new Date().toISOString());
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => getInbox(openedAt), refetchInterval: 30_000, retry: false });
  const pendingCount = inbox.data?.count ?? 0;
  // A pilot repair's question holds nothing up: the pilot is already there to read.
  const onlyQuestions = pendingCount > 0 && (inbox.data?.items ?? []).every((item) => item.kind === "question");
  const navigate = useNavigate();
  const seen = useRef<Set<string>>(loadSeen());
  useEffect(() => {
    if (inbox.data) announce(inbox.data, seen.current, (path) => navigate(path));
  }, [inbox.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // The tab title says it too, so a reader on another tab sees that the book is waiting on them.
    document.title = pendingCount > 0 ? `(${pendingCount}) 等你处理 · ${APP_TITLE}` : APP_TITLE;
    return () => {
      document.title = APP_TITLE;
    };
  }, [pendingCount]);

  const bookPath = location.pathname.startsWith("/books/");
  const reading = bookPath && location.pathname.endsWith("/read");
  const page = NAV_ITEMS.find((item) => item.to === location.pathname)?.label;

  return (
    <div className={s.shell}>
      <a className="skip-link" href="#main">跳到主要内容</a>

      <aside className={s.sidebar} aria-label="侧边栏">
        <NavLink to="/" className={s.logo}>
          <span className={s.logoMark} aria-hidden="true">{bookMark}</span>
          <span className={s.logoText}>Book Agent</span>
        </NavLink>
        <nav className={s.nav} aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => `${s.navItem} ${isActive || (item.to === "/library" && bookPath) ? s.navActive : ""}`}
            >
              <span className={s.navIcon} aria-hidden="true">{item.icon}</span>
              <span className={s.navLabel}>{item.label}</span>
              {item.to === "/inbox" && pendingCount > 0 && (
                <span className={s.navBadge} aria-label={`${pendingCount} 件事等你处理`}>
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className={s.sidebarFooter}><BudgetMeter /></div>
      </aside>

      <div className={s.main}>
        <header className={s.topbar}>
          <div className={s.topbarInner}>
            <div className={s.topbarLeft}>
              <NavLink to="/" className={`${s.logo} ${s.mobileLogo}`}>
                <span className={s.logoMark} aria-hidden="true">{bookMark}</span>
                <span className={s.logoText}>Book Agent</span>
              </NavLink>
              {bookPath && !reading ? (
                <NavLink to="/library" className={s.backLink}><span aria-hidden="true">←</span> 返回书库</NavLink>
              ) : <span className={s.pageName}>{reading ? "阅读" : page}</span>}
            </div>
            <div className={s.topbarRight}>
              {pendingCount > 0 && !location.pathname.startsWith("/inbox") && (
                <NavLink to="/inbox" className={s.pendingNotice} role="status">
                  {onlyQuestions ? `试译有 ${pendingCount} 个问题等你回答 →` : `有 ${pendingCount} 件事等你处理，处理后翻译会继续 →`}
                </NavLink>
              )}
              <div className={s.healthRow}>
                <span className={s.healthDot} data-status={healthLoading ? "check" : health?.status === "ok" ? "ok" : "down"} />
                {healthLoading ? "检查中" : health?.status === "ok" ? "服务在线" : "服务离线"}
              </div>
            </div>
          </div>
        </header>

        <main className={s.content} id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
