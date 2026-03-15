from __future__ import annotations


def build_homepage_html(*, app_name: str, app_version: str, api_prefix: str) -> str:
    docs_href = f"{api_prefix}/docs"
    openapi_href = f"{api_prefix}/openapi.json"
    health_href = f"{api_prefix}/health"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Crimson+Pro:ital,wght@0,300;0,400;0,600;0,700;1,400&family=DM+Sans:ital,wght@0,400;0,500;0,700;1,400&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet" />
    <title>{app_name}</title>
    <meta
      name="description"
      content="Long-document translation control room for EPUB book translation, QA, rerun, export, and run control."
    />
    <style>
      :root {{
        --paper: #f7f2e8;
        --paper-strong: #efe6d3;
        --ink: #16232b;
        --ink-soft: #4e5f67;
        --teal: #0f6b62;
        --teal-strong: #0a4d49;
        --gold: #cb8f2f;
        --mist: rgba(255, 255, 255, 0.72);
        --line: rgba(22, 35, 43, 0.12);
        --shadow: 0 24px 80px rgba(13, 33, 35, 0.12);
        --radius-xl: 28px;
        --radius-lg: 22px;
        --radius-md: 16px;
        --radius-sm: 12px;
        --mono: "JetBrains Mono", "SF Mono", "Roboto Mono", monospace;
        --serif: "Crimson Pro", "Palatino Linotype", "Georgia", serif;
        --sans: "DM Sans", "Avenir Next", "Segoe UI", sans-serif;
      }}

      * {{
        box-sizing: border-box;
      }}

      html {{
        scroll-behavior: smooth;
      }}

      body {{
        margin: 0;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(203, 143, 47, 0.14), transparent 28%),
          radial-gradient(circle at top right, rgba(15, 107, 98, 0.18), transparent 32%),
          linear-gradient(180deg, #fbf8f1 0%, var(--paper) 40%, #f4ecdf 100%);
        font-family: var(--sans);
      }}

      a {{
        color: inherit;
        text-decoration: none;
      }}

      .page-shell {{
        min-height: 100vh;
        padding: 28px;
      }}

      .frame {{
        position: relative;
        overflow: hidden;
        max-width: 1440px;
        margin: 0 auto;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.65), rgba(255, 255, 255, 0.38));
        border: 1px solid rgba(255, 255, 255, 0.7);
        border-radius: 36px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(20px);
      }}

      .frame::before {{
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        background:
          linear-gradient(120deg, rgba(255, 255, 255, 0.45), transparent 34%),
          radial-gradient(circle at 78% 16%, rgba(203, 143, 47, 0.18), transparent 22%);
      }}

      .nav {{
        position: sticky;
        top: 0;
        z-index: 10;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 24px;
        padding: 20px 36px;
        background: linear-gradient(180deg, rgba(251, 248, 241, 0.95), rgba(251, 248, 241, 0.82));
        backdrop-filter: blur(18px);
        border-bottom: 1px solid rgba(22, 35, 43, 0.06);
      }}

      .brand {{
        display: grid;
        gap: 2px;
      }}

      .brand-kicker {{
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        color: var(--teal);
        font-weight: 700;
      }}

      .brand-name {{
        font-family: var(--serif);
        font-size: 30px;
        line-height: 1;
        letter-spacing: -0.02em;
        font-weight: 600;
      }}

      .brand-note {{
        color: var(--ink-soft);
        font-size: 14px;
      }}

      .nav-links {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
        justify-content: flex-end;
      }}

      .nav-pill {{
        padding: 10px 14px;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.58);
        font-size: 14px;
        color: var(--ink-soft);
      }}

      .nav-pill.primary {{
        background: var(--teal);
        border-color: var(--teal);
        color: white;
        box-shadow: 0 14px 30px rgba(15, 107, 98, 0.25);
      }}

      .hero {{
        display: grid;
        grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.95fr);
        gap: 32px;
        padding: 48px 36px 20px;
      }}

      .hero-panel,
      .hero-aside,
      .section-card,
      .stat-card,
      .cap-card,
      .signal-card,
      .surface-card {{
        position: relative;
        background: var(--mist);
        border: 1px solid rgba(255, 255, 255, 0.72);
        border-radius: var(--radius-xl);
        backdrop-filter: blur(14px);
      }}

      .hero-panel {{
        padding: 40px 36px;
        display: grid;
        gap: 24px;
        position: relative;
        overflow: hidden;
      }}

      .hero-kicker {{
        display: inline-flex;
        align-items: center;
        gap: 10px;
        width: fit-content;
        padding: 8px 16px;
        border-radius: 999px;
        background: rgba(15, 107, 98, 0.07);
        border: 1px solid rgba(15, 107, 98, 0.12);
        color: var(--teal-strong);
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        font-size: 11px;
      }}

      .hero-title {{
        margin: 0;
        font-family: var(--serif);
        font-size: clamp(34px, 3.8vw, 54px);
        line-height: 1.12;
        letter-spacing: -0.025em;
        font-weight: 600;
      }}

      .hero-copy {{
        max-width: 56ch;
        color: var(--ink-soft);
        font-size: 17px;
        line-height: 1.78;
      }}

      .hero-actions {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }}

      .cta {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        padding: 14px 18px;
        border-radius: 999px;
        font-size: 15px;
        font-weight: 700;
        border: 1px solid var(--line);
        transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
      }}

      .cta:hover {{
        transform: translateY(-1px);
      }}

      .cta-primary {{
        background: linear-gradient(135deg, var(--teal) 0%, #0d5956 100%);
        color: white;
        border-color: transparent;
        box-shadow: 0 18px 32px rgba(15, 107, 98, 0.28);
      }}

      .cta-secondary {{
        background: rgba(255, 255, 255, 0.78);
        color: var(--ink);
      }}

      .hero-stats {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
      }}

      .stat-card {{
        padding: 20px;
        display: grid;
        gap: 8px;
        border-left: 3px solid transparent;
        border-image: linear-gradient(180deg, var(--teal), rgba(15, 107, 98, 0.15)) 1;
      }}

      .stat-label {{
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--ink-soft);
      }}

      .stat-value {{
        font-family: var(--serif);
        font-size: 30px;
        line-height: 1;
        font-weight: 600;
      }}

      .stat-note {{
        font-size: 13px;
        line-height: 1.5;
        color: var(--ink-soft);
      }}

      .hero-aside {{
        padding: 28px 24px;
        display: grid;
        gap: 18px;
        align-content: start;
        background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(239, 230, 211, 0.55));
        border-left: 3px solid rgba(15, 107, 98, 0.12);
      }}

      .aside-header {{
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }}

      .aside-title {{
        margin: 0;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--ink-soft);
      }}

      .aside-kpi {{
        margin: 6px 0 0;
        font-family: var(--serif);
        font-size: 32px;
        line-height: 1;
      }}

      .health-badge {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid var(--line);
        font-size: 13px;
        color: var(--ink-soft);
      }}

      .health-dot {{
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #b28d58;
        box-shadow: 0 0 0 4px rgba(178, 141, 88, 0.16);
      }}

      .health-dot.ok {{
        background: var(--teal);
        box-shadow: 0 0 0 4px rgba(15, 107, 98, 0.16);
      }}

      .signal-stack {{
        display: grid;
        gap: 12px;
      }}

      .signal-card {{
        padding: 14px 16px;
        display: grid;
        gap: 6px;
      }}

      .signal-label {{
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--ink-soft);
      }}

      .signal-value {{
        font-size: 16px;
        font-weight: 700;
      }}

      .signal-note {{
        font-size: 13px;
        line-height: 1.6;
        color: var(--ink-soft);
      }}

      .main-grid {{
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
        gap: 28px;
        padding: 28px 36px 36px;
      }}

      .stack {{
        display: grid;
        gap: 24px;
      }}

      .section-card {{
        padding: 32px;
      }}

      .section-title {{
        margin: 0 0 12px;
        font-family: var(--serif);
        font-size: 32px;
        line-height: 1.1;
        letter-spacing: -0.03em;
        font-weight: 600;
        position: relative;
        padding-bottom: 16px;
      }}

      .section-title::after {{
        content: "";
        position: absolute;
        bottom: 0;
        left: 0;
        width: 48px;
        height: 2px;
        background: linear-gradient(90deg, var(--teal), var(--gold));
        border-radius: 2px;
      }}

      .section-copy {{
        margin: 8px 0 0;
        color: var(--ink-soft);
        font-size: 15px;
        line-height: 1.78;
      }}

      .workflow {{
        margin-top: 22px;
        display: grid;
        gap: 14px;
      }}

      .workflow-step {{
        display: grid;
        grid-template-columns: 56px minmax(0, 1fr);
        gap: 14px;
        align-items: start;
        padding: 16px;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.54);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .workflow-index {{
        width: 56px;
        height: 56px;
        border-radius: 16px;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, rgba(15, 107, 98, 0.12), rgba(203, 143, 47, 0.14));
        color: var(--teal-strong);
        font-family: var(--serif);
        font-size: 24px;
        flex-shrink: 0;
      }}

      .workflow-label {{
        font-size: 18px;
        font-weight: 700;
      }}

      .workflow-body {{
        margin-top: 4px;
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.7;
      }}

      .cap-grid,
      .surface-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
        margin-top: 20px;
      }}

      .cap-card,
      .surface-card {{
        padding: 18px;
        min-height: 170px;
      }}

      .cap-kicker,
      .surface-kicker {{
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--teal);
        font-weight: 700;
      }}

      .cap-title,
      .surface-title {{
        margin: 10px 0 8px;
        font-size: 18px;
        line-height: 1.25;
        font-weight: 700;
      }}

      .cap-body,
      .surface-body {{
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.7;
      }}

      .signal-matrix {{
        display: grid;
        gap: 12px;
        margin-top: 20px;
      }}

      .mini-list {{
        margin: 0;
        padding: 0;
        list-style: none;
        display: grid;
        gap: 10px;
      }}

      .mini-list li {{
        display: grid;
        gap: 4px;
        padding: 12px 14px;
        border-radius: var(--radius-sm);
        background: rgba(255, 255, 255, 0.52);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .mini-list strong {{
        font-size: 14px;
      }}

      .mini-list span {{
        font-size: 13px;
        line-height: 1.6;
        color: var(--ink-soft);
      }}

      .api-shell {{
        margin-top: 18px;
        padding: 18px;
        border-radius: var(--radius-lg);
        background: linear-gradient(180deg, rgba(22, 35, 43, 0.96), rgba(14, 26, 31, 0.96));
        color: #ebf1ee;
      }}

      .api-row {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 10px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      }}

      .api-row:last-child {{
        border-bottom: none;
      }}

      .api-method {{
        display: inline-flex;
        min-width: 60px;
        justify-content: center;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(203, 143, 47, 0.18);
        color: #f5d9a8;
        font-size: 12px;
        font-family: var(--mono);
        font-weight: 700;
        text-transform: uppercase;
      }}

      .api-path {{
        flex: 1;
        font-family: var(--mono);
        font-size: 13px;
        color: #d8e4de;
      }}

      .api-copy {{
        color: #97aea4;
        font-size: 13px;
      }}

      .footer {{
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 12px 20px;
        padding: 16px 36px 32px;
        color: var(--ink-soft);
        font-size: 13px;
        border-top: 1px solid var(--line);
        margin: 0 36px;
      }}

      .footer code {{
        font-family: var(--mono);
        font-size: 12px;
      }}

      .control-deck {{
        padding: 8px 28px 28px;
      }}

      .refresh-strip {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
        margin-bottom: 24px;
      }}

      .refresh-card {{
        padding: 18px 20px;
        border-radius: var(--radius-lg);
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(247, 242, 232, 0.62));
        border: 1px solid rgba(255, 255, 255, 0.78);
        display: grid;
        gap: 10px;
      }}

      .refresh-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }}

      .refresh-kicker {{
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--ink-soft);
        font-weight: 700;
      }}

      .refresh-state {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-size: 14px;
        font-weight: 700;
      }}

      .status-dot {{
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #b28d58;
        box-shadow: 0 0 0 4px rgba(178, 141, 88, 0.14);
      }}

      .status-dot.live {{
        background: var(--teal);
        box-shadow: 0 0 0 4px rgba(15, 107, 98, 0.18);
      }}

      .status-dot.refreshing {{
        background: var(--gold);
        box-shadow: 0 0 0 4px rgba(203, 143, 47, 0.18);
        animation: pulse 1.4s ease-in-out infinite;
      }}

      .status-dot.stale {{
        background: #a84f4f;
        box-shadow: 0 0 0 4px rgba(168, 79, 79, 0.14);
      }}

      @keyframes pulse {{
        0%, 100% {{
          transform: scale(1);
          opacity: 1;
        }}
        50% {{
          transform: scale(1.12);
          opacity: 0.72;
        }}
      }}

      .refresh-meta {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.6;
      }}

      .workspace-shell {{
        display: grid;
        grid-template-columns: minmax(0, 1.25fr) minmax(340px, 0.75fr);
        gap: 24px;
      }}

      .workspace-card {{
        position: relative;
        overflow: hidden;
        padding: 24px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(248, 242, 230, 0.6));
        border: 1px solid rgba(255, 255, 255, 0.78);
        border-radius: var(--radius-xl);
        backdrop-filter: blur(16px);
      }}

      .workspace-card.full-width {{
        grid-column: 1 / -1;
      }}

      .workspace-header {{
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 18px;
      }}

      .workspace-header-text {{
        max-width: 68ch;
      }}

      .workspace-kicker {{
        margin: 0 0 8px;
        color: var(--teal);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
      }}

      .workspace-title {{
        margin: 0;
        font-family: var(--serif);
        font-size: 30px;
        line-height: 1.1;
        letter-spacing: -0.03em;
        font-weight: 600;
      }}

      .workspace-copy {{
        margin: 10px 0 0;
        color: var(--ink-soft);
        font-size: 15px;
        line-height: 1.72;
      }}

      .control-chip {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.8);
        border: 1px solid var(--line);
        color: var(--ink-soft);
        font-size: 13px;
        white-space: nowrap;
      }}

      .workspace-grid {{
        display: grid;
        grid-template-columns: repeat(12, minmax(0, 1fr));
        gap: 16px;
      }}

      .panel {{
        grid-column: span 12;
        padding: 18px;
        border-radius: var(--radius-lg);
        background: rgba(255, 255, 255, 0.52);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .panel.half {{
        grid-column: span 6;
      }}

      .panel.third {{
        grid-column: span 4;
      }}

      .panel-title {{
        margin: 0;
        font-size: 18px;
        line-height: 1.2;
      }}

      .panel-copy {{
        margin: 8px 0 0;
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.66;
      }}

      .form-grid {{
        display: grid;
        gap: 12px;
        margin-top: 16px;
      }}

      .field-grid {{
        display: grid;
        grid-template-columns: repeat(12, minmax(0, 1fr));
        gap: 12px;
      }}

      .field {{
        display: grid;
        gap: 8px;
      }}

      .visually-hidden {{
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }}

      .field.span-12 {{
        grid-column: span 12;
      }}

      .field.span-8 {{
        grid-column: span 8;
      }}

      .field.span-6 {{
        grid-column: span 6;
      }}

      .field.span-4 {{
        grid-column: span 4;
      }}

      .field label {{
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--ink-soft);
      }}

      .field input,
      .field select,
      .field textarea {{
        width: 100%;
        border: 1px solid rgba(22, 35, 43, 0.14);
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.9);
        color: var(--ink);
        padding: 12px 14px;
        font: inherit;
        outline: none;
        transition: border-color 160ms ease, box-shadow 160ms ease;
      }}

      .field textarea {{
        min-height: 112px;
        resize: vertical;
      }}

      .field input:focus,
      .field select:focus,
      .field textarea:focus {{
        border-color: rgba(15, 107, 98, 0.36);
        box-shadow: 0 0 0 4px rgba(15, 107, 98, 0.12);
      }}

      .field-hint {{
        color: var(--ink-soft);
        font-size: 12px;
        line-height: 1.6;
      }}

      .file-picker {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 16px;
        border: 1px solid rgba(22, 35, 43, 0.14);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.9);
        transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
      }}

      .file-picker:focus-within {{
        border-color: rgba(15, 107, 98, 0.36);
        box-shadow: 0 0 0 4px rgba(15, 107, 98, 0.12);
      }}

      .file-picker[data-has-file="true"] {{
        border-color: rgba(15, 107, 98, 0.24);
        background: rgba(237, 248, 246, 0.92);
      }}

      .file-picker-copy {{
        min-width: 0;
        display: grid;
        gap: 4px;
      }}

      .file-picker-title {{
        font-size: 15px;
        font-weight: 700;
      }}

      .file-picker-note {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.6;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}

      .button-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 12px;
      }}

      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid rgba(22, 35, 43, 0.12);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.86);
        color: var(--ink);
        padding: 12px 16px;
        font: inherit;
        font-size: 14px;
        font-weight: 700;
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
      }}

      .button:hover {{
        transform: translateY(-1px);
      }}

      .button.primary {{
        background: linear-gradient(135deg, var(--teal) 0%, #0d5956 100%);
        color: white;
        border-color: transparent;
        box-shadow: 0 12px 28px rgba(15, 107, 98, 0.2);
      }}

      .button.gold {{
        background: linear-gradient(135deg, #c88d2f 0%, #a9721e 100%);
        color: white;
        border-color: transparent;
        box-shadow: 0 12px 28px rgba(201, 143, 47, 0.22);
      }}

      .button.ghost {{
        background: rgba(255, 255, 255, 0.52);
      }}

      .button:disabled {{
        cursor: not-allowed;
        opacity: 0.55;
        transform: none;
        box-shadow: none;
      }}

      .status-banner {{
        margin-top: 16px;
        padding: 12px 14px;
        border-radius: 14px;
        border: 1px solid rgba(22, 35, 43, 0.08);
        background: rgba(255, 255, 255, 0.68);
        color: var(--ink-soft);
        font-size: 14px;
        line-height: 1.6;
      }}

      .status-banner.error {{
        border-color: rgba(151, 61, 61, 0.22);
        background: rgba(171, 71, 63, 0.08);
        color: #7e312b;
      }}

      .status-banner.success {{
        border-color: rgba(15, 107, 98, 0.22);
        background: rgba(15, 107, 98, 0.08);
        color: #0c5750;
      }}

      .kpi-grid {{
        margin-top: 16px;
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
      }}

      .kpi-card {{
        padding: 14px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .kpi-label {{
        color: var(--ink-soft);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }}

      .kpi-value {{
        margin-top: 6px;
        font-family: var(--serif);
        font-size: 28px;
        line-height: 1;
      }}

      .kpi-note {{
        margin-top: 6px;
        color: var(--ink-soft);
        font-size: 12px;
        line-height: 1.55;
      }}

      .result-shell {{
        margin-top: 18px;
        display: grid;
        gap: 14px;
      }}

      .result-card {{
        padding: 16px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.6);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .result-head {{
        display: flex;
        justify-content: space-between;
        gap: 10px;
        align-items: flex-start;
        margin-bottom: 10px;
      }}

      .result-title {{
        margin: 0;
        font-size: 17px;
      }}

      .result-meta {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.6;
      }}

      .pill-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 8px;
      }}

      .pill {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        border-radius: 999px;
        padding: 7px 10px;
        background: rgba(15, 107, 98, 0.08);
        border: 1px solid rgba(15, 107, 98, 0.12);
        color: var(--teal-strong);
        font-size: 12px;
        font-weight: 700;
      }}

      .pill.warn {{
        background: rgba(201, 143, 47, 0.12);
        border-color: rgba(201, 143, 47, 0.16);
        color: #8a6318;
      }}

      .pill.soft {{
        background: rgba(22, 35, 43, 0.06);
        border-color: rgba(22, 35, 43, 0.08);
        color: var(--ink-soft);
      }}

      .data-table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 10px;
      }}

      .data-table th,
      .data-table td {{
        padding: 10px 8px;
        border-bottom: 1px solid rgba(22, 35, 43, 0.08);
        text-align: left;
        vertical-align: top;
        font-size: 13px;
      }}

      .data-table th {{
        color: var(--ink-soft);
        font-weight: 700;
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}

      .queue-grid {{
        display: grid;
        gap: 12px;
        margin-top: 14px;
      }}

      .queue-item {{
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.62);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .queue-item.clickable {{
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
      }}

      .queue-item.clickable:hover {{
        transform: translateY(-1px);
        box-shadow: 0 16px 32px rgba(22, 35, 43, 0.08);
      }}

      .queue-item.is-active {{
        border-color: rgba(15, 107, 98, 0.28);
        box-shadow: 0 0 0 4px rgba(15, 107, 98, 0.1);
      }}

      .queue-head {{
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
      }}

      .queue-rank {{
        width: 36px;
        height: 36px;
        display: grid;
        place-items: center;
        border-radius: 12px;
        background: linear-gradient(135deg, rgba(15, 107, 98, 0.16), rgba(203, 143, 47, 0.16));
        font-family: var(--serif);
        font-size: 20px;
      }}

      .queue-title {{
        margin: 0;
        font-size: 16px;
      }}

      .queue-meta {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.65;
      }}

      .table-button {{
        appearance: none;
        border: none;
        background: transparent;
        padding: 0;
        color: var(--teal-strong);
        font: inherit;
        font-weight: 700;
        text-align: left;
        cursor: pointer;
      }}

      .table-button:hover {{
        text-decoration: underline;
      }}

      .detail-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
        margin-top: 14px;
      }}

      .detail-block {{
        padding: 14px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.56);
        border: 1px solid rgba(22, 35, 43, 0.08);
      }}

      .detail-block.full {{
        grid-column: 1 / -1;
      }}

      .detail-label {{
        color: var(--ink-soft);
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-weight: 700;
      }}

      .detail-value {{
        margin-top: 6px;
        font-size: 15px;
        line-height: 1.65;
      }}

      .assignment-inline {{
        display: grid;
        gap: 12px;
        margin-top: 14px;
      }}

      .assignment-inline .field-grid {{
        margin: 0;
      }}

      .filter-toolbar {{
        margin-top: 16px;
        padding: 16px;
        border-radius: var(--radius-lg);
        background: rgba(255, 255, 255, 0.48);
        border: 1px solid rgba(22, 35, 43, 0.08);
        display: grid;
        gap: 14px;
      }}

      .filter-summary {{
        margin-top: 12px;
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.6;
      }}

      .owner-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        margin-top: 14px;
      }}

      .alert-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        margin-top: 14px;
      }}

      .alert-card {{
        padding: 16px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(22, 35, 43, 0.1);
        background: rgba(255, 255, 255, 0.72);
        display: grid;
        gap: 8px;
      }}

      .alert-card.warn {{
        background: rgba(252, 245, 230, 0.94);
        border-color: rgba(201, 143, 47, 0.24);
      }}

      .alert-card.danger {{
        background: rgba(250, 236, 234, 0.94);
        border-color: rgba(168, 79, 79, 0.24);
      }}

      .alert-card.success {{
        background: rgba(236, 248, 245, 0.94);
        border-color: rgba(15, 107, 98, 0.24);
      }}

      .alert-title {{
        font-size: 15px;
        font-weight: 700;
      }}

      .alert-body {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.65;
      }}

      .owner-card {{
        width: 100%;
        text-align: left;
        padding: 16px;
        border-radius: var(--radius-md);
        border: 1px solid rgba(22, 35, 43, 0.1);
        background: rgba(255, 255, 255, 0.72);
        display: grid;
        gap: 8px;
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
      }}

      .owner-card:hover {{
        transform: translateY(-1px);
        box-shadow: 0 10px 24px rgba(22, 35, 43, 0.08);
      }}

      .owner-card.is-active {{
        border-color: rgba(15, 107, 98, 0.44);
        box-shadow: 0 12px 28px rgba(15, 107, 98, 0.12);
        background: rgba(237, 248, 246, 0.94);
      }}

      .owner-card-head {{
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
      }}

      .owner-card-title {{
        font-size: 16px;
        font-weight: 700;
      }}

      .owner-card-meta {{
        color: var(--ink-soft);
        font-size: 13px;
        line-height: 1.6;
      }}

      .toggle-row {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 10px;
        margin-top: 14px;
      }}

      .toggle-button[data-active="true"] {{
        background: rgba(15, 107, 98, 0.12);
        border-color: rgba(15, 107, 98, 0.2);
        color: var(--teal-strong);
      }}

      .subtle-note {{
        color: var(--ink-soft);
        font-size: 12px;
        line-height: 1.65;
      }}

      .log-shell {{
        margin-top: 14px;
        padding: 14px;
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(22, 35, 43, 0.97), rgba(16, 28, 33, 0.97));
        color: #d9e6df;
        font-family: var(--mono);
        font-size: 12px;
        line-height: 1.7;
        max-height: 340px;
        overflow: auto;
      }}

      .log-line {{
        padding-bottom: 8px;
        margin-bottom: 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      }}

      .placeholder {{
        padding: 18px;
        border-radius: 18px;
        border: 1px dashed rgba(22, 35, 43, 0.16);
        color: var(--ink-soft);
        background: rgba(255, 255, 255, 0.42);
        font-size: 14px;
        line-height: 1.7;
      }}

      @media (max-width: 1120px) {{
        .refresh-strip,
        .hero,
        .main-grid {{
          grid-template-columns: 1fr;
        }}

        .workspace-shell {{
          grid-template-columns: 1fr;
        }}

        .panel.half,
        .panel.third {{
          grid-column: span 12;
        }}
      }}

      @media (max-width: 820px) {{
        .page-shell {{
          padding: 12px;
        }}

        .frame {{
          border-radius: 24px;
        }}

        .nav,
        .hero,
        .main-grid,
        .footer {{
          padding-left: 18px;
          padding-right: 18px;
        }}

        .hero-title {{
          font-size: clamp(38px, 15vw, 58px);
        }}

        .hero-stats,
        .cap-grid,
        .surface-grid,
        .kpi-grid {{
          grid-template-columns: 1fr;
        }}

        .nav {{
          align-items: flex-start;
          flex-direction: column;
        }}

        .nav-links {{
          justify-content: flex-start;
        }}

        .control-deck {{
          padding-left: 18px;
          padding-right: 18px;
        }}

        .field.span-8,
        .field.span-6,
        .field.span-4 {{
          grid-column: span 12;
        }}

        .file-picker {{
          align-items: stretch;
          flex-direction: column;
        }}

        .detail-grid {{
          grid-template-columns: 1fr;
        }}

        .owner-grid {{
          grid-template-columns: 1fr;
        }}

        .alert-grid {{
          grid-template-columns: 1fr;
        }}
      }}

      @keyframes fadeSlideUp {{
        from {{
          opacity: 0;
          transform: translateY(28px);
        }}
        to {{
          opacity: 1;
          transform: translateY(0);
        }}
      }}

      @keyframes fadeIn {{
        from {{ opacity: 0; }}
        to {{ opacity: 1; }}
      }}

      @keyframes gentlePulse {{
        0%, 100% {{ opacity: 0.07; }}
        50% {{ opacity: 0.12; }}
      }}

      .hero-panel {{
        animation: fadeSlideUp 0.9s cubic-bezier(0.22, 1, 0.36, 1) both;
      }}

      .hero-aside {{
        animation: fadeSlideUp 0.9s cubic-bezier(0.22, 1, 0.36, 1) 0.12s both;
      }}

      .main-grid .stack:first-child > .section-card:nth-child(1) {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.18s both;
      }}

      .main-grid .stack:first-child > .section-card:nth-child(2) {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.26s both;
      }}

      .main-grid .stack:last-child > .section-card:nth-child(1) {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.22s both;
      }}

      .main-grid .stack:last-child > .section-card:nth-child(2) {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.30s both;
      }}

      .main-grid .stack:last-child > .section-card:nth-child(3) {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.38s both;
      }}

      .workspace-card {{
        animation: fadeSlideUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) 0.1s both;
      }}

      .hero-illustration {{
        position: absolute;
        right: -20px;
        bottom: 0;
        width: 320px;
        height: 280px;
        color: var(--teal);
        opacity: 0.07;
        pointer-events: none;
        animation: gentlePulse 8s ease-in-out infinite;
      }}

      .workflow-step {{
        transition: transform 200ms ease, box-shadow 200ms ease;
      }}

      .workflow-step:hover {{
        transform: translateY(-2px);
        box-shadow: 0 12px 32px rgba(22, 35, 43, 0.08);
      }}

      .cap-card,
      .surface-card {{
        transition: transform 200ms ease, box-shadow 200ms ease;
      }}

      .cap-card:hover,
      .surface-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 12px 32px rgba(22, 35, 43, 0.08);
      }}

      .stat-card {{
        transition: transform 200ms ease, box-shadow 200ms ease;
      }}

      .stat-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(22, 35, 43, 0.06);
      }}

      .nav-pill {{
        transition: transform 180ms ease, background 180ms ease, box-shadow 180ms ease;
      }}

      .nav-pill:hover {{
        transform: translateY(-1px);
        background: rgba(255, 255, 255, 0.82);
        box-shadow: 0 4px 12px rgba(22, 35, 43, 0.06);
      }}

      .nav-pill.primary:hover {{
        background: var(--teal-strong);
        box-shadow: 0 14px 30px rgba(15, 107, 98, 0.32);
      }}

      .workspace-title {{
        position: relative;
        padding-bottom: 16px;
      }}

      .workspace-title::after {{
        content: "";
        position: absolute;
        bottom: 0;
        left: 0;
        width: 48px;
        height: 2px;
        background: linear-gradient(90deg, var(--teal), var(--gold));
        border-radius: 2px;
      }}

      .hero-copy {{
        max-width: 56ch;
      }}

      @media print {{
        body {{
          background: white;
        }}

        .page-shell {{
          padding: 0;
        }}

        .frame {{
          box-shadow: none;
          border: none;
          background: white;
        }}

        .nav,
        .hero-aside,
        .footer {{
          display: none;
        }}

        .hero,
        .main-grid {{
          display: block;
          padding: 0;
        }}

        .section-card,
        .hero-panel {{
          break-inside: avoid;
          border: 1px solid #ddd;
          margin-bottom: 16px;
        }}
      }}
    </style>
  </head>
  <body>
    <div class="page-shell">
      <div class="frame">
        <header class="nav" id="top">
          <div class="brand">
            <span class="brand-kicker">Book Translation Control Room</span>
            <span class="brand-name">{app_name}</span>
            <span class="brand-note">Version {app_version} · EPUB-first · sentence-aligned · export-gated</span>
          </div>
          <nav class="nav-links" aria-label="Primary">
            <a class="nav-pill" href="#workflow">Workflow</a>
            <a class="nav-pill" href="#workspace">Workspace</a>
            <a class="nav-pill" href="#surfaces">Surfaces</a>
            <a class="nav-pill" href="#api">API</a>
            <a class="nav-pill primary" href="{docs_href}">Open API Docs</a>
          </nav>
        </header>

        <section class="hero">
          <article class="hero-panel">
            <svg class="hero-illustration" viewBox="0 0 400 320" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M200 55 C160 50 80 60 50 80 L50 250 C80 235 160 228 200 258 C240 228 320 235 350 250 L350 80 C320 60 240 50 200 55Z" stroke="currentColor" stroke-width="1.5"/>
              <line x1="200" y1="55" x2="200" y2="258" stroke="currentColor" stroke-width="1"/>
              <line x1="78" y1="108" x2="175" y2="103" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.6"/>
              <line x1="78" y1="128" x2="162" y2="124" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.5"/>
              <line x1="78" y1="148" x2="170" y2="145" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.4"/>
              <line x1="78" y1="168" x2="152" y2="166" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.35"/>
              <line x1="78" y1="188" x2="166" y2="186" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.3"/>
              <line x1="78" y1="208" x2="158" y2="207" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.25"/>
              <g stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
                <path d="M225 103 L262 103" stroke-width="1.8" opacity="0.55"/>
                <path d="M243 93 L243 116" stroke-width="1.5" opacity="0.45"/>
                <path d="M280 98 L312 98" stroke-width="1.8" opacity="0.45"/>
                <path d="M296 88 L296 112" stroke-width="1.5" opacity="0.4"/>
                <path d="M282 112 L314 112" stroke-width="1.2" opacity="0.35"/>
                <path d="M225 140 L260 140" stroke-width="1.8" opacity="0.45"/>
                <path d="M232 130 L232 155" stroke-width="1.5" opacity="0.4"/>
                <path d="M250 130 L250 155" stroke-width="1.2" opacity="0.35"/>
                <path d="M278 135 L318 135" stroke-width="1.8" opacity="0.4"/>
                <path d="M298 125 L298 150" stroke-width="1.5" opacity="0.35"/>
                <path d="M225 175 L258 175" stroke-width="1.5" opacity="0.35"/>
                <path d="M242 165 L242 190" stroke-width="1.2" opacity="0.3"/>
                <path d="M275 172 L310 172" stroke-width="1.5" opacity="0.3"/>
                <path d="M292 162 L292 186" stroke-width="1.2" opacity="0.25"/>
              </g>
              <path d="M178 220 Q200 208 222 220" stroke="currentColor" stroke-width="0.8" opacity="0.2" stroke-dasharray="4 4"/>
              <path d="M173 236 Q200 222 227 236" stroke="currentColor" stroke-width="0.8" opacity="0.15" stroke-dasharray="4 4"/>
            </svg>
            <div class="hero-kicker">Long-document translation, built for control</div>
            <h1 class="hero-title">A publishing-grade cockpit for traceable book translation.</h1>
            <p class="hero-copy">
              book-agent is not a thin “upload and pray” wrapper around an LLM. It is an
              execution surface for parsing, packetizing, translating, reviewing, rerunning,
              and exporting long English books into reviewable Chinese drafts with auditable
              evidence at every step.
            </p>
            <div class="hero-actions">
              <a class="cta cta-primary" href="{docs_href}">Inspect live API surface</a>
              <a class="cta cta-secondary" href="#workspace">Open control workspace</a>
            </div>
            <div class="hero-stats">
              <div class="stat-card">
                <div class="stat-label">Core Contract</div>
                <div class="stat-value">Sentence</div>
                <div class="stat-note">Coverage, alignment, provenance, and rerun all resolve to the sentence ledger.</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Execution Window</div>
                <div class="stat-value">Packet</div>
                <div class="stat-note">Paragraph-scoped translation packets preserve context without exploding prompt history.</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Control Surface</div>
                <div class="stat-value">Run Plane</div>
                <div class="stat-note">Pause, drain, budget guardrails, leases, recovery, and export telemetry are first-class.</div>
              </div>
            </div>
          </article>

          <aside class="hero-aside" aria-label="System pulse">
            <div class="aside-header">
              <div>
                <p class="aside-title">System Pulse</p>
                <p class="aside-kpi" id="health-state">Checking…</p>
              </div>
              <div class="health-badge">
                <span class="health-dot" id="health-dot"></span>
                <span id="health-caption">Waiting for API</span>
              </div>
            </div>
            <div class="signal-stack">
              <div class="signal-card">
                <div class="signal-label">Primary Runway</div>
                <div class="signal-value">EPUB ingest → packet translation → QA gate → merged export</div>
                <div class="signal-note">The homepage is intentionally operator-facing: it mirrors the system’s execution contract instead of hiding it behind generic marketing chrome.</div>
              </div>
              <div class="signal-card">
                <div class="signal-label">Current Strength</div>
                <div class="signal-value">Merged bilingual reading export</div>
                <div class="signal-note">Structured rendering now preserves code, tables, formulas, references, and source-only artifacts instead of pretending every block should become prose.</div>
              </div>
              <div class="signal-card">
                <div class="signal-label">Operator Promise</div>
                <div class="signal-value">No silent failures</div>
                <div class="signal-note">Review issues, rerun actions, export-time anomaly evidence, and long-run control telemetry are exposed as queryable surfaces, not buried in logs.</div>
              </div>
            </div>
          </aside>
        </section>

        <main class="main-grid">
          <div class="stack">
            <section class="section-card" id="workflow">
              <h2 class="section-title">Operational Workflow</h2>
              <p class="section-copy">
                The system is designed as a controlled long-document pipeline. Each phase
                produces durable state so the next phase can be resumed, audited, or repaired
                without replaying the whole book.
              </p>
              <div class="workflow">
                <div class="workflow-step">
                  <div class="workflow-index">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/><path d="M8 7h6"/><path d="M8 11h8"/></svg>
                  </div>
                  <div>
                    <div class="workflow-label">Ingest and structure recovery</div>
                    <div class="workflow-body">EPUB chapters, blocks, headings, figures, tables, code, references, and frontmatter are normalized into stable block objects instead of being flattened into anonymous text.</div>
                  </div>
                </div>
                <div class="workflow-step">
                  <div class="workflow-index">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
                  </div>
                  <div>
                    <div class="workflow-label">Sentence ledger and context packetization</div>
                    <div class="workflow-body">Every source sentence receives a durable identifier, while translation executes inside bounded packets enriched by book profile, chapter brief, and term/entity memory snapshots.</div>
                  </div>
                </div>
                <div class="workflow-step">
                  <div class="workflow-index">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><circle cx="18" cy="18" r="3"/><path d="M22 22l-1.5-1.5"/><path d="m14 18 1-1"/></svg>
                  </div>
                  <div>
                    <div class="workflow-label">Translation, provenance, and repair loops</div>
                    <div class="workflow-body">Worker outputs become target segments, alignments, translation runs, and rerunnable evidence. Actions can rebuild packets, rebuild chapter briefs, realign, or target reruns without restarting the book.</div>
                  </div>
                </div>
                <div class="workflow-step">
                  <div class="workflow-index">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="m9 15 2 2 4-4"/></svg>
                  </div>
                  <div>
                    <div class="workflow-label">Export surfaces for readers and operators</div>
                    <div class="workflow-body">Review packages, chapter-level bilingual exports, merged reading HTML, export manifests, worklists, and run dashboards all expose the same underlying traceable state.</div>
                  </div>
                </div>
              </div>
            </section>

            <section class="section-card" id="surfaces">
              <h2 class="section-title">What The Frontend Surfaces</h2>
              <p class="section-copy">
                This first UI entry is intentionally product-grade but scoped: a control room
                for operators, not yet a full review workstation. It gives the project a
                credible front door while staying aligned with the existing FastAPI runtime.
              </p>
              <div class="surface-grid">
                <article class="surface-card">
                  <div class="surface-kicker">Document Plane</div>
                  <h3 class="surface-title">Bootstrap, translate, review, export</h3>
                  <p class="surface-body">Back-end APIs already support the full path. The homepage introduces the system with the same nouns the runtime uses, so later UI expansion can stay truthful.</p>
                </article>
                <article class="surface-card">
                  <div class="surface-kicker">Run Plane</div>
                  <h3 class="surface-title">Pause, resume, drain, budget control</h3>
                  <p class="surface-body">Long-running DeepSeek or OpenAI-compatible book runs are now durable. The UI is ready to grow into a real run console without needing a parallel product vocabulary.</p>
                </article>
                <article class="surface-card">
                  <div class="surface-kicker">Review Plane</div>
                  <h3 class="surface-title">Issues, actions, chapter worklists</h3>
                  <p class="surface-body">Issue hotspots, chapter pressure, owner queueing, assignment history, and export anomalies already exist as APIs; this page frames them as part of one coherent operator product.</p>
                </article>
                <article class="surface-card">
                  <div class="surface-kicker">Reader Plane</div>
                  <h3 class="surface-title">Merged reading export with preserved artifacts</h3>
                  <p class="surface-body">Code, equations, tables, literal tags, and references are treated as intentional source-side artifacts rather than as failed translations, which is critical for technical books.</p>
                </article>
              </div>
            </section>
          </div>

          <div class="stack">
            <section class="section-card">
              <h2 class="section-title">Capability Spine</h2>
              <p class="section-copy">
                The current UI is small on purpose. It should feel like the front door to a
                serious internal product, not a decorative wrapper around backend endpoints.
              </p>
              <div class="cap-grid">
                <article class="cap-card">
                  <div class="cap-kicker">Parsing</div>
                  <h3 class="cap-title">Structure-aware EPUB ingestion</h3>
                  <p class="cap-body">Preserves headings, code, figures, tables, formulas, references, and frontmatter so downstream translation and export can make differentiated decisions.</p>
                </article>
                <article class="cap-card">
                  <div class="cap-kicker">Translation</div>
                  <h3 class="cap-title">LLM-driven packets with traceable alignment</h3>
                  <p class="cap-body">Every packet run produces target segments, alignment suggestions, provenance, usage telemetry, and rerunnable repair evidence.</p>
                </article>
                <article class="cap-card">
                  <div class="cap-kicker">Quality</div>
                  <h3 class="cap-title">Gated review with targeted recovery</h3>
                  <p class="cap-body">Coverage, duplication, context failure, alignment failure, format pollution, export anomalies, and chapter worklists all feed a controlled repair loop.</p>
                </article>
                <article class="cap-card">
                  <div class="cap-kicker">Operations</div>
                  <h3 class="cap-title">Long-run control and budget awareness</h3>
                  <p class="cap-body">Durable runs, leases, audit trails, auto-followup export repair, owner assignments, and SLA-aware worklists make the system operable at book scale.</p>
                </article>
              </div>
            </section>

            <section class="section-card">
              <h2 class="section-title">Signals To Watch</h2>
              <p class="section-copy">
                These are the high-value state surfaces we expect future UI modules to grow
                around. Even before adding a full SPA, the homepage already anchors the right
                mental model for operators and reviewers.
              </p>
              <div class="signal-matrix">
                <ul class="mini-list">
                  <li>
                    <strong>Run stability</strong>
                    <span>Control-plane budgets, run status, and worker lease recovery are the key to multi-hour book translation without fragile shell sessions.</span>
                  </li>
                  <li>
                    <strong>Chapter pressure</strong>
                    <span>Issue hotspots, chapter queueing, owner assignment, and SLA signals tell you where human review or targeted repair should go next.</span>
                  </li>
                  <li>
                    <strong>Export truthfulness</strong>
                    <span>Merged export rendering must distinguish expected source-only artifacts from actual missing translation, especially for technical books.</span>
                  </li>
                </ul>
              </div>
            </section>

            <section class="section-card" id="api">
              <h2 class="section-title">API Quick Access</h2>
              <p class="section-copy">
                The frontend entry intentionally stays close to the backend contract. These
                surfaces are the ones most likely to matter while the system evolves from an
                operator tool into a richer product.
              </p>
              <div class="api-shell">
                <div class="api-row">
                  <span class="api-method">GET</span>
                  <span class="api-path">{health_href}</span>
                  <span class="api-copy">Health and deployment sanity check</span>
                </div>
                <div class="api-row">
                  <span class="api-method">POST</span>
                  <span class="api-path">{api_prefix}/documents/bootstrap</span>
                  <span class="api-copy">Create a document from a server-visible local path</span>
                </div>
                <div class="api-row">
                  <span class="api-method">POST</span>
                  <span class="api-path">{api_prefix}/documents/bootstrap-upload</span>
                  <span class="api-copy">Upload an EPUB or PDF directly from the browser</span>
                </div>
                <div class="api-row">
                  <span class="api-method">POST</span>
                  <span class="api-path">{api_prefix}/documents/{{document_id}}/translate</span>
                  <span class="api-copy">Run translation packets through the current backend</span>
                </div>
                <div class="api-row">
                  <span class="api-method">GET</span>
                  <span class="api-path">{api_prefix}/documents/{{document_id}}/exports</span>
                  <span class="api-copy">Usage, issue, and export dashboard surfaces</span>
                </div>
                <div class="api-row">
                  <span class="api-method">GET</span>
                  <span class="api-path">{api_prefix}/documents/{{document_id}}/exports/download</span>
                  <span class="api-copy">Download the latest export bundle with a save dialog</span>
                </div>
                <div class="api-row">
                  <span class="api-method">GET</span>
                  <span class="api-path">{api_prefix}/documents/{{document_id}}/chapters/worklist</span>
                  <span class="api-copy">Chapter queue, SLA state, and owner workload</span>
                </div>
                <div class="api-row">
                  <span class="api-method">POST</span>
                  <span class="api-path">{api_prefix}/runs</span>
                  <span class="api-copy">Create and control a durable long-running book job</span>
                </div>
                <div class="api-row">
                  <span class="api-method">GET</span>
                  <span class="api-path">{openapi_href}</span>
                  <span class="api-copy">OpenAPI schema for tool or UI expansion</span>
                </div>
              </div>
            </section>
          </div>
        </main>

        <section class="control-deck" id="workspace">
          <div class="refresh-strip">
            <div class="refresh-card">
              <div class="refresh-head">
                <span class="refresh-kicker">Document Sync</span>
                <div class="refresh-state">
                  <span class="status-dot" id="document-live-dot"></span>
                  <span id="document-live-state">Idle</span>
                </div>
              </div>
              <div class="refresh-meta" id="document-live-meta">Load a document to start workspace refresh tracking.</div>
            </div>
            <div class="refresh-card">
              <div class="refresh-head">
                <span class="refresh-kicker">Run Sync</span>
                <div class="refresh-state">
                  <span class="status-dot" id="run-live-dot"></span>
                  <span id="run-live-state">Idle</span>
                </div>
              </div>
              <div class="refresh-meta" id="run-live-meta">Create or load a run to start control-plane refresh tracking.</div>
            </div>
            <div class="refresh-card">
              <div class="refresh-head">
                <span class="refresh-kicker">Worklist Sync</span>
                <div class="refresh-state">
                  <span class="status-dot" id="worklist-live-dot"></span>
                  <span id="worklist-live-state">Idle</span>
                </div>
              </div>
              <div class="refresh-meta" id="worklist-live-meta">The worklist inherits document polling and exposes stale queue state directly.</div>
            </div>
          </div>
          <div class="workspace-shell">
            <section class="workspace-card">
              <div class="workspace-header">
                <div class="workspace-header-text">
                  <p class="workspace-kicker">Document Workspace</p>
                  <h2 class="workspace-title">Bootstrap, inspect, translate, review, and export from one surface.</h2>
                  <p class="workspace-copy">
                    This is the first operator workspace layer. It does not hide the backend:
                    it gives you fast access to the exact document lifecycle the system already
                    supports, with current state rendered in a way that is actually readable.
                  </p>
                </div>
                <div class="control-chip">EPUB + text PDF · packet-aware · export-gated</div>
              </div>

              <div class="workspace-grid">
                <div class="panel half">
                  <h3 class="panel-title">Bootstrap a document</h3>
                  <p class="panel-copy">Choose a local EPUB or low-risk text PDF from your device and immediately lock the created document into the workspace.</p>
                  <form class="form-grid" id="bootstrap-form">
                    <div class="field-grid">
                      <div class="field span-12">
                        <label for="source-file">Source file</label>
                        <input
                          id="source-file"
                          name="source_file"
                          type="file"
                          accept=".epub,.pdf,application/epub+zip,application/pdf"
                          class="visually-hidden"
                        />
                        <div class="file-picker" id="source-file-picker" data-has-file="false" role="button" tabindex="0">
                          <div class="file-picker-copy">
                            <div class="file-picker-title">Choose an EPUB or PDF</div>
                            <div class="file-picker-note" id="source-file-name">No file selected yet.</div>
                          </div>
                          <button class="button ghost" type="button" id="choose-source-file">Open file picker</button>
                        </div>
                        <div class="field-hint">Upload a local EPUB or text PDF directly from your device. P1-A still rejects OCR-required PDFs, but short academic papers can enter a medium-risk recovery lane instead of being hard-rejected.</div>
                      </div>
                    </div>
                    <div class="button-row">
                      <button class="button primary" type="submit">Bootstrap document</button>
                    </div>
                  </form>
                </div>

                <div class="panel half">
                  <h3 class="panel-title">Operate an existing document</h3>
                  <p class="panel-copy">Load a document, then run translate, review, or export without leaving the workspace. Export now opens a save flow instead of dumping server paths into the UI.</p>
                  <form class="form-grid" id="document-form">
                    <div class="field-grid">
                      <div class="field span-12">
                        <label for="document-id">Document ID</label>
                        <input id="document-id" name="document_id" type="text" placeholder="Paste a document UUID" />
                      </div>
                      <div class="field span-6">
                        <label for="export-type">Export type</label>
                        <select id="export-type" name="export_type">
                          <option value="merged_html">merged_html</option>
                          <option value="bilingual_html">bilingual_html</option>
                          <option value="review_package">review_package</option>
                        </select>
                      </div>
                      <div class="field span-6">
                        <label for="auto-followup">Export gate auto-followup</label>
                        <select id="auto-followup" name="auto_followup">
                          <option value="false">disabled</option>
                          <option value="true">enabled</option>
                        </select>
                      </div>
                    </div>
                    <div class="button-row">
                      <button class="button ghost" type="button" id="load-document">Load summary</button>
                      <button class="button" type="button" id="translate-document">Translate</button>
                      <button class="button" type="button" id="review-document">Review</button>
                      <button class="button gold" type="button" id="export-document">Export & save</button>
                    </div>
                  </form>
                </div>
              </div>

              <div class="status-banner" id="document-status-banner">No document loaded yet. Upload a new EPUB/PDF or paste an existing document id.</div>

              <div class="toggle-row">
                <button class="button ghost toggle-button" type="button" id="toggle-document-polling" data-active="true">Auto-refresh document: on</button>
                <span class="subtle-note" id="document-polling-note">When enabled, the workspace refreshes document summary, export dashboard, and worklist for the currently loaded document.</span>
              </div>

              <div class="kpi-grid" id="document-kpis">
                <div class="kpi-card">
                  <div class="kpi-label">Document</div>
                  <div class="kpi-value">—</div>
                  <div class="kpi-note">Title and status will appear here after a load.</div>
                </div>
                <div class="kpi-card">
                  <div class="kpi-label">Chapters</div>
                  <div class="kpi-value">—</div>
                  <div class="kpi-note">Recovered structural chapter count.</div>
                </div>
                <div class="kpi-card">
                  <div class="kpi-label">Sentences</div>
                  <div class="kpi-value">—</div>
                  <div class="kpi-note">Sentence ledger count for coverage and alignment.</div>
                </div>
                <div class="kpi-card">
                  <div class="kpi-label">Open Issues</div>
                  <div class="kpi-value">—</div>
                  <div class="kpi-note">Live open review pressure for the whole document.</div>
                </div>
              </div>

              <div class="result-shell">
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Document summary</h3>
                      <div class="result-meta">Document contract, chapter counts, and current lifecycle status.</div>
                    </div>
                  </div>
                  <div id="document-summary-shell" class="placeholder">Load a document to see title, author, chapter progression, and stored quality summaries.</div>
                </div>

                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Export and usage dashboard</h3>
                      <div class="result-meta">Snapshot of export history, usage totals, issue hotspots, and auto-followup evidence.</div>
                    </div>
                  </div>
                  <div id="export-dashboard-shell" class="placeholder">Once a document is loaded, this panel will show export history, issue pressure, and translation usage signals.</div>
                </div>
              </div>
            </section>

            <section class="workspace-card">
              <div class="workspace-header">
                <div class="workspace-header-text">
                  <p class="workspace-kicker">Run Console</p>
                  <h2 class="workspace-title">Control long jobs without dropping to shell scripts.</h2>
                  <p class="workspace-copy">
                    Runs are durable now. This pane lets you create a `translate_full` run,
                    inspect the latest summary, review event flow, and apply pause or drain
                    transitions through the same control plane your long jobs already use.
                  </p>
                </div>
                <div class="control-chip">Durable runs · leases · budget guardrails</div>
              </div>

              <div class="panel">
                <h3 class="panel-title">Create or attach a run</h3>
                <form class="form-grid" id="run-form">
                  <div class="field-grid">
                    <div class="field span-12">
                      <label for="run-document-id">Document ID for new run</label>
                      <input id="run-document-id" name="run_document_id" type="text" placeholder="Uses current document id when left empty" />
                    </div>
                    <div class="field span-6">
                      <label for="run-max-cost">Max total cost (USD)</label>
                      <input id="run-max-cost" name="max_total_cost_usd" type="number" step="0.01" min="0" placeholder="0.50" />
                    </div>
                    <div class="field span-6">
                      <label for="run-max-workers">Max parallel workers</label>
                      <input id="run-max-workers" name="max_parallel_workers" type="number" min="1" placeholder="4" />
                    </div>
                    <div class="field span-12">
                      <label for="run-id">Run ID</label>
                      <input id="run-id" name="run_id" type="text" placeholder="Paste a run UUID to inspect or control it" />
                    </div>
                  </div>
                  <div class="button-row">
                    <button class="button primary" type="button" id="create-run">Create translate_full run</button>
                    <button class="button ghost" type="button" id="load-run">Load run</button>
                  </div>
                </form>
                <div class="button-row">
                  <button class="button" type="button" id="pause-run">Pause</button>
                  <button class="button" type="button" id="resume-run">Resume</button>
                  <button class="button" type="button" id="drain-run">Drain</button>
                  <button class="button gold" type="button" id="cancel-run">Cancel</button>
                </div>
              </div>

              <div class="status-banner" id="run-status-banner">No run loaded yet. Create one from the current document or paste a run id.</div>

              <div class="toggle-row">
                <button class="button ghost toggle-button" type="button" id="toggle-run-polling" data-active="true">Auto-refresh run: on</button>
                <span class="subtle-note" id="run-polling-note">When enabled, the console refreshes the loaded run summary and newest events every few seconds while the page stays open.</span>
              </div>

              <div class="result-shell">
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Run summary</h3>
                      <div class="result-meta">Status, budgets, work item counts, worker leases, and latest control-plane signals.</div>
                    </div>
                  </div>
                  <div id="run-summary-shell" class="placeholder">Run summary will appear here after create or load.</div>
                </div>

                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Run events</h3>
                      <div class="result-meta">Newest-first audit stream for pause, resume, guardrail stops, and execution lifecycle changes.</div>
                    </div>
                  </div>
                  <div id="run-events-shell" class="placeholder">Recent run audit events will appear here after a run is loaded.</div>
                </div>
              </div>
            </section>

            <section class="workspace-card full-width">
              <div class="workspace-header">
                <div class="workspace-header-text">
                  <p class="workspace-kicker">Chapter Worklist Board</p>
                  <h2 class="workspace-title">See which chapters actually need attention next.</h2>
                  <p class="workspace-copy">
                    This board uses the live chapter worklist contract, not a fake kanban shell.
                    It surfaces queue rank, issue family driver, SLA state, owner readiness, and
                    assignment so triage decisions can happen without scanning raw JSON.
                  </p>
                </div>
                <div class="control-chip">Queue rank · SLA pressure · owner-ready</div>
              </div>

              <div class="panel">
                <div class="button-row">
                  <button class="button ghost" type="button" id="refresh-worklist">Refresh worklist</button>
                  <button class="button ghost" type="button" id="open-current-exports">Refresh export dashboard</button>
                </div>
                <form class="filter-toolbar" id="worklist-filter-form">
                  <div class="field-grid">
                    <div class="field span-4">
                      <label for="filter-queue-priority">Queue priority</label>
                      <select id="filter-queue-priority">
                        <option value="">All priorities</option>
                        <option value="immediate">Immediate</option>
                        <option value="high">High</option>
                        <option value="medium">Medium</option>
                      </select>
                    </div>
                    <div class="field span-4">
                      <label for="filter-sla-status">SLA status</label>
                      <select id="filter-sla-status">
                        <option value="">All SLA states</option>
                        <option value="breached">Breached</option>
                        <option value="due_soon">Due soon</option>
                        <option value="on_track">On track</option>
                        <option value="unknown">Unknown</option>
                      </select>
                    </div>
                    <div class="field span-4">
                      <label for="filter-owner-ready">Owner ready</label>
                      <select id="filter-owner-ready">
                        <option value="">All</option>
                        <option value="true">Owner-ready</option>
                        <option value="false">Not owner-ready</option>
                      </select>
                    </div>
                    <div class="field span-4">
                      <label for="filter-assigned">Assignment</label>
                      <select id="filter-assigned">
                        <option value="">All</option>
                        <option value="true">Assigned</option>
                        <option value="false">Unassigned</option>
                      </select>
                    </div>
                    <div class="field span-8">
                      <label for="filter-owner-name">Assigned owner</label>
                      <input id="filter-owner-name" type="text" placeholder="Click an owner card or type owner name" />
                    </div>
                  </div>
                  <div class="button-row">
                    <button class="button primary" type="submit" id="apply-worklist-filters">Apply filters</button>
                    <button class="button ghost" type="button" id="clear-worklist-filters">Clear filters</button>
                  </div>
                </form>
                <div class="filter-summary" id="worklist-filter-summary">
                  Showing the live actionable queue with no extra filters.
                </div>
              </div>

              <div class="status-banner" id="worklist-status-banner">Load a document to inspect chapter queue pressure and owner-ready chapters.</div>
              <div id="worklist-shell" class="queue-grid">
                <div class="placeholder">The chapter board will populate from <code>{api_prefix}/documents/&lt;document_id&gt;/chapters/worklist</code> after a document is loaded.</div>
              </div>

              <div class="result-shell" style="margin-top:18px;">
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Owner workload lane</h3>
                      <div class="result-meta">Current assigned workload by owner, derived from the same chapter queue contract instead of a separate reporting path.</div>
                    </div>
                  </div>
                  <div id="owner-workload-shell" class="placeholder">Owner workload summary will appear here once the chapter worklist is loaded.</div>
                </div>
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Owner alerts and routing cues</h3>
                      <div class="result-meta">Turn current workload pressure into quick routing decisions without scanning every chapter row.</div>
                    </div>
                  </div>
                  <div id="owner-alert-shell" class="placeholder">Owner alerts will appear here once the worklist exposes assignment pressure.</div>
                </div>
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Owner drill-down and balancing hints</h3>
                      <div class="result-meta">Focus one owner, inspect the pressure profile, and get simple rebalance suggestions without leaving the current queue surface.</div>
                    </div>
                  </div>
                  <div id="owner-detail-shell" class="placeholder">Select an owner card to focus the queue and inspect owner-level workload hints.</div>
                </div>
              </div>

              <div class="result-shell" style="margin-top:18px;">
                <div class="result-card">
                  <div class="result-head">
                    <div>
                      <h3 class="result-title">Chapter detail drawer</h3>
                      <div class="result-meta">Click a chapter row or queue card to inspect issue families, recent actions, and assignment history.</div>
                    </div>
                  </div>
                  <div id="chapter-detail-shell" class="placeholder">Select a chapter from the document summary or worklist board to open its operator detail.</div>
                  <form class="assignment-inline" id="assignment-form" hidden>
                    <div class="field-grid">
                      <div class="field span-6">
                        <label for="assignment-owner">Owner name</label>
                        <input id="assignment-owner" type="text" placeholder="ops-alice" />
                      </div>
                      <div class="field span-6">
                        <label for="assignment-actor">Assigned by</label>
                        <input id="assignment-actor" type="text" value="ui-operator" />
                      </div>
                      <div class="field span-12">
                        <label for="assignment-note">Note</label>
                        <input id="assignment-note" type="text" placeholder="Optional routing note for this chapter." />
                      </div>
                    </div>
                    <div class="button-row">
                      <button class="button primary" type="submit" id="assign-owner">Assign owner</button>
                      <button class="button ghost" type="button" id="clear-owner">Clear assignment</button>
                    </div>
                  </form>
                </div>
              </div>
            </section>
          </div>
        </section>

        <footer class="footer">
          <span>Designed as a calm technical-book control room, not a generic upload form.</span>
          <span>This surface now spans product entry, document workspace, run console, and chapter worklist board.</span>
          <span><code>{health_href}</code></span>
        </footer>
      </div>
    </div>
    <script>
      (function() {{
        const apiPrefix = "{api_prefix}";
        const healthUrl = "{health_href}";
        const storageKeys = {{
          documentId: "book-agent.currentDocumentId",
          runId: "book-agent.currentRunId",
        }};

        const state = {{
          currentDocumentId: window.localStorage.getItem(storageKeys.documentId) || "",
          currentRunId: window.localStorage.getItem(storageKeys.runId) || "",
          selectedChapterId: "",
          documentPollingEnabled: true,
          documentPollHandle: null,
          documentPollInFlight: false,
          runPollingEnabled: true,
          runPollHandle: null,
          runPollInFlight: false,
          worklistRefreshInFlight: false,
          lastRefreshedAt: {{
            document: null,
            run: null,
            worklist: null,
          }},
          worklistFilters: {{
            queuePriority: "",
            slaStatus: "",
            ownerReady: "",
            assigned: "",
            assignedOwnerName: "",
          }},
        }};

        const els = {{
          healthState: document.getElementById("health-state"),
          healthDot: document.getElementById("health-dot"),
          healthCaption: document.getElementById("health-caption"),
          sourceFile: document.getElementById("source-file"),
          sourceFilePicker: document.getElementById("source-file-picker"),
          sourceFileName: document.getElementById("source-file-name"),
          chooseSourceFile: document.getElementById("choose-source-file"),
          documentId: document.getElementById("document-id"),
          exportType: document.getElementById("export-type"),
          autoFollowup: document.getElementById("auto-followup"),
          bootstrapForm: document.getElementById("bootstrap-form"),
          loadDocument: document.getElementById("load-document"),
          translateDocument: document.getElementById("translate-document"),
          reviewDocument: document.getElementById("review-document"),
          exportDocument: document.getElementById("export-document"),
          documentBanner: document.getElementById("document-status-banner"),
          toggleDocumentPolling: document.getElementById("toggle-document-polling"),
          documentPollingNote: document.getElementById("document-polling-note"),
          documentLiveDot: document.getElementById("document-live-dot"),
          documentLiveState: document.getElementById("document-live-state"),
          documentLiveMeta: document.getElementById("document-live-meta"),
          documentKpis: document.getElementById("document-kpis"),
          documentSummary: document.getElementById("document-summary-shell"),
          exportDashboard: document.getElementById("export-dashboard-shell"),
          runDocumentId: document.getElementById("run-document-id"),
          runId: document.getElementById("run-id"),
          runMaxCost: document.getElementById("run-max-cost"),
          runMaxWorkers: document.getElementById("run-max-workers"),
          createRun: document.getElementById("create-run"),
          loadRun: document.getElementById("load-run"),
          pauseRun: document.getElementById("pause-run"),
          resumeRun: document.getElementById("resume-run"),
          drainRun: document.getElementById("drain-run"),
          cancelRun: document.getElementById("cancel-run"),
          runBanner: document.getElementById("run-status-banner"),
          toggleRunPolling: document.getElementById("toggle-run-polling"),
          runPollingNote: document.getElementById("run-polling-note"),
          runLiveDot: document.getElementById("run-live-dot"),
          runLiveState: document.getElementById("run-live-state"),
          runLiveMeta: document.getElementById("run-live-meta"),
          runSummary: document.getElementById("run-summary-shell"),
          runEvents: document.getElementById("run-events-shell"),
          refreshWorklist: document.getElementById("refresh-worklist"),
          openCurrentExports: document.getElementById("open-current-exports"),
          worklistLiveDot: document.getElementById("worklist-live-dot"),
          worklistLiveState: document.getElementById("worklist-live-state"),
          worklistLiveMeta: document.getElementById("worklist-live-meta"),
          worklistFilterForm: document.getElementById("worklist-filter-form"),
          worklistFilterSummary: document.getElementById("worklist-filter-summary"),
          filterQueuePriority: document.getElementById("filter-queue-priority"),
          filterSlaStatus: document.getElementById("filter-sla-status"),
          filterOwnerReady: document.getElementById("filter-owner-ready"),
          filterAssigned: document.getElementById("filter-assigned"),
          filterOwnerName: document.getElementById("filter-owner-name"),
          applyWorklistFilters: document.getElementById("apply-worklist-filters"),
          clearWorklistFilters: document.getElementById("clear-worklist-filters"),
          worklistBanner: document.getElementById("worklist-status-banner"),
          worklist: document.getElementById("worklist-shell"),
          ownerWorkload: document.getElementById("owner-workload-shell"),
          ownerAlert: document.getElementById("owner-alert-shell"),
          ownerDetail: document.getElementById("owner-detail-shell"),
          chapterDetail: document.getElementById("chapter-detail-shell"),
          assignmentForm: document.getElementById("assignment-form"),
          assignmentOwner: document.getElementById("assignment-owner"),
          assignmentActor: document.getElementById("assignment-actor"),
          assignmentNote: document.getElementById("assignment-note"),
          assignOwner: document.getElementById("assign-owner"),
          clearOwner: document.getElementById("clear-owner"),
        }};

        function escapeHtml(value) {{
          return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
        }}

        function formatNumber(value) {{
          if (value === null || value === undefined || Number.isNaN(Number(value))) {{
            return "—";
          }}
          return new Intl.NumberFormat("zh-CN").format(Number(value));
        }}

        function formatMoney(value) {{
          if (value === null || value === undefined || Number.isNaN(Number(value))) {{
            return "—";
          }}
          return "$" + Number(value).toFixed(4);
        }}

        function formatDecimal(value, digits = 2) {{
          if (value === null || value === undefined || Number.isNaN(Number(value))) {{
            return "—";
          }}
          return Number(value).toFixed(digits);
        }}

        function formatDate(value) {{
          if (!value) {{
            return "—";
          }}
          const date = new Date(value);
          if (Number.isNaN(date.getTime())) {{
            return escapeHtml(value);
          }}
          return date.toLocaleString("zh-CN", {{
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
          }});
        }}

        function formatRelativeTime(value) {{
          if (!value) {{
            return "not synced yet";
          }}
          const date = new Date(value);
          if (Number.isNaN(date.getTime())) {{
            return "timestamp unavailable";
          }}
          const deltaMs = Date.now() - date.getTime();
          const deltaSeconds = Math.max(0, Math.round(deltaMs / 1000));
          if (deltaSeconds < 60) {{
            return deltaSeconds + "s ago";
          }}
          const deltaMinutes = Math.round(deltaSeconds / 60);
          if (deltaMinutes < 60) {{
            return deltaMinutes + "m ago";
          }}
          return formatDate(value);
        }}

        function toBooleanOrNull(value) {{
          if (value === "true") {{
            return true;
          }}
          if (value === "false") {{
            return false;
          }}
          return null;
        }}

        function setRefreshInFlight(scope, active) {{
          if (scope === "document") {{
            state.documentPollInFlight = active;
          }} else if (scope === "run") {{
            state.runPollInFlight = active;
          }} else if (scope === "worklist") {{
            state.worklistRefreshInFlight = active;
          }}
          updateLiveStatusUi();
        }}

        function markRefreshed(scope) {{
          state.lastRefreshedAt[scope] = new Date().toISOString();
          updateLiveStatusUi();
        }}

        function getRefreshModel(scope) {{
          const lastRefreshedAt = state.lastRefreshedAt[scope];
          const inFlight =
            scope === "document"
              ? state.documentPollInFlight
              : scope === "run"
                ? state.runPollInFlight
                : state.worklistRefreshInFlight;
          const pollingEnabled = scope === "run" ? state.runPollingEnabled : state.documentPollingEnabled;
          const hasSubject =
            scope === "run"
              ? Boolean(state.currentRunId || els.runId.value.trim())
              : Boolean(state.currentDocumentId || els.documentId.value.trim());

          if (!hasSubject) {{
            return {{
              label: "Idle",
              meta:
                scope === "run"
                  ? "Create or load a run to start live monitoring."
                  : "Load a document to start live monitoring.",
              dotClass: "status-dot",
            }};
          }}

          if (inFlight) {{
            return {{
              label: "Refreshing",
              meta: "A fresh snapshot is loading right now.",
              dotClass: "status-dot refreshing",
            }};
          }}

          if (!lastRefreshedAt) {{
            return {{
              label: "Waiting",
              meta: "A first successful refresh has not completed yet.",
              dotClass: "status-dot",
            }};
          }}

          const ageMs = Date.now() - new Date(lastRefreshedAt).getTime();
          const staleAfterMs = scope === "run" ? 45000 : 90000;
          if (ageMs > staleAfterMs) {{
            return {{
              label: "Stale",
              meta: "Last refreshed " + formatRelativeTime(lastRefreshedAt) + ".",
              dotClass: "status-dot stale",
            }};
          }}

          return {{
            label: pollingEnabled ? "Live" : "Manual",
            meta:
              "Last refreshed " +
              formatRelativeTime(lastRefreshedAt) +
              (pollingEnabled ? " with auto-refresh enabled." : " while auto-refresh is paused."),
            dotClass: "status-dot live",
          }};
        }}

        function updateLiveStatusUi() {{
          const documentModel = getRefreshModel("document");
          els.documentLiveState.textContent = documentModel.label;
          els.documentLiveMeta.textContent = documentModel.meta;
          els.documentLiveDot.className = documentModel.dotClass;

          const runModel = getRefreshModel("run");
          els.runLiveState.textContent = runModel.label;
          els.runLiveMeta.textContent = runModel.meta;
          els.runLiveDot.className = runModel.dotClass;

          const worklistModel = getRefreshModel("worklist");
          els.worklistLiveState.textContent = worklistModel.label;
          els.worklistLiveMeta.textContent = worklistModel.meta;
          els.worklistLiveDot.className = worklistModel.dotClass;
        }}

        function setBanner(element, message, kind) {{
          if (!element) {{
            return;
          }}
          element.textContent = message;
          element.className = kind ? "status-banner " + kind : "status-banner";
        }}

        function setButtonLoading(button, loading, label) {{
          if (!button) {{
            return;
          }}
          if (!button.dataset.originalLabel) {{
            button.dataset.originalLabel = button.textContent;
          }}
          button.disabled = loading;
          button.textContent = loading ? label : button.dataset.originalLabel;
        }}

        function getSelectedSourceFile() {{
          return els.sourceFile.files && els.sourceFile.files[0] ? els.sourceFile.files[0] : null;
        }}

        function syncSelectedSourceFileUi() {{
          const file = getSelectedSourceFile();
          els.sourceFilePicker.dataset.hasFile = file ? "true" : "false";
          els.sourceFileName.textContent = file
            ? file.name + " · " + formatNumber(file.size) + " bytes"
            : "No file selected yet.";
        }}

        function clearSelectedSourceFile() {{
          els.sourceFile.value = "";
          syncSelectedSourceFileUi();
        }}

        function setDocumentId(documentId) {{
          state.currentDocumentId = documentId || "";
          window.localStorage.setItem(storageKeys.documentId, state.currentDocumentId);
          els.documentId.value = state.currentDocumentId;
          if (!els.runDocumentId.value) {{
            els.runDocumentId.value = state.currentDocumentId;
          }}
        }}

        function setRunId(runId) {{
          state.currentRunId = runId || "";
          window.localStorage.setItem(storageKeys.runId, state.currentRunId);
          els.runId.value = state.currentRunId;
        }}

        function setSelectedChapterId(chapterId) {{
          state.selectedChapterId = chapterId || "";
        }}

        function syncRunPollingUi() {{
          els.toggleRunPolling.dataset.active = state.runPollingEnabled ? "true" : "false";
          els.toggleRunPolling.textContent = state.runPollingEnabled
            ? "Auto-refresh run: on"
            : "Auto-refresh run: off";
          els.runPollingNote.textContent = state.runPollingEnabled
            ? "When enabled, the console refreshes the loaded run summary and newest events every few seconds while the page stays open."
            : "Auto-refresh is paused. Use Load run to fetch a fresh snapshot manually.";
        }}

        function syncDocumentPollingUi() {{
          els.toggleDocumentPolling.dataset.active = state.documentPollingEnabled ? "true" : "false";
          els.toggleDocumentPolling.textContent = state.documentPollingEnabled
            ? "Auto-refresh document: on"
            : "Auto-refresh document: off";
          els.documentPollingNote.textContent = state.documentPollingEnabled
            ? "When enabled, the workspace refreshes document summary, export dashboard, and worklist for the currently loaded document."
            : "Document auto-refresh is paused. Use the explicit controls to refresh document state.";
        }}

        function syncWorklistFilterForm() {{
          els.filterQueuePriority.value = state.worklistFilters.queuePriority;
          els.filterSlaStatus.value = state.worklistFilters.slaStatus;
          els.filterOwnerReady.value = state.worklistFilters.ownerReady;
          els.filterAssigned.value = state.worklistFilters.assigned;
          els.filterOwnerName.value = state.worklistFilters.assignedOwnerName;
        }}

        function buildWorklistQuery(documentId) {{
          const params = new URLSearchParams();
          params.set("limit", "8");
          params.set("offset", "0");
          if (state.worklistFilters.queuePriority) {{
            params.set("queue_priority", state.worklistFilters.queuePriority);
          }}
          if (state.worklistFilters.slaStatus) {{
            params.set("sla_status", state.worklistFilters.slaStatus);
          }}
          if (state.worklistFilters.ownerReady) {{
            params.set("owner_ready", state.worklistFilters.ownerReady);
          }}
          if (state.worklistFilters.assigned) {{
            params.set("assigned", state.worklistFilters.assigned);
          }}
          if (state.worklistFilters.assignedOwnerName) {{
            params.set("assigned_owner_name", state.worklistFilters.assignedOwnerName);
          }}
          return apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/chapters/worklist?" + params.toString();
        }}

        function renderWorklistFilterSummary(worklist) {{
          const active = [];
          if (worklist.applied_queue_priority_filter) {{
            active.push("priority=" + worklist.applied_queue_priority_filter);
          }}
          if (worklist.applied_sla_status_filter) {{
            active.push("sla=" + worklist.applied_sla_status_filter);
          }}
          if (worklist.applied_owner_ready_filter !== null && worklist.applied_owner_ready_filter !== undefined) {{
            active.push("owner-ready=" + (worklist.applied_owner_ready_filter ? "yes" : "no"));
          }}
          if (worklist.applied_assigned_filter !== null && worklist.applied_assigned_filter !== undefined) {{
            active.push("assigned=" + (worklist.applied_assigned_filter ? "yes" : "no"));
          }}
          if (worklist.applied_assigned_owner_filter) {{
            active.push("owner=" + worklist.applied_assigned_owner_filter);
          }}

          if (!active.length) {{
            els.worklistFilterSummary.textContent =
              "Showing the live actionable queue with no extra filters. " +
              formatNumber(worklist.worklist_count) +
              " total actionable chapters, " +
              formatNumber(worklist.owner_ready_count) +
              " owner-ready.";
            return;
          }}

          els.worklistFilterSummary.textContent =
            "Filters active: " +
            active.join(" · ") +
            ". Showing " +
            formatNumber(worklist.filtered_worklist_count) +
            " of " +
            formatNumber(worklist.worklist_count) +
            " actionable chapters.";
        }}

        function clearChapterDetail() {{
          setSelectedChapterId("");
          els.assignmentForm.hidden = true;
          els.chapterDetail.innerHTML =
            '<div class="placeholder">Select a chapter from the document summary or worklist board to open its operator detail.</div>';
        }}

        async function fetchJson(url, options) {{
          const response = await fetch(url, {{
            method: options?.method || "GET",
            headers: {{
              "Accept": "application/json",
              ...(options?.body ? {{ "Content-Type": "application/json" }} : {{}}),
            }},
            body: options?.body ? JSON.stringify(options.body) : undefined,
          }});

          const raw = await response.text();
          const payload = raw ? (() => {{
            try {{
              return JSON.parse(raw);
            }} catch (_error) {{
              return raw;
            }}
          }})() : null;

          if (!response.ok) {{
            const detail = payload && typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload || ("HTTP " + response.status);
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
          }}
          return payload;
        }}

        async function fetchMultipartJson(url, formData) {{
          const response = await fetch(url, {{
            method: "POST",
            headers: {{
              "Accept": "application/json",
            }},
            body: formData,
          }});

          const raw = await response.text();
          const payload = raw ? (() => {{
            try {{
              return JSON.parse(raw);
            }} catch (_error) {{
              return raw;
            }}
          }})() : null;

          if (!response.ok) {{
            const detail = payload && typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload || ("HTTP " + response.status);
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
          }}
          return payload;
        }}

        function parseContentDispositionFilename(header) {{
          if (!header) {{
            return "";
          }}
          const utfMatch = header.match(/filename\\*=UTF-8''([^;]+)/i);
          if (utfMatch && utfMatch[1]) {{
            return decodeURIComponent(utfMatch[1]);
          }}
          const plainMatch = header.match(/filename="?([^"]+)"?/i);
          return plainMatch && plainMatch[1] ? plainMatch[1] : "";
        }}

        async function fetchBlob(url, options) {{
          const response = await fetch(url, {{
            method: options?.method || "GET",
            headers: {{
              "Accept": "*/*",
            }},
          }});

          if (!response.ok) {{
            const raw = await response.text();
            let payload = raw;
            try {{
              payload = raw ? JSON.parse(raw) : raw;
            }} catch (_error) {{
              payload = raw;
            }}
            const detail = payload && typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload || ("HTTP " + response.status);
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
          }}

          return {{
            blob: await response.blob(),
            filename: parseContentDispositionFilename(response.headers.get("content-disposition")),
            contentType: response.headers.get("content-type") || "application/octet-stream",
          }};
        }}

        function buildSavePickerTypes(filename, contentType) {{
          if (!filename || !filename.includes(".") || !contentType || contentType === "application/octet-stream") {{
            return [];
          }}
          const extension = "." + filename.split(".").pop().toLowerCase();
          return [
            {{
              description: "Book Agent export",
              accept: {{
                [contentType]: [extension],
              }},
            }},
          ];
        }}

        async function saveBlob(blob, filename, contentType) {{
          const suggestedName = filename || "book-agent-export";
          if (window.showSaveFilePicker) {{
            const pickerOptions = {{
              suggestedName,
              excludeAcceptAllOption: false,
              types: buildSavePickerTypes(suggestedName, contentType),
            }};
            if (!pickerOptions.types.length) {{
              delete pickerOptions.types;
            }}
            const handle = await window.showSaveFilePicker(pickerOptions);
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
            return suggestedName;
          }}

          const objectUrl = URL.createObjectURL(blob);
          const anchor = document.createElement("a");
          anchor.href = objectUrl;
          anchor.download = suggestedName;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
          return suggestedName;
        }}

        async function downloadCurrentExport(documentId, exportType) {{
          const params = new URLSearchParams({{ export_type: exportType }});
          const download = await fetchBlob(
            apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/exports/download?" + params.toString()
          );
          return saveBlob(download.blob, download.filename, download.contentType);
        }}

        function renderDocumentKpis(summary) {{
          els.documentKpis.innerHTML = [
            {{
              label: "Document",
              value: summary.title || "Untitled",
              note: (summary.author || "Unknown author") + " · " + summary.status,
            }},
            {{
              label: "Chapters",
              value: formatNumber(summary.chapter_count),
              note: "Recovered chapter nodes in the document contract.",
            }},
            {{
              label: "Sentences",
              value: formatNumber(summary.sentence_count),
              note: "Sentence-ledger count used for coverage and alignment.",
            }},
            {{
              label: "Open Issues",
              value: formatNumber(summary.open_issue_count),
              note: "Current open review pressure across all chapters.",
            }},
          ].map((item) => `
            <div class="kpi-card">
              <div class="kpi-label">${{escapeHtml(item.label)}}</div>
              <div class="kpi-value">${{escapeHtml(item.value)}}</div>
              <div class="kpi-note">${{escapeHtml(item.note)}}</div>
            </div>
          `).join("");
        }}

        function renderDocumentSummary(summary) {{
          const chapters = (summary.chapters || []).slice(0, 8).map((chapter) => `
            <tr>
              <td>
                <button
                  class="table-button"
                  type="button"
                  data-chapter-id="${{escapeHtml(chapter.chapter_id)}}"
                  data-chapter-source="summary"
                >
                  #${{escapeHtml(chapter.ordinal)}}
                </button>
              </td>
              <td>
                <button
                  class="table-button"
                  type="button"
                  data-chapter-id="${{escapeHtml(chapter.chapter_id)}}"
                  data-chapter-source="summary"
                >
                  ${{escapeHtml(chapter.title_src || "Untitled chapter")}}
                </button>
              </td>
              <td>${{escapeHtml(chapter.status)}}</td>
              <td>
                ${{
                  escapeHtml(chapter.risk_level || "none")
                }}
                <div class="mini-meta">
                  conf=${{formatDecimal(chapter.parse_confidence)}}${{
                    chapter.structure_flags?.length ? " · " + escapeHtml(chapter.structure_flags.join(", ")) : ""
                  }}
                </div>
              </td>
              <td>${{formatNumber(chapter.sentence_count)}}</td>
              <td>${{formatNumber(chapter.packet_count)}}</td>
              <td>${{formatNumber(chapter.open_issue_count)}}</td>
            </tr>
          `).join("");

          els.documentSummary.innerHTML = `
            <div class="result-head">
              <div>
                <h3 class="result-title">${{escapeHtml(summary.title || "Untitled document")}}</h3>
                <div class="result-meta">
                  ${{
                    escapeHtml(summary.author || "Unknown author")
                  }} · ${{
                    escapeHtml(summary.source_type)
                  }} · ${{
                    escapeHtml(summary.status)
                  }} · document_id=${{
                    escapeHtml(summary.document_id)
                  }}
                </div>
              </div>
            </div>
            <div class="pill-row">
              <span class="pill">chapters: ${{formatNumber(summary.chapter_count)}}</span>
              <span class="pill">blocks: ${{formatNumber(summary.block_count)}}</span>
              <span class="pill">sentences: ${{formatNumber(summary.sentence_count)}}</span>
              ${{
                summary.pdf_profile
                  ? `<span class="pill soft">${{escapeHtml(summary.pdf_profile.pdf_kind || "pdf")}} · risk=${{escapeHtml(summary.pdf_profile.layout_risk || "unknown")}}</span>`
                  : ""
              }}
              <span class="pill warn">open issues: ${{formatNumber(summary.open_issue_count)}}</span>
            </div>
            <table class="data-table">
              <thead>
                <tr>
                  <th>Chapter</th>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Risk</th>
                  <th>Sentences</th>
                  <th>Packets</th>
                  <th>Open Issues</th>
                </tr>
              </thead>
              <tbody>
                ${{
                  chapters || `<tr><td colspan="6">No chapter records found.</td></tr>`
                }}
              </tbody>
            </table>
          `;
        }}

        function renderExportDashboard(dashboard) {{
          const hotspotMarkup = (dashboard.issue_hotspots || []).slice(0, 5).map((entry) => `
            <span class="pill warn">${{escapeHtml(entry.issue_type)}} / ${{escapeHtml(entry.root_cause_layer)}} · open ${{formatNumber(entry.open_issue_count)}}</span>
          `).join("");
          const recordMarkup = (dashboard.records || []).slice(0, 5).map((record) => `
            <tr>
              <td>${{escapeHtml(record.export_type)}}</td>
              <td>${{escapeHtml(record.status)}}</td>
              <td>${{formatDate(record.updated_at)}}</td>
              <td>${{formatMoney(record.translation_usage_summary?.total_cost_usd)}}</td>
              <td>${{formatNumber(record.export_auto_followup_summary?.executed_event_count || 0)}}</td>
            </tr>
          `).join("");

          els.exportDashboard.innerHTML = `
            <div class="pill-row">
              <span class="pill">exports: ${{formatNumber(dashboard.export_count)}}</span>
              <span class="pill">successful: ${{formatNumber(dashboard.successful_export_count)}}</span>
              <span class="pill">auto-followup executed: ${{formatNumber(dashboard.total_auto_followup_executed_count)}}</span>
              <span class="pill soft">latest export: ${{formatDate(dashboard.latest_export_at)}}</span>
            </div>
            <div class="pill-row" style="margin-top:12px;">
              <span class="pill">usage cost: ${{formatMoney(dashboard.translation_usage_summary?.total_cost_usd)}}</span>
              <span class="pill">token in: ${{formatNumber(dashboard.translation_usage_summary?.total_token_in)}}</span>
              <span class="pill">token out: ${{formatNumber(dashboard.translation_usage_summary?.total_token_out)}}</span>
              <span class="pill soft">avg latency: ${{formatNumber(dashboard.translation_usage_summary?.avg_latency_ms)}} ms</span>
            </div>
            <div class="result-shell">
              <div class="result-card">
                <div class="result-head">
                  <div>
                    <h3 class="result-title">Issue hotspots</h3>
                    <div class="result-meta">Current live review pressure grouped by issue family.</div>
                  </div>
                </div>
                <div class="pill-row">
                  ${{
                    hotspotMarkup || '<span class="pill soft">No live issue hotspots.</span>'
                  }}
                </div>
              </div>
              <div class="result-card">
                <div class="result-head">
                  <div>
                    <h3 class="result-title">Recent exports</h3>
                    <div class="result-meta">Latest export records with usage and follow-up hints.</div>
                  </div>
                </div>
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Updated</th>
                      <th>Cost</th>
                      <th>Auto-Followup</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${{
                      recordMarkup || `<tr><td colspan="5">No export records yet.</td></tr>`
                    }}
                  </tbody>
                </table>
              </div>
            </div>
          `;
        }}

        function renderOwnerWorkload(worklist) {{
          const owners = worklist.owner_workload_summary || [];
          const highlights = worklist.owner_workload_highlights || {{}};
          const cards = owners.slice(0, 6).map((owner) => `
            <button
              class="owner-card${{state.worklistFilters.assignedOwnerName === owner.owner_name ? " is-active" : ""}}"
              type="button"
              data-owner-filter="${{escapeHtml(owner.owner_name)}}"
            >
              <div class="owner-card-head">
                <span class="owner-card-title">${{escapeHtml(owner.owner_name)}}</span>
                <span class="pill soft">${{formatNumber(owner.assigned_chapter_count)}} chapters</span>
              </div>
              <div class="owner-card-meta">
                immediate=${{formatNumber(owner.immediate_count)}} · breached=${{formatNumber(owner.breached_count)}} · blocking=${{formatNumber(owner.total_active_blocking_issue_count)}}
              </div>
              <div class="owner-card-meta">
                owner-ready=${{formatNumber(owner.owner_ready_count)}} · oldest=${{formatDate(owner.oldest_active_issue_at)}}
              </div>
              <div class="subtle-note">Click to filter the queue to this owner.</div>
            </button>
          `).join("");
          const highlightMarkup = [
            ["Top loaded", highlights.top_loaded_owner],
            ["Top breached", highlights.top_breached_owner],
            ["Top blocking", highlights.top_blocking_owner],
            ["Top immediate", highlights.top_immediate_owner],
          ].map(([label, entry]) => `
            <span class="pill${{entry ? "" : " soft"}}">${{escapeHtml(label)}}: ${{entry ? escapeHtml(entry.owner_name) : "—"}}</span>
          `).join("");

          els.ownerWorkload.innerHTML = `
            <div class="pill-row">
              ${{
                highlightMarkup || '<span class="pill soft">No owner workload highlights yet.</span>'
              }}
            </div>
            <div class="owner-grid">
              ${{
                cards || '<div class="placeholder" style="grid-column:1/-1;">No assigned owner workload yet. Chapters are either unassigned or currently quiet.</div>'
              }}
            </div>
          `;

          const selectedOwnerName =
            state.worklistFilters.assignedOwnerName ||
            highlights.top_loaded_owner?.owner_name ||
            owners[0]?.owner_name ||
            "";
          const selectedOwner = owners.find((owner) => owner.owner_name === selectedOwnerName) || null;
          const secondOwner = owners.find((owner) => owner.owner_name !== selectedOwnerName) || null;
          const entries = worklist.entries || [];
          const currentOwnerEntries = selectedOwnerName
            ? entries.filter((entry) => entry.assigned_owner_name === selectedOwnerName)
            : [];
          const unassignedImmediateCount = entries.filter(
            (entry) => !entry.is_assigned && entry.queue_priority === "immediate"
          ).length;
          const unassignedHighCount = entries.filter(
            (entry) => !entry.is_assigned && entry.queue_priority === "high"
          ).length;

          const alerts = [];
          if (highlights.top_breached_owner) {{
            alerts.push(`
              <div class="alert-card danger">
                <div class="alert-title">Breached owner workload</div>
                <div class="alert-body">${{escapeHtml(highlights.top_breached_owner.owner_name)}} currently owns ${{
                  formatNumber(highlights.top_breached_owner.breached_count)
                }} breached chapter(s).</div>
                <div class="button-row">
                  <button class="button ghost" type="button" data-owner-filter="${{escapeHtml(highlights.top_breached_owner.owner_name)}}">Focus owner</button>
                  <button class="button ghost" type="button" data-worklist-preset="breached">Show breached queue</button>
                </div>
              </div>
            `);
          }}
          if (unassignedImmediateCount > 0) {{
            alerts.push(`
              <div class="alert-card warn">
                <div class="alert-title">Unassigned immediate queue</div>
                <div class="alert-body">${{formatNumber(unassignedImmediateCount)}} immediate chapter(s) are still unassigned in the visible queue.</div>
                <div class="button-row">
                  <button class="button ghost" type="button" data-worklist-preset="unassigned-immediate">Focus unassigned immediate</button>
                </div>
              </div>
            `);
          }}
          if (selectedOwner && secondOwner && selectedOwner.assigned_chapter_count - secondOwner.assigned_chapter_count >= 2) {{
            alerts.push(`
              <div class="alert-card warn">
                <div class="alert-title">Rebalance suggested</div>
                <div class="alert-body">${{escapeHtml(selectedOwner.owner_name)}} carries ${{
                  formatNumber(selectedOwner.assigned_chapter_count)
                }} chapter(s), versus ${{
                  formatNumber(secondOwner.assigned_chapter_count)
                }} for ${{
                  escapeHtml(secondOwner.owner_name)
                }}.</div>
                <div class="button-row">
                  <button class="button ghost" type="button" data-owner-filter="${{escapeHtml(selectedOwner.owner_name)}}">Review loaded owner</button>
                  <button class="button ghost" type="button" data-owner-filter="${{escapeHtml(secondOwner.owner_name)}}">Inspect lighter owner</button>
                </div>
              </div>
            `);
          }}
          if (!alerts.length) {{
            alerts.push(`
              <div class="alert-card success">
                <div class="alert-title">No urgent owner alert</div>
                <div class="alert-body">Current visible queue does not show breached owner load, obvious owner imbalance, or unassigned immediate pressure.</div>
              </div>
            `);
          }}

          els.ownerAlert.innerHTML = `<div class="alert-grid">${{alerts.join("")}}</div>`;

          const hints = [];
          if (selectedOwner && secondOwner && selectedOwner.assigned_chapter_count - secondOwner.assigned_chapter_count >= 2) {{
            hints.push(
              `<li><strong>Rebalance candidate</strong><span>${{escapeHtml(selectedOwner.owner_name)}} currently carries ${{
                formatNumber(selectedOwner.assigned_chapter_count)
              }} chapters versus ${{
                formatNumber(secondOwner.assigned_chapter_count)
              }} for the next-loaded owner.</span></li>`
            );
          }}
          if (selectedOwner && selectedOwner.breached_count > 0) {{
            hints.push(
              `<li><strong>SLA breach concentration</strong><span>${{escapeHtml(selectedOwner.owner_name)}} is currently carrying ${{
                formatNumber(selectedOwner.breached_count)
              }} breached chapter(s).</span></li>`
            );
          }}
          if (unassignedImmediateCount > 0 || unassignedHighCount > 0) {{
            hints.push(
              `<li><strong>Unassigned queue pressure</strong><span>There are ${{
                formatNumber(unassignedImmediateCount)
              }} unassigned immediate chapter(s) and ${{
                formatNumber(unassignedHighCount)
              }} unassigned high-priority chapter(s) visible in the current queue.</span></li>`
            );
          }}

          if (!selectedOwner) {{
            els.ownerDetail.innerHTML =
              '<div class="placeholder">No assigned owners yet. Once chapters are assigned, this panel will surface owner-specific load, focus state, and balancing hints.</div>';
            return;
          }}

          const focusedChapters = currentOwnerEntries.slice(0, 5).map((entry) => `
            <li>
              <strong>#${{escapeHtml(entry.ordinal)}} · ${{escapeHtml(entry.title_src || "Untitled chapter")}}</strong>
              <span>priority=${{escapeHtml(entry.queue_priority)}} · sla=${{escapeHtml(entry.sla_status)}} · open=${{formatNumber(entry.open_issue_count)}} · blocking=${{formatNumber(entry.active_blocking_issue_count)}}</span>
            </li>
          `).join("");

          els.ownerDetail.innerHTML = `
            <div class="pill-row">
              <span class="pill">focus: ${{escapeHtml(selectedOwner.owner_name)}}</span>
              <span class="pill soft">assigned=${{formatNumber(selectedOwner.assigned_chapter_count)}}</span>
              <span class="pill warn">breached=${{formatNumber(selectedOwner.breached_count)}}</span>
              <span class="pill soft">blocking=${{formatNumber(selectedOwner.total_active_blocking_issue_count)}}</span>
              <button class="button ghost" type="button" data-clear-owner-filter="true">Clear owner focus</button>
            </div>
            <div class="detail-grid">
              <div class="detail-block">
                <div class="detail-label">Focused owner workload</div>
                <div class="detail-value">${{escapeHtml(selectedOwner.owner_name)}} owns ${{
                  formatNumber(selectedOwner.assigned_chapter_count)
                }} chapter(s), with ${{
                  formatNumber(selectedOwner.immediate_count)
                }} immediate and ${{
                  formatNumber(selectedOwner.high_count)
                }} high-priority items.</div>
              </div>
              <div class="detail-block">
                <div class="detail-label">Pressure profile</div>
                <div class="detail-value">open=${{formatNumber(selectedOwner.total_open_issue_count)}} · owner-ready=${{formatNumber(selectedOwner.owner_ready_count)}} · oldest=${{formatDate(selectedOwner.oldest_active_issue_at)}}</div>
              </div>
              <div class="detail-block full">
                <div class="detail-label">Balancing hints</div>
                <ul class="mini-list">
                  ${{
                    hints.join("") || `<li><strong>Balanced for now</strong><span>No obvious owner imbalance or unassigned urgent pressure is visible in the current queue window.</span></li>`
                  }}
                </ul>
              </div>
              <div class="detail-block full">
                <div class="detail-label">Visible chapters for focused owner</div>
                <ul class="mini-list">
                  ${{
                    focusedChapters || `<li><strong>No visible chapters</strong><span>The current queue filters do not expose chapter rows for this owner yet.</span></li>`
                  }}
                </ul>
              </div>
            </div>
          `;
        }}

        function renderWorklist(worklist) {{
          renderWorklistFilterSummary(worklist);
          const entries = worklist.entries || [];
          if (!entries.length) {{
            els.worklist.innerHTML = '<div class="placeholder">No actionable chapter pressure right now. This usually means the document is either clean or still pre-review.</div>';
          }} else {{
            els.worklist.innerHTML = entries.slice(0, 8).map((entry) => `
              <article
                class="queue-item clickable${{state.selectedChapterId === entry.chapter_id ? " is-active" : ""}}"
                data-chapter-id="${{escapeHtml(entry.chapter_id)}}"
              >
                <div class="queue-head">
                  <div style="display:flex; gap:12px;">
                    <div class="queue-rank">${{escapeHtml(entry.queue_rank)}}</div>
                    <div>
                      <h3 class="queue-title">#${{escapeHtml(entry.ordinal)}} · ${{escapeHtml(entry.title_src || "Untitled chapter")}}</h3>
                      <div class="queue-meta">
                        priority=${{escapeHtml(entry.queue_priority)}} · driver=${{escapeHtml(entry.queue_driver)}} ·
                        status=${{escapeHtml(entry.chapter_status)}} · heat=${{escapeHtml(entry.heat_level)}}
                      </div>
                    </div>
                  </div>
                  <div class="pill-row">
                    <span class="pill warn">open ${{formatNumber(entry.open_issue_count)}}</span>
                    <span class="pill warn">blocking ${{formatNumber(entry.active_blocking_issue_count)}}</span>
                  </div>
                </div>
                <div class="pill-row">
                  <span class="pill">SLA ${{escapeHtml(entry.sla_status)}}</span>
                  <span class="pill soft">age ${{formatNumber(entry.age_hours)}}h</span>
                  <span class="pill soft">owner-ready: ${{entry.owner_ready ? "yes" : "no"}}</span>
                  <span class="pill soft">assigned: ${{entry.assigned_owner_name ? escapeHtml(entry.assigned_owner_name) : "unassigned"}}</span>
                </div>
                <div class="queue-meta" style="margin-top:10px;">
                  dominant issue: ${{escapeHtml(entry.dominant_issue_type || "n/a")}} / ${{escapeHtml(entry.dominant_root_cause_layer || "n/a")}} ·
                  regression=${{escapeHtml(entry.regression_hint)}} · flapping=${{entry.flapping_hint ? "true" : "false"}}
                </div>
              </article>
            `).join("");
          }}
          renderOwnerWorkload(worklist);
        }}

        function renderChapterDetail(detail) {{
          setSelectedChapterId(detail.chapter_id);
          const familyBreakdown = (detail.issue_family_breakdown || []).slice(0, 6).map((entry) => `
            <tr>
              <td>${{escapeHtml(entry.issue_type)}}</td>
              <td>${{escapeHtml(entry.root_cause_layer)}}</td>
              <td>${{formatNumber(entry.open_issue_count)}}</td>
              <td>${{formatNumber(entry.active_blocking_issue_count)}}</td>
            </tr>
          `).join("");
          const recentIssues = (detail.recent_issues || []).slice(0, 5).map((issue) => `
            <li>
              <strong>${{escapeHtml(issue.issue_type)}} · ${{escapeHtml(issue.status)}}</strong>
              <span>severity=${{escapeHtml(issue.severity)}} · blocking=${{issue.blocking ? "true" : "false"}} · detector=${{escapeHtml(issue.detector)}} · created=${{formatDate(issue.created_at)}}</span>
            </li>
          `).join("");
          const recentActions = (detail.recent_actions || []).slice(0, 5).map((action) => `
            <li>
              <strong>${{escapeHtml(action.action_type)}} · ${{escapeHtml(action.status)}}</strong>
              <span>issue=${{escapeHtml(action.issue_type)}} · scope=${{escapeHtml(action.scope_type)}} · created=${{formatDate(action.created_at)}}</span>
              ${{
                action.status === "planned"
                  ? `<span><button class="button ghost" type="button" data-action-id="${{escapeHtml(action.action_id)}}" data-execute-action="true">Execute action</button></span>`
                  : ""
              }}
            </li>
          `).join("");
          const assignmentHistory = (detail.assignment_history || []).slice(0, 6).map((event) => `
            <li>
              <strong>${{escapeHtml(event.event_type)}}</strong>
              <span>owner=${{escapeHtml(event.owner_name || "—")}} · by=${{escapeHtml(event.performed_by || "—")}} · at=${{formatDate(event.created_at)}}</span>
            </li>
          `).join("");

          els.chapterDetail.innerHTML = `
            <div class="result-head">
              <div>
                <h3 class="result-title">#${{escapeHtml(detail.ordinal)}} · ${{escapeHtml(detail.title_src || "Untitled chapter")}}</h3>
                <div class="result-meta">
                  chapter_id=${{escapeHtml(detail.chapter_id)}} · status=${{escapeHtml(detail.chapter_status)}} · packets=${{formatNumber(detail.packet_count)}} · translated=${{formatNumber(detail.translated_packet_count)}}
                </div>
              </div>
            </div>
            <div class="pill-row">
              <span class="pill">open ${{formatNumber(detail.current_open_issue_count)}}</span>
              <span class="pill warn">blocking ${{formatNumber(detail.current_active_blocking_issue_count)}}</span>
              <span class="pill soft">assignment: ${{detail.assignment?.owner_name ? escapeHtml(detail.assignment.owner_name) : "unassigned"}}</span>
            </div>
            <div class="detail-grid">
              <div class="detail-block">
                <div class="detail-label">Queue driver</div>
                <div class="detail-value">${{escapeHtml(detail.queue_entry?.queue_driver || "—")}}</div>
              </div>
              <div class="detail-block">
                <div class="detail-label">SLA / Age</div>
                <div class="detail-value">${{escapeHtml(detail.queue_entry?.sla_status || "unknown")}} · ${{formatNumber(detail.queue_entry?.age_hours)}}h</div>
              </div>
              <div class="detail-block">
                <div class="detail-label">Owner-ready</div>
                <div class="detail-value">${{detail.queue_entry?.owner_ready ? "yes" : "no"}}${{detail.queue_entry?.owner_ready_reason ? " · " + escapeHtml(detail.queue_entry.owner_ready_reason) : ""}}</div>
              </div>
              <div class="detail-block">
                <div class="detail-label">Quality summary</div>
                <div class="detail-value">
                  coverage=${{detail.quality_summary?.coverage_ok ? "ok" : "check"}} ·
                  alignment=${{detail.quality_summary?.alignment_ok ? "ok" : "check"}} ·
                  format=${{detail.quality_summary?.format_ok ? "ok" : "check"}}
                </div>
              </div>
              <div class="detail-block full">
                <div class="detail-label">Issue family breakdown</div>
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Issue</th>
                      <th>Layer</th>
                      <th>Open</th>
                      <th>Blocking</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${{
                      familyBreakdown || `<tr><td colspan="4">No active issue families.</td></tr>`
                    }}
                  </tbody>
                </table>
              </div>
              <div class="detail-block">
                <div class="detail-label">Recent issues</div>
                <ul class="mini-list">
                  ${{
                    recentIssues || `<li><strong>No recent issues</strong><span>The chapter is currently quiet.</span></li>`
                  }}
                </ul>
              </div>
              <div class="detail-block">
                <div class="detail-label">Recent actions</div>
                <ul class="mini-list">
                  ${{
                    recentActions || `<li><strong>No recent actions</strong><span>No repair or rerun actions recorded yet.</span></li>`
                  }}
                </ul>
              </div>
              <div class="detail-block full">
                <div class="detail-label">Assignment history</div>
                <ul class="mini-list">
                  ${{
                    assignmentHistory || `<li><strong>No assignment history</strong><span>This chapter has not been assigned yet.</span></li>`
                  }}
                </ul>
              </div>
            </div>
          `;
          els.assignmentForm.hidden = false;
          els.assignmentOwner.value = detail.assignment?.owner_name || "";
          els.assignmentNote.value = detail.assignment?.note || "";
        }}

        function renderRunSummary(summary) {{
          els.runSummary.innerHTML = `
            <div class="pill-row">
              <span class="pill">status: ${{escapeHtml(summary.status)}}</span>
              <span class="pill">type: ${{escapeHtml(summary.run_type)}}</span>
              <span class="pill soft">backend: ${{escapeHtml(summary.backend || "default")}}</span>
              <span class="pill soft">model: ${{escapeHtml(summary.model_name || "default")}}</span>
            </div>
            <div class="kpi-grid" style="margin-top:14px;">
              <div class="kpi-card">
                <div class="kpi-label">Work Items</div>
                <div class="kpi-value">${{formatNumber(summary.work_items.total_count)}}</div>
                <div class="kpi-note">Queued and processed items under this run.</div>
              </div>
              <div class="kpi-card">
                <div class="kpi-label">Worker Leases</div>
                <div class="kpi-value">${{formatNumber(summary.worker_leases.total_count)}}</div>
                <div class="kpi-note">Latest heartbeat: ${{formatDate(summary.worker_leases.latest_heartbeat_at)}}</div>
              </div>
              <div class="kpi-card">
                <div class="kpi-label">Events</div>
                <div class="kpi-value">${{formatNumber(summary.events.event_count)}}</div>
                <div class="kpi-note">Latest event: ${{formatDate(summary.events.latest_event_at)}}</div>
              </div>
              <div class="kpi-card">
                <div class="kpi-label">Budget</div>
                <div class="kpi-value">${{formatMoney(summary.budget?.max_total_cost_usd)}}</div>
                <div class="kpi-note">Parallel workers: ${{formatNumber(summary.budget?.max_parallel_workers)}}</div>
              </div>
            </div>
            <table class="data-table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                <tr><td>run_id</td><td><code>${{escapeHtml(summary.run_id)}}</code></td></tr>
                <tr><td>document_id</td><td><code>${{escapeHtml(summary.document_id)}}</code></td></tr>
                <tr><td>requested_by</td><td>${{escapeHtml(summary.requested_by || "—")}}</td></tr>
                <tr><td>created_at</td><td>${{formatDate(summary.created_at)}}</td></tr>
                <tr><td>started_at</td><td>${{formatDate(summary.started_at)}}</td></tr>
                <tr><td>finished_at</td><td>${{formatDate(summary.finished_at)}}</td></tr>
                <tr><td>stop_reason</td><td>${{escapeHtml(summary.stop_reason || "—")}}</td></tr>
              </tbody>
            </table>
          `;
        }}

        function renderRunEvents(page) {{
          const entries = page.entries || [];
          if (!entries.length) {{
            els.runEvents.innerHTML = '<div class="placeholder">No run events recorded yet.</div>';
            return;
          }}
          const rows = entries.map((entry) => `
            <div class="log-line">
              <div><strong>${{escapeHtml(entry.event_type)}}</strong> · ${{formatDate(entry.created_at)}}</div>
              <div style="color:#9eb2a8;">actor=${{escapeHtml(entry.actor_type)}}${{entry.actor_id ? " / " + escapeHtml(entry.actor_id) : ""}}</div>
              <div style="margin-top:4px; white-space:pre-wrap;">${{escapeHtml(JSON.stringify(entry.payload_json || {{}}, null, 2))}}</div>
            </div>
          `).join("");
          els.runEvents.innerHTML = `<div class="log-shell">${{rows}}</div>`;
        }}

        async function refreshDocument(documentId, options = {{}}) {{
          const id = (documentId || state.currentDocumentId || els.documentId.value).trim();
          if (!id) {{
            setBanner(els.documentBanner, "Please provide a document id first.", "error");
            return;
          }}
          setRefreshInFlight("document", true);
          try {{
            const summary = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(id));
            setDocumentId(summary.document_id);
            if (!options.silent) {{
              setBanner(els.documentBanner, "Document loaded: " + (summary.title || summary.document_id), "success");
            }}
            renderDocumentKpis(summary);
            renderDocumentSummary(summary);
            await Promise.all([
              refreshExportDashboard(summary.document_id, {{ silent: options.silent }}),
              refreshWorklist(summary.document_id, {{ silent: options.silent }}),
            ]);
            if (state.selectedChapterId) {{
              try {{
                await loadChapterDetail(state.selectedChapterId, {{ silent: true }});
              }} catch (_error) {{
                clearChapterDetail();
              }}
            }}
            markRefreshed("document");
          }} finally {{
            setRefreshInFlight("document", false);
          }}
        }}

        async function refreshExportDashboard(documentId, _options = {{}}) {{
          const dashboard = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/exports?limit=5&offset=0");
          renderExportDashboard(dashboard);
        }}

        async function refreshWorklist(documentId, options = {{}}) {{
          setRefreshInFlight("worklist", true);
          try {{
            const worklist = await fetchJson(buildWorklistQuery(documentId));
            if (!options.silent) {{
              setBanner(
                els.worklistBanner,
                "Worklist loaded: " + formatNumber(worklist.filtered_worklist_count) + " filtered chapters, " + formatNumber(worklist.owner_ready_count) + " owner-ready.",
                "success",
              );
            }}
            renderWorklist(worklist);
            markRefreshed("worklist");
          }} finally {{
            setRefreshInFlight("worklist", false);
          }}
        }}

        function startDocumentPolling() {{
          if (state.documentPollHandle) {{
            window.clearInterval(state.documentPollHandle);
          }}
          state.documentPollHandle = window.setInterval(async function() {{
            if (!state.documentPollingEnabled || !state.currentDocumentId || state.documentPollInFlight || document.hidden) {{
              return;
            }}
            state.documentPollInFlight = true;
            try {{
              await refreshDocument(state.currentDocumentId, {{ silent: true }});
            }} catch (_error) {{
              // keep auto-refresh quiet; manual actions remain explicit
            }} finally {{
              state.documentPollInFlight = false;
            }}
          }}, 12000);
        }}

        function stopDocumentPolling() {{
          if (state.documentPollHandle) {{
            window.clearInterval(state.documentPollHandle);
            state.documentPollHandle = null;
          }}
        }}

        async function loadChapterDetail(chapterId, options = {{}}) {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId || !chapterId) {{
            return;
          }}
          const detail = await fetchJson(
            apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/chapters/" + encodeURIComponent(chapterId) + "/worklist"
          );
          renderChapterDetail(detail);
          if (!options.silent) {{
            setBanner(els.worklistBanner, "Loaded chapter detail for #" + detail.ordinal + ".", "success");
          }}
        }}

        async function executeChapterAction(actionId) {{
          if (!actionId) {{
            return;
          }}
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          const chapterId = state.selectedChapterId;
          setBanner(els.worklistBanner, "Executing action " + actionId + " with follow-up rerun/review…", "success");
          try {{
            const result = await fetchJson(
              apiPrefix + "/actions/" + encodeURIComponent(actionId) + "/execute?run_followup=true",
              {{
                method: "POST",
              }}
            );
            setBanner(
              els.worklistBanner,
              "Action executed: " + result.action_id + " · issue_resolved=" + (result.issue_resolved === null ? "n/a" : String(result.issue_resolved)),
              "success",
            );
            if (documentId) {{
              await Promise.all([
                refreshDocument(documentId),
                chapterId ? loadChapterDetail(chapterId, {{ silent: true }}) : Promise.resolve(),
              ]);
            }}
          }} catch (error) {{
            setBanner(els.worklistBanner, error.message, "error");
          }}
        }}

        function startRunPolling() {{
          if (state.runPollHandle) {{
            window.clearInterval(state.runPollHandle);
          }}
          state.runPollHandle = window.setInterval(async function() {{
            if (!state.runPollingEnabled || !state.currentRunId || state.runPollInFlight || document.hidden) {{
              return;
            }}
            state.runPollInFlight = true;
            try {{
              await refreshRun(state.currentRunId, {{ silent: true }});
            }} catch (_error) {{
              // keep polling silent; explicit failures are still visible on manual load/control
            }} finally {{
              state.runPollInFlight = false;
            }}
          }}, 7000);
        }}

        function stopRunPolling() {{
          if (state.runPollHandle) {{
            window.clearInterval(state.runPollHandle);
            state.runPollHandle = null;
          }}
        }}

        async function refreshRun(runId, options = {{}}) {{
          const id = (runId || state.currentRunId || els.runId.value).trim();
          if (!id) {{
            setBanner(els.runBanner, "Please provide a run id first.", "error");
            return;
          }}
          setRefreshInFlight("run", true);
          try {{
            const summary = await fetchJson(apiPrefix + "/runs/" + encodeURIComponent(id));
            const events = await fetchJson(apiPrefix + "/runs/" + encodeURIComponent(id) + "/events?limit=10&offset=0");
            setRunId(summary.run_id);
            if (!options.silent) {{
              setBanner(els.runBanner, "Run loaded: " + summary.status + " · " + summary.run_id, "success");
            }}
            renderRunSummary(summary);
            renderRunEvents(events);
            markRefreshed("run");
          }} finally {{
            setRefreshInFlight("run", false);
          }}
        }}

        async function assignChapterOwner(event) {{
          event.preventDefault();
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          const chapterId = state.selectedChapterId;
          if (!documentId || !chapterId) {{
            setBanner(els.worklistBanner, "Select a chapter before assigning an owner.", "error");
            return;
          }}
          const ownerName = els.assignmentOwner.value.trim();
          const assignedBy = els.assignmentActor.value.trim() || "ui-operator";
          if (!ownerName) {{
            setBanner(els.worklistBanner, "Owner name cannot be empty.", "error");
            return;
          }}
          setButtonLoading(els.assignOwner, true, "Assigning…");
          try {{
            await fetchJson(
              apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/chapters/" + encodeURIComponent(chapterId) + "/worklist/assignment",
              {{
                method: "PUT",
                body: {{
                  owner_name: ownerName,
                  assigned_by: assignedBy,
                  note: els.assignmentNote.value.trim() || null,
                }},
              }}
            );
            setBanner(els.worklistBanner, "Owner assigned to selected chapter.", "success");
            await Promise.all([
              refreshWorklist(documentId),
              loadChapterDetail(chapterId, {{ silent: true }}),
            ]);
          }} catch (error) {{
            setBanner(els.worklistBanner, error.message, "error");
          }} finally {{
            setButtonLoading(els.assignOwner, false, "Assigning…");
          }}
        }}

        async function clearChapterOwner() {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          const chapterId = state.selectedChapterId;
          if (!documentId || !chapterId) {{
            setBanner(els.worklistBanner, "Select a chapter before clearing assignment.", "error");
            return;
          }}
          setButtonLoading(els.clearOwner, true, "Clearing…");
          try {{
            await fetchJson(
              apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/chapters/" + encodeURIComponent(chapterId) + "/worklist/assignment/clear",
              {{
                method: "POST",
                body: {{
                  cleared_by: els.assignmentActor.value.trim() || "ui-operator",
                  note: els.assignmentNote.value.trim() || null,
                }},
              }}
            );
            setBanner(els.worklistBanner, "Assignment cleared for selected chapter.", "success");
            await Promise.all([
              refreshWorklist(documentId),
              loadChapterDetail(chapterId, {{ silent: true }}),
            ]);
          }} catch (error) {{
            setBanner(els.worklistBanner, error.message, "error");
          }} finally {{
            setButtonLoading(els.clearOwner, false, "Clearing…");
          }}
        }}

        async function applyWorklistFilters(event) {{
          if (event) {{
            event.preventDefault();
          }}
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.worklistBanner, "Load a document before applying queue filters.", "error");
            return;
          }}
          state.worklistFilters.queuePriority = els.filterQueuePriority.value;
          state.worklistFilters.slaStatus = els.filterSlaStatus.value;
          state.worklistFilters.ownerReady = els.filterOwnerReady.value;
          state.worklistFilters.assigned = els.filterAssigned.value;
          state.worklistFilters.assignedOwnerName = els.filterOwnerName.value.trim();
          syncWorklistFilterForm();
          await refreshWorklist(documentId);
        }}

        async function clearWorklistFilters() {{
          state.worklistFilters = {{
            queuePriority: "",
            slaStatus: "",
            ownerReady: "",
            assigned: "",
            assignedOwnerName: "",
          }};
          syncWorklistFilterForm();
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            return;
          }}
          await refreshWorklist(documentId);
        }}

        async function applyWorklistPreset(preset) {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.worklistBanner, "Load a document before applying queue presets.", "error");
            return;
          }}
          if (preset === "breached") {{
            state.worklistFilters.slaStatus = "breached";
            state.worklistFilters.queuePriority = "";
            state.worklistFilters.ownerReady = "";
            state.worklistFilters.assigned = "";
            state.worklistFilters.assignedOwnerName = "";
          }} else if (preset === "unassigned-immediate") {{
            state.worklistFilters.queuePriority = "immediate";
            state.worklistFilters.assigned = "false";
            state.worklistFilters.slaStatus = "";
            state.worklistFilters.ownerReady = "";
            state.worklistFilters.assignedOwnerName = "";
          }}
          syncWorklistFilterForm();
          await refreshWorklist(documentId);
        }}

        async function bootstrapDocument(event) {{
          event.preventDefault();
          const sourceFile = getSelectedSourceFile();
          if (!sourceFile) {{
            setBanner(els.documentBanner, "Choose an EPUB or PDF to bootstrap.", "error");
            return;
          }}
          const submitButton = event.submitter || els.bootstrapForm.querySelector("button[type=submit]");
          setButtonLoading(submitButton, true, "Uploading…");
          try {{
            const formData = new FormData();
            formData.append("source_file", sourceFile);
            const summary = await fetchMultipartJson(apiPrefix + "/documents/bootstrap-upload", formData);
            setDocumentId(summary.document_id);
            els.runDocumentId.value = summary.document_id;
            clearSelectedSourceFile();
            setBanner(
              els.documentBanner,
              "Bootstrap completed for " + (summary.title || summary.document_id) + " from " + sourceFile.name + ".",
              "success"
            );
            renderDocumentKpis(summary);
            renderDocumentSummary(summary);
            await Promise.all([
              refreshExportDashboard(summary.document_id),
              refreshWorklist(summary.document_id),
            ]);
          }} catch (error) {{
            setBanner(els.documentBanner, error.message, "error");
          }} finally {{
            setButtonLoading(submitButton, false, "Uploading…");
          }}
        }}

        async function runDocumentAction(action) {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.documentBanner, "Load or bootstrap a document before running actions.", "error");
            return;
          }}
          const buttonMap = {{
            translate: els.translateDocument,
            review: els.reviewDocument,
            export: els.exportDocument,
          }};
          const button = buttonMap[action];
          setButtonLoading(button, true, action === "export" ? "Exporting…" : "Running…");
          try {{
            let response;
            if (action === "translate") {{
              response = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/translate", {{
                method: "POST",
                body: {{ packet_ids: [] }},
              }});
              setBanner(els.documentBanner, "Translate completed: " + formatNumber(response.translated_packet_count) + " packets translated.", "success");
            }} else if (action === "review") {{
              response = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/review", {{
                method: "POST",
              }});
              setBanner(els.documentBanner, "Review completed: " + formatNumber(response.total_issue_count) + " issues, " + formatNumber(response.total_action_count) + " actions.", "success");
            }} else {{
              response = await fetchJson(apiPrefix + "/documents/" + encodeURIComponent(documentId) + "/export", {{
                method: "POST",
                body: {{
                  export_type: els.exportType.value,
                  auto_execute_followup_on_gate: els.autoFollowup.value === "true",
                  max_auto_followup_attempts: 2,
                }},
              }});
              try {{
                const savedName = await downloadCurrentExport(documentId, els.exportType.value);
                setBanner(els.documentBanner, "Export completed and saved as " + savedName + ".", "success");
              }} catch (downloadError) {{
                if (downloadError && downloadError.name === "AbortError") {{
                  setBanner(els.documentBanner, "Export completed, but local save was cancelled.", "success");
                }} else {{
                  throw downloadError;
                }}
              }}
            }}
            await refreshDocument(documentId);
          }} catch (error) {{
            setBanner(els.documentBanner, error.message, "error");
            if (action === "export") {{
              try {{
                await Promise.all([
                  refreshExportDashboard(documentId),
                  refreshWorklist(documentId),
                ]);
              }} catch (_secondary) {{
                // no-op: banner already shows the gate error
              }}
            }}
          }} finally {{
            setButtonLoading(button, false, action === "export" ? "Exporting…" : "Running…");
          }}
        }}

        async function createRun() {{
          const documentId = (els.runDocumentId.value || state.currentDocumentId).trim();
          if (!documentId) {{
            setBanner(els.runBanner, "Provide a document id or load a document before creating a run.", "error");
            return;
          }}
          setButtonLoading(els.createRun, true, "Creating…");
          try {{
            const budget = {{}};
            if (els.runMaxCost.value) {{
              budget.max_total_cost_usd = Number(els.runMaxCost.value);
            }}
            if (els.runMaxWorkers.value) {{
              budget.max_parallel_workers = Number(els.runMaxWorkers.value);
            }}
            const payload = {{
              document_id: documentId,
              run_type: "translate_full",
              requested_by: "ui-operator",
              priority: 100,
              status_detail_json: {{
                source: "homepage-workspace",
              }},
            }};
            if (Object.keys(budget).length) {{
              payload.budget = budget;
            }}
            const summary = await fetchJson(apiPrefix + "/runs", {{
              method: "POST",
              body: payload,
            }});
            setRunId(summary.run_id);
            setBanner(els.runBanner, "Run created: " + summary.run_id, "success");
            renderRunSummary(summary);
            renderRunEvents({{ entries: [], event_count: 0 }});
            markRefreshed("run");
          }} catch (error) {{
            setBanner(els.runBanner, error.message, "error");
          }} finally {{
            setButtonLoading(els.createRun, false, "Creating…");
          }}
        }}

        async function controlRun(action) {{
          const runId = (state.currentRunId || els.runId.value).trim();
          if (!runId) {{
            setBanner(els.runBanner, "Load a run before applying control actions.", "error");
            return;
          }}
          const buttonMap = {{
            pause: els.pauseRun,
            resume: els.resumeRun,
            drain: els.drainRun,
            cancel: els.cancelRun,
          }};
          const button = buttonMap[action];
          setButtonLoading(button, true, "Applying…");
          try {{
            const summary = await fetchJson(apiPrefix + "/runs/" + encodeURIComponent(runId) + "/" + action, {{
              method: "POST",
              body: {{
                actor_id: "ui-operator",
                note: "Triggered from homepage run console",
                detail_json: {{ source: "homepage-run-console" }},
              }},
            }});
            setRunId(summary.run_id);
            setBanner(els.runBanner, "Run " + action + " applied: " + summary.status, "success");
            renderRunSummary(summary);
            markRefreshed("run");
            await refreshRun(summary.run_id);
          }} catch (error) {{
            setBanner(els.runBanner, error.message, "error");
          }} finally {{
            setButtonLoading(button, false, "Applying…");
          }}
        }}

        async function checkHealth() {{
          try {{
            const payload = await fetchJson(healthUrl);
            els.healthState.textContent = "Online";
            els.healthCaption.textContent = payload.status || "ok";
            els.healthDot.classList.add("ok");
          }} catch (error) {{
            els.healthState.textContent = "Degraded";
            els.healthCaption.textContent = "Health check unavailable";
            console.warn("Health check failed", error);
          }}
        }}

        els.chooseSourceFile.addEventListener("click", function() {{
          els.sourceFile.click();
        }});
        els.sourceFilePicker.addEventListener("click", function(event) {{
          if (event.target.closest("button")) {{
            return;
          }}
          els.sourceFile.click();
        }});
        els.sourceFilePicker.addEventListener("keydown", function(event) {{
          if (event.key !== "Enter" && event.key !== " ") {{
            return;
          }}
          event.preventDefault();
          els.sourceFile.click();
        }});
        els.sourceFile.addEventListener("change", syncSelectedSourceFileUi);
        els.bootstrapForm.addEventListener("submit", bootstrapDocument);
        els.loadDocument.addEventListener("click", function() {{
          refreshDocument().catch((error) => setBanner(els.documentBanner, error.message, "error"));
        }});
        els.translateDocument.addEventListener("click", function() {{
          runDocumentAction("translate");
        }});
        els.reviewDocument.addEventListener("click", function() {{
          runDocumentAction("review");
        }});
        els.exportDocument.addEventListener("click", function() {{
          runDocumentAction("export");
        }});
        els.documentSummary.addEventListener("click", function(event) {{
          const button = event.target.closest("[data-chapter-id]");
          if (!button) {{
            return;
          }}
          loadChapterDetail(button.dataset.chapterId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.createRun.addEventListener("click", createRun);
        els.loadRun.addEventListener("click", function() {{
          refreshRun().catch((error) => setBanner(els.runBanner, error.message, "error"));
        }});
        els.pauseRun.addEventListener("click", function() {{ controlRun("pause"); }});
        els.resumeRun.addEventListener("click", function() {{ controlRun("resume"); }});
        els.drainRun.addEventListener("click", function() {{ controlRun("drain"); }});
        els.cancelRun.addEventListener("click", function() {{ controlRun("cancel"); }});
        els.toggleDocumentPolling.addEventListener("click", function() {{
          state.documentPollingEnabled = !state.documentPollingEnabled;
          syncDocumentPollingUi();
          if (state.documentPollingEnabled) {{
            startDocumentPolling();
          }} else {{
            stopDocumentPolling();
          }}
        }});
        els.toggleRunPolling.addEventListener("click", function() {{
          state.runPollingEnabled = !state.runPollingEnabled;
          syncRunPollingUi();
          if (state.runPollingEnabled) {{
            startRunPolling();
          }} else {{
            stopRunPolling();
          }}
        }});
        els.refreshWorklist.addEventListener("click", function() {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.worklistBanner, "Load a document before refreshing the worklist.", "error");
            return;
          }}
          refreshWorklist(documentId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.openCurrentExports.addEventListener("click", function() {{
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.documentBanner, "Load a document before refreshing exports.", "error");
            return;
          }}
          refreshExportDashboard(documentId).catch((error) => setBanner(els.documentBanner, error.message, "error"));
        }});
        els.worklist.addEventListener("click", function(event) {{
          const card = event.target.closest("[data-chapter-id]");
          if (!card) {{
            return;
          }}
          loadChapterDetail(card.dataset.chapterId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.ownerWorkload.addEventListener("click", function(event) {{
          const ownerButton = event.target.closest("[data-owner-filter]");
          if (!ownerButton) {{
            return;
          }}
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          if (!documentId) {{
            setBanner(els.worklistBanner, "Load a document before filtering by owner.", "error");
            return;
          }}
          state.worklistFilters.assigned = "true";
          state.worklistFilters.assignedOwnerName = ownerButton.dataset.ownerFilter || "";
          syncWorklistFilterForm();
          refreshWorklist(documentId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.ownerAlert.addEventListener("click", function(event) {{
          const ownerButton = event.target.closest("[data-owner-filter]");
          if (ownerButton) {{
            const documentId = (state.currentDocumentId || els.documentId.value).trim();
            if (!documentId) {{
              setBanner(els.worklistBanner, "Load a document before filtering by owner.", "error");
              return;
            }}
            state.worklistFilters.assigned = "true";
            state.worklistFilters.assignedOwnerName = ownerButton.dataset.ownerFilter || "";
            syncWorklistFilterForm();
            refreshWorklist(documentId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
            return;
          }}
          const presetButton = event.target.closest("[data-worklist-preset]");
          if (!presetButton) {{
            return;
          }}
          applyWorklistPreset(presetButton.dataset.worklistPreset).catch((error) =>
            setBanner(els.worklistBanner, error.message, "error")
          );
        }});
        els.ownerDetail.addEventListener("click", function(event) {{
          const clearButton = event.target.closest("[data-clear-owner-filter]");
          if (!clearButton) {{
            return;
          }}
          const documentId = (state.currentDocumentId || els.documentId.value).trim();
          state.worklistFilters.assignedOwnerName = "";
          if (state.worklistFilters.assigned === "true" && !els.filterAssigned.value) {{
            state.worklistFilters.assigned = "";
          }}
          syncWorklistFilterForm();
          if (!documentId) {{
            return;
          }}
          refreshWorklist(documentId).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.chapterDetail.addEventListener("click", function(event) {{
          const button = event.target.closest("[data-execute-action]");
          if (!button) {{
            return;
          }}
          executeChapterAction(button.dataset.actionId);
        }});
        els.worklistFilterForm.addEventListener("submit", function(event) {{
          applyWorklistFilters(event).catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.clearWorklistFilters.addEventListener("click", function() {{
          clearWorklistFilters().catch((error) => setBanner(els.worklistBanner, error.message, "error"));
        }});
        els.assignmentForm.addEventListener("submit", assignChapterOwner);
        els.clearOwner.addEventListener("click", clearChapterOwner);

        els.documentId.value = state.currentDocumentId;
        els.runDocumentId.value = state.currentDocumentId;
        els.runId.value = state.currentRunId;
        syncSelectedSourceFileUi();
        syncDocumentPollingUi();
        syncRunPollingUi();
        syncWorklistFilterForm();
        updateLiveStatusUi();
        startDocumentPolling();
        startRunPolling();
        window.setInterval(updateLiveStatusUi, 5000);

        checkHealth();
        if (state.currentDocumentId) {{
          refreshDocument(state.currentDocumentId).catch((error) => setBanner(els.documentBanner, error.message, "error"));
        }}
        if (state.currentRunId) {{
          refreshRun(state.currentRunId).catch((error) => setBanner(els.runBanner, error.message, "error"));
        }}
      }})();
    </script>
  </body>
</html>
"""
