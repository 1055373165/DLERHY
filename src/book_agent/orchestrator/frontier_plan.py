"""Explicit return contract for the translate-stage frontier planner.

The planner is the DECIDE half of the run's main loop: it reads the
current DB snapshot (active work_items, pending translation_packets,
chapter metadata) and decides which packet IDs the EXECUTE half should
seed into new TRANSLATE work_items next. It must not write — that's
:meth:`RunExecutionService.seed_translate_work_items`'s job.

Returning a dataclass instead of a bare ``list[str]`` upgrades the
contract in three ways:

1. The single "what packets to seed" answer is still ``packet_ids``,
   so call-sites that only care about the target list don't change
   their shape.
2. ``blocked_chapter_ids`` and ``represented_packet_ids`` carry the
   reasoning the planner used, so future auditors (reconciler,
   debug logs, tests) can verify the frontier invariants — at most
   one active packet per chapter; no duplicate seeds — without
   having to re-derive them from DB state.
3. Callers that accidentally try to treat the plan as a mutable
   collection get a clear type error, rather than silently
   discovering a bug later when reads and writes get interleaved.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class TranslateFrontierPlan:
    packet_ids: list[str]
    blocked_chapter_ids: frozenset[str] = field(default_factory=frozenset)
    represented_packet_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_empty(self) -> bool:
        return not self.packet_ids
