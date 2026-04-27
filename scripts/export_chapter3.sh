#!/bin/bash
# Export Chapter 3 of the LLM book to a Chinese-only HTML document.
# Use this after running scripts/translate_chapter3.py against the
# matching ordinal range. Defaults assume the latest re-ingest (the
# one that absorbed fake headings and synthesized text-only figures).
set -euo pipefail
cd "$(dirname "$0")/.."

DOCUMENT_ID="${DOCUMENT_ID:-d71027f0-6537-58d1-8e47-42ef2834fca4}"
CHAPTER_ID="${CHAPTER_ID:-de30483c-ec5f-5d3d-a728-69de943db663}"
ORDINAL_LO="${ORDINAL_LO:-198}"
ORDINAL_HI="${ORDINAL_HI:-377}"
OUTPUT_PATH="${OUTPUT_PATH:-.test-tmp/ch3-export-v2/chapter3-zh.html}"
CHAPTER_LABEL="${CHAPTER_LABEL:-第 3 章}"
CHAPTER_TITLE="${CHAPTER_TITLE:-Transformer 架构: 输入如何转换为输出}"
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
