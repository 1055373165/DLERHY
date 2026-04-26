"""Re-parse the source PDF and report the figure-clustering impact.

Reads the same PDF that's currently in the DB, runs the parser (with
clustering active), and prints a summary:
- How many blocks the parser emits now
- How many FIGURE blocks were created
- How many text blocks got absorbed as inline labels
- Per-page breakdown for the chapter under review

No DB writes. Use this to sanity-check the algorithm before any commit
that activates clustering on production data.
"""
# ruff: noqa: E402
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Avoid the local proxy adding latency; we don't make network calls but
# pydantic-settings probes them at import time on some envs.
for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.figure_clustering import FigureClusterConfig
from book_agent.domain.structure.pdf import (
    PdfFileProfiler,
    PdfStructureRecoveryService,
    PyMuPDFTextExtractor,
)


PDF_PATH = Path(
    os.environ.get(
        "PDF_PATH",
        "/Users/smy/project/book-agent/artifacts/uploads/8676b71d9dc34225b86939a5ecec92fc/llm-book.pdf",
    )
)
PAGE_RANGE = os.environ.get("PAGE_RANGE", "")  # "1-12" optional filter


def main() -> int:
    if not PDF_PATH.is_file():
        print(f"PDF not found at {PDF_PATH}", file=sys.stderr)
        return 1

    extractor = PyMuPDFTextExtractor()
    profiler = PdfFileProfiler(extractor)
    profile = profiler.profile(PDF_PATH)
    extraction = extractor.extract(PDF_PATH)

    # First pass: clustering DISABLED — baseline block count.
    baseline_service = PdfStructureRecoveryService(
        figure_cluster_config=FigureClusterConfig(enabled=False),
    )
    baseline = baseline_service.recover(PDF_PATH, extraction, profile)

    # Second pass: clustering ENABLED with default config.
    enabled_service = PdfStructureRecoveryService(
        figure_cluster_config=FigureClusterConfig(),
    )
    enabled = enabled_service.recover(PDF_PATH, extraction, profile)

    def block_counts(parsed) -> dict[str, int]:
        counts: dict[str, int] = {}
        for chapter in parsed.chapters:
            for block in chapter.blocks:
                counts[block.block_type] = counts.get(block.block_type, 0) + 1
        return counts

    print(f"[dryrun] PDF: {PDF_PATH}")
    print(f"[dryrun] page_count={profile.page_count}")
    print()
    print("=== block_type counts ===")
    print(f"{'type':<14} {'baseline':>10} {'clustered':>10} {'delta':>8}")
    base_counts = block_counts(baseline)
    new_counts = block_counts(enabled)
    types = sorted(set(base_counts) | set(new_counts))
    for t in types:
        b = base_counts.get(t, 0)
        n = new_counts.get(t, 0)
        delta = n - b
        marker = " <" if delta != 0 else ""
        print(f"{t:<14} {b:>10} {n:>10} {delta:+8d}{marker}")
    print()

    figure_blocks = [
        block
        for chapter in enabled.chapters
        for block in chapter.blocks
        if block.block_type == BlockType.FIGURE.value
    ]
    print(f"=== FIGURE blocks emitted: {len(figure_blocks)} ===")

    # Per-page summary so we can see where clustering bites and where it doesn't.
    page_summary: dict[int, dict[str, int]] = {}
    for fig in figure_blocks:
        cluster = fig.metadata.get("figure_cluster") if fig.metadata else None
        if not cluster:
            continue
        page = fig.metadata.get("source_page_start") or 0
        slot = page_summary.setdefault(page, {"figures": 0, "anchors_merged": 0, "labels_absorbed": 0})
        slot["figures"] += 1
        slot["anchors_merged"] += len(cluster.get("anchor_block_anchors", []))
        slot["labels_absorbed"] += len(cluster.get("absorbed_label_anchors", []))

    page_focus_lo = int(os.environ.get("PAGE_FOCUS_LO", "0"))
    page_focus_hi = int(os.environ.get("PAGE_FOCUS_HI", "0"))
    print(f"=== per-page (focus: {page_focus_lo}-{page_focus_hi if page_focus_hi else 'end'}) ===")
    for page, stats in sorted(page_summary.items()):
        if page_focus_hi and not (page_focus_lo <= page <= page_focus_hi):
            continue
        print(
            f"  page={page:>3} figures={stats['figures']} "
            f"anchors_merged={stats['anchors_merged']} labels_absorbed={stats['labels_absorbed']}"
        )

    print()
    print("=== sample figure clusters ===")
    for fig in figure_blocks[:20]:
        cluster = fig.metadata.get("figure_cluster") if fig.metadata else None
        if not cluster:
            continue
        anchors = cluster.get("anchor_block_anchors", [])
        absorbed = cluster.get("absorbed_label_anchors", [])
        caption = cluster.get("caption_block_anchor")
        page = fig.metadata.get("source_page_start") or "?"
        print(
            f"  page={page} anchor={fig.anchor} anchors_merged={len(anchors)} "
            f"labels_absorbed={len(absorbed)} caption={'yes' if caption else 'no'}"
        )
        if absorbed:
            print(f"    absorbed_anchors={absorbed[:6]}{'...' if len(absorbed) > 6 else ''}")
    if len(figure_blocks) > 20:
        print(f"  (+{len(figure_blocks) - 20} more)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
