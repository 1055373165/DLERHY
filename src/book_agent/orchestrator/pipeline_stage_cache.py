"""Reader-compat helpers for the pipeline-stage cache payload.

The cache lives in ``document_runs.status_detail_json.pipeline``.
Historically stored under the key ``"stages"``; M1.4 renames it to
``"_cached_pipeline_stages"``. The underscore prefix is load-bearing:
it flags the payload as a *derived projection*, never authoritative.
The authoritative source for stage status is
:class:`book_agent.orchestrator.stage_status.StageStatusCalculator`,
which reads the physical ``translation_packets`` / ``work_items`` rows.

Rollout policy:

* **Readers** prefer ``_cached_pipeline_stages``, fall back to the
  legacy ``stages`` key so runs populated before this migration keep
  projecting until their next write touches them.
* **Writers** emit only the new key and strip the legacy key on the
  same row, so each write performs an in-place migration.

No Alembic migration exists because the key lives inside an opaque
JSON column — cleaning it up would require scanning every historic
run and offers no safety benefit. The shim here absorbs the gap.
"""

from __future__ import annotations

from typing import Any


CACHED_STAGES_KEY = "_cached_pipeline_stages"
_LEGACY_STAGES_KEY = "stages"


def read_cached_stages(pipeline: Any) -> dict[str, Any] | None:
    """Return the cached-stages dict, preferring the new key.

    Returns ``None`` if ``pipeline`` is not a dict or neither key
    holds a dict. Never returns the legacy key's value when the new
    key is present — even if the new key holds a non-dict — so a
    half-migrated row doesn't fall back to stale truth.
    """
    if not isinstance(pipeline, dict):
        return None
    if CACHED_STAGES_KEY in pipeline:
        value = pipeline.get(CACHED_STAGES_KEY)
        return value if isinstance(value, dict) else None
    legacy = pipeline.get(_LEGACY_STAGES_KEY)
    return legacy if isinstance(legacy, dict) else None


def write_cached_stages(pipeline: dict[str, Any], stages: dict[str, Any]) -> None:
    """Store ``stages`` under the new key and drop the legacy key.

    Mutates ``pipeline`` in place. Callers pass a dict they own
    (typically a shallow copy of ``run.status_detail_json["pipeline"]``).
    """
    pipeline[CACHED_STAGES_KEY] = stages
    pipeline.pop(_LEGACY_STAGES_KEY, None)


__all__ = [
    "CACHED_STAGES_KEY",
    "read_cached_stages",
    "write_cached_stages",
]
