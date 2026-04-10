from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from book_agent.core.config import get_settings


def _utcnow_iso() -> str:
    return datetime.now().isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _slugify(value: str) -> str:
    lowered = value.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "book"


def _parse_report_sequence(path: Path) -> int:
    stem = path.stem
    if stem == "report":
        return 1
    match = re.search(r"report-slice(\d+)", stem)
    if match:
        return int(match.group(1))
    return 0


@dataclass(slots=True)
class QueueItem:
    queue_index: int
    path: str
    exists: bool
    suffix: str
    lane_guess: str | None
    family_guess: str | None
    recommended_next_step: str
    risk_tags: list[str]


@dataclass(slots=True)
class LivePilotState:
    source_path: str
    root: str
    latest_report_path: str
    latest_sequence: int
    latest_report_mtime: float
    chapter_ordinal: int | None
    chapter_title: str | None
    fully_translated: bool
    translated_count: int
    built_count: int
    running_count: int
    no_work_remaining: bool


@dataclass(slots=True)
class BenchmarkState:
    source_path: str
    verdict: str
    summary_path: str
    manifest_path: str
    sample_ids: list[str]


@dataclass(slots=True)
class BenchmarkManifestState:
    source_path: str
    manifest_path: str
    gold_label_path: str
    gold_label_status: str | None
    sample_id: str


@dataclass(slots=True)
class PlannedAction:
    action: str
    queue_index: int
    source_path: str
    reason: str
    command: list[str] | None
    working_root: str | None
    report_path: str | None
    benchmark_summary_path: str | None = None
    benchmark_manifest_path: str | None = None


def load_queue_profile(path: Path) -> list[QueueItem]:
    payload = _load_json(path)
    items: list[QueueItem] = []
    for row in payload.get("book_queue", []):
        classification = dict(row.get("classification") or {})
        items.append(
            QueueItem(
                queue_index=int(row["queue_index"]),
                path=str(row["path"]),
                exists=bool(row.get("exists")),
                suffix=str(row.get("suffix") or ""),
                lane_guess=classification.get("lane_guess"),
                family_guess=classification.get("family_guess"),
                recommended_next_step=str(classification.get("recommended_next_step") or "benchmark_first"),
                risk_tags=[str(tag) for tag in classification.get("risk_tags") or []],
            )
        )
    return items


def discover_live_pilots(real_book_root: Path) -> dict[str, LivePilotState]:
    latest_by_source: dict[str, LivePilotState] = {}
    for report_path in real_book_root.rglob("report*.json"):
        try:
            payload = _load_json(report_path)
        except Exception:
            continue
        source_path = payload.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            continue
        chapter = dict(payload.get("selected_chapter") or {})
        snapshot = dict(chapter.get("packet_status_snapshot") or {})
        counts = dict(snapshot.get("counts") or {})
        state = LivePilotState(
            source_path=source_path,
            root=str(report_path.parent.resolve()),
            latest_report_path=str(report_path.resolve()),
            latest_sequence=_parse_report_sequence(report_path),
            latest_report_mtime=report_path.stat().st_mtime,
            chapter_ordinal=chapter.get("ordinal") if isinstance(chapter.get("ordinal"), int) else None,
            chapter_title=chapter.get("title_src") if isinstance(chapter.get("title_src"), str) else None,
            fully_translated=bool(chapter.get("fully_translated")),
            translated_count=int(counts.get("translated", 0)),
            built_count=int(counts.get("built", 0)),
            running_count=int(counts.get("running", 0)),
            no_work_remaining=bool(payload.get("no_work_remaining")),
        )
        current = latest_by_source.get(source_path)
        if current is None or (state.latest_sequence, state.latest_report_mtime, state.latest_report_path) > (
            current.latest_sequence,
            current.latest_report_mtime,
            current.latest_report_path,
        ):
            latest_by_source[source_path] = state
    return latest_by_source


