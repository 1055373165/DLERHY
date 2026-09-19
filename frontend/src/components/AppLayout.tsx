import { ApiKeySetting } from "./ApiKeySetting";
import { BudgetMeter } from "./BudgetMeter";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { SERVICE_LINKS, listApprovals } from "../lib/api";
import { preferredTitle } from "../lib/workflow";
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
  { to: "/", label: "工作台", icon: Icons.work },
  { to: "/library", label: "书库", icon: Icons.lib },
  { to: "/approvals", label: "审批", icon: Icons.approve },
  { to: "/runs", label: "运行", icon: Icons.runs },
  { to: "/deliverables", label: "交付", icon: Icons.ship },
  { to: "/issues", label: "问题", icon: Icons.issues },
  { to: "/providers", label: "服务商", icon: Icons.settings },
] as const;

const APP_TITLE = "Book Agent";

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const { currentDocument, health, healthLoading } = useWorkspace();
  const documentId = currentDocument?.document_id ?? null;
  // Same query key as the approvals page, so deciding there updates the count here at once.
  const pendingApprovals = useQuery({
    queryKey: ["approvals", documentId],
    queryFn: () => listApprovals(documentId as string, "pending"),
    enabled: Boolean(documentId),
    refetchInterval: 10_000,
  });
  const pendingCount = pendingApprovals.data?.length ?? 0;

  const docTitle = currentDocument ? preferredTitle(currentDocument) : null;

  useEffect(() => {
    // The tab title says it too, so a reader on another tab sees that the book is waiting on them.
    document.title = pendingCount > 0 ? `(${pendingCount}) 等你审批 · ${APP_TITLE}` : APP_TITLE;
    return () => {
      document.title = APP_TITLE;
    };
  }, [pendingCount]);

  const activeNav =
    NAV_ITEMS.find((n) =>
      n.to === "/" ? location.pathname === "/" : location.pathname.startsWith(n.to),
    ) ?? NAV_ITEMS[0];

  return (
    <div className={s.shell} data-nav-open={navOpen}>
      {/* Mobile backdrop */}
      <button
        className={s.backdrop}
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavOpen(false)}
      />

      {/* ── Sidebar ── */}
      <aside className={s.sidebar}>
        <div className={s.logo}>
          <span className={s.logoMark}>B</span>
          <span className={s.logoText}>Book Agent</span>
        </div>

        <nav className={s.nav}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `${s.navItem} ${isActive ? s.navActive : ""}`
              }
              onClick={() => setNavOpen(false)}
              title={item.label}
            >
              <span className={s.navIcon}>{item.icon}</span>
              <span className={s.navLabel}>{item.label}</span>
              {item.to === "/approvals" && pendingCount > 0 && (
                <span className={s.navBadge} aria-label={`${pendingCount} 项待审批`}>
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className={s.sidebarFooter}>
          <div className={s.footerControls}>
            <BudgetMeter />
            <ApiKeySetting />
          </div>
          <div className={s.systemLinks}>
            <a href={SERVICE_LINKS.docs} target="_blank" rel="noopener" className={s.sysLink}>
              API
            </a>
            <a href={SERVICE_LINKS.openapi} target="_blank" rel="noopener" className={s.sysLink}>
              OpenAPI
            </a>
          </div>
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
            aria-label="Toggle navigation"
          >
            {navOpen ? "\u2715" : "\u2630"}
          </button>
          <div className={s.topbarLeft}>
            <span className={s.pageName}>{activeNav.label}</span>
            {docTitle && (
              <>
                <span className={s.separator}>/</span>
                <span className={s.docTitle}>{docTitle}</span>
              </>
            )}
          </div>
          {pendingCount > 0 && !location.pathname.startsWith("/approvals") && (
            <NavLink to="/approvals" className={s.pendingNotice} role="status">
              有 {pendingCount} 项等你审批，处理后翻译会继续 →
            </NavLink>
          )}
        </header>

        <div className={s.content}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
