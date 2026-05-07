"""Backfill OCR + translation for figure images that look like callout
boxes (highlighted "NOTE-style" boxes the parser captured as PNGs).

Why: the PDF parser snapshots highlighted callouts as PNGs and emits an
orphan paragraph that contains only the body text WITH the title and the
first 1-3 words of the body lost (parser confusion with the highlighted
background). OCR on the PNG recovers the full title + body; we then
translate via the configured provider so the renderer can emit a proper
bilingual callout instead of embedding a redundant image.

Run with:
    uv run --with ocrmac python scripts/backfill_callout_ocr.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import create_engine, text  # noqa: E402

DB_URL = "postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"
DOC_ID = "d71027f0-6537-58d1-8e47-42ef2834fca4"
ARTIFACTS_ROOT = ROOT / "artifacts"

# A figure-image block qualifies as a "callout candidate" only if its OCR
# text is substantial and structurally callout-like:
#   * length > 200 chars (short labels are diagrams, not prose callouts)
#   * at least one full sentence (terminator present)
#   * tokens/words ratio of capitalised tokens < 0.6 (rules out diagram
#     label dumps which are mostly proper nouns)
def is_callout_text(text_value: str) -> bool:
    if not text_value or len(text_value) < 200:
        return False
    if not re.search(r"[.!?]", text_value):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z']+", text_value)
    if len(words) < 30:
        return False
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised / len(words) < 0.6


# Page running headers and isolated page-numbers slip into OCR; strip them.
_PAGE_HEADER_LINE = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+){0,3}\s+.+?\s+\d+"          # "1.2 Title 5"
    r"|\d+\s+(?:CHAPTER|Chapter)\s+\d+.*"     # "5 CHAPTER 1 Title"
    r"|\d{1,3}"                               # bare page number
    r")\s*$"
)
# Macos Vision often splits visual line into two — title-only + page-only.
# Match a section-style header WITHOUT trailing page number when it
# occurs at the *top* of the OCR output.
_SECTION_HEADER_ONLY = re.compile(
    r"^\s*\d+(?:\.\d+){1,3}\s+[A-Z].+$"
)


def clean_ocr_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for idx, ln in enumerate(lines):
        ln = ln.strip()
        if not ln:
            continue
        if _PAGE_HEADER_LINE.match(ln):
            continue
        # First few OCR lines often contain the page running header in
        # two pieces (title alone, page alone). Drop section-style header
        # lines that appear in the top 3 lines OR in the bottom 3 lines.
        if (idx < 3 or idx >= len(lines) - 3) and _SECTION_HEADER_ONLY.match(ln):
            continue
        cleaned.append(ln)
    return cleaned


def split_callout_title_body(lines: list[str]) -> tuple[str, str]:
    """The first short line (≤ 5 words, no terminating period) is the
    callout title; everything after joins with single spaces as body.
    Returns ("", joined) if no clear title."""
    if not lines:
        return "", ""
    first = lines[0]
    words = first.split()
    if len(words) <= 5 and not first.endswith((".", "!", "?")):
        return first.strip(), " ".join(lines[1:]).strip()
    return "", " ".join(lines).strip()


def ocr_image(path: Path) -> str:
    from ocrmac import ocrmac  # macOS-only; pure-pip install
    raw = ocrmac.OCR(str(path)).recognize()
    lines = [text for text, _conf, _bbox in raw if text and text.strip()]
    return "\n".join(clean_ocr_lines(lines))


def deepseek_translate(en_text: str, *, api_key: str, base_url: str, model: str) -> str:
    """One-shot translation via the configured provider. Plain JSON HTTP
    request — no streaming, no retries — sufficient for the small number
    of callouts in a typical document.
    """
    import urllib.request

    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional EN→ZH technical translator. "
                    "Translate the user's text into faithful, fluent simplified Chinese. "
                    "Output ONLY the Chinese translation — no preamble, no commentary, no quotes."
                ),
            },
            {"role": "user", "content": en_text},
        ],
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"].strip()


def main() -> int:
    force_reprocess = "--force" in sys.argv
    env_path = ROOT / ".env"
    env = {}
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    api_key = env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = env.get("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = env.get("BOOK_AGENT_TRANSLATION_MODEL") or "deepseek-v4-flash"

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT di.id::text, di.block_id::text, di.storage_path,
                       di.image_type, di.metadata_json, di.ocr_text
                FROM document_images di
                WHERE di.document_id = :did
                ORDER BY di.created_at
                """
            ),
            {"did": DOC_ID},
        ).fetchall()

        scanned = 0
        ocred = 0
        callouts = 0
        translated = 0
        for img_id, block_id, storage_path, img_type, meta, existing_ocr in rows:
            full = ARTIFACTS_ROOT / storage_path
            if not full.exists():
                continue
            scanned += 1

            # In --force mode we re-OCR (cheap; macOS Vision is local).
            ocr_text = None if force_reprocess else existing_ocr
            if not ocr_text:
                try:
                    ocr_text = ocr_image(full)
                except Exception as exc:
                    print(f"  ! ocr fail {full.name}: {exc}")
                    continue
                ocred += 1

            if not is_callout_text(ocr_text):
                # Persist the OCR even for non-callouts; useful debug data.
                conn.execute(
                    text(
                        "UPDATE document_images SET ocr_text=:t WHERE id=:i"
                    ),
                    {"t": ocr_text, "i": img_id},
                )
                continue
            callouts += 1

            lines = [ln for ln in ocr_text.splitlines() if ln.strip()]
            title, body = split_callout_title_body(lines)
            display_text = (title + ". " + body) if title else body

            meta = meta or {}
            existing_zh = meta.get("callout_translation_zh") if isinstance(meta, dict) else None
            existing_title_zh = meta.get("callout_title_zh") if isinstance(meta, dict) else None
            existing_title_en = meta.get("callout_title_en") if isinstance(meta, dict) else None
            existing_body_en = meta.get("callout_body_en") if isinstance(meta, dict) else None
            # Re-translate when force_reprocess OR when no existing
            # translation OR when the EN title/body changed (e.g. cleaner
            # ran new heuristics on stored OCR).
            content_changed = (
                title != (existing_title_en or "") or body != (existing_body_en or "")
            )
            need_translate = force_reprocess or content_changed or not existing_zh

            zh_body = existing_zh
            zh_title = existing_title_zh
            if need_translate:
                try:
                    if title:
                        zh_title = deepseek_translate(
                            title, api_key=api_key, base_url=base_url, model=model
                        )
                        time.sleep(0.5)
                    zh_body = deepseek_translate(
                        body, api_key=api_key, base_url=base_url, model=model
                    )
                    translated += 1
                except Exception as exc:
                    print(f"  ! translate fail block={block_id[:8]}: {exc}")
                    continue

            new_meta = dict(meta) if isinstance(meta, dict) else {}
            new_meta["callout_kind"] = "highlighted_box"
            new_meta["callout_title_en"] = title
            new_meta["callout_body_en"] = body
            new_meta["callout_title_zh"] = zh_title or ""
            new_meta["callout_translation_zh"] = zh_body or ""

            conn.execute(
                text(
                    "UPDATE document_images SET ocr_text=:t, metadata_json=:m WHERE id=:i"
                ),
                {"t": ocr_text, "m": json.dumps(new_meta), "i": img_id},
            )
            print(
                f"  + callout ord-block={block_id[:8]} title={title!r}\n"
                f"      body[:80]={body[:80]!r}\n"
                f"      zh_title={zh_title!r}\n"
                f"      zh_body[:80]={(zh_body or '')[:80]!r}"
            )

    print(
        f"\nDONE: scanned={scanned} ocred={ocred} "
        f"callouts={callouts} translated={translated}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