def discover_benchmark_states(review_root: Path) -> dict[str, BenchmarkState]:
    latest_by_source: dict[str, BenchmarkState] = {}
    for summary_path in review_root.rglob("*execution-summary*.json"):
        try:
            summary = _load_json(summary_path)
        except Exception:
            continue
        manifest_path_raw = summary.get("manifest_path")
        if not isinstance(manifest_path_raw, str) or not manifest_path_raw.strip():
            continue
        manifest_path = Path(manifest_path_raw)
        if not manifest_path.is_absolute():
            # Try relative to summary file's parent first, then relative to CWD
            candidate = (summary_path.parent / manifest_path).resolve()
            if candidate.exists():
                manifest_path = candidate
            else:
                manifest_path = Path.cwd() / manifest_path
                manifest_path = manifest_path.resolve()
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        sample_results = {
            str(sample.get("sample_id")): sample
            for sample in summary.get("sample_results", [])
            if isinstance(sample, dict)
        }
        for sample in manifest.get("samples", []):
            if not isinstance(sample, dict):
                continue
            source_path = sample.get("document_path")
            sample_id = sample.get("sample_id")
            if not isinstance(source_path, str) or not source_path.strip():
                continue
            sample_result = sample_results.get(str(sample_id), {})
            verdict = str(sample_result.get("verdict") or summary.get("overall_verdict") or "unknown")
            state = BenchmarkState(
                source_path=source_path,
                verdict=verdict,
                summary_path=str(summary_path.resolve()),
                manifest_path=str(manifest_path.resolve()),
                sample_ids=[str(sample_id)] if sample_id is not None else [],
            )
            current = latest_by_source.get(source_path)
            if current is None or state.summary_path > current.summary_path:
                latest_by_source[source_path] = state
    return latest_by_source


def discover_benchmark_manifests(review_root: Path) -> dict[str, BenchmarkManifestState]:
    manifests: dict[str, BenchmarkManifestState] = {}
    for manifest_path in review_root.rglob("*.yaml"):
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for sample in manifest.get("samples", []):
            if not isinstance(sample, dict):
                continue
            source_path = sample.get("document_path")
            gold_label_path = sample.get("gold_label_path")
            sample_id = sample.get("sample_id")
            if not (isinstance(source_path, str) and source_path.strip()):
                continue
            if not (isinstance(gold_label_path, str) and gold_label_path.strip()):
                continue
            label_path = Path(gold_label_path)
            if not label_path.is_absolute():
                label_path = (manifest_path.parent / label_path).resolve()
            gold_label_status = None
            if label_path.exists():
                try:
                    gold_payload = _load_json(label_path)
                    if isinstance(gold_payload.get("status"), str):
                        gold_label_status = gold_payload["status"]
                except Exception:
                    gold_label_status = None
            manifests[source_path] = BenchmarkManifestState(
                source_path=source_path,
                manifest_path=str(manifest_path.resolve()),
                gold_label_path=str(label_path.resolve()),
                gold_label_status=gold_label_status,
                sample_id=str(sample_id or ""),
            )
    return manifests


def _next_report_path(root: Path) -> Path:
    candidates = sorted(root.glob("report*.json"))
    if not candidates:
        return root / "report.json"
    latest = max(candidates, key=_parse_report_sequence)
    latest_sequence = _parse_report_sequence(latest)
    return root / f"report-slice{latest_sequence + 1}.json"


def _book_root_for_new_item(real_book_root: Path, item: QueueItem) -> Path:
    stem = _slugify(Path(item.path).stem)
    return real_book_root / f"translate-agent-autopilot-{item.queue_index:02d}-{stem}"


def _chapter_smoke_command(
    *,
    source_path: str,
    root: Path,
    packet_limit: int,
) -> tuple[list[str], Path]:
    report_path = _next_report_path(root)
    settings = get_settings()
    database_url = settings.database_url
    export_root = root / "exports"
    command = [
        sys.executable,
        "scripts/run_pdf_chapter_smoke.py",
        "--source-path",
        source_path,
        "--database-url",
        database_url,
        "--export-root",
        str(export_root.resolve()),
        "--report-path",
        str(report_path.resolve()),
        "--chapter-ordinal",
        "auto",
        "--packet-limit",
        str(packet_limit),
    ]
    return command, report_path


