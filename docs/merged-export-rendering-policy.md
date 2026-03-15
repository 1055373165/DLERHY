# Merged Export Rendering Policy

## Goal

Define how full-book merged export should render content that is:

- translated into Chinese
- preserved in the original English or source form
- partially translated with protected inline spans
- emitted as a diagnostic or fallback artifact when structure fidelity is not strong enough

This policy exists to prevent a common failure mode:

- treating every empty Chinese cell as a missing translation
- or, in the opposite direction, force-translating executable / structural / symbolic content that should remain unchanged

For our real book workflow, merged export should optimize for **reading quality + copy/paste safety + structural fidelity**, not just “fill every row with Chinese text”.

## Core Principle

Merged export must distinguish between:

1. `translated reading content`
2. `protected source artifact`
3. `hybrid content with inline protected spans`
4. `unreliable structure that must be preserved rather than over-translated`

Code blocks are only one instance of category 2.

## Current Signals Already Available

The current pipeline already exposes useful control signals:

- `block_type`
  - `heading`
  - `paragraph`
  - `quote`
  - `footnote`
  - `caption`
  - `code`
  - `table`
  - `list_item`
- `protected_policy`
  - `translate`
  - `protect`
  - `mixed`
- `sentence.translatable`
- `sentence.nontranslatable_reason`
- `sentence_status`
- `target segment type`
  - `sentence`
  - `merged_sentence`
  - `heading`
  - `footnote`
  - `caption`
  - `protected`

Merged export should consume these signals instead of re-guessing everything from final text.

## Content Classes

### 1. Fully Translatable Reading Content

Typical examples:

- normal paragraphs
- headings
- quotes
- list items
- footnotes
- captions

Merged export behavior:

- Chinese is the primary reading text
- English source may be optional, collapsible, or rendered in a secondary lane

Default render mode:

- `zh_primary_with_optional_source`

### 2. Protected Executable / Structural Artifacts

These should usually remain in the original source form.

Typical examples:

- code blocks
- shell commands
- CLI examples
- JSON / YAML / XML / TOML / SQL snippets
- stack traces / logs / console output
- API signatures
- class / function definitions
- file paths
- URLs
- env vars
- flags
- package names
- model ids
- literal placeholders such as `${VAR}`, `{user_id}`, `%s`
- literal markup tokens discussed in prose, such as `<think>`

Why they should usually stay unmodified:

- copy/paste safety matters
- a translated artifact may become invalid or misleading
- many of these are not “reading prose”; they are executable or referential objects

Merged export behavior:

- render the original block once, full width
- do not create an empty “Chinese translation body” lane beside it
- optionally add a small Chinese label such as:
  - `原文保留`
  - `代码保持原样`
  - `命令保持原样`
- if the block has a caption/title, translate the caption but preserve the artifact body

Default render mode:

- `source_artifact_full_width`

### 3. Hybrid Reading Content With Inline Protected Spans

These are sentences that should be translated, but some spans inside them should remain original.

Typical examples:

- prose that mentions API names
- prose that contains file paths
- prose that embeds function names
- prose that discusses XML/HTML tags
- prose that includes inline code
- prose that references env vars or flags

Merged export behavior:

- translate the sentence into Chinese
- preserve protected spans inline using monospace / code styling
- do not split the whole sentence into “all source” just because it contains one protected token

Default render mode:

- `zh_primary_with_inline_protected_spans`

### 4. Preserved Structured Artifacts With Translated Wrapper

These are objects where the surrounding label can be translated, but the internal structure should be preserved unless we have a structure-safe translator.

Typical examples:

- tables
- equations
- ASCII diagrams
- Mermaid-like pseudo-diagrams
- schema listings

Merged export behavior:

- translate:
  - table title
  - figure caption
  - equation label
  - surrounding explanatory prose
- preserve:
  - raw table grid
  - equation body
  - diagram body
- if table structure is weak or lossy, prefer preserved original over fake rewritten table

Default render mode:

- `translated_wrapper_with_preserved_artifact`

### 5. Image / Figure Anchors And Unparsed Visual Content

Typical examples:

- images
- screenshots
- diagrams embedded as images
- scanned figure text not yet OCR’d

Merged export behavior:

- preserve image position and anchor if available
- translate caption if available
- if image text is not parsed, do not hallucinate translation
- optionally render a note:
  - `图像内容未翻译`
  - `图中文字未解析`

Default render mode:

- `image_anchor_with_translated_caption`

### 6. Reference / Bibliographic Material

Typical examples:

- bibliography entries
- ISBN / DOI / edition metadata
- URLs in notes
- repo links
- exact paper / book titles

Merged export behavior:

- preserve exact reference fields and identifiers
- translate surrounding explanatory labels if needed
- avoid “naturalizing” exact citation metadata into non-standard Chinese

Default render mode:

- `reference_preserve_with_translated_label`

## Important Non-Translation Scenarios Beyond Code

Before implementing merged export, we should explicitly treat all of the following as first-class “not necessarily translated” cases:

