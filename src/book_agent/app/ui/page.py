# ruff: noqa: E501

from __future__ import annotations

import json
from html import escape


def build_homepage_html(*, app_name: str, app_version: str, api_prefix: str) -> str:
    docs_href = f"{api_prefix}/docs"
    openapi_href = f"{api_prefix}/openapi.json"
    health_href = f"{api_prefix}/health"
    boot_payload = json.dumps(
        {
            "appName": app_name,
            "appVersion": app_version,
            "apiPrefix": api_prefix,
            "docsHref": docs_href,
            "openapiHref": openapi_href,
            "healthHref": health_href,
        },
        ensure_ascii=False,
    )
    template = """<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta
      name="description"
      content="面向真实书籍翻译生产流程的工作台：上传英文 EPUB / PDF，追踪整书 translate、review 与 export，并直接下载中文阅读稿。"
    />
    <title>__APP_NAME__</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Manrope:wght@400;500;600;700;800&display=swap"
      rel="stylesheet"
    />
    <style>
      :root {
        color-scheme: light;
        --bg: #eef2f6;
        --bg-strong: #dde5eb;
        --surface: rgba(251, 253, 255, 0.94);
        --surface-strong: rgba(255, 255, 255, 0.98);
        --surface-muted: rgba(243, 247, 250, 0.94);
        --ink: #12202d;
        --ink-soft: #53606c;
        --ink-faint: #77838e;
        --teal: #14394b;
        --teal-strong: #0d2a3a;
        --teal-soft: rgba(20, 57, 75, 0.08);
        --copper: #8c6b42;
        --copper-soft: rgba(140, 107, 66, 0.1);
        --success: #176a5a;
        --success-soft: rgba(23, 106, 90, 0.1);
        --warning: #8a6426;
        --warning-soft: rgba(138, 100, 38, 0.12);
        --danger: #a04747;
        --danger-soft: rgba(160, 71, 71, 0.12);
        --line: rgba(18, 32, 45, 0.1);
        --line-strong: rgba(18, 32, 45, 0.16);
        --shadow-page: 0 24px 72px rgba(13, 32, 47, 0.09);
        --shadow-surface: 0 14px 28px rgba(13, 32, 47, 0.05);
        --shadow-hover: 0 18px 34px rgba(13, 32, 47, 0.1);
        --radius-page: 32px;
        --radius-xl: 24px;
        --radius-lg: 20px;
        --radius-md: 16px;
        --radius-sm: 12px;
        --sans: "Manrope", "Inter", "Segoe UI", sans-serif;
        --mono: "JetBrains Mono", "SFMono-Regular", monospace;
        --fast: 160ms ease;
        --smooth: 240ms cubic-bezier(0.2, 1, 0.36, 1);
      }

      * {
        box-sizing: border-box;
      }

      html {
        scroll-behavior: smooth;
      }

      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(20, 57, 75, 0.1), transparent 24%),
          radial-gradient(circle at 82% 0%, rgba(140, 107, 66, 0.08), transparent 16%),
          linear-gradient(180deg, #f7f9fb 0%, #edf2f5 44%, #e6edf1 100%);
        color: var(--ink);
        font-family: var(--sans);
      }

      a {
        color: inherit;
        text-decoration: none;
      }

      button,
      input,
      select {
        font: inherit;
      }

      button,
      a,
      input,
      select {
        transition:
          transform var(--fast),
          background var(--fast),
          border-color var(--fast),
          box-shadow var(--fast),
          color var(--fast);
      }

      button:focus-visible,
      input:focus-visible,
      select:focus-visible,
      a:focus-visible {
        outline: 2px solid rgba(23, 71, 84, 0.34);
        outline-offset: 2px;
      }

      .page-shell {
        padding: 16px;
      }

      .workspace {
        position: relative;
        max-width: 1680px;
        margin: 0 auto;
        padding: 26px 28px 28px;
        border: 1px solid rgba(255, 255, 255, 0.76);
        border-radius: var(--radius-page);
        background: linear-gradient(180deg, rgba(252, 253, 254, 0.96), rgba(245, 248, 250, 0.93));
        box-shadow: var(--shadow-page);
        overflow: visible;
      }

      .workspace::before {
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        background:
          linear-gradient(125deg, rgba(255, 255, 255, 0.26), transparent 34%),
          radial-gradient(circle at 78% 20%, rgba(20, 57, 75, 0.05), transparent 18%);
      }

      .masthead {
        position: sticky;
        top: 0;
        z-index: 20;
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 22px;
        padding: 2px 0 22px;
        margin-bottom: 26px;
        border-bottom: 1px solid rgba(18, 32, 45, 0.08);
        background: linear-gradient(180deg, rgba(252, 253, 254, 0.92), rgba(252, 253, 254, 0.78));
        backdrop-filter: blur(10px);
      }

      .brand-cluster {
        display: grid;
        gap: 6px;
      }

      .brand-kicker {
        color: var(--teal);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.16em;
        text-transform: uppercase;
      }

      .brand-title {
        font-size: 36px;
        font-weight: 800;
        letter-spacing: -0.05em;
        line-height: 0.94;
      }

      .brand-copy {
        color: var(--ink-soft);
        font-size: 15px;
        line-height: 1.55;
        max-width: 62ch;
      }

      .masthead-tools {
        display: grid;
        justify-items: end;
        gap: 12px;
      }

      .utility-row {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 10px;
      }

      .utility-link,
      .status-chip {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 40px;
        padding: 9px 14px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.82);
        color: var(--ink-soft);
        font-size: 13px;
        font-weight: 600;
      }

      .utility-link:hover,
      .action-button:hover,
      .history-item:hover,
      .asset-card:hover,
      .phase-card:hover,
      .chapter-row:hover,
      .event-card:hover {
        transform: translateY(-1px);
        box-shadow: var(--shadow-hover);
      }

      .utility-link.primary {
        border-color: transparent;
        background: linear-gradient(135deg, var(--teal) 0%, var(--teal-strong) 100%);
        color: #f8fbfd;
      }

      .status-dot {
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: var(--warning);
        box-shadow: 0 0 0 4px rgba(154, 106, 46, 0.14);
      }

      .status-dot.live {
        background: var(--success);
        box-shadow: 0 0 0 4px rgba(31, 107, 89, 0.14);
      }

      .status-dot.error {
        background: var(--danger);
        box-shadow: 0 0 0 4px rgba(157, 74, 66, 0.16);
      }

      .status-dot.loading {
        animation: pulse 1.4s ease-in-out infinite;
      }

      @keyframes pulse {
        0%,
        100% {
          transform: scale(1);
          opacity: 1;
        }
        50% {
          transform: scale(0.86);
          opacity: 0.72;
        }
      }

      .intro-band {
        position: relative;
        display: grid;
        grid-template-columns: minmax(0, 1.24fr) minmax(360px, 0.76fr);
        gap: 18px;
        align-items: stretch;
        margin-bottom: 28px;
      }

      .intro-copy,
      .intro-board,
      .surface,
      .subsurface {
        position: relative;
        border-radius: var(--radius-xl);
        border: 1px solid rgba(255, 255, 255, 0.78);
        box-shadow: var(--shadow-surface);
      }

      .intro-copy {
        overflow: hidden;
        padding: 30px 32px 30px;
        background:
          linear-gradient(140deg, rgba(249, 252, 254, 0.96), rgba(242, 247, 249, 0.92));
      }

      .intro-copy::after {
        content: "";
        position: absolute;
        right: -62px;
        bottom: -72px;
        width: 220px;
        height: 220px;
        border-radius: 50%;
        background: radial-gradient(circle, rgba(20, 57, 75, 0.08), transparent 70%);
        pointer-events: none;
      }

      .intro-label,
      .section-kicker,
      .subsurface-label,
      .mini-overline {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: var(--teal);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.16em;
        text-transform: uppercase;
      }

      .intro-title {
        max-width: 12ch;
        margin: 18px 0 16px;
        font-size: clamp(42px, 5vw, 66px);
        font-weight: 800;
        letter-spacing: -0.065em;
        line-height: 0.93;
      }

      .intro-text {
        max-width: 62ch;
        margin: 0;
        color: var(--ink-soft);
        font-size: 17px;
        line-height: 1.72;
      }

      .intro-points {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-top: 24px;
      }

      .intro-point {
        min-height: 126px;
        padding: 16px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.72);
        display: grid;
        align-content: start;
        gap: 8px;
      }

      .intro-point strong {
        font-size: 17px;
        line-height: 1.22;
      }

      .intro-point span {
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.55;
      }

      .intro-board {
        padding: 22px;
        background: linear-gradient(180deg, rgba(252, 254, 255, 0.98), rgba(244, 248, 250, 0.92));
        display: grid;
        gap: 14px;
        align-content: start;
      }

      .board-callout {
        padding: 16px;
        border-radius: var(--radius-lg);
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(20, 32, 40, 0.08);
      }

      .board-title {
        margin: 6px 0 0;
        font-size: 30px;
        font-weight: 800;
        letter-spacing: -0.05em;
        line-height: 1;
      }

      .board-copy {
        margin: 10px 0 0;
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.6;
      }

      .board-list {
        display: grid;
        gap: 10px;
      }

      .board-list-item {
        padding: 14px 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid rgba(20, 32, 40, 0.08);
      }

      .board-list-item strong {
        display: block;
        margin-bottom: 4px;
        font-size: 14px;
      }

      .board-list-item span,
      .mono-copy {
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.55;
      }

      .mono,
      .mono-copy {
        font-family: var(--mono);
      }

      .content-stack {
        display: grid;
        gap: 22px;
      }

      .history-surface {
        position: relative;
      }

      .surface {
        padding: 26px;
        background: var(--surface);
        backdrop-filter: blur(10px);
      }

      .subsurface {
        padding: 20px;
        background: var(--surface-muted);
      }

      .section-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 18px;
      }

      .section-title {
        margin: 8px 0 6px;
        font-size: 34px;
        font-weight: 800;
        letter-spacing: -0.055em;
        line-height: 1.02;
      }

      .section-copy {
        max-width: 64ch;
        margin: 0;
        color: var(--ink-soft);
        font-size: 15px;
        line-height: 1.62;
      }

      .section-aside {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 14px;
        border-radius: 999px;
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.84);
        color: var(--ink-soft);
        font-size: 12px;
        font-weight: 600;
        text-align: right;
      }

      .intake-layout {
        display: grid;
        grid-template-columns: minmax(0, 1.05fr) minmax(340px, 0.95fr);
        gap: 20px;
      }

      .dropzone-panel {
        padding: 22px;
        border-radius: var(--radius-lg);
        border: 1px dashed rgba(23, 71, 84, 0.24);
        background:
          linear-gradient(180deg, rgba(246, 251, 253, 0.96), rgba(251, 253, 255, 0.92));
      }

      .dropzone {
        display: grid;
        gap: 14px;
      }

      .dropzone[data-dragging="true"] {
        transform: translateY(-1px);
      }

      .dropzone-title {
        font-size: 26px;
        font-weight: 800;
        line-height: 1.08;
        letter-spacing: -0.03em;
      }

      .dropzone-copy {
        color: var(--ink-soft);
        font-size: 15px;
        line-height: 1.65;
      }

      .tag-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .tag {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 34px;
        padding: 8px 12px;
        border-radius: 999px;
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.82);
        color: var(--ink-soft);
        font-size: 12px;
        font-weight: 700;
      }

      .dropzone-file {
        padding: 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.68);
        border: 1px solid rgba(20, 32, 40, 0.08);
        display: grid;
        gap: 4px;
      }

      .dropzone-file strong {
        font-size: 16px;
        line-height: 1.4;
        overflow-wrap: anywhere;
      }

      .dropzone-file span {
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.55;
      }

      .button-row {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .action-button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        min-height: 46px;
        padding: 12px 16px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.82);
        color: var(--ink);
        font-size: 14px;
        font-weight: 700;
        cursor: pointer;
      }

      .action-button.primary {
        border-color: transparent;
        background: linear-gradient(135deg, var(--teal) 0%, var(--teal-strong) 100%);
        color: #f7fbfd;
        box-shadow: 0 16px 30px rgba(18, 55, 66, 0.18);
      }

      .action-button.secondary {
        background: rgba(255, 255, 255, 0.86);
      }

      .action-button.gold {
        background: linear-gradient(135deg, #a77a43 0%, #8c6638 100%);
        border-color: transparent;
        color: #fcfbf9;
      }

      .action-button.ghost {
        background: rgba(255, 255, 255, 0.7);
      }

      .action-button.small {
        min-height: 38px;
        padding: 8px 12px;
        font-size: 13px;
      }

      .action-button:disabled {
        cursor: not-allowed;
        transform: none;
        box-shadow: none;
        opacity: 0.48;
      }

      .banner {
        margin-top: 14px;
        padding: 14px 16px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.74);
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.55;
      }

      .banner.success {
        background: var(--success-soft);
        border-color: rgba(31, 107, 89, 0.18);
        color: var(--success);
      }

      .banner.warning {
        background: var(--warning-soft);
        border-color: rgba(154, 106, 46, 0.18);
        color: var(--warning);
      }

      .banner.error {
        background: var(--danger-soft);
        border-color: rgba(157, 74, 66, 0.18);
        color: var(--danger);
      }

      .empty-state {
        padding: 18px;
        border-radius: var(--radius-lg);
        border: 1px dashed rgba(20, 32, 40, 0.18);
        background: rgba(255, 255, 255, 0.68);
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.65;
      }

      .book-stack {
        display: grid;
        gap: 16px;
      }

      .book-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 18px;
      }

      .book-title {
        margin: 0;
        font-size: 38px;
        font-weight: 800;
        letter-spacing: -0.06em;
        line-height: 0.98;
      }

      .book-meta,
      .book-note,
      .phase-copy,
      .asset-note,
      .history-copy,
      .event-copy,
      .chapter-copy,
      .metric-note,
      .table-note,
      .filter-help {
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.58;
        overflow-wrap: anywhere;
      }

      .book-brief {
        margin: 0;
        font-size: 16px;
        line-height: 1.7;
      }

      .path-block {
        padding: 14px 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.68);
        border: 1px solid rgba(20, 32, 40, 0.08);
        overflow-wrap: anywhere;
      }

      .metric-grid,
      .summary-grid,
      .snapshot-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }

      .metric-card,
      .summary-card,
      .snapshot-card {
        min-height: 110px;
        padding: 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(20, 32, 40, 0.08);
        display: grid;
        align-content: start;
        gap: 8px;
      }

      .metric-label,
      .summary-label,
      .snapshot-label,
      .asset-label,
      .chapter-label,
      .history-label {
        color: var(--ink-faint);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }

      .metric-value,
      .summary-value,
      .snapshot-value {
        font-size: 32px;
        font-weight: 800;
        letter-spacing: -0.05em;
        line-height: 0.98;
      }

      .operations-stack {
        display: grid;
        gap: 18px;
        align-items: start;
      }

      .support-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px;
      }

      .run-console,
      .delivery-column,
      .history-shell {
        display: grid;
        gap: 14px;
      }

      .delivery-column {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .phase-rail {
        display: grid;
        gap: 12px;
      }

      .phase-card {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        gap: 14px;
        align-items: start;
        padding: 16px 18px;
        border-radius: var(--radius-lg);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.76);
      }

      .phase-card.current {
        background: linear-gradient(180deg, rgba(245, 250, 252, 0.98), rgba(252, 253, 255, 0.92));
        border-color: rgba(23, 71, 84, 0.22);
      }

      .phase-index {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        border-radius: 14px;
        background: rgba(23, 71, 84, 0.08);
        color: var(--teal);
        font-size: 20px;
        font-weight: 700;
      }

      .phase-title {
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.03em;
        line-height: 1.08;
      }

      .status-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 34px;
        padding: 7px 12px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 700;
        border: 1px solid transparent;
        white-space: nowrap;
      }

      .status-badge.pending,
      .status-badge.queued {
        background: rgba(255, 255, 255, 0.9);
        border-color: rgba(20, 32, 40, 0.08);
        color: var(--ink-soft);
      }

      .run-action-strip {
        margin-top: 14px;
        padding: 14px 16px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.72);
        display: grid;
        gap: 10px;
      }

      .run-action-strip[hidden] {
        display: none;
      }

      .run-action-title {
        font-size: 14px;
        font-weight: 800;
        color: var(--ink);
      }

      .run-action-copy {
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.6;
      }

      .status-badge.running,
      .status-badge.draining {
        background: var(--teal-soft);
        border-color: rgba(23, 71, 84, 0.18);
        color: var(--teal);
      }

      .status-badge.paused,
      .status-badge.warning {
        background: var(--warning-soft);
        border-color: rgba(154, 106, 46, 0.18);
        color: var(--warning);
      }

      .status-badge.succeeded,
      .status-badge.active,
      .status-badge.exported,
      .status-badge.partially_exported {
        background: var(--success-soft);
        border-color: rgba(31, 107, 89, 0.18);
        color: var(--success);
      }

      .status-badge.failed,
      .status-badge.cancelled,
      .status-badge.retryable_failed {
        background: var(--danger-soft);
        border-color: rgba(157, 74, 66, 0.18);
        color: var(--danger);
      }

      .delivery-column {
        align-content: start;
      }

      .subsurface-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 14px;
      }

      .subsurface-title {
        margin: 8px 0 0;
        font-size: 24px;
        font-weight: 800;
        letter-spacing: -0.04em;
        line-height: 1.08;
      }

      .asset-grid {
        display: grid;
        gap: 10px;
      }

      .asset-card {
        padding: 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.74);
        border: 1px solid rgba(20, 32, 40, 0.08);
        display: grid;
        gap: 10px;
      }

      .asset-title {
        font-size: 18px;
        font-weight: 700;
        line-height: 1.22;
      }

      .asset-meta {
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.55;
      }

      .attention-shell {
        display: grid;
        gap: 12px;
      }

      .attention-list {
        display: grid;
        gap: 10px;
      }

      .attention-card {
        padding: 14px 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(20, 32, 40, 0.08);
        display: grid;
        gap: 6px;
      }

      .attention-card strong {
        font-size: 15px;
        line-height: 1.4;
      }

      .table-shell {
        overflow: auto;
        border-radius: var(--radius-md);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.7);
      }

      table {
        width: 100%;
        border-collapse: collapse;
      }

      th,
      td {
        padding: 12px 14px;
        border-bottom: 1px solid rgba(20, 32, 40, 0.08);
        text-align: left;
        vertical-align: top;
      }

      th {
        color: var(--ink-faint);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }

      td {
        font-size: 14px;
        line-height: 1.55;
      }

      tr:last-child td {
        border-bottom: 0;
      }

      .chapter-list,
      .event-list,
      .history-list,
      .history-stack {
        display: grid;
        gap: 12px;
      }

      .chapter-row,
      .event-card,
      .history-item {
        min-width: 0;
        padding: 18px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.78);
      }

      .chapter-row {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        gap: 14px;
        align-items: center;
      }

      .chapter-index {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 42px;
        height: 42px;
        border-radius: 12px;
        background: rgba(23, 71, 84, 0.08);
        color: var(--teal);
        font-size: 15px;
        font-weight: 700;
      }

      .chapter-title {
        font-size: 16px;
        font-weight: 700;
        line-height: 1.45;
        overflow-wrap: anywhere;
      }

      .event-card {
        display: grid;
        gap: 8px;
      }

      .event-head,
      .history-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 14px;
      }

      .event-title,
      .history-title {
        font-size: 18px;
        font-weight: 800;
        line-height: 1.32;
        letter-spacing: -0.02em;
        overflow-wrap: anywhere;
      }

      .history-item {
        display: grid;
        gap: 12px;
      }

      .history-stack {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .history-progress {
        display: grid;
        gap: 8px;
      }

      .history-path {
        padding: 10px 12px;
        border-radius: 12px;
        background: rgba(244, 247, 249, 0.96);
        border: 1px solid rgba(18, 32, 45, 0.08);
        color: var(--ink-faint);
        font-size: 12px;
        line-height: 1.6;
        overflow-wrap: anywhere;
      }

      .meter {
        position: relative;
        width: 100%;
        height: 10px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(20, 32, 40, 0.08);
      }

      .meter span {
        position: absolute;
        inset: 0 auto 0 0;
        width: 0;
        border-radius: inherit;
        background: linear-gradient(135deg, var(--teal) 0%, #2d6978 100%);
      }

      .filter-stack {
        display: grid;
        gap: 12px;
        margin-bottom: 14px;
      }

      .field-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }

      .field {
        display: grid;
        gap: 6px;
      }

      .field label {
        color: var(--ink-faint);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      .field input,
      .field select {
        width: 100%;
        min-height: 44px;
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid rgba(20, 32, 40, 0.12);
        background: rgba(255, 255, 255, 0.82);
        color: var(--ink);
      }

      .field.search-field {
        grid-column: 1 / -1;
      }

      .footer-bar {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 28px;
        padding-top: 18px;
        border-top: 1px solid rgba(20, 32, 40, 0.08);
      }

      .footer-pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 38px;
        padding: 8px 12px;
        border-radius: 999px;
        border: 1px solid rgba(20, 32, 40, 0.08);
        background: rgba(255, 255, 255, 0.82);
        color: var(--ink-soft);
        font-size: 12px;
        font-weight: 600;
      }

      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        border: 0;
      }

      @media (max-width: 1040px) {
        .intro-band,
        .intake-layout,
        .support-grid,
        .metric-grid,
        .summary-grid,
        .snapshot-grid {
          grid-template-columns: 1fr;
        }

        .delivery-column,
        .history-stack,
        .field-grid {
          grid-template-columns: 1fr;
        }

        .intro-points {
          grid-template-columns: 1fr;
        }

        .masthead {
          position: static;
          flex-direction: column;
        }

        .masthead-tools {
          justify-items: stretch;
          width: 100%;
        }

        .utility-row {
          justify-content: flex-start;
        }
      }

      @media (max-width: 720px) {
        .page-shell {
          padding: 10px;
        }

        .workspace {
          padding: 14px;
          border-radius: 24px;
        }

        .intro-copy,
        .surface {
          padding: 18px;
        }

        .section-head,
        .book-head,
        .history-head,
        .event-head,
        .chapter-row {
          grid-template-columns: 1fr;
          display: grid;
        }

        .section-title {
          font-size: 28px;
        }

        .intro-title,
        .book-title {
          max-width: none;
          font-size: 38px;
        }

        .button-row,
        .utility-row,
        .field-grid,
        .delivery-column,
        .history-stack {
          grid-template-columns: 1fr;
          display: grid;
        }

        .action-button {
          width: 100%;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        *,
        *::before,
        *::after {
          animation-duration: 0.01ms !important;
          animation-iteration-count: 1 !important;
          transition-duration: 0.01ms !important;
          scroll-behavior: auto !important;
        }
      }
    </style>
  </head>
  <body>
    <div class="page-shell">
      <div class="workspace">
        <header class="masthead">
          <div class="brand-cluster">
            <span class="brand-kicker">Book Agent Pressroom</span>
            <div class="brand-title">整书译制工作台</div>
            <div class="brand-copy">
              把一本英文书，从原稿推进到中文交付。上传 PDF / EPUB，启动整书 translate_full，
              随时看清它卡在哪一环、为什么还不能导出、现在已经能拿走什么。
            </div>
          </div>
          <div class="masthead-tools">
            <div class="status-chip">
              <span id="health-dot" class="status-dot loading"></span>
              <span id="health-label">检查服务中</span>
            </div>
            <div class="utility-row">
              <a class="utility-link" href="#workspace">开始新书</a>
              <a class="utility-link" href="#pipeline">查看运行</a>
              <a class="utility-link" href="#history">书库历史</a>
              <a class="utility-link primary" href="__DOCS_HREF__">API Docs</a>
            </div>
          </div>
        </header>

        <section class="intro-band">
          <article class="intro-copy">
            <span class="intro-label">Publishing-grade workflow</span>
            <h1 class="intro-title">让整书翻译，像一条可靠的出版流程。</h1>
            <p class="intro-text">
              这里不是接口演示页，而是给真实使用者的工作台。你能在同一张桌面上完成新书导入、阶段判断、
              review 风险识别、导出下载和历史回看，而不是在零散状态里猜测系统到底进行了什么。
            </p>
            <div class="intro-points">
              <div class="intro-point">
                <strong>当前书籍永远在中心位</strong>
                <span>先看现在这本书到哪一步，再决定继续跑、等待导出，还是回头处理阻塞。</span>
              </div>
              <div class="intro-point">
                <strong>阶段信息比原始数字更重要</strong>
                <span>把 translate / review / export 解释成用户能判断下一步的状态，而不是纯技术字段。</span>
              </div>
              <div class="intro-point">
                <strong>历史资产像书库，而不是日志表</strong>
                <span>随时回看过去处理过的书，立刻知道哪本可下载、哪本还在跑、哪本仍被 review gate 挡住。</span>
              </div>
            </div>
          </article>

          <aside class="intro-board">
            <div class="board-callout">
              <span class="mini-overline">当前页面定位</span>
              <div class="board-title">Production Translation Desk</div>
              <p class="board-copy">
                Version __APP_VERSION__ · translation-studio<br />
                当前主路径：上传书籍 → 启动整书转换 → 监控状态 → 下载导出结果
              </p>
            </div>
            <div class="board-list">
              <div class="board-list-item">
                <strong>你先需要知道什么</strong>
                <span>当前书籍是谁、最新 run 状态是什么、现在为什么还不能导出。</span>
              </div>
              <div class="board-list-item">
                <strong>你不该被迫理解什么</strong>
                <span>零散的 API 名称、work item 细节或导出内部机制。</span>
              </div>
              <div class="board-list-item">
                <strong>系统出口</strong>
                <span class="mono">health → __HEALTH_HREF__</span><br />
                <span class="mono">openapi → __OPENAPI_HREF__</span>
              </div>
            </div>
          </aside>
        </section>

        <div class="content-stack">
            <section class="surface" id="workspace">
              <div class="section-head">
                <div>
                  <div class="section-kicker">Ingest</div>
                  <h2 class="section-title">新建书籍任务</h2>
                  <p class="section-copy">
                    上传英文 EPUB / PDF 后，系统会先完成导入与结构解析。页面会自动载入这本书，并在同一视图里继续后续阶段。
                  </p>
                </div>
                <div class="section-aside">不复用旧布局 · 直接重写主工作台结构</div>
              </div>

              <div class="intake-layout">
                <article class="dropzone-panel">
                  <div id="file-dropzone" class="dropzone" data-dragging="false">
                    <input
                      id="source-file"
                      class="sr-only"
                      type="file"
                      accept=".epub,.pdf,application/epub+zip,application/pdf"
                    />
                    <div class="dropzone-title">拖入书籍文件，或点击选择</div>
                    <div class="dropzone-copy">
                      推荐直接上传英文原稿。文件会通过 <span class="mono">bootstrap-upload</span> 写入服务端上传目录，
                      完成 document 建立、章节切分和 packet 准备。
                    </div>
                    <div class="tag-row">
                      <span class="tag">英文 EPUB</span>
                      <span class="tag">英文 PDF</span>
                      <span class="tag">高保真结构解析</span>
                      <span class="tag">直接进入整书流水线</span>
                    </div>
                    <div class="dropzone-file">
                      <strong id="selected-file-name">还没有选中文件</strong>
                      <span id="selected-file-note">支持拖拽上传，也可以手动选择一本书开始。</span>
                    </div>
                    <div class="button-row">
                      <button id="pick-file" class="action-button secondary" type="button">选择书籍文件</button>
                      <button id="upload-file" class="action-button primary" type="button">上传并解析</button>
                    </div>
                  </div>
                  <div id="upload-banner" class="banner">上传完成后，这里会提示解析结果，并自动把当前书籍放到当前工作区。</div>
                </article>

                <article class="subsurface">
                  <div class="subsurface-head">
                    <div>
                      <div class="subsurface-label">Current Book</div>
                      <div class="subsurface-title">当前书籍</div>
                    </div>
                    <div class="section-aside">自动记住上次查看的 document</div>
                  </div>
                  <div id="document-shell" class="empty-state">
                    先上传一本英文书。解析成功后，这里会告诉你当前书籍是谁、最新 run 到了哪一环、为什么还不能导出，以及下一步应该怎么做。
                  </div>
                </article>
              </div>
            </section>

            <section class="surface" id="pipeline">
              <div class="section-head">
                <div>
                  <div class="section-kicker">Run Flow</div>
                  <h2 class="section-title">运行总览</h2>
                  <p class="section-copy">
                    整书转换是长任务。这里不只告诉你 run 是否存在，还会把阶段、工作量、阻塞原因和交付就绪度放在同一屏里。
                  </p>
                </div>
                <div class="section-aside">translate_full · review / repair / export 全链路可见</div>
              </div>

              <div class="operations-stack">
                <div class="run-console">
                  <div class="button-row">
                    <button id="start-run" class="action-button primary" type="button">开始整书转换</button>
                    <button id="refresh-current" class="action-button ghost" type="button">刷新当前状态</button>
                  </div>
                  <div id="run-banner" class="banner">当前还没有活跃的整书 run。上传书籍后即可开始。</div>
                  <div id="run-summary" class="summary-grid"></div>
                  <div id="run-actions" class="run-action-strip" hidden></div>
                  <div id="pipeline-steps" class="phase-rail"></div>
                </div>

                <div class="delivery-column" id="results">
                  <article class="subsurface">
                    <div class="subsurface-head">
                      <div>
                        <div class="subsurface-label">Deliverables</div>
                        <div class="subsurface-title">交付资产</div>
                      </div>
                    </div>
                    <div id="download-shell" class="asset-grid"></div>
                  </article>

                  <article class="subsurface">
                    <div class="subsurface-head">
                      <div>
                        <div class="subsurface-label">Review Gate</div>
                        <div class="subsurface-title">复核与阻塞</div>
                      </div>
                    </div>
                    <div id="attention-shell" class="empty-state">
                      载入当前书籍后，这里会解释为什么现在能导出，或者为什么仍被 review gate 挡住。
                    </div>
                  </article>

                  <article class="subsurface">
                    <div class="subsurface-head">
                      <div>
                        <div class="subsurface-label">Export Snapshot</div>
                        <div class="subsurface-title">导出快照</div>
                      </div>
                    </div>
                    <div id="export-dashboard-shell" class="empty-state">
                      当前还没有导出快照。进入导出阶段后，这里会展示最近产物、成本和导出历史。
                    </div>
                  </article>
                </div>
              </div>
            </section>

            <div class="support-grid">
            <section class="surface">
              <div class="section-head">
                <div>
                  <div class="section-kicker">Operational Detail</div>
                  <h2 class="section-title">章节注意区</h2>
                  <p class="section-copy">
                    真正影响体验的不是“章节总数”，而是哪些章节需要关注、哪些章节已经可直接下载，以及哪些章节仍然承受 issue 压力。
                  </p>
                </div>
                <div class="section-aside">先处理最值得关注的章节</div>
              </div>

              <div id="chapter-shell" class="empty-state">
                当前没有已载入书籍。文档到位后，这里会优先列出需要关注或可直接下载双语导出的章节。
              </div>
            </section>

            <section class="surface">
              <div class="section-head">
                <div>
                  <div class="section-kicker">Recent Activity</div>
                  <h2 class="section-title">最近运行记录</h2>
                  <p class="section-copy">
                    如果一条书籍任务没有继续推进，这里应该成为你判断它是正常运行、暂停、失败还是已进入导出的第一现场。
                  </p>
                </div>
                <div class="section-aside">按最近事件理解系统刚刚做了什么</div>
              </div>
              <div id="event-shell" class="empty-state">
                当前没有可展示的运行事件。启动整书转换后，这里会显示最近的 run 审计时间线。
              </div>
            </section>
            </div>

            <section class="surface history-surface" id="history">
              <div class="section-head">
                <div>
                  <div class="section-kicker">Library</div>
                  <h2 class="section-title">书库历史</h2>
                  <p class="section-copy">
                    这是次级信息区，但不能牺牲可读性。它会全宽铺开，方便回看长标题、长路径和真实的阶段说明，不再挤在右侧窄栏里。
                  </p>
                </div>
                <div class="section-aside">次要信息下沉到底部，避免干扰当前工作流</div>
              </div>

              <form id="history-form" class="filter-stack">
                <div class="field search-field">
                  <label for="history-query">搜索</label>
                  <input
                    id="history-query"
                    name="query"
                    type="search"
                    placeholder="搜索标题、作者、路径或 document id"
                  />
                  <div class="filter-help">支持按书名、作者、源路径和 document id 回找。</div>
                </div>
                <div class="field-grid">
                  <div class="field">
                    <label for="history-status">文档状态</label>
                    <select id="history-status" name="status">
                      <option value="">全部状态</option>
                      <option value="active">已入库</option>
                      <option value="partially_exported">部分可下载</option>
                      <option value="exported">可下载</option>
                      <option value="failed">失败</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="history-run-status">最近运行</label>
                    <select id="history-run-status" name="latest_run_status">
                      <option value="">全部运行状态</option>
                      <option value="queued">已排队</option>
                      <option value="running">进行中</option>
                      <option value="paused">已暂停</option>
                      <option value="failed">失败</option>
                      <option value="cancelled">已取消</option>
                      <option value="succeeded">已完成</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="history-merged-ready">中文阅读稿</label>
                    <select id="history-merged-ready" name="merged_export_ready">
                      <option value="">全部</option>
                      <option value="true">已生成</option>
                      <option value="false">未生成</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>&nbsp;</label>
                    <button id="apply-history" class="action-button gold" type="submit">刷新书库视图</button>
                  </div>
                </div>
              </form>

              <div id="history-banner" class="banner">历史列表会自动加载。你也可以随时变更筛选条件，继续打开任意一本书。</div>
              <div id="history-shell" class="history-stack"></div>
            </section>
        </div>

        <footer class="footer-bar">
          <div class="footer-pill">__APP_NAME__</div>
          <a class="footer-pill" href="__DOCS_HREF__">API 文档</a>
          <a class="footer-pill" href="__OPENAPI_HREF__">OpenAPI</a>
          <a class="footer-pill" href="__HEALTH_HREF__">健康检查</a>
        </footer>
      </div>
    </div>

    <script>
      window.__BOOK_AGENT_BOOT__ = __BOOT_PAYLOAD__;
    </script>
    <script>
      (function () {
        const boot = window.__BOOK_AGENT_BOOT__;
        const apiPrefix = boot.apiPrefix;
        const STORAGE_KEY_DOCUMENT = "book-agent.current-document-id";
        const PIPELINE_STEPS = [
          {
            key: "bootstrap",
            label: "文档解析",
            description: "导入源文件、识别结构、切章节并准备 packet。",
          },
          {
            key: "translate",
            label: "全文翻译",
            description: "逐 packet 产出中文译文，并持续写回运行进度。",
          },
          {
            key: "review",
            label: "自动复核",
            description: "执行 review 与 follow-up，清理会阻塞导出的质量问题。",
          },
          {
            key: "bilingual_html",
            label: "双语章节导出",
            description: "生成逐章双语 HTML，方便精校和对照阅读。",
          },
          {
            key: "merged_html",
            label: "中文阅读稿导出",
            description: "输出整书中文阅读包，进入最终可交付状态。",
          },
        ];
        const STALE_FAILED_STAGE_RETRY_MS = 3 * 60 * 1000;
        const DELIVERY_ASSETS = [
          {
            key: "merged_html",
            title: "中文阅读包",
            label: "Primary Delivery",
            buttonText: "下载中文阅读包",
            tone: "primary",
          },
          {
            key: "bilingual_html",
            title: "双语章节包",
            label: "Editing Pack",
            buttonText: "下载双语章节包",
            tone: "ghost",
          },
          {
            key: "review_package",
            title: "Review Package",
            label: "Diagnostic Pack",
            buttonText: "下载 Review Package",
            tone: "secondary",
          },
        ];
        const STATUS_LABELS = {
          pending: "待执行",
          queued: "已排队",
          running: "进行中",
          draining: "收尾中",
          succeeded: "已完成",
          failed: "失败",
          retryable_failed: "待重试",
          paused: "已暂停",
          cancelled: "已取消",
          active: "已入库",
          partially_exported: "部分可下载",
          exported: "可下载",
        };

        const state = {
          selectedFile: null,
          currentDocument: null,
          currentRun: null,
          currentRunEvents: [],
          currentExportDashboard: null,
          historyEntries: [],
          historyMeta: null,
          pollTimer: null,
          pollInFlight: false,
          restoringContext: false,
        };

        const els = {
          healthDot: document.getElementById("health-dot"),
          healthLabel: document.getElementById("health-label"),
          fileDropzone: document.getElementById("file-dropzone"),
          sourceFile: document.getElementById("source-file"),
          pickFile: document.getElementById("pick-file"),
          uploadFile: document.getElementById("upload-file"),
          selectedFileName: document.getElementById("selected-file-name"),
          selectedFileNote: document.getElementById("selected-file-note"),
          uploadBanner: document.getElementById("upload-banner"),
          documentShell: document.getElementById("document-shell"),
          startRun: document.getElementById("start-run"),
          refreshCurrent: document.getElementById("refresh-current"),
          runBanner: document.getElementById("run-banner"),
          runSummary: document.getElementById("run-summary"),
          runActions: document.getElementById("run-actions"),
          pipelineSteps: document.getElementById("pipeline-steps"),
          downloadShell: document.getElementById("download-shell"),
          attentionShell: document.getElementById("attention-shell"),
          exportDashboardShell: document.getElementById("export-dashboard-shell"),
          chapterShell: document.getElementById("chapter-shell"),
          eventShell: document.getElementById("event-shell"),
          historyForm: document.getElementById("history-form"),
          historyQuery: document.getElementById("history-query"),
          historyStatus: document.getElementById("history-status"),
          historyRunStatus: document.getElementById("history-run-status"),
          historyMergedReady: document.getElementById("history-merged-ready"),
          historyBanner: document.getElementById("history-banner"),
          historyShell: document.getElementById("history-shell"),
        };

        function escapeHtml(value) {
          return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
        }

        function formatNumber(value) {
          if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return "0";
          }
          return new Intl.NumberFormat("zh-CN").format(Number(value));
        }

        function formatMoney(value) {
          if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return "$0.000";
          }
          return "$" + Number(value).toFixed(3);
        }

        function formatDate(value) {
          if (!value) {
            return "—";
          }
          try {
            return new Intl.DateTimeFormat("zh-CN", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            }).format(new Date(value));
          } catch (_error) {
            return String(value);
          }
        }

        function shorten(value, keep = 8) {
          if (!value) {
            return "—";
          }
          const text = String(value);
          return text.length <= keep * 2 + 1
            ? text
            : text.slice(0, keep) + "…" + text.slice(-keep);
        }

        function statusLabel(status) {
          if (!status) {
            return "未开始";
          }
          return STATUS_LABELS[status] || status;
        }

        function sourceLabel(value) {
          const mapping = {
            epub: "EPUB",
            pdf_text: "PDF（文本）",
            pdf_scan: "PDF（扫描）",
            pdf_mixed: "PDF（混合）",
          };
          return mapping[value] || value || "未识别";
        }

        function exportLabel(value) {
          const mapping = {
            bilingual_html: "双语章节 HTML",
            merged_html: "中文阅读稿 HTML",
            merged_markdown: "中文阅读稿 Markdown",
            review_package: "Review Package",
          };
          return mapping[value] || value || "导出";
        }

        function pipelineStageLabel(stage) {
          const labels = {
            bootstrap: "文档解析",
            translate: "全文翻译",
            review: "自动复核",
            bilingual_html: "双语章节导出",
            merged_html: "中文阅读稿导出",
            completed: "全部完成",
          };
          return labels[stage] || stage || "等待开始";
        }

        function preferredTitle(entity) {
          return entity?.title_tgt || entity?.title || entity?.title_src || "未命名书籍";
        }

        function currentRunId() {
          if (state.currentRun?.run_id) {
            return state.currentRun.run_id;
          }
          if (state.currentDocument?.latest_run_id) {
            return state.currentDocument.latest_run_id;
          }
          return null;
        }

        function currentPipelineDetail() {
          return state.currentRun?.status_detail_json?.pipeline || {};
        }

        function currentStageKey() {
          return currentPipelineDetail().current_stage || null;
        }

        function stageDetail(stageKey) {
          return currentPipelineDetail().stages?.[stageKey] || null;
        }

        function stageStatus(stageKey) {
          if (stageKey === "bootstrap") {
            if (!state.currentDocument) {
              return "pending";
            }
            return state.currentDocument.status === "failed" ? "failed" : "succeeded";
          }
          const detail = stageDetail(stageKey);
          if (detail?.status) {
            return detail.status;
          }
          if (!state.currentRun) {
            return "pending";
          }
          if (
            state.currentRun.status === "succeeded" &&
            ["translate", "review", "bilingual_html", "merged_html"].includes(stageKey)
          ) {
            return "succeeded";
          }
          return "pending";
        }

        function failedPipelineStage() {
          for (const step of PIPELINE_STEPS) {
            const status = stageStatus(step.key);
            if (status === "failed" || status === "cancelled") {
              return { key: step.key, status: status, detail: stageDetail(step.key) };
            }
          }
          return null;
        }

        function failedStageRetryEligible() {
          const run = state.currentRun;
          const failedStage = failedPipelineStage();
          if (!run || !failedStage) {
            return false;
          }
          if (["failed", "cancelled", "paused"].includes(run.status || "")) {
            return true;
          }
          const freshestSignal = run.worker_leases?.latest_heartbeat_at || run.updated_at;
          if (!freshestSignal) {
            return false;
          }
          const freshestAt = new Date(freshestSignal).getTime();
          if (Number.isNaN(freshestAt)) {
            return false;
          }
          return (Date.now() - freshestAt) >= STALE_FAILED_STAGE_RETRY_MS;
        }

        function readErrorMessage(payload, fallback) {
          if (!payload) {
            return fallback;
          }
          if (typeof payload.detail === "string") {
            return payload.detail;
          }
          if (typeof payload.message === "string") {
            return payload.message;
          }
          return fallback;
        }

        function setBanner(element, message, tone) {
          element.className = tone ? "banner " + tone : "banner";
          element.textContent = message;
        }

        function setButtonLoading(button, loading, loadingText) {
          if (!button.dataset.originalText) {
            button.dataset.originalText = button.textContent;
          }
          button.disabled = loading;
          button.textContent = loading ? loadingText : button.dataset.originalText;
        }

        function delay(ms) {
          return new Promise((resolve) => window.setTimeout(resolve, ms));
        }

        async function fetchJson(url, options) {
          const response = await fetch(url, options);
          const isJson = (response.headers.get("content-type") || "").includes("application/json");
          const payload = isJson ? await response.json() : null;
          if (!response.ok) {
            throw new Error(readErrorMessage(payload, "请求失败：" + response.status));
          }
          return payload;
        }

        async function fetchBinary(url) {
          const response = await fetch(url);
          if (!response.ok) {
            let detail = "下载失败：" + response.status;
            try {
              const payload = await response.json();
              detail = readErrorMessage(payload, detail);
            } catch (_error) {
              // ignore
            }
            throw new Error(detail);
          }
          return response;
        }

        function filenameFromDisposition(headerValue, fallbackName) {
          if (!headerValue) {
            return fallbackName;
          }
          const utf8Match = headerValue.match(/filename\\*=UTF-8''([^;]+)/i);
          if (utf8Match) {
            try {
              return decodeURIComponent(utf8Match[1]);
            } catch (_error) {
              return utf8Match[1];
            }
          }
          const plainMatch = headerValue.match(/filename="?([^"]+)"?/i);
          if (plainMatch) {
            return plainMatch[1];
          }
          return fallbackName;
        }

        async function saveResponse(response, fallbackName) {
          const blob = await response.blob();
          const filename = filenameFromDisposition(
            response.headers.get("content-disposition"),
            fallbackName
          );
          const objectUrl = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = objectUrl;
          anchor.download = filename;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          setTimeout(() => URL.revokeObjectURL(objectUrl), 500);
          return filename;
        }

        function safeStorageSet(key, value) {
          try {
            window.localStorage.setItem(key, value);
          } catch (_error) {
            // ignore
          }
        }

        function safeStorageRemove(key) {
          try {
            window.localStorage.removeItem(key);
          } catch (_error) {
            // ignore
          }
        }

        function safeStorageGet(key) {
          try {
            return window.localStorage.getItem(key);
          } catch (_error) {
            return null;
          }
        }

        function rememberCurrentDocument(documentId) {
          if (!documentId) {
            safeStorageRemove(STORAGE_KEY_DOCUMENT);
            return;
          }
          safeStorageSet(STORAGE_KEY_DOCUMENT, documentId);
        }

        function currentTranslateProgress() {
          const translateDetail = stageDetail("translate");
          const total = Number(
            translateDetail?.total_packet_count ??
              state.currentDocument?.packet_count ??
              state.currentRun?.work_items?.stage_counts?.translate ??
              0
          );
          const completed = Number(
            state.currentRun?.status_detail_json?.control_counters?.completed_work_item_count ?? 0
          );
          return {
            total: total,
            completed: completed,
            ratio: total > 0 ? Math.max(0, Math.min(1, completed / total)) : 0,
          };
        }

        function blockingIssueCount() {
          const hotspots = state.currentExportDashboard?.issue_hotspots || [];
          return hotspots.reduce((total, entry) => total + Number(entry.blocking_issue_count || 0), 0);
        }

        function nextMilestoneText() {
          const summary = state.currentDocument;
          const dashboard = state.currentExportDashboard;
          if (!summary) {
            return "先上传一本英文书，才能进入整书工作流。";
          }
          if (summary.merged_export_ready) {
            return "中文阅读包已经准备好，可以直接下载保存。";
          }
          const stage = currentStageKey();
          if (!summary.latest_run_id) {
            return "解析已经完成，下一步是启动整书转换。";
          }
          if (stage === "translate") {
            return "系统仍在全文翻译阶段，尚未进入 review 和导出。";
          }
          if (stage === "review") {
            const blockers = blockingIssueCount();
            return blockers > 0
              ? "仍有 review blocker 未清理，所以整书导出不会开始。"
              : "review 已在进行，系统准备进入导出阶段。";
          }
          if (stage === "bilingual_html") {
            return "双语章节导出正在生成，中文阅读稿还需要再走一段。";
          }
          if (stage === "merged_html") {
            return "整书中文阅读稿正在导出，接近最终交付。";
          }
          if (summary.chapter_bilingual_export_count > 0) {
            return "双语章节包已经可用，整书中文阅读包还在等待最终导出。";
          }
          if (dashboard?.successful_export_count > 0) {
            return "部分导出已完成，等待整书中文阅读包就绪。";
          }
          if (summary.latest_run_status === "failed" || summary.latest_run_status === "cancelled") {
            return "上次整书运行中断，可以直接重试。";
          }
          if (summary.latest_run_status === "paused") {
            return "上次整书运行已暂停，可以继续。";
          }
          return "当前还没有可下载结果，系统会继续推进后续阶段。";
        }

        function documentBadgeMeta() {
          const summary = state.currentDocument;
          if (!summary) {
            return { tone: "pending", label: "等待书籍" };
          }
          if (summary.merged_export_ready) {
            return { tone: "succeeded", label: "可下载" };
          }
          const runStatus = state.currentRun?.status || summary.latest_run_status;
          const stage = currentStageKey();
          if (stage === "translate" && ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "翻译已暂停" : runStatus === "queued" ? "待翻译" : "翻译中",
            };
          }
          if (stage === "review" && ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "复核已暂停" : runStatus === "queued" ? "待复核" : "复核中",
            };
          }
          if (["bilingual_html", "merged_html"].includes(stage || "") &&
            ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "导出已暂停" : runStatus === "queued" ? "待导出" : "导出中",
            };
          }
          if (runStatus === "failed" || runStatus === "cancelled") {
            return { tone: "failed", label: statusLabel(runStatus) };
          }
          if (!summary.latest_run_id) {
            return { tone: "active", label: "已解析" };
          }
          return { tone: "active", label: statusLabel(summary.status) };
        }

        function runPrimaryAction() {
          const run = state.currentRun;
          const summary = state.currentDocument;
          const latestStatus = run?.status || summary?.latest_run_status || null;
          const runId = run?.run_id || summary?.latest_run_id || null;
          const failedStage = failedPipelineStage();
          if (!summary) {
            return { mode: "disabled", label: "先上传并解析书籍", disabled: true };
          }
          if (failedStage && runId) {
            if (["failed", "cancelled", "paused"].includes(latestStatus || "") || failedStageRetryEligible()) {
              return { mode: "retry", label: "重试上次转换", disabled: false, runId: runId, failedStage: failedStage.key };
            }
            return {
              mode: "recover",
              label: "刷新并准备重试",
              disabled: false,
              runId: runId,
              failedStage: failedStage.key,
            };
          }
          if (latestStatus === "running" || latestStatus === "draining") {
            return { mode: "disabled", label: "整书转换进行中", disabled: true };
          }
          if ((latestStatus === "queued" || latestStatus === "paused") && runId) {
            return { mode: "resume", label: "继续当前转换", disabled: false, runId: runId };
          }
          if ((latestStatus === "failed" || latestStatus === "cancelled") && runId) {
            return { mode: "retry", label: "重试上次转换", disabled: false, runId: runId };
          }
          return {
            mode: "create",
            label: latestStatus === "succeeded" ? "重新运行整书转换" : "开始整书转换",
            disabled: false,
          };
        }

        function historyBadgeMeta(entry) {
          if (entry.merged_export_ready) {
            return { tone: "succeeded", label: "可下载" };
          }
          const runStatus = entry.latest_run_status || null;
          const stage = entry.latest_run_current_stage || null;
          if (stage === "translate" && ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "翻译已暂停" : runStatus === "queued" ? "待翻译" : "翻译中",
            };
          }
          if (stage === "review" && ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "复核已暂停" : runStatus === "queued" ? "待复核" : "复核中",
            };
          }
          if (["bilingual_html", "merged_html"].includes(stage || "") &&
            ["queued", "running", "draining", "paused"].includes(runStatus)) {
            return {
              tone: runStatus === "paused" ? "paused" : runStatus === "queued" ? "pending" : "running",
              label: runStatus === "paused" ? "导出已暂停" : runStatus === "queued" ? "待导出" : "导出中",
            };
          }
          if (entry.chapter_bilingual_export_count > 0) {
            return { tone: "succeeded", label: "部分可下载" };
          }
          if (runStatus === "failed" || runStatus === "cancelled") {
            return { tone: "failed", label: statusLabel(runStatus) };
          }
          if (!entry.latest_run_id) {
            return { tone: "active", label: "已入库" };
          }
          return { tone: "active", label: statusLabel(entry.status) };
        }

        function historyProgress(entry) {
          const total = Number(entry.latest_run_total_work_item_count || entry.packet_count || 0);
          const completed = Number(entry.latest_run_completed_work_item_count || 0);
          if (entry.merged_export_ready) {
            return { ratio: 1, text: "中文阅读包已经可直接下载。" };
          }
          if (entry.latest_run_current_stage === "translate" && total > 0) {
            return {
              ratio: Math.max(0, Math.min(1, completed / total)),
              text:
                "全文翻译阶段 · 已完成 " +
                formatNumber(completed) +
                " / " +
                formatNumber(total) +
                " 个 packet",
            };
          }
          if (entry.latest_run_current_stage === "review") {
            return { ratio: 0.76, text: "自动复核阶段 · 等待清理 blocker 后再进入导出。" };
          }
          if (entry.latest_run_current_stage === "bilingual_html") {
            return { ratio: 0.88, text: "双语章节包正在生成，整书中文阅读包尚未完成。" };
          }
          if (entry.latest_run_current_stage === "merged_html") {
            return { ratio: 0.96, text: "整书中文阅读稿正在导出，接近最终完成。" };
          }
          if (entry.chapter_bilingual_export_count > 0) {
            return { ratio: 0.82, text: "双语章节包已可用，中文阅读包仍待生成。" };
          }
          if (!entry.latest_run_id) {
            return { ratio: 0.15, text: "书籍已入库，尚未启动整书转换。" };
          }
          if (entry.latest_run_status === "failed" || entry.latest_run_status === "cancelled") {
            return { ratio: 0.36, text: "上次运行中断，可以打开这本书后继续处理或重试。" };
          }
          if (entry.latest_run_status === "paused") {
            return { ratio: 0.42, text: "上次运行已暂停，打开这本书后可以继续。" };
          }
          return { ratio: 0.28, text: "当前处于处理中，可以打开这本书查看详情。" };
        }

        function pipelineMeta(stepKey) {
          if (stepKey === "bootstrap" && state.currentDocument) {
            return "已准备 " + formatNumber(state.currentDocument.packet_count) + " 个 packet";
          }
          const detail = stageDetail(stepKey);
          if (!detail) {
            return "等待进入该阶段";
          }
          if (stepKey === "translate") {
            const progress = currentTranslateProgress();
            const inflight =
              Number(state.currentRun?.work_items?.status_counts?.running || 0) +
              Number(state.currentRun?.work_items?.status_counts?.leased || 0);
            const inflightText = inflight > 0 ? " · 进行中 " + formatNumber(inflight) : "";
            return (
              "已完成 " +
              formatNumber(progress.completed) +
              " / " +
              formatNumber(progress.total) +
              " 个 packet" +
              inflightText
            );
          }
          if (stepKey === "review") {
            return (
              "issues " +
              formatNumber(detail.total_issue_count ?? 0) +
              " · actions " +
              formatNumber(detail.total_action_count ?? 0)
            );
          }
          if (stepKey === "bilingual_html" || stepKey === "merged_html") {
            const exportCount = detail.chapter_export_count ?? 0;
            return exportCount > 0
              ? "导出记录 " + formatNumber(exportCount) + " 个章节结果"
              : "等待生成导出资产";
          }
          return detail.updated_at ? "最近更新 " + formatDate(detail.updated_at) : "等待进入该阶段";
        }

        function renderDocumentShell() {
          const summary = state.currentDocument;
          if (!summary) {
            els.documentShell.className = "empty-state";
            els.documentShell.innerHTML =
              "先上传一本英文书。解析成功后，这里会告诉你当前书籍是谁、最新 run 到了哪一环、为什么还不能导出，以及下一步应该怎么做。";
            return;
          }

          const badge = documentBadgeMeta();
          const pathHtml = summary.source_path
            ? `
              <div class="path-block">
                <div class="metric-label">源文件路径</div>
                <div class="mono-copy">${escapeHtml(summary.source_path)}</div>
              </div>
            `
            : "";
          els.documentShell.className = "book-stack";
          els.documentShell.innerHTML = `
            <div class="book-head">
              <div>
                <div class="section-kicker">Current book dossier</div>
                <h3 class="book-title">${escapeHtml(preferredTitle(summary))}</h3>
                <div class="book-meta">
                  ${escapeHtml(summary.author || "作者待识别")} ·
                  ${escapeHtml(sourceLabel(summary.source_type))} ·
                  文档状态 ${escapeHtml(statusLabel(summary.status))}
                </div>
              </div>
              <span class="status-badge ${escapeHtml(badge.tone)}">${escapeHtml(badge.label)}</span>
            </div>
            <p class="book-brief">${escapeHtml(nextMilestoneText())}</p>
            <div class="tag-row">
              <span class="tag">document ${escapeHtml(shorten(summary.document_id, 6))}</span>
              <span class="tag">最近运行 ${escapeHtml(statusLabel(summary.latest_run_status || "pending"))}</span>
              <span class="tag">packet ${formatNumber(summary.packet_count)}</span>
              <span class="tag">open issues ${formatNumber(summary.open_issue_count)}</span>
            </div>
            <div class="metric-grid">
              <div class="metric-card">
                <div class="metric-label">章节</div>
                <div class="metric-value">${formatNumber(summary.chapter_count)}</div>
                <div class="metric-note">已完成章节级拆分</div>
              </div>
              <div class="metric-card">
                <div class="metric-label">句子</div>
                <div class="metric-value">${formatNumber(summary.sentence_count)}</div>
                <div class="metric-note">翻译和 review 的底层计量单位</div>
              </div>
              <div class="metric-card">
                <div class="metric-label">双语导出</div>
                <div class="metric-value">${formatNumber(summary.chapter_bilingual_export_count)}</div>
                <div class="metric-note">可直接用于人工精校的章节结果</div>
              </div>
              <div class="metric-card">
                <div class="metric-label">阅读稿</div>
                <div class="metric-value">${summary.merged_export_ready ? "已就绪" : "未生成"}</div>
                <div class="metric-note">${escapeHtml(summary.latest_merged_export_at ? "更新于 " + formatDate(summary.latest_merged_export_at) : "等待最终导出")}</div>
              </div>
            </div>
            ${pathHtml}
          `;
        }

        function renderPipeline() {
          const action = runPrimaryAction();
          const failedStage = failedPipelineStage();
          els.startRun.textContent = action.label;
          els.startRun.disabled = action.disabled;

          els.pipelineSteps.innerHTML = PIPELINE_STEPS.map((step, index) => {
            const status = stageStatus(step.key);
            const isCurrent = currentStageKey() === step.key;
            const errorMessage = stageDetail(step.key)?.error_message
              ? `<div class="phase-copy">错误：${escapeHtml(stageDetail(step.key).error_message)}</div>`
              : "";
            return `
              <article class="phase-card ${isCurrent ? "current" : ""}">
                <div class="phase-index">${index + 1}</div>
                <div>
                  <div class="phase-title">${escapeHtml(step.label)}</div>
                  <div class="phase-copy">${escapeHtml(step.description)}</div>
                  <div class="phase-copy">${escapeHtml(pipelineMeta(step.key))}</div>
                  ${errorMessage}
                </div>
                <span class="status-badge ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
              </article>
            `;
          }).join("");

          const run = state.currentRun;
          if (!run) {
            els.runSummary.innerHTML = "";
            els.runActions.hidden = true;
            els.runActions.innerHTML = "";
            setBanner(els.runBanner, "当前还没有活跃的整书 run。上传书籍后即可开始。", "warning");
            return;
          }

          const currentStage = currentStageKey() || "waiting";
          const progress = currentTranslateProgress();
          const tone = failedStage
            ? "error"
            : run.status === "succeeded"
            ? "success"
            : run.status === "failed" || run.status === "cancelled"
              ? "error"
              : run.status === "paused"
                ? "warning"
                : "warning";
          const bannerMessage = failedStage && !["failed", "cancelled", "paused"].includes(run.status || "")
            ? "当前 run " +
              shorten(run.run_id, 6) +
              " 仍显示为“" +
              statusLabel(run.status) +
              "”，但阶段“" +
              pipelineStageLabel(failedStage.key) +
              "”已经失败。请先刷新状态，随后可直接重试。"
            : "当前 run " +
              shorten(run.run_id, 6) +
              " 处于“" +
              statusLabel(run.status) +
              "”状态，当前阶段：" +
              pipelineStageLabel(currentStage);
          setBanner(
            els.runBanner,
            bannerMessage,
            tone
          );

          els.runSummary.innerHTML = `
            <div class="summary-card">
              <div class="summary-label">当前阶段</div>
              <div class="summary-value">${escapeHtml(pipelineStageLabel(currentStage))}</div>
              <div class="metric-note">status ${escapeHtml(statusLabel(failedStage?.status || run.status))}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">Run ID</div>
              <div class="summary-value mono">${escapeHtml(shorten(run.run_id, 6))}</div>
              <div class="metric-note">created ${escapeHtml(formatDate(run.created_at))}</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">全文翻译</div>
              <div class="summary-value">${formatNumber(progress.completed)} / ${formatNumber(progress.total)}</div>
              <div class="metric-note">packet progress</div>
            </div>
            <div class="summary-card">
              <div class="summary-label">最近更新</div>
              <div class="summary-value">${escapeHtml(formatDate(run.updated_at))}</div>
              <div class="metric-note">worker leases ${formatNumber(run.worker_leases?.total_count || 0)}</div>
            </div>
          `;

          if (action.mode === "retry" || action.mode === "recover" || action.mode === "resume") {
            const staleRetry = action.mode === "retry" &&
              failedStage &&
              !["failed", "cancelled", "paused"].includes(run.status || "") &&
              failedStageRetryEligible();
            const title = action.mode === "resume"
              ? "这条整书运行目前是暂停态"
              : staleRetry
                ? "这条整书运行已经卡成 stale failed-stage，可直接重试"
              : action.mode === "retry"
                ? "修好配置后，可以直接重试这条整书运行"
                : "检测到阶段失败，先刷新状态再进入重试";
            const copy = action.mode === "resume"
              ? "继续后会沿用当前 run 继续往下跑，不会新建 document。"
              : staleRetry
                ? "点击后系统会先把旧 run 收敛为 `clean_retry_after_stale_run`，然后立即创建一条新的 lineage run。"
              : action.mode === "retry"
                ? "点击后会创建一条新的 lineage run，并沿用这本书和上一次的运行预算。"
                : "当前阶段已经失败，但 run 总状态还没完全收敛。你现在点击后，页面会先同步最新状态；一旦 run 进入 failed / cancelled / paused，就会立刻发起重试。";
            const buttonClass = action.mode === "resume" ? "action-button secondary" : "action-button gold";
            const buttonLabel = action.mode === "resume" ? "继续当前转换" : action.label;
            els.runActions.hidden = false;
            els.runActions.innerHTML = `
              <div class="run-action-title">${escapeHtml(title)}</div>
              <div class="run-action-copy">${escapeHtml(copy)}</div>
              <div class="button-row">
                <button id="retry-run-inline" class="${escapeHtml(buttonClass)}" type="button">
                  ${escapeHtml(buttonLabel)}
                </button>
              </div>
            `;
            document.getElementById("retry-run-inline")?.addEventListener("click", startOrResumeRun);
          } else {
            els.runActions.hidden = true;
            els.runActions.innerHTML = "";
          }
        }

        function downloadReady(key) {
          const summary = state.currentDocument;
          const dashboard = state.currentExportDashboard;
          if (!summary) {
            return false;
          }
          if (key === "merged_html") {
            return Boolean(summary.merged_export_ready);
          }
          if (key === "bilingual_html") {
            return Number(summary.chapter_bilingual_export_count || 0) > 0;
          }
          return Boolean(dashboard?.records?.some((record) => record.export_type === key && record.status === "succeeded"));
        }

        function assetAvailabilityText(key) {
          const summary = state.currentDocument;
          if (!summary) {
            return "请先载入当前书籍。";
          }
          if (downloadReady(key)) {
            return "已可直接下载";
          }
          const stage = currentStageKey();
          if (key === "merged_html") {
            if (stage === "translate") {
              return "全文翻译尚未完成，整书阅读包还不会生成。";
            }
            if (stage === "review") {
              return "review 未完成，整书阅读包仍被 gate 挡住。";
            }
            if (stage === "merged_html") {
              return "整书阅读包正在导出。";
            }
          }
          if (key === "bilingual_html") {
            if (stage === "translate" || stage === "review") {
              return "双语章节包会在 review 之后生成。";
            }
            if (stage === "bilingual_html") {
              return "双语章节包正在导出。";
            }
          }
          if (key === "review_package") {
            return "当 review 产物存在时，这里会开放下载。";
          }
          return "尚未生成";
        }

        function renderDownloads() {
          const documentId = state.currentDocument?.document_id;
          els.downloadShell.innerHTML = DELIVERY_ASSETS.map((asset) => {
            const ready = documentId ? downloadReady(asset.key) : false;
            const buttonClass = asset.tone === "primary"
              ? "action-button primary"
              : asset.tone === "secondary"
                ? "action-button secondary"
                : "action-button ghost";
            return `
              <article class="asset-card">
                <div class="asset-label">${escapeHtml(asset.label)}</div>
                <div class="asset-title">${escapeHtml(asset.title)}</div>
                <div class="asset-note">${escapeHtml(assetAvailabilityText(asset.key))}</div>
                <div class="button-row">
                  <button
                    class="${escapeHtml(buttonClass)}"
                    type="button"
                    data-action="download-export"
                    data-export-type="${escapeHtml(asset.key)}"
                    ${ready && documentId ? "" : "disabled"}
                  >
                    ${escapeHtml(asset.buttonText)}
                  </button>
                </div>
              </article>
            `;
          }).join("");

          const dashboard = state.currentExportDashboard;
          if (!state.currentDocument) {
            els.exportDashboardShell.className = "empty-state";
            els.exportDashboardShell.innerHTML = "当前还没有导出快照。进入导出阶段后，这里会展示最近产物、成本和导出历史。";
            return;
          }
          if (!dashboard) {
            els.exportDashboardShell.className = "empty-state";
            els.exportDashboardShell.innerHTML = "还没有拿到导出 dashboard。点击“刷新当前状态”后会重新同步。";
            return;
          }

          const records = (dashboard.records || []).slice(0, 5);
          const rows = records.map((record) => `
            <tr>
              <td>${escapeHtml(exportLabel(record.export_type))}</td>
              <td>${escapeHtml(statusLabel(record.status))}</td>
              <td>${escapeHtml(formatDate(record.created_at))}</td>
            </tr>
          `).join("");

          els.exportDashboardShell.className = "";
          els.exportDashboardShell.innerHTML = `
            <div class="snapshot-grid">
              <div class="snapshot-card">
                <div class="snapshot-label">导出总数</div>
                <div class="snapshot-value">${formatNumber(dashboard.export_count || 0)}</div>
                <div class="metric-note">successful ${formatNumber(dashboard.successful_export_count || 0)}</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">最近导出</div>
                <div class="snapshot-value">${escapeHtml(formatDate(dashboard.latest_export_at))}</div>
                <div class="metric-note">适合直接下载保存</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">翻译运行数</div>
                <div class="snapshot-value">${formatNumber(dashboard.translation_usage_summary?.run_count || 0)}</div>
                <div class="metric-note">token in ${formatNumber(dashboard.translation_usage_summary?.total_token_in || 0)}</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">Provider 成本</div>
                <div class="snapshot-value">${escapeHtml(formatMoney(dashboard.translation_usage_summary?.total_cost_usd || 0))}</div>
                <div class="metric-note">累计到当前导出快照</div>
              </div>
            </div>
            <div class="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>导出类型</th>
                    <th>状态</th>
                    <th>时间</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows || '<tr><td colspan="3">暂无导出记录</td></tr>'}
                </tbody>
              </table>
            </div>
          `;
        }

        function renderAttention() {
          const summary = state.currentDocument;
          const dashboard = state.currentExportDashboard;
          if (!summary) {
            els.attentionShell.className = "empty-state";
            els.attentionShell.innerHTML = "载入当前书籍后，这里会解释为什么现在能导出，或者为什么仍被 review gate 挡住。";
            return;
          }
          if (summary.merged_export_ready) {
            els.attentionShell.className = "attention-shell";
            els.attentionShell.innerHTML = `
              <div class="attention-card">
                <strong>整书交付已完成</strong>
                <div class="asset-note">中文阅读包已经生成，当前不再有导出阻塞。</div>
              </div>
            `;
            return;
          }
          if (!dashboard) {
            els.attentionShell.className = "empty-state";
            els.attentionShell.innerHTML = "还没有同步到 review / export dashboard，先点击“刷新当前状态”查看阻塞解释。";
            return;
          }

          const topBlocking = dashboard.issue_chapter_highlights?.top_blocking_chapter || null;
          const topOpen = dashboard.issue_chapter_highlights?.top_open_chapter || null;
          const hotspots = (dashboard.issue_hotspots || []).slice(0, 3);
          const blockers = blockingIssueCount();
          const why = summary.latest_run_status === "failed"
            ? "上一次整书运行失败，所以导出不会继续推进。"
            : blockers > 0
              ? "当前仍有 blocking review issue 未清理，所以系统不会放行整书导出。"
              : nextMilestoneText();
          const hotspotHtml = hotspots.length
            ? hotspots.map((entry) => `
                <div class="attention-card">
                  <strong>${escapeHtml(entry.issue_type)} · ${escapeHtml(entry.root_cause_layer || "unknown")}</strong>
                  <div class="asset-note">
                    issue ${formatNumber(entry.issue_count)} · open ${formatNumber(entry.open_issue_count)} · blocking ${formatNumber(entry.blocking_issue_count)}
                  </div>
                </div>
              `).join("")
            : `
              <div class="attention-card">
                <strong>暂未看到明显热点</strong>
                <div class="asset-note">系统还没有积累出足够的 issue hotspot 数据。</div>
              </div>
            `;

          els.attentionShell.className = "attention-shell";
          els.attentionShell.innerHTML = `
            <div class="attention-card">
              <strong>${escapeHtml(why)}</strong>
              <div class="asset-note">blocking issue ${formatNumber(blockers)} · latest stage ${escapeHtml(pipelineStageLabel(currentStageKey()))}</div>
            </div>
            <div class="snapshot-grid">
              <div class="snapshot-card">
                <div class="snapshot-label">阻塞问题</div>
                <div class="snapshot-value">${formatNumber(blockers)}</div>
                <div class="metric-note">当前 export gate 关注的 blocker 数</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">最重章节</div>
                <div class="snapshot-value">${escapeHtml(topBlocking ? String(topBlocking.ordinal) : "—")}</div>
                <div class="metric-note">${escapeHtml(topBlocking ? (topBlocking.title_src || "未命名章节") : "暂无")}</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">开放问题最多</div>
                <div class="snapshot-value">${escapeHtml(topOpen ? String(topOpen.ordinal) : "—")}</div>
                <div class="metric-note">${escapeHtml(topOpen ? (topOpen.title_src || "未命名章节") : "暂无")}</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">最近成本</div>
                <div class="snapshot-value">${escapeHtml(formatMoney(dashboard.translation_usage_summary?.total_cost_usd || 0))}</div>
                <div class="metric-note">provider cost</div>
              </div>
            </div>
            <div class="attention-list">${hotspotHtml}</div>
          `;
        }

        function renderChapters() {
          const summary = state.currentDocument;
          if (!summary) {
            els.chapterShell.className = "empty-state";
            els.chapterShell.innerHTML = "当前没有已载入书籍。文档到位后，这里会优先列出需要关注或可直接下载双语导出的章节。";
            return;
          }

          const chapters = summary.chapters || [];
          if (!chapters.length) {
            els.chapterShell.className = "empty-state";
            els.chapterShell.innerHTML = "当前书籍还没有章节明细。";
            return;
          }

          const exportedCount = chapters.filter((chapter) => chapter.bilingual_export_ready).length;
          const issueCount = chapters.filter((chapter) => Number(chapter.open_issue_count || 0) > 0).length;
          const focusChapters = [...chapters]
            .sort((left, right) => {
              const issueDelta = Number(right.open_issue_count || 0) - Number(left.open_issue_count || 0);
              if (issueDelta !== 0) {
                return issueDelta;
              }
              return Number(left.ordinal || 0) - Number(right.ordinal || 0);
            })
            .slice(0, 8);

          els.chapterShell.className = "chapter-list";
          els.chapterShell.innerHTML = `
            <div class="snapshot-grid">
              <div class="snapshot-card">
                <div class="snapshot-label">章节总数</div>
                <div class="snapshot-value">${formatNumber(chapters.length)}</div>
                <div class="metric-note">当前书籍的章节级概览</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">已有双语导出</div>
                <div class="snapshot-value">${formatNumber(exportedCount)}</div>
                <div class="metric-note">章节级双语资产已可下载</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">待关注章节</div>
                <div class="snapshot-value">${formatNumber(issueCount)}</div>
                <div class="metric-note">当前仍有 open issue 的章节数量</div>
              </div>
              <div class="snapshot-card">
                <div class="snapshot-label">当前排序</div>
                <div class="snapshot-value">Top 8</div>
                <div class="metric-note">按 open issue 优先，其次按章节序号</div>
              </div>
            </div>
            ${focusChapters.map((chapter) => `
              <article class="chapter-row">
                <div class="chapter-index">${formatNumber(chapter.ordinal)}</div>
                <div>
                  <div class="chapter-title">${escapeHtml(chapter.title_src || "未命名章节")}</div>
                  <div class="chapter-copy">
                    状态 ${escapeHtml(statusLabel(chapter.status))} ·
                    packet ${formatNumber(chapter.packet_count || 0)} ·
                    open issue ${formatNumber(chapter.open_issue_count || 0)}
                  </div>
                </div>
                <div class="button-row">
                  <button
                    class="action-button small ghost"
                    type="button"
                    data-action="download-chapter"
                    data-chapter-id="${escapeHtml(chapter.chapter_id)}"
                    ${chapter.bilingual_export_ready ? "" : "disabled"}
                  >
                    ${chapter.bilingual_export_ready ? "下载双语章节" : "等待导出"}
                  </button>
                </div>
              </article>
            `).join("")}
          `;
        }

        function eventTitle(entry) {
          const mapping = {
            "run.created": "创建 run",
            "run.started": "启动 run",
            "run.resumed": "继续 run",
            "run.paused": "暂停 run",
            "run.retry_requested": "请求重试",
            "run.cancelled": "取消 run",
            "run.succeeded": "run 完成",
            "run.failed": "run 失败",
          };
          return mapping[entry.event_type] || entry.event_type;
        }

        function renderEvents() {
          const events = state.currentRunEvents || [];
          if (!state.currentRun) {
            els.eventShell.className = "empty-state";
            els.eventShell.innerHTML = "当前没有可展示的运行事件。启动整书转换后，这里会显示最近的 run 审计时间线。";
            return;
          }
          if (!events.length) {
            els.eventShell.className = "empty-state";
            els.eventShell.innerHTML = "当前 run 已存在，但还没有可展示的审计事件。";
            return;
          }

          els.eventShell.className = "event-list";
          els.eventShell.innerHTML = events.map((entry) => `
            <article class="event-card">
              <div class="event-head">
                <div>
                  <div class="event-title">${escapeHtml(eventTitle(entry))}</div>
                  <div class="event-copy">${escapeHtml(entry.event_type)} · ${escapeHtml(formatDate(entry.created_at))}</div>
                </div>
                <span class="tag">${escapeHtml(entry.actor_type || "system")}</span>
              </div>
              <div class="event-copy">
                event ${escapeHtml(shorten(entry.event_id, 6))}
                ${entry.work_item_id ? " · work item " + escapeHtml(shorten(entry.work_item_id, 6)) : ""}
              </div>
            </article>
          `).join("");
        }

        function renderHistory() {
          const entries = state.historyEntries || [];
          if (!entries.length) {
            els.historyShell.className = "empty-state";
            els.historyShell.innerHTML = "当前筛选条件下没有命中的历史书籍。可以调整条件后重新查询。";
            return;
          }

          els.historyShell.className = "history-stack";
          els.historyShell.innerHTML = entries.map((entry) => {
            const badge = historyBadgeMeta(entry);
            const progress = historyProgress(entry);
            const ratioPercent = Math.round(progress.ratio * 100);
            const canRetry = Boolean(entry.latest_run_id) && ["failed", "cancelled"].includes(entry.latest_run_status || "");
            return `
              <article class="history-item">
                <div class="history-head">
                  <div>
                    <div class="history-label">${escapeHtml(sourceLabel(entry.source_type))}</div>
                    <div class="history-title">${escapeHtml(preferredTitle(entry))}</div>
                    <div class="history-copy">
                      ${escapeHtml(entry.author || "作者待识别")} ·
                      文档状态 ${escapeHtml(statusLabel(entry.status))}
                    </div>
                  </div>
                  <span class="status-badge ${escapeHtml(badge.tone)}">${escapeHtml(badge.label)}</span>
                </div>
                <div class="tag-row">
                  <span class="tag">document ${escapeHtml(shorten(entry.document_id, 6))}</span>
                  <span class="tag">章节 ${formatNumber(entry.chapter_count)}</span>
                  <span class="tag">句子 ${formatNumber(entry.sentence_count)}</span>
                  <span class="tag">阅读稿 ${entry.merged_export_ready ? "已生成" : "未生成"}</span>
                </div>
                <div class="history-progress">
                  <div class="meter"><span style="width: ${ratioPercent}%;"></span></div>
                  <div class="history-copy">${escapeHtml(progress.text)}</div>
                </div>
                <div class="history-copy">更新于 ${escapeHtml(formatDate(entry.updated_at))}</div>
                ${entry.source_path ? `<div class="history-path mono">${escapeHtml(entry.source_path)}</div>` : ""}
                <div class="button-row">
                  <button
                    class="action-button ghost small"
                    type="button"
                    data-action="open-history"
                    data-document-id="${escapeHtml(entry.document_id)}"
                  >
                    打开这本书
                  </button>
                  <button
                    class="action-button small"
                    type="button"
                    data-action="download-history-merged"
                    data-document-id="${escapeHtml(entry.document_id)}"
                    ${entry.merged_export_ready ? "" : "disabled"}
                  >
                    下载中文阅读包
                  </button>
                  <button
                    class="action-button gold small"
                    type="button"
                    data-action="retry-history-run"
                    data-document-id="${escapeHtml(entry.document_id)}"
                    data-run-id="${escapeHtml(entry.latest_run_id || "")}"
                    ${canRetry ? "" : "disabled"}
                  >
                    重试转换
                  </button>
                </div>
              </article>
            `;
          }).join("");
        }

        function renderAll() {
          renderDocumentShell();
          renderPipeline();
          renderDownloads();
          renderAttention();
          renderChapters();
          renderEvents();
          renderHistory();
        }

        async function refreshHealth() {
          try {
            await fetchJson(boot.healthHref);
            els.healthDot.className = "status-dot live";
            els.healthLabel.textContent = "服务正常";
          } catch (_error) {
            els.healthDot.className = "status-dot error";
            els.healthLabel.textContent = "服务异常";
          }
        }

        function updateSelectedFile(file) {
          state.selectedFile = file || null;
          if (!file) {
            els.selectedFileName.textContent = "还没有选中文件";
            els.selectedFileNote.textContent = "支持拖拽上传，也可以手动选择一本书开始。";
            return;
          }
          els.selectedFileName.textContent = file.name;
          els.selectedFileNote.textContent =
            "文件大小 " +
            formatNumber(Math.max(1, Math.round(file.size / 1024))) +
            " KB，准备上传并解析。";
        }

        async function uploadSelectedFile() {
          if (!state.selectedFile) {
            setBanner(els.uploadBanner, "请先选择一个 EPUB 或 PDF 文件。", "error");
            return;
          }
          const formData = new FormData();
          formData.append("source_file", state.selectedFile);
          setButtonLoading(els.uploadFile, true, "上传中…");
          try {
            const summary = await fetchJson(apiPrefix + "/documents/bootstrap-upload", {
              method: "POST",
              body: formData,
            });
            state.currentDocument = summary;
            state.currentRun = null;
            state.currentRunEvents = [];
            state.currentExportDashboard = null;
            rememberCurrentDocument(summary.document_id);
            renderAll();
            setBanner(
              els.uploadBanner,
              "上传成功，已解析《" + (preferredTitle(summary) || state.selectedFile.name) + "》。现在可以启动整书转换。",
              "success"
            );
            await refreshHistory().catch(() => undefined);
            await loadDocumentWithRetry(summary.document_id, {
              attempts: 6,
              delayMs: 250,
              silent: true,
            });
            document.getElementById("pipeline").scrollIntoView({ behavior: "smooth", block: "start" });
          } catch (error) {
            setBanner(els.uploadBanner, error.message, "error");
          } finally {
            setButtonLoading(els.uploadFile, false, "上传中…");
          }
        }

        async function loadRun(runId) {
          if (!runId) {
            state.currentRun = null;
            state.currentRunEvents = [];
            return;
          }
          const [run, events] = await Promise.all([
            fetchJson(apiPrefix + "/runs/" + encodeURIComponent(runId)),
            fetchJson(apiPrefix + "/runs/" + encodeURIComponent(runId) + "/events?limit=8&offset=0"),
          ]);
          state.currentRun = run;
          state.currentRunEvents = events.entries || [];
        }

        async function loadDocument(documentId) {
          const summary = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(documentId));
          state.currentDocument = summary;
          state.currentRun = null;
          state.currentRunEvents = [];
          state.currentExportDashboard = null;
          rememberCurrentDocument(documentId);

          const tasks = [
            fetchJson(
              apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/exports?limit=5&offset=0"
            ).catch(() => null),
          ];
          if (summary.latest_run_id) {
            tasks.push(loadRun(summary.latest_run_id).catch(() => null));
          }
          const results = await Promise.all(tasks);
          state.currentExportDashboard = results[0];
          renderAll();
          ensurePolling();
        }

        async function loadDocumentWithRetry(documentId, options) {
          const attempts = Math.max(1, options?.attempts || 1);
          const delayMs = Math.max(0, options?.delayMs || 0);
          const silent = Boolean(options?.silent);
          let lastError = null;
          for (let attempt = 0; attempt < attempts; attempt += 1) {
            try {
              await loadDocument(documentId);
              return true;
            } catch (error) {
              lastError = error;
              const isRetryableNotFound =
                typeof error.message === "string" && error.message.includes("Document not found");
              if (!isRetryableNotFound || attempt === attempts - 1) {
                if (!silent) {
                  throw error;
                }
                return false;
              }
              await delay(delayMs);
            }
          }
          if (!silent && lastError) {
            throw lastError;
          }
          return false;
        }

        async function syncCurrentDocument() {
          if (!state.currentDocument?.document_id) {
            return;
          }
          if (state.pollInFlight) {
            return;
          }
          state.pollInFlight = true;
          try {
            await loadDocument(state.currentDocument.document_id);
          } catch (_error) {
            // keep last successful frame during polling
          } finally {
            state.pollInFlight = false;
          }
        }

        function shouldPoll() {
          const status = state.currentRun?.status || state.currentDocument?.latest_run_status || null;
          return Boolean(state.currentDocument) && ["queued", "running", "draining", "paused"].includes(status);
        }

        function ensurePolling() {
          if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
          }
          if (!shouldPoll()) {
            return;
          }
          state.pollTimer = setInterval(syncCurrentDocument, 2500);
        }

        async function startOrResumeRun() {
          const action = runPrimaryAction();
          if (action.disabled) {
            return;
          }
          setButtonLoading(els.startRun, true, "处理中…");
          try {
            if (action.mode === "create") {
              const created = await fetchJson(apiPrefix + "/runs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  document_id: state.currentDocument.document_id,
                  run_type: "translate_full",
                  requested_by: "web-ui",
                  status_detail_json: {
                    source: "web-ui",
                    surface: "translation-studio",
                  },
                }),
              });
              state.currentRun = created;
              state.currentRun = await fetchJson(
                apiPrefix + "/runs/" + encodeURIComponent(created.run_id) + "/resume",
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    actor_id: "web-ui",
                    note: "start from production translation desk",
                  }),
                }
              );
            } else if (action.mode === "recover") {
              await loadDocument(state.currentDocument.document_id);
              const refreshedAction = runPrimaryAction();
              if (refreshedAction.mode !== "retry" && refreshedAction.mode !== "resume") {
                throw new Error(
                  "阶段失败已经被识别到，但这条 run 还没有收敛成可恢复状态。请再点一次“刷新当前状态”，或等待几秒后重试。"
                );
              }
              if (refreshedAction.mode === "resume") {
                state.currentRun = await fetchJson(
                  apiPrefix + "/runs/" + encodeURIComponent(refreshedAction.runId) + "/resume",
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      actor_id: "web-ui",
                      note: "resume after failed-stage refresh from production translation desk",
                    }),
                  }
                );
              } else {
                state.currentRun = await fetchJson(
                  apiPrefix + "/runs/" + encodeURIComponent(refreshedAction.runId) + "/retry",
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      actor_id: "web-ui",
                      note: "retry after failed-stage refresh from production translation desk",
                    }),
                  }
                );
              }
            } else if (action.mode === "resume") {
              state.currentRun = await fetchJson(
                apiPrefix + "/runs/" + encodeURIComponent(action.runId) + "/resume",
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    actor_id: "web-ui",
                    note: "resume from production translation desk",
                  }),
                }
              );
            } else if (action.mode === "retry") {
              state.currentRun = await fetchJson(
                apiPrefix + "/runs/" + encodeURIComponent(action.runId) + "/retry",
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    actor_id: "web-ui",
                    note: "retry from production translation desk",
                  }),
                }
              );
            }
            await loadDocumentWithRetry(state.currentDocument.document_id, {
              attempts: 4,
              delayMs: 200,
              silent: true,
            });
            setBanner(els.runBanner, "后台整书转换已启动，页面会自动同步翻译、review 和导出进度。", "success");
          } catch (error) {
            setBanner(els.runBanner, error.message, "error");
          } finally {
            setButtonLoading(els.startRun, false, "处理中…");
            renderPipeline();
          }
        }

        async function downloadDocumentExport(documentId, exportType) {
          const response = await fetchBinary(
            apiPrefix +
              "/documents/" +
              encodeURIComponent(documentId) +
              "/exports/download?export_type=" +
              encodeURIComponent(exportType)
          );
          return saveResponse(response, "book-agent-" + exportType + ".zip");
        }

        async function downloadChapterExport(documentId, chapterId) {
          const response = await fetchBinary(
            apiPrefix +
              "/documents/" +
              encodeURIComponent(documentId) +
              "/chapters/" +
              encodeURIComponent(chapterId) +
              "/exports/download?export_type=bilingual_html"
          );
          return saveResponse(response, chapterId + "-bilingual_html.zip");
        }

        async function refreshHistory() {
          const params = new URLSearchParams();
          params.set("limit", "12");
          params.set("offset", "0");
          if (els.historyQuery.value.trim()) {
            params.set("query", els.historyQuery.value.trim());
          }
          if (els.historyStatus.value) {
            params.set("status", els.historyStatus.value);
          }
          if (els.historyRunStatus.value) {
            params.set("latest_run_status", els.historyRunStatus.value);
          }
          if (els.historyMergedReady.value) {
            params.set("merged_export_ready", els.historyMergedReady.value);
          }
          try {
            const payload = await fetchJson(apiPrefix + "/documents/history?" + params.toString());
            state.historyEntries = payload.entries || [];
            state.historyMeta = payload;
            setBanner(
              els.historyBanner,
              "共找到 " + formatNumber(payload.total_count || 0) + " 本书，本页显示 " + formatNumber(payload.record_count || 0) + " 本。",
              "success"
            );
            renderHistory();
          } catch (error) {
            state.historyEntries = [];
            setBanner(els.historyBanner, error.message, "error");
            renderHistory();
          }
        }

        async function restoreCurrentContext() {
          if (state.restoringContext) {
            return;
          }
          state.restoringContext = true;
          try {
            const storedDocumentId = safeStorageGet(STORAGE_KEY_DOCUMENT);
            if (storedDocumentId) {
              const restored = await loadDocumentWithRetry(storedDocumentId, {
                attempts: 2,
                delayMs: 150,
                silent: true,
              });
              if (restored) {
                setBanner(els.uploadBanner, "已恢复上次查看的书籍上下文。", "success");
                return;
              }
              safeStorageRemove(STORAGE_KEY_DOCUMENT);
            }

            const activeEntry = state.historyEntries.find((entry) =>
              ["queued", "running", "draining", "paused"].includes(entry.latest_run_status || "")
            );
            if (activeEntry) {
              const restored = await loadDocumentWithRetry(activeEntry.document_id, {
                attempts: 2,
                delayMs: 150,
                silent: true,
              });
              if (restored) {
                setBanner(els.historyBanner, "已自动打开当前仍在处理中的书籍。", "success");
              }
            }
          } finally {
            state.restoringContext = false;
          }
        }

        function closestActionTarget(event) {
          return event.target.closest("[data-action]");
        }

        async function handleActionClick(event) {
          const target = closestActionTarget(event);
          if (!target) {
            return;
          }
          const action = target.dataset.action;
          try {
            if (action === "download-export") {
              const documentId = state.currentDocument?.document_id;
              if (!documentId) {
                throw new Error("请先载入当前书籍。");
              }
              target.disabled = true;
              const filename = await downloadDocumentExport(documentId, target.dataset.exportType);
              setBanner(els.runBanner, "已开始下载：" + filename, "success");
            }
            if (action === "download-chapter") {
              const documentId = state.currentDocument?.document_id;
              if (!documentId) {
                throw new Error("请先载入当前书籍。");
              }
              target.disabled = true;
              const filename = await downloadChapterExport(documentId, target.dataset.chapterId);
              setBanner(els.runBanner, "已开始下载章节文件：" + filename, "success");
            }
            if (action === "open-history") {
              await loadDocument(target.dataset.documentId);
              setBanner(els.historyBanner, "已打开这本书，并同步到当前工作台。", "success");
              document.getElementById("workspace").scrollIntoView({ behavior: "smooth", block: "start" });
            }
            if (action === "download-history-merged") {
              target.disabled = true;
              const filename = await downloadDocumentExport(target.dataset.documentId, "merged_html");
              setBanner(els.historyBanner, "已开始下载：" + filename, "success");
            }
            if (action === "retry-history-run") {
              target.disabled = true;
              const retried = await fetchJson(
                apiPrefix + "/runs/" + encodeURIComponent(target.dataset.runId) + "/retry",
                {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    actor_id: "web-ui",
                    note: "retry from history card in production translation desk",
                  }),
                }
              );
              await loadDocumentWithRetry(target.dataset.documentId, {
                attempts: 4,
                delayMs: 200,
                silent: true,
              });
              setBanner(els.historyBanner, "已创建新的 retry run：" + shorten(retried.run_id, 6), "success");
            }
          } catch (error) {
            const destination = action.startsWith("download-history") || action === "open-history"
              || action === "retry-history-run"
              ? els.historyBanner
              : els.runBanner;
            setBanner(destination, error.message, "error");
          } finally {
            if (target instanceof HTMLButtonElement) {
              target.disabled = false;
            }
          }
        }

        function handleDroppedFiles(files) {
          if (!files || !files.length) {
            return;
          }
          updateSelectedFile(files[0]);
        }

        function wireDropzone() {
          ["dragenter", "dragover"].forEach((eventName) => {
            els.fileDropzone.addEventListener(eventName, (event) => {
              event.preventDefault();
              els.fileDropzone.dataset.dragging = "true";
            });
          });
          ["dragleave", "drop"].forEach((eventName) => {
            els.fileDropzone.addEventListener(eventName, (event) => {
              event.preventDefault();
              els.fileDropzone.dataset.dragging = "false";
            });
          });
          els.fileDropzone.addEventListener("drop", (event) => {
            handleDroppedFiles(event.dataTransfer?.files);
          });
          els.fileDropzone.addEventListener("click", (event) => {
            if (event.target.closest("button")) {
              return;
            }
            els.sourceFile.click();
          });
        }

        function wireEvents() {
          els.pickFile.addEventListener("click", () => els.sourceFile.click());
          els.sourceFile.addEventListener("change", () => {
            handleDroppedFiles(els.sourceFile.files);
          });
          els.uploadFile.addEventListener("click", uploadSelectedFile);
          els.startRun.addEventListener("click", startOrResumeRun);
          els.refreshCurrent.addEventListener("click", syncCurrentDocument);
          els.historyForm.addEventListener("submit", (event) => {
            event.preventDefault();
            refreshHistory();
          });
          document.body.addEventListener("click", handleActionClick);
          wireDropzone();
        }

        async function initialize() {
          wireEvents();
          renderAll();
          await Promise.all([refreshHealth(), refreshHistory()]);
          await restoreCurrentContext();
        }

        initialize();
      })();
    </script>
  </body>
</html>
"""
    replacements = {
        "__APP_NAME__": escape(app_name),
        "__APP_VERSION__": escape(app_version),
        "__DOCS_HREF__": escape(docs_href),
        "__OPENAPI_HREF__": escape(openapi_href),
        "__HEALTH_HREF__": escape(health_href),
        "__BOOT_PAYLOAD__": boot_payload,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template