def _benchmark_outputs_for_manifest(manifest_path: Path) -> tuple[Path, Path, Path]:
    stem = manifest_path.stem
    review_root = manifest_path.parent
    execution_pack = review_root / f"execution-pack-{stem}"
    summary_json = review_root / f"{stem}-execution-summary.json"
    summary_md = review_root / f"{stem}-execution-summary.md"
    return execution_pack, summary_json, summary_md


def _benchmark_command(manifest_path: Path) -> tuple[list[str], Path]:
    execution_pack, summary_json, summary_md = _benchmark_outputs_for_manifest(manifest_path)
    command = [
        sys.executable,
        "artifacts/review/scripts/run_translate_agent_benchmark_execution.py",
        "--manifest",
        str(manifest_path.resolve()),
        "--execution-pack",
        str(execution_pack.resolve()),
        "--summary-json",
        str(summary_json.resolve()),
        "--summary-md",
        str(summary_md.resolve()),
    ]
    return command, summary_json


def _plan_for_item(
    item: QueueItem,
    *,
    live_state: LivePilotState | None,
    benchmark_state: BenchmarkState | None,
    benchmark_manifest_state: BenchmarkManifestState | None,
    queue_profile_path: Path,
    real_book_root: Path,
    packet_limit: int,
) -> PlannedAction:
    if live_state is not None and not live_state.no_work_remaining:
        command, report_path = _chapter_smoke_command(
            source_path=item.path,
            root=Path(live_state.root),
            packet_limit=packet_limit,
        )
        return PlannedAction(
            action="continue_live_chapter_pilot",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="existing live root has remaining or next auto-selected chapter work",
            command=command,
            working_root=live_state.root,
            report_path=str(report_path.resolve()),
        )

    if benchmark_state is not None and benchmark_state.verdict == "go":
        root = Path(live_state.root) if live_state is not None else _book_root_for_new_item(real_book_root, item)
        command, report_path = _chapter_smoke_command(
            source_path=item.path,
            root=root,
            packet_limit=packet_limit,
        )
        return PlannedAction(
            action="start_or_continue_live_pilot_after_benchmark_go",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="benchmark evidence is already measured go",
            command=command,
            working_root=str(root.resolve()),
            report_path=str(report_path.resolve()),
            benchmark_summary_path=benchmark_state.summary_path,
            benchmark_manifest_path=benchmark_state.manifest_path,
        )

    if item.recommended_next_step == "chapter_pilot_ready":
        root = Path(live_state.root) if live_state is not None else _book_root_for_new_item(real_book_root, item)
        command, report_path = _chapter_smoke_command(
            source_path=item.path,
            root=root,
            packet_limit=packet_limit,
        )
        return PlannedAction(
            action="start_live_chapter_pilot",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="queue profile marked this document chapter-pilot-ready",
            command=command,
            working_root=str(root.resolve()),
            report_path=str(report_path.resolve()),
        )

    if benchmark_manifest_state and benchmark_manifest_state.gold_label_status == "annotated_v1":
        manifest_path = Path(benchmark_manifest_state.manifest_path)
        command, summary_json = _benchmark_command(manifest_path)
        return PlannedAction(
            action="run_benchmark_manifest",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="document requires benchmark-first and a manifest already exists",
            command=command,
            working_root=str(manifest_path.parent.resolve()),
            report_path=None,
            benchmark_summary_path=str(summary_json.resolve()),
            benchmark_manifest_path=str(manifest_path.resolve()),
        )

    if benchmark_manifest_state:
        if benchmark_manifest_state.gold_label_status == "stub_pending_annotation":
            # Auto-annotate the stub gold label then run the benchmark
            command = [
                sys.executable,
                "scripts/generate_translate_agent_benchmark_draft.py",
                "--queue-profile",
                str(queue_profile_path.resolve()),
                "--review-root",
                str((Path.cwd() / "artifacts/review").resolve()),
                "--queue-index",
                str(item.queue_index),
                "--auto-annotate",
            ]
            return PlannedAction(
                action="auto_annotate_gold_label",
                queue_index=item.queue_index,
                source_path=item.path,
                reason="benchmark draft exists with stub gold label; auto-annotating to unblock benchmark",
                command=command,
                working_root=str(Path.cwd().resolve()),
                report_path=None,
                benchmark_manifest_path=benchmark_manifest_state.manifest_path,
            )
        return PlannedAction(
            action="benchmark_annotation_pending",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="benchmark draft exists but gold label is not yet annotated_v1",
            command=None,
            working_root=None,
            report_path=None,
            benchmark_manifest_path=benchmark_manifest_state.manifest_path,
        )

    if benchmark_state is not None:
        return PlannedAction(
            action="benchmark_no_go_hold",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="latest benchmark verdict is not go; do not spend translation tokens",
            command=None,
            working_root=None,
            report_path=None,
            benchmark_summary_path=benchmark_state.summary_path,
            benchmark_manifest_path=benchmark_state.manifest_path,
        )

    if item.recommended_next_step == "benchmark_first":
        command = [
            sys.executable,
            "scripts/generate_translate_agent_benchmark_draft.py",
            "--queue-profile",
            str(queue_profile_path.resolve()),
            "--review-root",
            str((Path.cwd() / "artifacts/review").resolve()),
            "--queue-index",
            str(item.queue_index),
        ]
        return PlannedAction(
            action="generate_benchmark_draft",
            queue_index=item.queue_index,
            source_path=item.path,
            reason="benchmark-first document still lacks any benchmark draft or manifest",
            command=command,
            working_root=str(Path.cwd().resolve()),
            report_path=None,
        )

    return PlannedAction(
        action="benchmark_manifest_missing",
        queue_index=item.queue_index,
        source_path=item.path,
        reason="benchmark-first document has no benchmark manifest yet",
        command=None,
        working_root=None,
        report_path=None,
    )


