from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _ensure_utc(parsed)


def project_runtime_repair_blockage(
    repair_dispatch_json: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    dispatch = dict(repair_dispatch_json or {})
    observed_at = _ensure_utc(now) or _utcnow()
    status = str(dispatch.get("status") or "").strip()
    decision = str(dispatch.get("decision") or "").strip()
    next_action = str(dispatch.get("next_action") or "").strip()
    retry_after_seconds = max(0, int(dispatch.get("retry_after_seconds") or 0))
    next_retry_after_raw = str(dispatch.get("next_retry_after") or "").strip()
    next_retry_after = _parse_iso_datetime(next_retry_after_raw)

    state = "ready_to_continue"
    blocked = False
    reason = next_action or status or "ready"

    if status == "manual_escalation_required" or decision == "manual_escalation_required":
        state = "manual_escalation_waiting"
        blocked = True
        reason = "manual_escalation_required"
    elif status == "retry_later" or decision == "retry_later":
        if next_retry_after is not None and next_retry_after > observed_at:
            state = "backoff_blocked"
            blocked = True
            reason = "retry_later"
        else:
            state = "ready_to_continue"
            blocked = False
            reason = "retry_window_elapsed"

    projection: dict[str, Any] = {
        "state": state,
        "blocked": blocked,
        "reason": reason,
        "repair_status": status,
        "repair_decision": decision or None,
        "next_action": next_action or None,
        "observed_at": observed_at.isoformat(),
    }
    if retry_after_seconds > 0:
        projection["retry_after_seconds"] = retry_after_seconds
    if next_retry_after is not None:
        projection["next_retry_after"] = next_retry_after.isoformat()
    if state == "backoff_blocked" and next_retry_after is not None:
        projection["retry_after_seconds_remaining"] = max(
            1,
            int(math.ceil((next_retry_after - observed_at).total_seconds())),
        )
    return projection


def summarize_runtime_repair_blockage(runtime_v2_json: dict[str, Any] | None) -> dict[str, Any] | None:
    runtime_v2 = dict(runtime_v2_json or {})
    for source in (
        "last_export_route_recovery",
        "pending_export_route_repair",
        "last_deadlock_recovery",
        "last_runtime_defect_recovery",
    ):
        payload = dict(runtime_v2.get(source) or {})
        blockage = dict(payload.get("repair_blockage") or {})
        if not blockage:
            continue
        return {
            "repair_blockage": blockage,
            "repair_blockage_state": blockage.get("state"),
            "repair_blocked": bool(blockage.get("blocked")),
            "repair_blockage_source": source,
        }
    return None
