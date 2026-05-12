"""Translate boxed sidebar callouts (bold title + body in a tinted box).

The PDF parser sometimes captures a sidebar callout as a SINGLE paragraph
block, joining its bold title with the body and occasionally dropping the
title's first word when it textually overlaps with the body (e.g. the
"Training LLMs is expensive" box: the parser stored
``"LLMs is expensive Training an LLM is not realistically possible …"``,
losing the leading "Training" of the title).

For these cases the chunk-level translator produced one Chinese paragraph
that smashes title and body together. This script:

  1. Reads a hand-curated list of ``(chapter_id, ordinal, title_en)``
     entries below.
  2. Looks up the block in the DB, computes the body source text by
     stripping the in-DB title fragment, and asks DeepSeek to translate
     the title and body separately (concise translation, no preamble).
  3. Caches the result in ``.test-tmp/_sidebar_callout_cache.json``
     keyed by block UUID. The exporter reads this cache and renders a
     styled ``<aside class="sidebar-callout">`` with a bold title and
     a flowing body, replacing the legacy single-paragraph render.

Keep the curated list short and explicit. Add more entries as new
callout shapes are discovered during review.
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


CACHE_PATH = Path(".test-tmp/_sidebar_callout_cache.json")


# Hand-curated. Each entry: chapter_id, ordinal, full title (English),
# and an optional title fragment to strip from the DB source_text when
# computing the body. ``title_fragment_in_db`` is the substring the
# parser actually stored (which may be shorter than ``title_en`` when a
# word was dropped during text extraction).
CALLOUTS = [
    {
        "chapter_id": "b13f7481-d2af-5629-bb8f-52d9c2b9abc9",  # ch1
        "ordinal": 32,
        "title_en": "Training LLMs is expensive",
        "title_fragment_in_db": "LLMs is expensive",
    },
]


def _translate_one(text: str, *, settings, kind: str) -> str:
    api_key = settings.translation_openai_api_key
    if not api_key:
        raise SystemExit("translation_openai_api_key not set in env / config")
    base_url = settings.translation_openai_base_url.rstrip("/")
    if "responses" in base_url:
        chat_url = base_url.rsplit("/", 1)[0] + "/chat/completions"
    elif base_url.endswith(("/v1", "/v1/")):
        chat_url = base_url.rstrip("/") + "/chat/completions"
    else:
        chat_url = base_url + "/chat/completions"
    if kind == "title":
        system = (
            "You translate a short English sidebar-callout TITLE to "
            "Simplified Chinese. STRICT RULES:\n"
            "1. Output ONLY the Chinese translation — no preamble, no "
            "quotation marks, no period.\n"
            "2. Keep it tight (≤ 20 Chinese characters when source ≤ 6 words).\n"
            "3. Keep tech terms in English: LLM, GPT, RLHF, RAG, ChatGPT, "
            "OpenAI, Transformer, BERT, T5."
        )
    else:
        system = (
            "You translate an English paragraph (a sidebar-callout body) "
            "to Simplified Chinese. STRICT RULES:\n"
            "1. Output ONLY the Chinese translation — no preamble.\n"
            "2. Preserve paragraph structure and punctuation.\n"
            "3. Keep tech terms in English: LLM, GPT, RLHF, RAG, ChatGPT, "
            "OpenAI, Transformer, BERT, T5."
        )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
        resp = client.post(chat_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _load_cache() -> dict:
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
    calls = 0

    with session_scope(factory) as session:
        for entry in CALLOUTS:
            blk = session.execute(
                select(Block)
                .where(Block.chapter_id == entry["chapter_id"])
                .where(Block.ordinal == entry["ordinal"])
            ).scalars().first()
            if blk is None:
                print(f"[warn] block not found: chapter={entry['chapter_id']} ord={entry['ordinal']}")
                continue
            src = (blk.source_text or "").strip()
            fragment = entry["title_fragment_in_db"]
            if fragment and src.startswith(fragment):
                body_en = src[len(fragment):].lstrip()
            else:
                body_en = src
            # Drop PDF column-wrap newlines from body before sending.
            body_en = re.sub(r"(?<!\n)\n(?!\n)", " ", body_en)
            body_en = re.sub(r" {2,}", " ", body_en).strip()
            title_en = entry["title_en"]

            block_key = str(blk.id)
            existing = cache.get(block_key, {})
            need_title = existing.get("title_en") != title_en or not existing.get("title_zh")
            need_body = existing.get("body_en") != body_en or not existing.get("body_zh")
            if not (need_title or need_body):
                print(f"[skip] ch ord={entry['ordinal']}: cache hit")
                continue
            print(f"[run] ch ord={entry['ordinal']}: translating title + body")
            title_zh = (
                _translate_one(title_en, settings=settings, kind="title")
                if need_title else existing.get("title_zh", "")
            )
            body_zh = (
                _translate_one(body_en, settings=settings, kind="body")
                if need_body else existing.get("body_zh", "")
            )
            if need_title:
                calls += 1
            if need_body:
                calls += 1
            cache[block_key] = {
                "chapter_id": entry["chapter_id"],
                "ordinal": entry["ordinal"],
                "title_en": title_en,
                "title_zh": title_zh,
                "body_en": body_en,
                "body_zh": body_zh,
            }
            _save_cache(cache)

    print(f"[done] cache entries: {len(cache)}; api calls: {calls}")
    print(f"[done] cache file: {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
