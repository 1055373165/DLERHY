---
name: text-only-figures
kind: pdf-recovery
passes: [recover_text_only_figures]
default: enabled
---

# Figures drawn as text

Applies to: Books whose diagrams are drawn with text boxes rather than images, so the parser only sees a caption `Figure N.M` and short labels above it.

## What it does
Pairs an unlinked figure caption with an unlinked image above it on the same page; otherwise gathers short text blocks above the caption into a `text_only_figure` FIGURE block so the labels are not translated as prose.

## Switch it off when
- short headings, list items or table rows directly above figure captions disappear from the translation;
- export QA reports hollow figures or missing headings next to captions.

## Side effects of switching off
Diagram labels are translated as short paragraphs in reading order.

## How to switch it
`PUT /v1/documents/{document_id}/recovery-skills` with `{"skills": {"text-only-figures": false}}`, then `POST /v1/documents/{document_id}/structure-refresh`. The refresh reparses the PDF without the pass and forks changed sentences, carrying over translations of unchanged ones (see `docs/agent-upgrade/04-parse-revision-forking.md`).
