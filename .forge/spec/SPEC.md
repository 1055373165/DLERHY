# High-Fidelity Document Translation Foundation Spec

## North Star

Book Agent should evolve from a strong translation production pipeline into a high-fidelity
document translation system.

The target is not merely “English in, Chinese out.” The target is:

- parser truth that survives export
- structure-aware translation that preserves meaning, hierarchy, and reading flow
- source-preserving EPUB output
- risk-aware PDF output that defaults to a readable Chinese edition instead of pretending to be a
  page-faithful facsimile before the system is ready
- a single-ledger Forge v2 execution model that can keep shipping this mainline without drifting

## Explicit Requirements

1. The new mainline is high-fidelity document translation foundation, not runtime self-heal
   closure.
2. The repo must preserve the current translation execution backbone:
   - `Document / Chapter / Block / Sentence / TranslationPacket / TranslationRun / Review / Export`
3. The system must introduce a Canonical Document IR layer that captures document structure truth
   beyond the current execution-oriented block model.
4. The first delivery slice must add parse revision persistence and canonical IR artifacts without
   breaking the current parse/bootstrap pipeline.
5. Canonical IR must be sidecar-first:
   - revision record in the database
   - canonical IR artifact on disk
   - projection hints back into execution objects
6. `Block` and `Sentence` provenance must be able to point back to parse revision and canonical
   node identity.
7. EPUB’s long-term primary export path must become source-preserving patch export rather than only
   rebuilt export.
8. PDF’s near-term product truth must remain “high-quality Chinese reading edition,” not
   misleading page-faithful facsimile.
9. Parser, translator, and exporter must converge on one shared source truth instead of each
   carrying their own private structure guesses.
10. Forge v2 remains the active owner of execution:
    - `change_request` intake
    - `mainline_required / mainline_adjacent / out_of_band` branch classification
    - continuation scan after verified slices
    - stop-legality proven from file truth

## Hidden Requirements

- Existing runtime self-heal work must remain reusable and not be reopened as accidental collateral
  damage.
- The first canonical IR slice must be small enough to land without a schema explosion.
- The canonical IR artifact must be inspectable and restartable from file truth.
- The first slice must improve future parser/export work immediately, not merely create abstract
  types no one consumes.
- The repo must remain resumable by Forge v2 after the mainline rewrite; a credible next change
  request must stay visible from `.forge/` truth.

## Constraints

- Stay in the single shared checkout on `main`.
- Do not revert unrelated user changes.
- Keep the current parse/bootstrap/translation/review/export path working while introducing the
  new layer.
- Prefer sidecar artifact persistence over a first-pass page/line/span schema explosion.
- Keep baseline verification cheap enough to run every resume session.

## Chosen Problem Framing

The user is not asking for “another parser improvement.”
The real request is to re-found the system around document structure truth so future translation
and export quality stop being capped by the current execution-only IR.

A naive framing would keep tuning prompts or adding parser heuristics directly into `Block`.
That would underperform because exporter-grade provenance, DOM/node identity, page/zone structure,
and source-preserving rewrites need a deeper truth layer than the current pipeline stores.

## Chosen Solution Topology

The chosen topology is:

1. introduce Canonical Document IR as a sidecar-first source-of-truth layer
2. keep Translation Execution IR as a projection of that layer
3. tag projected execution artifacts with parse revision + canonical node provenance
4. later add source-preserving EPUB export and risk-aware PDF planning on top of the same truth

Rejected topologies:

- “Just extend `Block.source_span_json` forever”
  - loses query discipline and turns provenance into ad hoc nested metadata
- “Fully normalize page/line/span tables immediately”
  - too much migration cost before the first value-producing slice lands
- “Start with rebuilt exporter redesign before shared source truth exists”
  - exporter quality would still be capped by lossy upstream data

## User And Operator Flows

### Parse Truth Flow

1. A document is ingested and parsed.
2. The parser produces `ParsedDocument`.
3. The canonical IR service materializes a parse revision plus canonical IR sidecar artifact.
4. The bootstrap projection emits `Chapter / Block / Sentence / Packet` as before.
5. Execution artifacts carry parse revision and canonical node provenance.

### Future EPUB Flow

1. Canonical IR maps EPUB spine, DOM structure, and text-node identity.
2. Translation units are projected from canonical nodes.
3. Export patches the original EPUB structure instead of rebuilding the whole book from scratch.

### Future PDF Flow

1. Canonical IR captures page, zone, reading-order, asset, and relation truth.
2. Translation units project from that truth.
3. Export defaults to a faithful Chinese reading edition until facsimile-grade truth is proven.

### Forge v2 Flow

1. A verified slice lands.
2. Forge v2 performs a continuation scan.
3. Any newly discovered work is classified as `mainline_required`, `mainline_adjacent`, or
   `out_of_band`.
4. The ledger is rewritten transactionally if the mainline changes.
5. The run continues unless a real blocker or no next dependency-closed slice exists.

## Edge Cases And Failure Modes

- canonical IR exists but execution projection never consumes it
- parse revisions are persisted but sidecar artifacts are not restartable
- provenance is attached to blocks but lost at sentence level
- EPUB patch export is attempted before stable DOM/node locators exist
- PDF work drifts into page-faithful export claims before the parser truth supports them
- the new mainline is only written in chat and not in `.forge/`, causing the next resume to
  restart the old runtime self-heal mainline by accident

## Non-Goals

- page-faithful Chinese PDF facsimile in the first slice
- image text redraw in the first slice
- replacing the current translation pipeline
- reopening the already verified runtime self-heal closure mainline

## Success Criteria

The foundation is successful when:

- the active mainline in `.forge/` clearly points at high-fidelity document translation
  foundation
- parse revisions can be persisted and associated with canonical IR artifacts
- canonical IR has a stable minimal schema for document/chapter/block/asset/relation truth
- projected execution artifacts can point back to parse revision and canonical node identity
- the first slice lands with targeted verification and without regressing the baseline
- Forge v2 can resume this new mainline from file truth and continue on the next credible change
  request
- continuation scan and stop-legality remain part of the active execution contract

## Initial Delivery Strategy

1. Rewrite the mainline from runtime self-heal closure to high-fidelity document translation
   foundation through `change_request` intake.
2. Freeze batch-64 as the smallest dependency-closed first slice:
   - parse revision models
   - canonical IR types
   - repository/service skeleton
   - projection provenance wiring
   - targeted tests
3. Verify batch-64.
4. Perform a continuation scan for the next credible change request.
5. Keep the single-ledger `.forge/` contract current as the system moves from parser truth to
   source-preserving export.
