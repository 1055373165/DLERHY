---
name: contextual-image-legends
kind: pdf-recovery
passes: [promote_contextual_image_legends]
default: enabled
---

# Legends next to images

Applies to: Books that place a short centred legend ("The architecture of ... 3") next to an image instead of a numbered caption.

## What it does
Turns centred 3-9 word lines of the form `This/These/The ... <number>` that sit right next to an image into captions linked to it.

## Switch it off when
- ordinary body sentences near images are rendered as captions;
- captions are duplicated in body text (export QA check R3).

## Side effects of switching off
Such legends are translated as body paragraphs rather than figure captions.

## How to switch it
`PUT /v1/documents/{document_id}/recovery-skills` with `{"skills": {"contextual-image-legends": false}}`, then `POST /v1/documents/{document_id}/structure-refresh`. The refresh reparses the PDF without the pass and forks changed sentences, carrying over translations of unchanged ones (see `docs/agent-upgrade/04-parse-revision-forking.md`).
