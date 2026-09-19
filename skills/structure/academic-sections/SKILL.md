---
name: academic-sections
kind: pdf-recovery
passes: [recover_academic_sections]
default: enabled
---

# Academic paper inline sections

Applies to: Papers in the academic recovery lane whose section titles run inline with the first paragraph (`3.2 Method We propose ...`).

## What it does
Splits such paragraphs at numbered inline section titles into a heading and the remaining body text, and renumbers the reading order.

## Switch it off when
- numbered sentences or list items (`2.5 times faster ...`) are cut into fake section headings;
- the document is not really a paper but was profiled into the academic lane.

## Side effects of switching off
Inline section titles stay inside their paragraph; the table of contents of exports loses those sections.

## How to switch it
`PUT /v1/documents/{document_id}/recovery-skills` with `{"skills": {"academic-sections": false}}`, then `POST /v1/documents/{document_id}/structure-refresh`. The refresh reparses the PDF without the pass and forks changed sentences, carrying over translations of unchanged ones (see `docs/agent-upgrade/04-parse-revision-forking.md`).
