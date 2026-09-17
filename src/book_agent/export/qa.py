"""Export QA checks over rendered HTML (pure functions, no database).

The R1-R8 rules come from ``scripts/verify_chapter.py`` (now a thin CLI over
this module) and keep their names so existing reports stay comparable. The
product checks (heading hierarchy, empty blocks, untranslated ratio) are
used by ``services/export_qa.py`` after run-based exports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ERROR = "error"
WARNING = "warning"


@dataclass(slots=True)
class QaCheck:
    name: str
    ok: bool
    detail: str
    severity: str = ERROR
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "severity": self.severity, **({"data": self.data} if self.data else {})}


@dataclass(slots=True)
class QaReport:
    checks: list[QaCheck]
    structure: dict[str, int]
    figcaptions: list[str]

    @property
    def all_ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failed(self) -> list[QaCheck]:
        return [check for check in self.checks if not check.ok]

    def to_json(self) -> dict[str, Any]:
        return {
            "all_ok": self.all_ok,
            "structure": dict(self.structure),
            "checks": [check.to_json() for check in self.checks],
            "figcaptions": list(self.figcaptions),
        }


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def audit_html(
    html: str,
    *,
    figure_prefix: str | None = None,
    figure_count: int | None = None,
    max_h2: int = 30,
    bilingual: bool = False,
    min_img_coverage: int | None = None,
) -> QaReport:
    """R1-R8. R1 needs a figure prefix and count; R6 needs a count; R7/R8 are bilingual only."""
    structural_html = html
    if bilingual:
        structural_html = re.sub(
            r"<details\s+class=['\"]source-fold['\"][^>]*>.*?</details>", "", html, flags=re.DOTALL
        )
    figcaptions = re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", structural_html, flags=re.DOTALL)
    standalone_caps = re.findall(
        r"<p\s+class=['\"]caption['\"][^>]*><em>(.*?)</em></p>", structural_html, flags=re.DOTALL
    )
    figcap_texts = [_norm_ws(c)[:200] for c in figcaptions + standalone_caps if c.strip()]
    head_texts = [_norm_ws(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", structural_html, flags=re.DOTALL)]
    para_norm = [
        _norm_ws(p)
        for p in re.findall(r"<p(?![^>]*class=['\"]caption['\"])[^>]*>(.*?)</p>", structural_html, flags=re.DOTALL)
    ]
    figure_blocks = re.findall(r"<figure[^>]*>(.*?)</figure>", structural_html, flags=re.DOTALL)
    img_blocks = sum(1 for block in figure_blocks if "<img " in block)
    placeholder_blocks = sum(1 for block in figure_blocks if "image-placeholder" in block)
    checks: list[QaCheck] = []

    # R1: at least figure_count distinct "Figure P.N" captions rendered (parser may skip numbers).
    if figure_prefix is not None and figure_count is not None:
        found: set[str] = set()
        for cap in figcap_texts:
            match = re.match(r"^(?:图|Figure|Fig\.?|图\s*)\s*(\d+\.\d+)(?!\d)", cap)
            if match and match.group(1).startswith(f"{figure_prefix}."):
                found.add(match.group(1))
        checks.append(
            QaCheck(
                "R1 figure_coverage",
                len(found) >= figure_count,
                f"found={len(found)} expected≥{figure_count} "
                f"numbers={sorted(found, key=lambda s: tuple(int(x) for x in s.split('.')))}",
            )
        )

    checks.append(QaCheck("R2 heading_count_sane", len(head_texts) <= max_h2, f"h2={len(head_texts)} cap={max_h2}"))

    # R3: a caption body (>= 12 chars) must not also appear as body text.
    dup_hits = [cap[:60] for cap in figcap_texts if len(cap.strip()) >= 12 and any(cap[:60] in p for p in para_norm)]
    checks.append(QaCheck("R3 caption_no_body_dup", not dup_hits, f"duplicates={dup_hits[:3]}" if dup_hits else "clean"))

    body_lead_re = re.compile(
        r"^(?:Figure|Fig\.?|Image|图)\s*\d+\.\d+\s+(?:describes|shows|illustrates|displays|presents|demonstrates)\b",
        re.IGNORECASE,
    )
    lead_hits = [cap for cap in figcap_texts if body_lead_re.match(cap)]
    checks.append(QaCheck("R4 caption_no_body_lead", not lead_hits, f"hits={lead_hits[:2]}" if lead_hits else "clean"))

    orphans = [p for p in para_norm if p in {"]", "[", "{", "}"}]
    checks.append(QaCheck("R5 no_orphan_brackets", not orphans, f"orphans={orphans[:4]}" if orphans else "clean"))

    expected_images = min_img_coverage if min_img_coverage is not None else figure_count
    if expected_images is not None:
        checks.append(
            QaCheck(
                "R6 image_render_coverage",
                img_blocks >= expected_images and placeholder_blocks <= 3,
                f"<img>={img_blocks} placeholders={placeholder_blocks} total_figures={len(figure_blocks)} expected≥{expected_images}",
            )
        )

    if bilingual:
        folds = re.findall(r"<details\s+class=['\"]source-fold['\"][^>]*>(.*?)</details>", html, flags=re.DOTALL)
        bad_folds: list[str] = []
        for fold in folds:
            if "英文原文" not in fold:
                bad_folds.append("missing-summary")
            elif "src-body" not in fold:
                bad_folds.append("missing-src-body")
            else:
                body = re.search(r"<div\s+class=['\"]src-body['\"][^>]*>(.*?)</div>", fold, flags=re.DOTALL)
                if not body or not _strip_tags(body.group(1)).strip():
                    bad_folds.append("empty-src-body")
        checks.append(
            QaCheck("R7 source_fold_well_formed", bool(folds) and not bad_folds, f"folds={len(folds)} bad={bad_folds[:3]}")
        )
        skinny = re.sub(r"<ol[^>]*>.*?</ol>", "", structural_html, flags=re.DOTALL)
        skinny = re.sub(r"<ul[^>]*>.*?</ul>", "", skinny, flags=re.DOTALL)
        skinny = re.sub(r"<aside[^>]*>.*?</aside>", "", skinny, flags=re.DOTALL)
        skinny = re.sub(r"<details[^>]*>.*?</details>", "\x00", skinny, flags=re.DOTALL)
        units = len(re.findall(r"(?:<p(?![^>]*class=['\"]caption['\"])[^>]*>.*?</p>\s*)+", skinny, flags=re.DOTALL))
        coverage = len(folds) / max(units, 1)
        checks.append(
            QaCheck(
                "R8 source_fold_coverage",
                coverage >= 0.8,
                f"folds={len(folds)} body_units={units} coverage={coverage:.0%}",
            )
        )

    return QaReport(
        checks=checks,
        structure={
            "h2": len(head_texts),
            "figcaption": len(figcap_texts),
            "p": len(para_norm),
            "figure": len(figure_blocks),
            "img": img_blocks,
            "placeholder": placeholder_blocks,
        },
        figcaptions=figcap_texts,
    )


def heading_hierarchy_check(html: str) -> QaCheck:
    """Headings may go up any number of levels but down only one at a time (h1 → h3 skips a level)."""
    levels = [int(level) for level in re.findall(r"<h([1-6])[\s>]", html)]
    jumps = [(previous, current) for previous, current in zip(levels, levels[1:]) if current > previous + 1]
    return QaCheck(
        "Q1 heading_hierarchy",
        not jumps,
        f"headings={len(levels)} skipped_levels={jumps[:5]}" if jumps else f"headings={len(levels)} clean",
        severity=WARNING,
        data={"skipped_levels": [list(jump) for jump in jumps[:20]]},
    )


def empty_block_check(html: str) -> QaCheck:
    """Empty paragraphs, list items and headings, and figures with neither an image, a placeholder nor a table."""
    empty_text = [
        tag
        for tag, body in re.findall(r"<(p|li|h[1-6])(?:\s[^>]*)?>(.*?)</\1>", html, flags=re.DOTALL)
        if not _strip_tags(body).strip()
    ]
    hollow_figures = sum(
        1
        for body in re.findall(r"<figure[^>]*>(.*?)</figure>", html, flags=re.DOTALL)
        if "<img " not in body and "image-placeholder" not in body and "<table" not in body and "<pre" not in body
        and not _strip_tags(body).strip()
    )
    count = len(empty_text) + hollow_figures
    return QaCheck(
        "Q2 no_empty_blocks",
        count == 0,
        f"empty_text_blocks={len(empty_text)} hollow_figures={hollow_figures}",
        severity=WARNING,
        data={"empty_tags": sorted(set(empty_text))},
    )


def untranslated_ratio_check(translatable: int, untranslated: int, *, error_ratio: float = 0.02) -> QaCheck:
    """Share of translatable source sentences with no current translation."""
    ratio = untranslated / translatable if translatable else 0.0
    return QaCheck(
        "Q3 untranslated_ratio",
        untranslated == 0,
        f"untranslated={untranslated}/{translatable} ({ratio:.1%})",
        severity=ERROR if ratio > error_ratio else WARNING,
        data={"untranslated": untranslated, "translatable": translatable, "ratio": round(ratio, 4)},
    )
