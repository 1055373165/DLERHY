import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { SERVICE_LINKS } from "../lib/api";
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
} as const;

const NAV_ITEMS = [
  { to: "/", label: "WORK", zh: "工作台", icon: Icons.work },
  { to: "/runs", label: "RUNS", zh: "运行", icon: Icons.runs },
  { to: "/deliverables", label: "SHIP", zh: "交付", icon: Icons.ship },
  { to: "/library", label: "LIB", zh: "书库", icon: Icons.lib },
] as const;

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const { currentDocument, health, healthLoading } = useWorkspace();

  const docTitle = currentDocument ? preferredTitle(currentDocument) : null;

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
              title={`${item.label} ${item.zh}`}
            >
              <span className={s.navIcon}>{item.icon}</span>
              <span className={s.navLabel}>{item.label}</span>
              <span className={s.navZh}>{item.zh}</span>
            </NavLink>
          ))}
        </nav>

        <div className={s.sidebarFooter}>
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
              {healthLoading ? "..." : health?.status === "ok" ? "Online" : "Offline"}
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
        </header>

        <div className={s.content}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
