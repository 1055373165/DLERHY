import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { nextMilestoneText, preferredTitle } from "../lib/workflow";
import styles from "./AppLayout.module.css";

const NAV_ITEMS = [
  { to: "/", label: "工作台", hint: "上传书稿、确认当前书籍和下一步。" },
  { to: "/runs", label: "运行", hint: "查看阶段进度、事件和重点章节。" },
  { to: "/deliverables", label: "交付", hint: "下载产物并理解导出阻塞。" },
  { to: "/library", label: "书库", hint: "检索历史书籍并重新打开。" },
];

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const {
    health,
    healthLoading,
    currentDocument,
    currentRun,
    currentExports,
    serviceLinks,
  } = useWorkspace();

  const activeItem =
    NAV_ITEMS.find((item) =>
      item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)
    ) ?? NAV_ITEMS[0];
  const currentTitle = currentDocument ? preferredTitle(currentDocument) : "等待载入书稿";
  const currentNote = nextMilestoneText(currentDocument, currentRun, currentExports);

  return (
    <div className={styles.chrome} data-nav-open={navOpen}>
      <button
        className={styles.mobileBackdrop}
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavOpen(false)}
      />
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <div className={styles.brandEyebrow}>Editorial Operations Desk</div>
          <h1 className={styles.brandTitle}>Book Agent</h1>
          <p className={styles.brandCopy}>
            把上传、运行、交付和回看拆成四个清晰界面，只保留真正影响判断与交付的内容。
          </p>
        </div>
        <nav className={styles.nav} aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `${styles.navLink} ${isActive ? styles.navLinkActive : ""}`
              }
              onClick={() => setNavOpen(false)}
            >
              <span className={styles.navLabel}>{item.label}</span>
              <span className={styles.navHint}>{item.hint}</span>
            </NavLink>
          ))}
        </nav>
        <div className={styles.sidebarFooter}>
          <span>主导航只保留真实工作流。</span>
          <span>系统能力收纳到右上角，避免打断操作视线。</span>
        </div>
      </aside>

      <main className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.topbarMeta}>
            <div className={styles.topbarEyebrow}>{activeItem.label}</div>
            <h2 className={styles.topbarTitle}>{currentTitle}</h2>
            <p className={styles.topbarCopy}>{currentNote}</p>
          </div>
          <div className={styles.toolbar}>
            <button
              className={styles.mobileToggle}
              type="button"
              onClick={() => setNavOpen((open) => !open)}
            >
              导航
            </button>
            <div className={styles.health}>
              <span
                className={`${styles.healthDot} ${
                  healthLoading ? "" : health?.status === "ok" ? styles.healthActive : styles.healthError
                }`}
              />
              <span>{healthLoading ? "检查服务中" : health?.status === "ok" ? "服务在线" : "服务异常"}</span>
            </div>
            <details className={styles.menu}>
              <summary className={styles.menuSummary}>系统</summary>
              <div className={styles.menuPanel}>
                <a className={styles.menuLink} href={serviceLinks.docs} target="_blank" rel="noreferrer">
                  <span className={styles.menuLabel}>API Docs</span>
                  <span className={styles.menuMeta}>{serviceLinks.docs}</span>
                </a>
                <a className={styles.menuLink} href={serviceLinks.openapi} target="_blank" rel="noreferrer">
                  <span className={styles.menuLabel}>OpenAPI</span>
                  <span className={styles.menuMeta}>{serviceLinks.openapi}</span>
                </a>
                <a className={styles.menuLink} href={serviceLinks.health} target="_blank" rel="noreferrer">
                  <span className={styles.menuLabel}>健康检查</span>
                  <span className={styles.menuMeta}>{serviceLinks.health}</span>
                </a>
              </div>
            </details>
          </div>
        </header>

        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
