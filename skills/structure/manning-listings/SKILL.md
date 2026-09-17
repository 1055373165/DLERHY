---
name: manning-listings
kind: pdf-recovery
passes: [lock_listing_scope]
default: enabled
---

# Manning-style code listings

Applies to: Books whose code examples are introduced by `Listing N.M` captions (Manning and publishers that copy the convention).

## What it does
Starting at a block matching `^Listing \d+\.\d+`, the next 16 blocks are treated as part of the listing: trailing code comments that the layout pass split off are merged back, numbered listing annotations are marked `pdf_listing_annotation_suppressed` and made non-translatable, and paragraphs that start with a programming keyword become code.

## Switch it off when
- the book has no `Listing N.M` captions but uses the word "Listing" in prose or tables of contents;
- prose after a listing caption is being turned into code or silently left untranslated;
- review shows many `STRUCTURE_POLLUTION` / omission issues around listing captions.

## Side effects of switching off
Listing annotations are translated as ordinary paragraphs; code comments that were split from their code block stay separate blocks.

## How to switch it
`PUT /v1/documents/{document_id}/recovery-skills` with `{"skills": {"manning-listings": false}}`, then `POST /v1/documents/{document_id}/structure-refresh`. The refresh reparses the PDF without the pass and forks changed sentences, carrying over translations of unchanged ones (see `docs/agent-upgrade/04-parse-revision-forking.md`).