def plan_rollout_actions(
    *,
    queue_items: list[QueueItem],
    queue_profile_path: Path,
    live_states: dict[str, LivePilotState],
    benchmark_states: dict[str, BenchmarkState],
    benchmark_manifests: dict[str, BenchmarkManifestState],
    real_book_root: Path,
    packet_limit: int,
) -> list[PlannedAction]:
    planned: list[PlannedAction] = []
    for item in queue_items:
        planned.append(
            _plan_for_item(
                item,
                live_state=live_states.get(item.path),
                benchmark_state=benchmark_states.get(item.path),
                benchmark_manifest_state=benchmark_manifests.get(item.path),
                queue_profile_path=queue_profile_path,
                real_book_root=real_book_root,
                packet_limit=packet_limit,
            )
        )

    priority = {
        "continue_live_chapter_pilot": 0,
        "start_or_continue_live_pilot_after_benchmark_go": 1,
        "start_live_chapter_pilot": 2,
        "generate_benchmark_draft": 3,
        "auto_annotate_gold_label": 4,
        "run_benchmark_manifest": 5,
        "benchmark_annotation_pending": 6,
        "benchmark_no_go_hold": 7,
        "benchmark_manifest_missing": 8,
    }
    live_states_by_source = {state.source_path: state for state in live_states.values()}
    planned.sort(
        key=lambda action: (
            priority.get(action.action, 99),
            live_states_by_source[action.source_path].latest_report_mtime
            if action.action == "continue_live_chapter_pilot" and action.source_path in live_states_by_source
            else 0.0,
            action.queue_index,
        )
    )
    return planned


def _provider_snapshot() -> dict[str, Any]:
    settings = get_settings()
    return {
        "backend": settings.translation_backend,
        "model": settings.translation_model,
        "base_url": settings.translation_openai_base_url,
        "max_output_tokens": settings.translation_max_output_tokens,
    }


