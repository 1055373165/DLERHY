"""DB-free diagnostic for the Figure 3.2 → 3.3 caption mismatch bug.

Re-parses the source PDF in-memory (no DB writes) and prints, for the
chapter 3 page range, every IMAGE/FIGURE block alongside the CAPTION
block that ``_link_artifact_captions`` chose to link to it. We can then
visually inspect whether the picked caption's text actually starts with
the same "Figure N.M" the user expects.

Output columns:
  page  block_type  anchor                   linked_caption_anchor       caption_text_preview
  text  caption                                                          text starting with "Figure 3.x" if any
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
PAGE_LO = int(os.environ.get("PAGE_LO", "0"))
PAGE_HI = int(os.environ.get("PAGE_HI", "0"))
ENABLE_CLUSTERING = os.environ.get("ENABLE_CLUSTERING", "1") == "1"


def main() -> int:
    if not PDF_PATH.is_file():
        print(f"PDF not found at {PDF_PATH}", file=sys.stderr)
        return 1

    extractor = PyMuPDFTextExtractor()
    profiler = PdfFileProfiler(extractor)
    profile = profiler.profile(PDF_PATH)
    extraction = extractor.extract(PDF_PATH)

    cluster_cfg = FigureClusterConfig() if ENABLE_CLUSTERING else FigureClusterConfig(enabled=False)
    service = PdfStructureRecoveryService(figure_cluster_config=cluster_cfg)
    parsed = service.recover(PDF_PATH, extraction, profile)

    print(f"[diag] PDF: {PDF_PATH}")
    print(f"[diag] page_count={profile.page_count}  clustering={'ON' if ENABLE_CLUSTERING else 'OFF'}")
    if PAGE_LO or PAGE_HI:
        print(f"[diag] focus pages {PAGE_LO}-{PAGE_HI or 'end'}")
    print()

    print("[diag] chapters list:")
    for idx, ch in enumerate(parsed.chapters):
        print(f"   {idx:>3} title={ch.title!r}  blocks={len(ch.blocks)}")
    print()

    target_idx = int(os.environ.get("CHAPTER_INDEX", "-1"))
    chapter3 = None
    if target_idx >= 0 and target_idx < len(parsed.chapters):
        chapter3 = parsed.chapters[target_idx]
    else:
        for ch in parsed.chapters:
            t = (ch.title or "").lower()
            if "tokens and embeddings" in t or "representing language" in t:
                chapter3 = ch
                break
    if chapter3 is None and len(parsed.chapters) >= 3:
        chapter3 = parsed.chapters[2]
    if chapter3 is None:
        print("could not locate chapter 3", file=sys.stderr)
        return 1
    print(f"[diag] selected chapter title={chapter3.title!r}  blocks={len(chapter3.blocks)}")
    print()

    blocks_by_anchor: dict[str, object] = {b.anchor: b for b in chapter3.blocks}

    artifact_types = {BlockType.IMAGE.value, BlockType.FIGURE.value, BlockType.TABLE.value, BlockType.EQUATION.value}
    caption_type = BlockType.CAPTION.value

    print(f"{'page':>5} {'ord':>5} {'type':<10} {'anchor':<22} {'linked_caption':<22}  caption_text")
    print("-" * 140)
    for blk in chapter3.blocks:
        meta = blk.metadata or {}
        page = meta.get("source_page_start") or meta.get("page_start") or 0
        if PAGE_LO and PAGE_HI and not (PAGE_LO <= page <= PAGE_HI):
            continue
        if blk.block_type not in artifact_types and blk.block_type != caption_type:
            continue

        linked_anchor = ""
        caption_preview = ""
        if blk.block_type in artifact_types:
            linked_anchor = (meta.get("linked_caption_source_anchor") or "").rsplit("|", 1)[-1] or "-"
            ltext = meta.get("linked_caption_text") or meta.get("image_alt") or ""
            caption_preview = (ltext or "").strip().replace("\n", " ")[:90]
        else:
            caption_preview = (blk.text or "").strip().replace("\n", " ")[:90]
            linked_anchor = (meta.get("caption_for_source_anchor") or "").rsplit("|", 1)[-1] or "-"

        print(
            f"{page:>5} {getattr(blk, 'ordinal', 0):>5} {blk.block_type:<10} "
            f"{(blk.anchor or '-')[-22:]:<22} {(linked_anchor or '-')[-22:]:<22}  {caption_preview!r}"
        )

    print()
    print("=== bbox detail (focus pages) ===")
    for blk in chapter3.blocks:
        meta = blk.metadata or {}
        page = meta.get("source_page_start") or 0
        if PAGE_LO and PAGE_HI and not (PAGE_LO <= page <= PAGE_HI):
            continue
        if blk.block_type not in artifact_types and blk.block_type != caption_type:
            continue
        bbox_regions = meta.get("source_bbox_json", {}).get("regions", []) if isinstance(meta.get("source_bbox_json"), dict) else []
        bbox = bbox_regions[0].get("bbox") if bbox_regions else None
        anchor_short = (blk.anchor or "")[-22:]
        text_short = ((blk.text or "").strip().replace("\n", " ")[:50] or "(no text)")
        if bbox:
            x0, y0, x1, y1 = bbox
            print(f"  page={page} {blk.block_type:<8} {anchor_short:<22} bbox=[{x0:6.1f},{y0:6.1f},{x1:6.1f},{y1:6.1f}] h={y1-y0:5.1f}  {text_short!r}")
        else:
            print(f"  page={page} {blk.block_type:<8} {anchor_short:<22} bbox=NONE  {text_short!r}")

    print()
    print("=== live algorithm trace for p55-img379 (re-running its candidate loop) ===")
    # Use the recovery internals directly so we see exactly what `_artifact_caption_target` decides.
    recovered_blocks = service._recover_blocks(  # noqa: SLF001
        sorted(extraction.pages, key=lambda p: p.page_number),
        service._find_repeated_edge_text(sorted(extraction.pages, key=lambda p: p.page_number)),  # noqa: SLF001
        service._page_contexts(sorted(extraction.pages, key=lambda p: p.page_number)),  # noqa: SLF001
        extraction.outline_entries,
        profile,
    )
    target_idx = next((i for i, b in enumerate(recovered_blocks) if b.anchor == "p55-img379"), None)
    if target_idx is not None:
        artifact_block = recovered_blocks[target_idx]
        artifact_bbox = service._page_bbox(artifact_block, artifact_block.page_start)  # noqa: SLF001
        print(f"  artifact role={artifact_block.role!r} bbox={artifact_bbox}")
        for ci, cand in enumerate(recovered_blocks):
            if cand.role != "caption":
                continue
            if cand.page_start not in {55, 56}:
                continue
            same_page = cand.page_start == artifact_block.page_start and cand.page_end == artifact_block.page_end
            next_page = cand.page_start == artifact_block.page_end + 1 and cand.page_start == cand.page_end
            cbbox = service._page_bbox(cand, cand.page_start)  # noqa: SLF001
            if cbbox is None:
                print(f"  cand {cand.anchor}  bbox=None — REJECTED (page_bbox)")
                continue
            artifact_role = service._normalized_artifact_caption_role(artifact_block)  # noqa: SLF001
            role_ok = service._caption_candidate_matches_artifact_role(cand, artifact_role)  # noqa: SLF001
            if not role_ok:
                print(f"  cand {cand.anchor}  REJECTED on role match")
                continue
            overlap = service._horizontal_overlap_ratio(artifact_bbox, cbbox)  # noqa: SLF001
            below_gap = cbbox[1] - artifact_bbox[3]
            above_gap = artifact_bbox[1] - cbbox[3]
            below_lower = -48.0 if artifact_role == "image" else -12.0
            in_below = same_page and below_lower <= below_gap <= 120.0
            in_above = same_page and -12.0 <= above_gap <= 80.0
            in_next = next_page and cbbox[1] <= 220.0 and (artifact_bbox[3] >= artifact_bbox[1] + 120.0)
            print(
                f"  cand p={cand.page_start} {cand.anchor!r:<28} same={same_page} next={next_page} "
                f"role_match={role_ok} overlap={overlap:.2f} below_gap={below_gap:+6.1f} "
                f"in_below={in_below} in_above={in_above} in_next={in_next}"
            )

    print()
    print("=== candidate-by-candidate trace for p55-img379 ===")
    target_image = next((b for b in chapter3.blocks if b.anchor == "p55-img379"), None)
    if target_image is not None:
        from book_agent.domain.structure.pdf import _caption_matches_artifact_role  # noqa: E402

        img_meta = target_image.metadata or {}
        img_bbox = img_meta.get("source_bbox_json", {}).get("regions", [{}])[0].get("bbox")
        print(f"  image bbox: {img_bbox}  role from metadata: {img_meta.get('role')}")
        for blk in chapter3.blocks:
            if blk.block_type != caption_type:
                continue
            cmeta = blk.metadata or {}
            page = cmeta.get("source_page_start") or 0
            if page not in {55, 56}:
                continue
            cbbox = cmeta.get("source_bbox_json", {}).get("regions", [{}])[0].get("bbox")
            if not cbbox or not img_bbox:
                continue
            below_gap = cbbox[1] - img_bbox[3]
            above_gap = img_bbox[1] - cbbox[3]
            overlap = min(img_bbox[2], cbbox[2]) - max(img_bbox[0], cbbox[0])
            iw = max(img_bbox[2] - img_bbox[0], 1.0)
            cw = max(cbbox[2] - cbbox[0], 1.0)
            overlap_ratio = max(overlap, 0.0) / min(iw, cw)
            role_match = _caption_matches_artifact_role(blk.text, "image")
            print(
                f"  candidate p={page} {blk.anchor[-22:]} text={blk.text[:40]!r:<42} "
                f"role={cmeta.get('role')!r} role_match={role_match} "
                f"below_gap={below_gap:+6.1f} above_gap={above_gap:+6.1f} overlap_r={overlap_ratio:.3f}"
            )

    print()
    print("=== summary: figure→caption pairings ===")
    for blk in chapter3.blocks:
        if blk.block_type not in artifact_types:
            continue
        meta = blk.metadata or {}
        page = meta.get("source_page_start") or 0
        if PAGE_LO and PAGE_HI and not (PAGE_LO <= page <= PAGE_HI):
            continue
        ltext = (meta.get("linked_caption_text") or meta.get("image_alt") or "").strip().replace("\n", " ")
        if not ltext:
            print(f"  page={page} {blk.anchor}  -> NO CAPTION LINKED")
            continue
        prefix = ltext[:60]
        print(f"  page={page} {blk.anchor}  -> {prefix!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
