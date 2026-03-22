from __future__ import annotations

from typing import Any

CURRENT_TELEMETRY_GENERATION = "current-runtime-report-v2"
LEGACY_TELEMETRY_GENERATION = "legacy-report-generation"
CURRENT_TELEMETRY_FIELDS = [
    "telemetry_generation",
    "stage",
    "database_path",
    "db_counts",
    "work_item_status_counts",
    "translation_packet_status_counts",
    "ocr_status",
    "ocr_progress",
    "resume_from_run_id",
    "resume_from_status",
    "retry_from_run_id",
    "retry_from_status",
    "failure_taxonomy",
    "recommended_recovery_action",
]


def _has_path(payload: dict[str, Any], path: str) -> bool:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def build_telemetry_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    generation = str(payload.get("telemetry_generation") or "").strip() or LEGACY_TELEMETRY_GENERATION
    missing_fields = [field for field in CURRENT_TELEMETRY_FIELDS if not _has_path(payload, field)]
    return {
        "generation": generation,
        "compatible_with_phase3": generation == CURRENT_TELEMETRY_GENERATION and not missing_fields,
        "missing_fields": missing_fields,
    }


def classify_failure_taxonomy(
    *,
    stage: str | None = None,
    error_message: str | None = None,
    stop_reason: str | None = None,
) -> dict[str, Any] | None:
    stage_value = str(stage or "").strip().lower()
    message = str(error_message or "").strip()
    message_lower = message.lower()
    stop_reason_value = str(stop_reason or "").strip().lower()

    if stop_reason_value == "provider.insufficient_balance" or (
        "http 402" in message_lower and "insufficient balance" in message_lower
    ):
        return {
            "family": "provider_exhaustion",
            "reason_code": "provider.insufficient_balance",
            "retryable": False,
            "recovery_action": "top_up_provider_balance_and_resume",
        }

    if stage_value in {"bootstrap", "resume", "bootstrap_ocr_failed", "bootstrap_ocr_running"} and (
        "ocr" in message_lower or "surya" in message_lower or "tesseract" in message_lower
    ):
        return {
            "family": "ocr_failure",
            "reason_code": "ocr.timeout" if "timed out" in message_lower or "timeout" in message_lower else "ocr.failure",
            "retryable": False,
            "recovery_action": "fix_ocr_runtime_and_rerun_bootstrap",
        }

    if "read operation timed out" in message_lower or (
        stage_value == "repair" and ("timed out" in message_lower or "timeout" in message_lower)
    ):
        return {
            "family": "repair_timeout",
            "reason_code": "repair.read_timeout",
            "retryable": True,
            "recovery_action": "retry_repair_slice_or_reduce_repair_batch",
        }

    if "timed out" in message_lower or "timeout" in message_lower:
        return {
            "family": "runtime_timeout",
            "reason_code": "runtime.timeout",
            "retryable": True,
            "recovery_action": "retry_current_work_item",
        }

    return None


def summarize_report_failure_taxonomy(payload: dict[str, Any]) -> dict[str, Any] | None:
    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
        taxonomy = error_payload.get("failure_taxonomy")
        if isinstance(taxonomy, dict):
            return taxonomy
        return classify_failure_taxonomy(
            stage=str(error_payload.get("stage") or ""),
            error_message=str(error_payload.get("message") or ""),
            stop_reason=str(((payload.get("run") or {}).get("stop_reason") or "")),
        )

    run_payload = payload.get("run")
    if isinstance(run_payload, dict):
        translate_payload = payload.get("translate")
        last_failure: dict[str, Any] = {}
        if isinstance(translate_payload, dict):
            last_failure = dict(translate_payload.get("last_failure") or {})
        taxonomy = classify_failure_taxonomy(
            stage="translate" if translate_payload else None,
            error_message=str(last_failure.get("error_message") or last_failure.get("message") or ""),
            stop_reason=str(run_payload.get("stop_reason") or ""),
        )
        if taxonomy is not None:
            return taxonomy

    for result in reversed(list(payload.get("translate_recent_results") or [])):
        if str(result.get("status") or "") not in {"retryable_failed", "terminal_failed", "runner_error"}:
            continue
        taxonomy = classify_failure_taxonomy(
            stage="translate",
            error_message=str(result.get("error_message") or ""),
            stop_reason=str(result.get("stop_reason") or ""),
        )
        if taxonomy is not None:
            return taxonomy
    return None
