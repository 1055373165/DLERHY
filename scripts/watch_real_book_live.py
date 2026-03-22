from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.real_book_live_reporting_common import build_telemetry_compatibility, summarize_report_failure_taxonomy


_OCR_PROGRESS_PATTERN = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z ]+):\s+\d+%.*?\|\s*(?P<current>\d+)/(?P<total>\d+)",
    re.S,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach a lightweight live monitor to a real-book run report."
    )
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--monitor-path", default=None)
    parser.add_argument("--interval-seconds", type=int, default=20)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--stop-when-terminal", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _monitor_path_for(report_path: Path, override: str | None) -> Path:
    if override:
        return Path(override).resolve()
    return report_path.with_suffix(".live.json")


def _ocr_status_path(report: dict[str, Any], *, report_path: Path) -> Path | None:
    raw_path = report.get("ocr_status_path")
    if isinstance(raw_path, str) and raw_path.strip():
        return Path(raw_path).resolve()
    fallback = report_path.with_name(f"{report_path.stem}.ocr.json")
    return fallback if fallback.exists() else None


def _read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return _read_json(path)


def _sqlite_path_from_url(database_url: str | None) -> Path | None:
    if not database_url:
        return None
    prefix = "sqlite+pysqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix) :]
    if not raw_path:
        return None
    if not raw_path.startswith("/"):
        raw_path = "/" + raw_path
    return Path(raw_path)


def _list_processes() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-axww", "-o", "pid=,ppid=,etime=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    processes: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_text, ppid_text, elapsed_text, command = parts
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "elapsed": elapsed_text,
                "command": command,
            }
        )
    return processes


def _extract_flag(command: str, flag: str) -> str | None:
    marker = f"{flag} "
    index = command.find(marker)
    if index < 0:
        return None
    remainder = command[index + len(marker) :]
    if not remainder:
        return None
    return remainder.split(" ", 1)[0]


def _select_runner_processes(
    processes: list[dict[str, Any]],
    *,
    report_path: Path,
) -> list[dict[str, Any]]:
    report_token = str(report_path.resolve())
    matches = [
        process
        for process in processes
        if "run_real_book_live.py" in process["command"] and report_token in process["command"]
    ]
    return sorted(
        matches,
        key=lambda process: (
            "uv run" in process["command"],
            process["pid"],
        ),
    )


def _collect_descendant_pids(processes: list[dict[str, Any]], root_pid: int) -> set[int]:
    child_map: dict[int, list[int]] = {}
    for process in processes:
        child_map.setdefault(process["ppid"], []).append(process["pid"])
    descendants = {root_pid}
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child_pid in child_map.get(pid, []):
            if child_pid in descendants:
                continue
            descendants.add(child_pid)
            frontier.append(child_pid)
    return descendants


def _select_ocr_process(
    processes: list[dict[str, Any]],
    *,
    runner_pid: int | None,
    source_path: str | None,
) -> dict[str, Any] | None:
    descendant_pids: set[int] = set()
    if runner_pid is not None:
        descendant_pids = _collect_descendant_pids(processes, runner_pid)
    matches: list[dict[str, Any]] = []
    for process in processes:
        command = process["command"]
        if "surya_ocr" not in command:
            continue
        if source_path and source_path not in command and descendant_pids and process["pid"] not in descendant_pids:
            continue
        matches.append(process)
    if not matches:
        return None
    return sorted(matches, key=lambda process: process["pid"])[0]


def _path_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve()
    if not resolved.exists():
        return {
            "path": str(resolved),
            "exists": False,
        }
    file_count = 0
    dir_count = 0
    latest_mtime = 0.0
    results_path: Path | None = None
    for candidate in resolved.rglob("*"):
        if candidate.is_dir():
            dir_count += 1
            continue
        file_count += 1
        latest_mtime = max(latest_mtime, candidate.stat().st_mtime)
        if candidate.name == "results.json" and results_path is None:
            results_path = candidate
    return {
        "path": str(resolved),
        "exists": True,
        "file_count": file_count,
        "dir_count": dir_count,
        "results_json_exists": results_path is not None,
        "results_json_path": str(results_path) if results_path is not None else None,
        "latest_file_mtime": (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat() if latest_mtime > 0 else None
        ),
    }


