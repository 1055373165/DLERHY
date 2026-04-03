from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from book_agent.infra.db.session import build_session_factory
from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_repair_contract import build_runtime_repair_result_payload
from book_agent.services.runtime_repair_registry import RuntimeRepairWorkerRegistry


def _load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_claimed(payload: dict[str, Any]) -> ClaimedRunWorkItem:
    claimed_json = dict(payload.get("claimed") or {})
    return ClaimedRunWorkItem(
        run_id=str(claimed_json["run_id"]),
        work_item_id=str(claimed_json["work_item_id"]),
        stage=str(claimed_json["stage"]),
        scope_type=str(claimed_json["scope_type"]),
        scope_id=str(claimed_json["scope_id"]),
        attempt=int(claimed_json["attempt"]),
        priority=int(claimed_json["priority"]),
        lease_token=str(claimed_json["lease_token"]),
        worker_name=str(claimed_json["worker_name"]),
        worker_instance_id=str(claimed_json["worker_instance_id"]),
        lease_expires_at=str(claimed_json["lease_expires_at"]),
    )


def execute_runtime_repair_runner(payload: dict[str, Any]) -> dict[str, Any]:
    database_url = str(payload["database_url"])
    run_id = str(payload["run_id"])
    lease_token = str(payload["lease_token"])
    input_bundle = dict(payload.get("input_bundle") or {})
    executor_descriptor = dict(payload.get("executor_descriptor") or {})
    transport_descriptor = dict(payload.get("transport_descriptor") or {})
    claimed = _build_claimed(payload)
    session_factory = build_session_factory(database_url=database_url)
    repair_agent = RuntimeRepairWorkerRegistry(session_factory=session_factory).resolve_for_input_bundle(
        input_bundle
    )
    prepared = repair_agent.prepare_execution(
        claimed=claimed,
        input_bundle=input_bundle,
    )
    prepared_with_executor = build_runtime_repair_result_payload(
        prepared_payload=prepared,
        executor_descriptor=executor_descriptor,
        transport_descriptor=transport_descriptor,
        repair_runner_status="succeeded",
        repair_runner_pid=os.getpid(),
    )
    repair_agent.complete_execution(
        run_id=run_id,
        payload=prepared_with_executor,
        lease_token=lease_token,
    )
    return prepared_with_executor


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute a runtime repair handoff in a subprocess.")
    parser.add_argument("--payload-file", required=True)
    args = parser.parse_args()
    payload = _load_payload(Path(args.payload_file))
    result = execute_runtime_repair_runner(payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
