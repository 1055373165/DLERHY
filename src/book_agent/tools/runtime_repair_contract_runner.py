from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from book_agent.services.runtime_repair_contract import build_runtime_repair_result_payload
from book_agent.services.runtime_repair_remote_agent import RuntimeRemoteRepairAgentRegistry


def _load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def execute_runtime_repair_contract_runner(payload: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    input_bundle = dict(payload.get("input_bundle") or {})
    executor_descriptor = dict(payload.get("executor_descriptor") or {})
    transport_descriptor = dict(payload.get("transport_descriptor") or {})
    repair_agent = RuntimeRemoteRepairAgentRegistry().resolve_for_input_bundle(input_bundle)
    prepared = repair_agent.prepare_execution_from_request_contract(input_bundle)
    completed_at = datetime.now(timezone.utc)
    return build_runtime_repair_result_payload(
        prepared_payload=prepared,
        executor_descriptor=executor_descriptor,
        transport_descriptor=transport_descriptor,
        repair_runner_status="succeeded",
        repair_runner_pid=os.getpid(),
        repair_agent_execution_id=str(uuid4()),
        repair_agent_execution_status="succeeded",
        repair_agent_execution_started_at=started_at.isoformat(),
        repair_agent_execution_completed_at=completed_at.isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute a runtime repair request contract in an independent subprocess."
    )
    parser.add_argument("--payload-file", required=True)
    args = parser.parse_args()
    payload = _load_payload(Path(args.payload_file))
    result = execute_runtime_repair_contract_runner(payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