def _chunk_summary(output_dir: Path | None) -> dict[str, Any] | None:
    if output_dir is None:
        return None
    resolved = output_dir.resolve()
    temp_root = resolved.parent
    if not temp_root.exists():
        return {
            "temp_root": str(temp_root),
            "current_chunk": resolved.name if resolved.name.startswith("chunk-") else None,
            "exists": False,
        }
    chunk_dirs = sorted(
        candidate for candidate in temp_root.iterdir() if candidate.is_dir() and candidate.name.startswith("chunk-")
    )
    completed_chunks: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        results_path = next(chunk_dir.rglob("results.json"), None)
        if results_path is None:
            continue
        completed_chunks.append(
            {
                "name": chunk_dir.name,
                "results_json_path": str(results_path),
            }
        )
    return {
        "temp_root": str(temp_root),
        "exists": True,
        "current_chunk": resolved.name if resolved.name.startswith("chunk-") else None,
        "chunk_dirs": [chunk_dir.name for chunk_dir in chunk_dirs],
        "chunk_dir_count": len(chunk_dirs),
        "completed_chunks": [chunk["name"] for chunk in completed_chunks],
        "completed_chunk_count": len(completed_chunks),
        "latest_completed_chunk": completed_chunks[-1]["name"] if completed_chunks else None,
        "latest_completed_results_json_path": (
            completed_chunks[-1]["results_json_path"] if completed_chunks else None
        ),
    }


def _table_counts(database_path: Path | None) -> dict[str, int]:
    if database_path is None:
        return {}
    resolved = database_path.resolve()
    if not resolved.exists():
        return {}
    tables = [
        "documents",
        "chapters",
        "blocks",
        "sentences",
        "translation_packets",
        "document_runs",
        "work_items",
        "document_images",
    ]
    counts: dict[str, int] = {}
    with sqlite3.connect(resolved) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        for table_name in tables:
            if table_name not in existing_tables:
                continue
            counts[table_name] = int(connection.execute(f"select count(*) from {table_name}").fetchone()[0])
    return counts


def _status_counts(database_path: Path | None, *, table_name: str) -> dict[str, int]:
    if database_path is None:
        return {}
    resolved = database_path.resolve()
    if not resolved.exists():
        return {}
    with sqlite3.connect(resolved) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        if table_name not in existing_tables:
            return {}
        rows = connection.execute(
            f"select status, count(*) from {table_name} group by status order by status"
        ).fetchall()
    return {str(status): int(count) for status, count in rows if status is not None}


def _infer_stage(
    report: dict[str, Any],
    *,
    runner_alive: bool,
    ocr_alive: bool,
    db_counts: dict[str, int],
    ocr_status: dict[str, Any] | None,
) -> str:
    if report.get("finished_at"):
        return "finished"
    run_payload = report.get("run")
    if isinstance(run_payload, dict) and run_payload.get("status") in {"succeeded", "failed", "paused", "cancelled"}:
        return f"run_{run_payload['status']}"
    if report.get("bootstrap_in_progress"):
        if isinstance(ocr_status, dict):
            ocr_state = str(ocr_status.get("state") or "").strip().lower()
            if ocr_state == "failed":
                return "bootstrap_ocr_failed"
            if ocr_state == "succeeded":
                return "bootstrap_ocr_succeeded_pre_persist"
            if ocr_state in {"starting", "running"}:
                return "bootstrap_ocr_running"
        if ocr_alive:
            return "bootstrap_ocr_running"
        if db_counts.get("documents", 0) > 0:
            return "bootstrap_persisting"
        if runner_alive:
            return "bootstrap_waiting"
        return "bootstrap_unknown"
    if report.get("resume_in_progress"):
        return "resume_in_progress" if runner_alive else "resume_unknown"
    if report.get("translate"):
        return "translate_in_progress"
    if report.get("run_seed"):
        return "run_seeded"
    if report.get("document_summary_after_bootstrap"):
        return "post_bootstrap_pre_run"
    return "report_initialized"


def _report_started_at(report: dict[str, Any]) -> datetime | None:
    raw_value = report.get("started_at") or report.get("bootstrap_started_at")
    if not isinstance(raw_value, str) or not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def _parse_ocr_progress(ocr_status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ocr_status, dict):
        return None
    stderr_tail = str(ocr_status.get("stderr_tail") or "")
    if not stderr_tail:
        return None
    matches = list(_OCR_PROGRESS_PATTERN.finditer(stderr_tail))
    if not matches:
        return None
    match = matches[-1]
    label = " ".join(match.group("label").split()).strip().lower().replace(" ", "_")
    current = int(match.group("current"))
    total = int(match.group("total"))
    percent = round((current / total) * 100.0, 3) if total > 0 else None
    return {
        "phase": label,
        "current": current,
        "total": total,
        "percent": percent,
        "page_range": ocr_status.get("page_range"),
    }


