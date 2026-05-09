"""Translate the 'This chapter covers' callout boxes per item.

The PDF parser collapses a chapter-cover bullet list into a single
paragraph block, joining 4-5 list items with spaces (and dropping the
bullet markers + the section heading). The translator then receives one
run-on sentence and naturally produces a one-paragraph summary, which
loses the visual structure the source had.

This script:
  1. Scans every chapter's first ≤5 blocks for the cover paragraph.
  2. Splits the joined source back into individual items using a regex
     of common item-starter words (How, Why, Understanding, ...).
  3. Translates each item independently via the configured DeepSeek-
     compatible provider, with a small JSON cache so re-runs are free.
  4. Writes ``.test-tmp/_chapter_cover_cache.json``.
The exporter reads this cache at render time and emits a proper
``<h3>本章涵盖</h3><ul>`` for the detected blocks.

Run-cost: ~6 calls total (one chapter, one block at most). Idempotent.
"""
# ruff: noqa: E402
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Drop proxy env so the DeepSeek call goes direct.
for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from book_agent.core.config import get_settings  # noqa: E402
from book_agent.domain.models import Block  # noqa: E402
from book_agent.infra.db.session import (  # noqa: E402
    build_engine,
    build_session_factory,
    session_scope,
)


CACHE_PATH = Path(".test-tmp/_chapter_cover_cache.json")


# Sentence-starter tokens that book TOC entries use. Any of these at a
# word boundary marks the start of a new bullet item.
_ITEM_STARTERS = (
    r"How|Why|What|When|Where|Which|Whether"
    r"|Understanding|Using|Helping|Making|Modifying|Tweaking|Constraining"
    r"|Enabling|Producing|Avoiding|Building|Creating|Designing|Engineering"
    r"|Improving|Detecting|Identifying|Reducing|Limiting|Optimizing"
    r"|Ways|Implications|Question|Reasons?|Methods?|Topics?|Issues?|Areas?"
    r"|The|A|An"
)
_ITEM_RE = re.compile(rf"(?<=\s)(?:{_ITEM_STARTERS})\b")
_TOC_PREFIX_RE = re.compile(r"^\s*This chapter covers\s+", re.IGNORECASE)


def split_chapter_cover_items(source_text: str) -> list[str]:
    """Split a joined chapter-cover paragraph into its bullet items.

    The parser glues column-wrapped lines together; we treat newlines
    and multiple spaces as item-internal whitespace, then split before
    any item-starter word.
    """
    text = (source_text or "").replace("\n", " ").strip()
    text = _TOC_PREFIX_RE.sub("", text)
    if not text:
        return []
    boundaries = [0]
    for m in _ITEM_RE.finditer(text):
        if m.start() > boundaries[-1]:
            boundaries.append(m.start())
    boundaries.append(len(text))
    items = []
    for a, b in zip(boundaries, boundaries[1:]):
        chunk = re.sub(r"\s+", " ", text[a:b]).strip()
        if chunk:
            items.append(chunk)
    return items


def is_chapter_cover_source(source_text: str) -> bool:
    s = (source_text or "").strip()
    if not s:
        return False
    if _TOC_PREFIX_RE.match(s):
        return True
    # ch1 case: "Transformers and large language models are How LLMs ..."
    items = split_chapter_cover_items(s)
    if len(items) < 4:
        return False
    # Real body paragraphs end with a sentence terminator; cover lists
    # don't (they're nominal phrases).
    return not s.rstrip().endswith((".", "。", "!", "?", "！", "？"))


