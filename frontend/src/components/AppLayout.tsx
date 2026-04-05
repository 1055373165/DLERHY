import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { SERVICE_LINKS } from "../lib/api";
import { nextMilestoneText, preferredTitle } from "../lib/workflow";
import s from "./AppLayout.module.css";

const NAV_ITEMS = [
  { to: "/", label: "WORK", icon: ">", zh: "工作台" },
  { to: "/runs", label: "RUNS", icon: "#", zh: "运行" },
  { to: "/deliverables", label: "SHIP", icon: "%", zh: "交付" },
  { to: "/library", label: "LIB", icon: "~", zh: "书库" },
] as const;

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const {
    currentDocument,
    currentRun,
    currentExports,
    health,
    healthLoading,
  } = useWorkspace();

  const docTitle = currentDocument ? preferredTitle(currentDocument) : null;
  const milestone = currentDocument
    ? nextMilestoneText(currentDocument, currentRun ?? undefined, currentExports ?? undefined)
    : null;

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
          <span className={s.logoGlyph}>[</span>
          <span className={s.logoText}>BOOK-AGENT</span>
          <span className={s.logoGlyph}>]</span>
        </div>
        <div className={s.logoSub}>Translation Terminal v2.0</div>

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
            >
              <span className={s.navIcon}>{item.icon}</span>
              <span className={s.navLabel}>{item.label}</span>
              <span className={s.navZh}>{item.zh}</span>
            </NavLink>
          ))}
        </nav>

        <div className={s.sidebarFooter}>
          <div className={s.divider}>{"─".repeat(28)}</div>
          <div className={s.systemLinks}>
            <a href={SERVICE_LINKS.docs} target="_blank" rel="noopener" className={s.sysLink}>
              [api-docs]
            </a>
            <a href={SERVICE_LINKS.openapi} target="_blank" rel="noopener" className={s.sysLink}>
              [openapi]
            </a>
          </div>
          <div className={s.healthRow}>
            <span
              className={s.healthDot}
              data-status={healthLoading ? "check" : health?.status === "ok" ? "ok" : "down"}
            />
            <span className={s.healthLabel}>
              {healthLoading ? "CHECKING..." : health?.status === "ok" ? "SYS.ONLINE" : "SYS.OFFLINE"}
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
            {navOpen ? "[-]" : "[=]"}
          </button>
          <div className={s.topbarLeft}>
            <span className={s.prompt}>$</span>
            <span className={s.pageName}>{activeNav.label}</span>
            {docTitle && (
              <>
                <span className={s.separator}>::</span>
                <span className={s.docTitle}>{docTitle}</span>
              </>
            )}
          </div>
          {milestone && (
            <div className={s.topbarRight}>
              <span className={s.milestone}>{milestone}</span>
              <span className={s.cursor} />
            </div>
          )}
        </header>

        <div className={s.content}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
