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
  runs: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5,2.5 13,8 5,13.5" />
    </svg>
  ),
  ship: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 10.5l1-7h10l1 7" />
      <rect x="1" y="10.5" width="14" height="3" rx="1" />
      <path d="M6.5 3.5V1.5h3v2" />
    </svg>
  ),
  lib: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="1.5" width="12" height="3" rx="1" />
      <rect x="3" y="6.5" width="10" height="3" rx="1" />
      <rect x="4" y="11.5" width="8" height="3" rx="1" />
    </svg>
  ),
  issues: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5l6.5 12h-13z" />
      <path d="M8 6v3.5" />
      <path d="M8 11.5v.01" />
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

function pageName(pathname: string): string {
  if (pathname.startsWith("/books/")) return "这本书";
  if (pathname.startsWith("/library")) return "书库";
  if (pathname.startsWith("/inbox")) return "需要你处理";
  if (pathname.startsWith("/settings")) return "设置";
  return "首页";
}

/* Where 「返回」 goes from here. A book belongs to the shelf; everything else to the home page. */
function backLink(pathname: string): { to: string; label: string } | null {
  if (pathname.startsWith("/books/")) return { to: "/library", label: "返回书库" };
  if (pathname === "/" ) return null;
  return { to: "/", label: "返回首页" };
}

const APP_TITLE = "Book Agent";

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
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


  const back = backLink(location.pathname);

  return (
    <div className={s.shell} data-nav-open={navOpen}>
      {/* First stop of a keyboard walk: jump past the navigation. */}
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>

      {/* Mobile backdrop */}
      <button
        className={s.backdrop}
        type="button"
        aria-label="关闭导航"
        onClick={() => setNavOpen(false)}
      />

      {/* ── Sidebar ── */}
      <aside className={s.sidebar} aria-label="侧边栏">
        <NavLink to="/" className={s.logo} onClick={() => setNavOpen(false)}>
          <span className={s.logoMark} aria-hidden="true">
            B
          </span>
          <span className={s.logoText}>Book Agent</span>
        </NavLink>

        <nav className={s.nav} aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `${s.navItem} ${isActive ? s.navActive : ""}`
              }
              onClick={() => setNavOpen(false)}
            >
              <span className={s.navIcon} aria-hidden="true">
                {item.icon}
              </span>
              <span className={s.navLabel}>{item.label}</span>
              {item.to === "/inbox" && pendingCount > 0 && (
                <span className={s.navBadge} aria-label={`${pendingCount} 件事等你处理`}>
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className={s.sidebarFooter}>
          <BudgetMeter />
          <div className={s.healthRow}>
            <span
              className={s.healthDot}
              data-status={healthLoading ? "check" : health?.status === "ok" ? "ok" : "down"}
            />
            <span className={s.healthLabel}>
              {healthLoading ? "检查中" : health?.status === "ok" ? "服务在线" : "服务离线"}
            </span>
          </div>
        </div>
      </aside>

      {/* ── Main Content ── */}
      <div className={s.main}>
        <header className={s.topbar}>
          <button
            className={s.mobileToggle}
            type="button"
            onClick={() => setNavOpen((o) => !o)}
            aria-label="打开或关闭导航"
          >
            {navOpen ? "\u2715" : "\u2630"}
          </button>
          <div className={s.topbarInner}>
            <div className={s.topbarLeft}>
              {back && (
                <NavLink to={back.to} className={s.backLink}>
                  <span aria-hidden="true">←</span> {back.label}
                </NavLink>
              )}
              <span className={s.pageName}>{pageName(location.pathname)}</span>
            </div>
            {pendingCount > 0 && !location.pathname.startsWith("/inbox") && (
              <NavLink to="/inbox" className={s.pendingNotice} role="status">
                {onlyQuestions ? `试译有 ${pendingCount} 个问题等你回答 →` : `有 ${pendingCount} 件事等你处理，处理后翻译会继续 →`}
              </NavLink>
            )}
          </div>
        </header>

        <main className={s.content} id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