def _translate_one(text: str, *, settings) -> str:
    """Direct call to the configured DeepSeek-compatible /chat/completions."""
    api_key = settings.translation_openai_api_key
    if not api_key:
        raise SystemExit("translation_openai_api_key not set in env / config")
    base_url = settings.translation_openai_base_url.rstrip("/")
    # Worker defaults to /responses; chat completions sit at /chat/completions
    # on the same host. Try chat/completions which DeepSeek supports natively.
    if "responses" in base_url:
        chat_url = base_url.rsplit("/", 1)[0] + "/chat/completions"
    elif base_url.endswith(("/v1", "/v1/")):
        chat_url = base_url.rstrip("/") + "/chat/completions"
    else:
        chat_url = base_url + "/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You translate one English book-TOC entry to Simplified "
                    "Chinese. STRICT RULES:\n"
                    "1. Output ONLY the Chinese translation — no preamble, no "
                    "   quotation marks, no numbering, no explanation.\n"
                    "2. Be concise and mirror the source phrasing — DO NOT "
                    "   elaborate. The Chinese should be ≤ 1.5× the source "
                    "   character count.\n"
                    "3. NEVER add content that isn't in the source. If source "
                    "   says 'How LLMs work in plain language', translate it "
                    "   as a NOUN PHRASE (≈ 'LLM 的通俗工作原理'), not as a "
                    "   sentence with definition.\n"
                    "4. Keep technical terms in English: LLM, GPT, RLHF, RAG, "
                    "   ChatGPT, Transformer, BERT, T5.\n"
                    "5. Keep proper nouns in English (OpenAI, Google, Meta, "
                    "   Anthropic, etc.)."
                ),
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.post(chat_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _load_cache() -> dict[str, dict]:
    if CACHE_PATH.is_file():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)
    cache = _load_cache()

    chapters = [
        ("ch1", "b13f7481-d2af-5629-bb8f-52d9c2b9abc9"),
        ("ch2", "732562f6-1d41-5dd6-9520-7fe7068fa760"),
        ("ch3", "e55d9240-670f-54a4-9448-f3e25ce69ee0"),
        ("ch4", "ef60bd3b-f1e6-5a40-9905-5cc783a93c49"),
        ("ch5", "3aba5820-ccb3-5614-b8ee-5b0b2ada3b01"),
        ("ch6", "d38b47bd-236e-5d51-b27d-d1e9fc1d91d3"),
        ("ch7", "cffe6908-541c-520a-898d-8ab596492401"),
        ("ch8", "f0b4c3ba-3bca-5771-b865-6c75171f9cc0"),
        ("ch9", "50593486-936f-5331-b6bb-d6097101e48d"),
    ]

    total_calls = 0
    for label, ch_id in chapters:
        with session_scope(factory) as session:
            blocks = session.execute(
                select(Block)
                .where(Block.chapter_id == ch_id)
                .where(Block.ordinal <= 5)
                .order_by(Block.ordinal.asc())
            ).scalars().all()
            for blk in blocks:
                if (blk.block_type or "").lower() != "paragraph":
                    continue
                src = (blk.source_text or "").strip()
                if not is_chapter_cover_source(src):
                    continue
                items = split_chapter_cover_items(src)
                if not items:
                    continue
                print(f"[{label}] cover-block ord={blk.ordinal}: {len(items)} items")
                entry = cache.setdefault(
                    str(blk.id),
                    {"chapter": label, "ordinal": blk.ordinal, "items": []},
                )
                # Refresh structure if items shape changed (e.g. heuristic update).
                entry["chapter"] = label
                entry["ordinal"] = blk.ordinal
                cached_items = entry.get("items") or []
                cached_by_src = {i["en"]: i for i in cached_items if isinstance(i, dict)}
                new_items = []
                for src_item in items:
                    if src_item in cached_by_src and cached_by_src[src_item].get("zh"):
                        new_items.append(cached_by_src[src_item])
                        continue
                    print(f"  → translating: {src_item[:80]}")
                    zh = _translate_one(src_item, settings=settings)
                    total_calls += 1
                    new_items.append({"en": src_item, "zh": zh})
                    # Persist incrementally so a kill mid-run isn't lost.
                    entry["items"] = new_items
                    _save_cache(cache)
                entry["items"] = new_items
                _save_cache(cache)
                break  # one cover-block per chapter

    print(f"\n[done] cache entries: {len(cache)}; api calls this run: {total_calls}")
    print(f"[done] cache file: {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