def _build_monitor_snapshot(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    database_path = _sqlite_path_from_url(report.get("database_url"))
    source_path = report.get("source_path")
    ocr_status_path = _ocr_status_path(report, report_path=report_path)
    ocr_status = _read_optional_json(ocr_status_path)
    processes = _list_processes()
    runner_processes = _select_runner_processes(processes, report_path=report_path)
    runner_process = runner_processes[0] if runner_processes else None
    ocr_process = _select_ocr_process(
        processes,
        runner_pid=(runner_process["pid"] if runner_process else None),
        source_path=source_path if isinstance(source_path, str) else None,
    )
    ocr_output_dir = None
    if ocr_process is not None:
        raw_output_dir = _extract_flag(ocr_process["command"], "--output_dir")
        if raw_output_dir:
            ocr_output_dir = Path(raw_output_dir)
    if ocr_output_dir is None and isinstance(ocr_status, dict):
        raw_output_dir = ocr_status.get("output_dir")
        if isinstance(raw_output_dir, str) and raw_output_dir.strip():
            ocr_output_dir = Path(raw_output_dir)

    db_counts = _table_counts(database_path)
    snapshot = {
        "probed_at": _utcnow().isoformat(),
        "stage": _infer_stage(
            report,
            runner_alive=runner_process is not None,
            ocr_alive=ocr_process is not None,
            db_counts=db_counts,
            ocr_status=ocr_status,
        ),
        "report_path": str(report_path.resolve()),
        "report_last_modified_at": datetime.fromtimestamp(
            report_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "runner_process": runner_process,
        "runner_processes": runner_processes,
        "ocr_process": ocr_process,
        "ocr_output": _path_summary(ocr_output_dir),
        "ocr_chunk_summary": _chunk_summary(ocr_output_dir),
        "ocr_status_path": str(ocr_status_path) if ocr_status_path is not None else None,
        "ocr_status": ocr_status,
        "ocr_progress": _parse_ocr_progress(ocr_status),
        "database_path": str(database_path.resolve()) if database_path is not None else None,
        "db_counts": db_counts,
        "work_item_status_counts": _status_counts(database_path, table_name="work_items"),
        "translation_packet_status_counts": _status_counts(database_path, table_name="translation_packets"),
    }
    snapshot["failure_taxonomy"] = summarize_report_failure_taxonomy(report)
    snapshot["recommended_recovery_action"] = (
        str(snapshot["failure_taxonomy"].get("recovery_action"))
        if isinstance(snapshot.get("failure_taxonomy"), dict)
        else None
    )
    snapshot["telemetry_compatibility"] = build_telemetry_compatibility(report)
    started_at = _report_started_at(report)
    if started_at is not None:
        snapshot["elapsed_seconds_since_start"] = round((_utcnow() - started_at).total_seconds(), 3)
    return snapshot


def _should_stop(report: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    run_payload = report.get("run")
    if report.get("finished_at"):
        return True
    if isinstance(run_payload, dict) and run_payload.get("status") in {"succeeded", "failed", "paused", "cancelled"}:
        return True
    if snapshot.get("runner_process") is None and snapshot.get("ocr_process") is None:
        if report.get("bootstrap_in_progress") or report.get("resume_in_progress"):
            return False
        if report.get("translate") or report.get("run_seed"):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report_path = Path(args.report_path).resolve()
    monitor_path = _monitor_path_for(report_path, args.monitor_path)

    iterations = 0
    while True:
        if not report_path.exists():
            raise FileNotFoundError(f"Report file not found: {report_path}")
        report = _read_json(report_path)
        snapshot = _build_monitor_snapshot(report, report_path=report_path)
        payload = {
            "report_path": str(report_path),
            "monitor_path": str(monitor_path),
            "snapshot": snapshot,
        }
        _write_json(monitor_path, payload)
        print(json.dumps({"event": "live_probe", **payload}, ensure_ascii=False), flush=True)

        iterations += 1
        if args.once:
            return 0
        if args.max_iterations is not None and iterations >= args.max_iterations:
            return 0
        if args.stop_when_terminal and _should_stop(report, snapshot):
            return 0
        time.sleep(max(1, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
