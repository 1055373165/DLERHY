from typing import Any, Mapping

PACKET_RUNTIME_STAGE_TRANSLATE = "translate"
PACKET_RUNTIME_SUBSTATE_READY = "ready"
PACKET_RUNTIME_SUBSTATE_LEASED = "leased"
PACKET_RUNTIME_SUBSTATE_RUNNING = "running"
PACKET_RUNTIME_SUBSTATE_RETRYABLE_FAILED = "retryable_failed"
PACKET_RUNTIME_SUBSTATE_TERMINAL_FAILED = "terminal_failed"
PACKET_RUNTIME_SUBSTATE_TRANSLATED = "translated"


def packet_runtime_state(packet_json: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(packet_json, Mapping):
        return {}
    runtime_state = packet_json.get("runtime_state")
    return dict(runtime_state) if isinstance(runtime_state, Mapping) else {}


def packet_runtime_substate(packet_json: Mapping[str, Any] | None) -> str | None:
    substate = packet_runtime_state(packet_json).get("substate")
    if substate is None:
        return None
    normalized = str(substate).strip()
    return normalized or None


def build_packet_runtime_state(
    *,
    substate: str,
    stage: str = PACKET_RUNTIME_STAGE_TRANSLATE,
    packet_ordinal: int | None = None,
    run_id: str | None = None,
    work_item_id: str | None = None,
    attempt: int | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "substate": substate,
    }
    if packet_ordinal is not None:
        payload["packet_ordinal"] = packet_ordinal
    if run_id is not None:
        payload["run_id"] = run_id
    if work_item_id is not None:
        payload["work_item_id"] = work_item_id
    if attempt is not None:
        payload["attempt"] = attempt
    if updated_at is not None:
        payload["updated_at"] = updated_at
    return payload