def build_rollout_state_payload(
    *,
    queue_profile_path: Path,
    queue_items: list[QueueItem],
    live_states: dict[str, LivePilotState],
    benchmark_states: dict[str, BenchmarkState],
    actions: list[PlannedAction],
    selected_action: PlannedAction | None,
    execution_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "generated_at": _utcnow_iso(),
        "queue_profile_path": str(queue_profile_path.resolve()),
        "provider": _provider_snapshot(),
        "queue_size": len(queue_items),
        "live_state_count": len(live_states),
        "benchmark_state_count": len(benchmark_states),
        "books": [
            {
                **asdict(item),
                "live_state": asdict(live_states[item.path]) if item.path in live_states else None,
                "benchmark_state": asdict(benchmark_states[item.path]) if item.path in benchmark_states else None,
                "planned_action": asdict(next(action for action in actions if action.source_path == item.path)),
            }
            for item in queue_items
        ],
        "selected_action": asdict(selected_action) if selected_action is not None else None,
        "execution_result": execution_result,
    }


def render_rollout_decision_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Translate Agent Rollout Supervisor",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Provider: `{payload['provider']['backend']} / {payload['provider']['model']}`",
        f"- Queue size: `{payload['queue_size']}`",
        "",
        "## Selected Action",
        "",
    ]
    selected = payload.get("selected_action")
    if not isinstance(selected, dict):
        lines.append("- No executable action selected.")
    else:
        lines.append(f"- Queue index: `{selected['queue_index']}`")
        lines.append(f"- Action: `{selected['action']}`")
        lines.append(f"- Source: `{selected['source_path']}`")
        lines.append(f"- Reason: {selected['reason']}")
        if selected.get("report_path"):
            lines.append(f"- Report path: `{selected['report_path']}`")
        if selected.get("benchmark_summary_path"):
            lines.append(f"- Benchmark summary: `{selected['benchmark_summary_path']}`")
        if selected.get("benchmark_manifest_path"):
            lines.append(f"- Benchmark manifest: `{selected['benchmark_manifest_path']}`")
    execution_result = payload.get("execution_result")
    if isinstance(execution_result, dict):
        lines.extend(
            [
                "",
                "## Execution",
                "",
                f"- Status: `{execution_result.get('status')}`",
                f"- Return code: `{execution_result.get('returncode')}`",
            ]
        )
        if execution_result.get("stdout_tail"):
            lines.append(f"- Stdout tail: `{execution_result['stdout_tail']}`")
        if execution_result.get("stderr_tail"):
            lines.append(f"- Stderr tail: `{execution_result['stderr_tail']}`")
    lines.extend(["", "## Queue Snapshot", ""])
    for book in payload.get("books", []):
        planned_action = dict(book.get("planned_action") or {})
        lines.append(f"### {book['queue_index']}. {Path(book['path']).name}")
        lines.append(f"- Recommended next step: `{book['recommended_next_step']}`")
        lines.append(f"- Planned action: `{planned_action.get('action')}`")
        lines.append(f"- Reason: {planned_action.get('reason')}")
        live_state = book.get("live_state")
        if isinstance(live_state, dict):
            lines.append(
                f"- Live state: `ch{live_state.get('chapter_ordinal')}` translated={live_state.get('translated_count')} built={live_state.get('built_count')}"
            )
        benchmark_state = book.get("benchmark_state")
        if isinstance(benchmark_state, dict):
            lines.append(f"- Benchmark verdict: `{benchmark_state.get('verdict')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def execute_action(action: PlannedAction, *, cwd: Path) -> dict[str, Any]:
    if not action.command:
        return {
            "status": "skipped",
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    completed = subprocess.run(
        action.command,
        cwd=str(cwd.resolve()),
        capture_output=True,
        text=True,
    )
    return {
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
    }


def _run_action_subprocess(command: list[str], cwd: str) -> dict[str, Any]:
    """Execute a single action in a subprocess — used by ProcessPoolExecutor."""
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return {
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
    }


def select_parallel_actions(actions: list[PlannedAction], *, max_parallel: int) -> list[PlannedAction]:
    """Select up to *max_parallel* executable actions targeting distinct books."""
    selected: list[PlannedAction] = []
    seen_sources: set[str] = set()
    for action in actions:
        if not action.command:
            continue
        if action.source_path in seen_sources:
            continue
        selected.append(action)
        seen_sources.add(action.source_path)
        if len(selected) >= max_parallel:
            break
    return selected


def execute_actions_parallel(
    actions: list[PlannedAction],
    *,
    cwd: Path,
    max_parallel: int,
) -> list[dict[str, Any]]:
    """Execute multiple actions concurrently, each targeting a different book."""
    selected = select_parallel_actions(actions, max_parallel=max_parallel)
    if not selected:
        return []

    results: list[dict[str, Any]] = [None] * len(selected)  # type: ignore[list-item]
    cwd_str = str(cwd.resolve())

    with ProcessPoolExecutor(max_workers=min(len(selected), max_parallel)) as pool:
        future_to_index = {
            pool.submit(_run_action_subprocess, action.command, cwd_str): idx
            for idx, action in enumerate(selected)
            if action.command
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = {
                    **future.result(),
                    "queue_index": selected[idx].queue_index,
                    "action": selected[idx].action,
                    "source_path": selected[idx].source_path,
                }
            except Exception as exc:
                results[idx] = {
                    "status": "error",
                    "returncode": -1,
                    "stdout_tail": "",
                    "stderr_tail": str(exc),
                    "queue_index": selected[idx].queue_index,
                    "action": selected[idx].action,
                    "source_path": selected[idx].source_path,
                }
    return [r for r in results if r is not None]


def run_supervisor(
    *,
    queue_profile_path: Path,
    review_root: Path,
    real_book_root: Path,
    state_json_path: Path,
    state_md_path: Path,
    packet_limit: int,
    execute: bool,
    parallel: int = 1,
) -> dict[str, Any]:
    queue_items = load_queue_profile(queue_profile_path)
    live_states = discover_live_pilots(real_book_root)
    benchmark_states = discover_benchmark_states(review_root)
    benchmark_manifests = discover_benchmark_manifests(review_root)
    actions = plan_rollout_actions(
        queue_items=queue_items,
        queue_profile_path=queue_profile_path,
        live_states=live_states,
        benchmark_states=benchmark_states,
        benchmark_manifests=benchmark_manifests,
        real_book_root=real_book_root,
        packet_limit=packet_limit,
    )

    selected_action = actions[0] if actions else None
    execution_result: dict[str, Any] | None = None
    parallel_results: list[dict[str, Any]] | None = None

    if execute and parallel > 1:
        # Parallel mode: execute up to N actions concurrently
        parallel_results = execute_actions_parallel(
            actions,
            cwd=Path.cwd(),
            max_parallel=parallel,
        )
    elif execute and selected_action is not None:
        # Sequential mode (original behavior)
        execution_result = execute_action(selected_action, cwd=Path.cwd())

    # Re-discover state after execution
    if execute and (execution_result or parallel_results):
        live_states = discover_live_pilots(real_book_root)
        benchmark_states = discover_benchmark_states(review_root)
        benchmark_manifests = discover_benchmark_manifests(review_root)
        actions = plan_rollout_actions(
            queue_items=queue_items,
            queue_profile_path=queue_profile_path,
            live_states=live_states,
            benchmark_states=benchmark_states,
            benchmark_manifests=benchmark_manifests,
            real_book_root=real_book_root,
            packet_limit=packet_limit,
        )

    payload = build_rollout_state_payload(
        queue_profile_path=queue_profile_path,
        queue_items=queue_items,
        live_states=live_states,
        benchmark_states=benchmark_states,
        actions=actions,
        selected_action=selected_action,
        execution_result=execution_result,
    )
    if parallel_results:
        payload["parallel_execution"] = {
            "parallel_count": len(parallel_results),
            "results": parallel_results,
        }
    _write_json(state_json_path, payload)
    _write_markdown(state_md_path, render_rollout_decision_markdown(payload))
    return payload
