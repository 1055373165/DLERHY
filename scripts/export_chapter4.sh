#!/bin/bash
# Export Chapter 4 ("How LLMs learn") of the LLM book to a Chinese-only
# HTML document. Use after running scripts/translate_chapter3.py with
# --ordinal-lo 326 --ordinal-hi 466 (the chapter 4 packet range).
set -euo pipefail
cd "$(dirname "$0")/.."

DOCUMENT_ID="${DOCUMENT_ID:-d71027f0-6537-58d1-8e47-42ef2834fca4}"
CHAPTER_ID="${CHAPTER_ID:-de30483c-ec5f-5d3d-a728-69de943db663}"
ORDINAL_LO="${ORDINAL_LO:-326}"
ORDINAL_HI="${ORDINAL_HI:-466}"
OUTPUT_PATH="${OUTPUT_PATH:-.test-tmp/ch4-export/chapter4-zh.html}"
CHAPTER_LABEL="${CHAPTER_LABEL:-第 4 章}"
CHAPTER_TITLE="${CHAPTER_TITLE:-LLM 如何学习}"
SOURCE_LABEL="${SOURCE_LABEL:-How Large Language Models Work}"

mkdir -p "$(dirname "$OUTPUT_PATH")"

DOCUMENT_ID="$DOCUMENT_ID" \
CHAPTER_ID="$CHAPTER_ID" \
ORDINAL_LO="$ORDINAL_LO" \
ORDINAL_HI="$ORDINAL_HI" \
OUTPUT_PATH="$OUTPUT_PATH" \
CHAPTER_LABEL="$CHAPTER_LABEL" \
CHAPTER_TITLE="$CHAPTER_TITLE" \
SOURCE_LABEL="$SOURCE_LABEL" \
.venv/bin/python scripts/export_chapter_zh_html.py