1. code blocks
2. terminal / console output
3. shell commands
4. config snippets
5. structured data snippets
6. stack traces / logs
7. formulas and equation bodies
8. tables when structure fidelity is uncertain
9. inline code / identifiers inside prose
10. file paths / URLs / flags / env vars
11. literal markup tokens such as `<think>`
12. placeholders / template variables
13. image-only or visually embedded content
14. exact citations and reference metadata
15. prompt templates that are meant to be copied and executed as-is

That last item matters for AI books in particular:

- many prompt blocks are operational artifacts, not just prose examples
- blindly translating them may reduce executability
- the safer default is:
  - preserve the prompt block body
  - translate the explanation around it
  - optionally support an explicit future mode for “translated prompt companion”

## Merged Export Render Modes

Merged export should have an explicit render contract per block:

- `zh_primary_with_optional_source`
- `zh_primary_with_inline_protected_spans`
- `source_artifact_full_width`
- `translated_wrapper_with_preserved_artifact`
- `image_anchor_with_translated_caption`
- `reference_preserve_with_translated_label`
- `fallback_source_only_with_notice`

This is better than implicitly inferring behavior from whether a Chinese target exists.

## Mapping Rules

### Block-level default mapping

- `heading` -> `zh_primary_with_optional_source`
- `paragraph` -> depends on `protected_policy`
  - `translate` -> `zh_primary_with_optional_source`
  - `mixed` -> `zh_primary_with_inline_protected_spans`
  - `protect` -> `source_artifact_full_width`
- `quote` -> `zh_primary_with_optional_source`
- `footnote` -> `zh_primary_with_optional_source`
- `caption` -> usually `zh_primary_with_optional_source`
- `code` -> `source_artifact_full_width`
- `table` -> `translated_wrapper_with_preserved_artifact`
- `list_item` -> `zh_primary_with_optional_source`

### Sentence-level override

If a sentence is:

- `translatable=false`
- or `sentence_status=protected`
- or `nontranslatable_reason` indicates protected artifact

then merged export must not treat missing Chinese text as an omission.

### Export fallback

If structure confidence is weak, merged export should prefer:

- preserving source block
- plus a small Chinese notice

instead of:

- emitting an empty translation lane
- or inventing a loosely structured Chinese replacement

## What Should Change Relative To Current Bilingual HTML

Current chapter-level bilingual HTML is QA-oriented:

- it shows source and target in parallel
- for protected blocks, the target side may be empty

That is acceptable for audit/review, but not ideal for a full merged reading export.

For merged export:

- protected artifacts should not appear as “blank translation rows”
- they should be rendered as intentional preserved blocks
- the reader should be able to visually distinguish:
  - translated prose
  - preserved executable artifact
  - preserved structural artifact
  - missing or failed translation

This distinction is critical. Otherwise the merged export becomes visually misleading.

## Recommended Full-Book Rendering Strategy

For a production-grade merged export, the default reading layout should be:

1. Chinese primary flow for normal reading blocks
2. source artifact cards for protected blocks
3. translated captions / titles above preserved artifacts
4. inline monospace spans for protected tokens inside prose
5. optional collapsible source text for prose blocks

In other words:

- prose becomes “readable Chinese”
- executable artifacts remain “copyable source”
- structural artifacts remain “faithful structure”

## Recommended Metadata For Implementation

Before implementing merged export, add or derive a render-layer object per block:

- `block_id`
- `chapter_id`
- `block_type`
- `protected_policy`
- `render_mode`
- `primary_language`
- `show_source_inline`
- `show_preserved_notice`
- `artifact_kind`
  - `code`
  - `command`
  - `config`
  - `table`
  - `equation`
  - `reference`
  - `image`
  - `literal_token`
- `caption_source`
- `caption_target`
- `body_source`
- `body_target`
- `has_translation_gap`
- `is_expected_source_only`

The key field is:

- `is_expected_source_only`

because it cleanly separates:

- “correctly preserved original artifact”

from:

- “translation missing unexpectedly”

## Export Gate Implications

Merged export must not fail just because a protected block has no Chinese body.

The export gate should treat these differently:

- `protected block with no Chinese body` -> valid
- `translatable prose block with missing Chinese body` -> invalid

Without this distinction, merged export will over-report omissions.

## P0 Recommendation

For the first merged-export implementation:

1. treat `code` as `source_artifact_full_width`
2. treat `table` as `translated_wrapper_with_preserved_artifact`
3. preserve inline literals inside prose
4. preserve prompt / config / command style blocks if they are mapped to protected content
5. render a small Chinese notice for source-only protected artifacts
6. never display protected artifacts as an empty translation lane

## Future Extension

Later we can add optional advanced modes:

- translated commentary for code blocks
- translated prompt companion blocks
- bilingual table header reconstruction
- formula-side Chinese explanation lane
- image OCR + caption enrichment

But these should be opt-in upgrades, not the default rule.

## Bottom Line

Before full-book merged export, we should freeze one principle:

- **not every block needs a Chinese body**

What every block needs is:

- a correct semantic rendering mode

That is the difference between:

- “translation pipeline output”

and:

- “readable, trustworthy, publication-ready merged export”.
