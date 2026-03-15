from __future__ import annotations

import argparse
import concurrent.futures
import json
import threading
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.enums import DocumentRunStatus, DocumentRunType, ExportType, PacketStatus, TargetSegmentStatus
from book_agent.domain.models import (
    AlignmentEdge,
    Chapter,
    Sentence,
    TargetSegment,
    TranslationPacket,
    TranslationRun,
)
from book_agent.infra.db.base import Base
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.export import ExportGateError
from book_agent.services.run_control import DocumentRunSummary, RunBudgetSummary, RunControlService
from book_agent.services.run_execution import RunExecutionService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real-book live translation workflow.")
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--export-root", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--requested-by", default="cli.real-book-runner")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--sample-count", type=int, default=12)
    parser.add_argument("--translate-batch-size", type=int, default=20)
    parser.add_argument("--parallel-workers", type=int, default=4)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--heartbeat-interval-seconds", type=int, default=15)
    parser.add_argument("--max-wall-clock-seconds", type=int, default=None)
    parser.add_argument("--max-total-cost-usd", type=float, default=None)
    parser.add_argument("--max-total-token-in", type=int, default=None)
    parser.add_argument("--max-total-token-out", type=int, default=None)
    parser.add_argument("--max-retry-count-per-work-item", type=int, default=2)
    parser.add_argument("--max-consecutive-failures", type=int, default=20)
    parser.add_argument("--skip-final-export", action="store_true")
    parser.add_argument("--auto-followup-on-gate", action="store_true")
    parser.add_argument("--max-auto-followup-attempts", type=int, default=2)
    return parser


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _new_service(session, *, export_root: str) -> DocumentWorkflowService:
    settings = get_settings()
    return DocumentWorkflowService(
        session,
        export_root=export_root,
        translation_worker=build_translation_worker(settings),
    )


def _new_run_control_service(session) -> RunControlService:
    return RunControlService(RunControlRepository(session))


def _new_run_execution_service(session) -> RunExecutionService:
    repository = RunControlRepository(session)
    return RunExecutionService(repository, RunControlService(repository))


def _build_run_budget(args: argparse.Namespace) -> RunBudgetSummary:
    return RunBudgetSummary(
        max_wall_clock_seconds=args.max_wall_clock_seconds,
        max_total_cost_usd=args.max_total_cost_usd,
        max_total_token_in=args.max_total_token_in,
        max_total_token_out=args.max_total_token_out,
        max_retry_count_per_work_item=args.max_retry_count_per_work_item,
        max_consecutive_failures=args.max_consecutive_failures,
        max_parallel_workers=args.parallel_workers,
        max_parallel_requests_per_provider=args.parallel_workers,
        max_auto_followup_attempts=args.max_auto_followup_attempts,
    )


def _is_retryable_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    retryable_markers = [
        "http 408",
        "http 409",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "request failed",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
    ]
    if any(marker in message for marker in retryable_markers):
        return True
    return isinstance(exc, RuntimeError)


def _summarize_samples(session, document_id: str, *, limit: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            Chapter.ordinal,
            Chapter.title_src,
            Sentence.source_text,
            TargetSegment.text_zh,
            TranslationRun.model_name,
            TranslationRun.created_at,
        )
        .join(Sentence, Sentence.chapter_id == Chapter.id)
        .join(AlignmentEdge, AlignmentEdge.sentence_id == Sentence.id)
        .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
        .join(TranslationRun, TranslationRun.id == TargetSegment.translation_run_id)
        .where(
            Chapter.document_id == document_id,
            Sentence.translatable.is_(True),
            TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED,
        )
        .order_by(Chapter.ordinal.asc(), TargetSegment.ordinal.asc(), Sentence.ordinal_in_block.asc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    samples: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, str, str]] = set()
    for row in rows:
        key = (row.ordinal, row.source_text, row.text_zh)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        samples.append(
            {
                "chapter_ordinal": row.ordinal,
                "chapter_title": row.title_src,
                "source_text": row.source_text,
                "target_text": row.text_zh,
                "model_name": row.model_name,
                "translated_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return samples


def _load_packet_ids(session, document_id: str) -> list[str]:
    stmt = (
        select(TranslationPacket.id)
        .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
        .where(Chapter.document_id == document_id)
        .order_by(Chapter.ordinal.asc(), TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
    )
    return [row[0] for row in session.execute(stmt).all()]


def _load_pending_packet_ids(session, document_id: str) -> list[str]:
    stmt = (
        select(TranslationPacket.id)
        .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
        .where(
            Chapter.document_id == document_id,
            TranslationPacket.status == PacketStatus.BUILT,
        )
        .order_by(Chapter.ordinal.asc(), TranslationPacket.created_at.asc(), TranslationPacket.id.asc())
    )
    return [row[0] for row in session.execute(stmt).all()]


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _run_translate_payload(
    *,
    run_summary: DocumentRunSummary,
    total_packet_count: int,
) -> dict[str, Any]:
    usage_summary = dict(run_summary.status_detail_json.get("usage_summary") or {})
    control_counters = dict(run_summary.status_detail_json.get("control_counters") or {})
    status_counts = dict(run_summary.work_items.status_counts or {})
    translated_packet_count = int(status_counts.get("succeeded", 0))
    retryable_failed_count = int(status_counts.get("retryable_failed", 0))
    terminal_failed_count = int(status_counts.get("terminal_failed", 0))
    pending_count = int(status_counts.get("pending", 0))
    leased_count = int(status_counts.get("leased", 0))
    running_count = int(status_counts.get("running", 0))
    cancelled_count = int(status_counts.get("cancelled", 0))
    remaining_packet_count = max(
        0,
        total_packet_count - translated_packet_count - terminal_failed_count - cancelled_count,
    )

    started_at = _parse_iso_datetime(run_summary.started_at) or _parse_iso_datetime(run_summary.created_at) or _utcnow()
    now = _utcnow()
    elapsed_seconds = max(0.0, (now - started_at).total_seconds())
    avg_packet_seconds = (
        round(elapsed_seconds / translated_packet_count, 3)
        if translated_packet_count > 0
        else None
    )
    estimated_remaining_seconds = (
        round(avg_packet_seconds * remaining_packet_count, 3)
        if avg_packet_seconds is not None
        else None
    )
    estimated_finish_at = (
        (now + timedelta(seconds=estimated_remaining_seconds)).isoformat()
        if estimated_remaining_seconds is not None
        else None
    )
    return {
        "run_id": run_summary.run_id,
        "document_id": run_summary.document_id,
        "run_status": run_summary.status,
        "stop_reason": run_summary.stop_reason,
        "total_packet_count": total_packet_count,
        "translated_packet_count": translated_packet_count,
        "pending_packet_count": pending_count,
        "retryable_failed_packet_count": retryable_failed_count,
        "terminal_failed_packet_count": terminal_failed_count,
        "inflight_packet_count": leased_count + running_count,
        "remaining_packet_count": remaining_packet_count,
        "usage_summary": usage_summary,
        "control_counters": control_counters,
        "last_progress": dict(run_summary.status_detail_json.get("last_progress") or {}),
        "last_failure": dict(run_summary.status_detail_json.get("last_failure") or {}),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "avg_packet_seconds": avg_packet_seconds,
        "estimated_remaining_seconds": estimated_remaining_seconds,
        "estimated_finish_at": estimated_finish_at,
    }


def _translate_single_packet(*, session_factory, export_root: str, packet_id: str) -> dict[str, Any]:
    with session_scope(session_factory) as session:
        service = _new_service(session, export_root=export_root)
        packet = session.get(TranslationPacket, packet_id)
        if packet is None:
            raise RuntimeError(f"Packet {packet_id} was not found.")
        if packet.status != PacketStatus.BUILT:
            return {
                "packet_id": packet_id,
                "skipped": True,
                "translation_run_id": None,
                "review_required_sentence_ids": [],
                "token_in": 0,
                "token_out": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
            }

        artifacts = service.translation_service.execute_packet(packet_id)
        translation_run = artifacts.translation_run
        return {
            "packet_id": packet_id,
            "skipped": False,
            "translation_run_id": translation_run.id,
            "review_required_sentence_ids": [
                sentence.id for sentence in artifacts.updated_sentences if sentence.sentence_status.value == "review_required"
            ],
            "token_in": translation_run.token_in or 0,
            "token_out": translation_run.token_out or 0,
            "cost_usd": float(translation_run.cost_usd or 0.0),
            "latency_ms": translation_run.latency_ms or 0,
        }


def _heartbeat_work_item_loop(
    *,
    session_factory,
    lease_token: str,
    lease_seconds: int,
    heartbeat_interval_seconds: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(max(1, heartbeat_interval_seconds)):
        try:
            with session_scope(session_factory) as session:
                execution_service = _new_run_execution_service(session)
                alive = execution_service.heartbeat_work_item(
                    lease_token=lease_token,
                    lease_seconds=lease_seconds,
                )
            if not alive:
                return
        except Exception:
            # Best-effort heartbeat loop: the main execution path will still resolve the lease.
            continue


def _execute_controlled_translate_work_item(
    *,
    session_factory,
    export_root: str,
    claimed_work_item,
    lease_seconds: int,
    heartbeat_interval_seconds: int,
) -> dict[str, Any]:
    packet_id = claimed_work_item.scope_id
    lease_token = claimed_work_item.lease_token
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_work_item_loop,
        kwargs={
            "session_factory": session_factory,
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
            "heartbeat_interval_seconds": heartbeat_interval_seconds,
            "stop_event": stop_event,
        },
        daemon=True,
    )

    try:
        with session_scope(session_factory) as session:
            execution_service = _new_run_execution_service(session)
            execution_service.start_work_item(
                lease_token=lease_token,
                lease_seconds=lease_seconds,
            )

        heartbeat_thread.start()
        packet_result = _translate_single_packet(
            session_factory=session_factory,
            export_root=export_root,
            packet_id=packet_id,
        )
        stop_event.set()
        heartbeat_thread.join(timeout=max(1, heartbeat_interval_seconds))

        translation_run_id = packet_result["translation_run_id"] or "already-translated"
        with session_scope(session_factory) as session:
            execution_service = _new_run_execution_service(session)
            execution_service.complete_translate_success(
                lease_token=lease_token,
                packet_id=packet_id,
                translation_run_id=translation_run_id,
                token_in=int(packet_result["token_in"]),
                token_out=int(packet_result["token_out"]),
                cost_usd=float(packet_result["cost_usd"]),
                latency_ms=int(packet_result["latency_ms"]),
            )
        return {
            "work_item_id": claimed_work_item.work_item_id,
            "packet_id": packet_id,
            "status": "succeeded",
            **packet_result,
        }
    except Exception as exc:
        stop_event.set()
        heartbeat_thread.join(timeout=max(1, heartbeat_interval_seconds))
        error_detail = {
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        try:
            with session_scope(session_factory) as session:
                execution_service = _new_run_execution_service(session)
                released = execution_service.complete_work_item_failure(
                    lease_token=lease_token,
                    error_class=exc.__class__.__name__,
                    error_detail_json=error_detail,
                    retryable=_is_retryable_exception(exc),
                )
                work_item = RunControlRepository(session).get_work_item(released.work_item_id)
            return {
                "work_item_id": claimed_work_item.work_item_id,
                "packet_id": packet_id,
                "status": work_item.status.value,
                "skipped": False,
                "translation_run_id": None,
                "review_required_sentence_ids": [],
                "token_in": 0,
                "token_out": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }
        except Exception as completion_exc:
            return {
                "work_item_id": claimed_work_item.work_item_id,
                "packet_id": packet_id,
                "status": "runner_error",
                "skipped": False,
                "translation_run_id": None,
                "review_required_sentence_ids": [],
                "token_in": 0,
                "token_out": 0,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
                "completion_error": str(completion_exc),
            }


def _refresh_run_report(
    *,
    report: dict[str, Any],
    report_path: Path,
    run_summary: DocumentRunSummary,
    total_packet_count: int,
    recent_results: list[dict[str, Any]],
) -> None:
    report["run"] = asdict(run_summary)
    report["translate"] = _run_translate_payload(
        run_summary=run_summary,
        total_packet_count=total_packet_count,
    )
    report["translate_recent_results"] = recent_results[-50:]
    _write_report(report_path, report)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    export_root = Path(args.export_root)
    report_path = Path(args.report_path)
    export_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    engine = build_engine(database_url=args.database_url)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine=engine)
    settings = get_settings()

    started_at = _utcnow()
    report: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "source_path": str(Path(args.source_path).resolve()),
        "database_url": args.database_url,
        "export_root": str(export_root.resolve()),
        "translation_backend": settings.translation_backend,
        "translation_model": settings.translation_model,
        "translate_batch_size": args.translate_batch_size,
        "parallel_workers": args.parallel_workers,
    }

    if args.run_id is None:
        with session_scope(session_factory) as session:
            service = _new_service(session, export_root=str(export_root))
            bootstrap = service.bootstrap_epub(args.source_path)
            report["bootstrap"] = asdict(bootstrap)
            document_id = bootstrap.document_id
        _write_report(report_path, report)
    else:
        with session_scope(session_factory) as session:
            run_control = _new_run_control_service(session)
            existing_run = run_control.get_run_summary(args.run_id)
            document_id = existing_run.document_id
            report["resume_from_run_id"] = args.run_id
            report["resume_from_status"] = existing_run.status
        _write_report(report_path, report)

    with session_scope(session_factory) as session:
        service = _new_service(session, export_root=str(export_root))
        summary = service.get_document_summary(document_id)
        report["document_summary_after_bootstrap"] = asdict(summary)
    _write_report(report_path, report)

    with session_scope(session_factory) as session:
        packet_ids = _load_packet_ids(session, document_id)
        pending_packet_ids = _load_pending_packet_ids(session, document_id)

    with session_scope(session_factory) as session:
        run_control = _new_run_control_service(session)
        run_execution = _new_run_execution_service(session)
        if args.run_id is None:
            run_summary = run_control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by=args.requested_by,
                backend=settings.translation_backend,
                model_name=settings.translation_model,
                status_detail_json={
                    "source_path": str(Path(args.source_path).resolve()),
                    "report_path": str(report_path.resolve()),
                    "export_root": str(export_root.resolve()),
                },
                budget=_build_run_budget(args),
            )
            run_summary = run_control.resume_run(
                run_summary.run_id,
                actor_id=args.requested_by,
                note="start translate_full live run",
            )
        else:
            run_summary = run_control.get_run_summary(args.run_id)
            if run_summary.status in {"queued", "paused"}:
                run_summary = run_control.resume_run(
                    run_summary.run_id,
                    actor_id=args.requested_by,
                    note="resume translate_full live run",
                )
        run_id = run_summary.run_id
        seeded_work_item_ids = run_execution.seed_translate_work_items(
            run_id=run_id,
            packet_ids=pending_packet_ids,
        )
        run_summary = run_control.get_run_summary(run_id)
        report["run_seed"] = {
            "run_id": run_id,
            "seeded_work_item_count": len(seeded_work_item_ids),
            "pending_packet_count_initial": len(pending_packet_ids),
        }
    recent_results: list[dict[str, Any]] = []
    _refresh_run_report(
        report=report,
        report_path=report_path,
        run_summary=run_summary,
        total_packet_count=len(packet_ids),
        recent_results=recent_results,
    )

    effective_parallel_workers = max(1, args.parallel_workers)
    if run_summary.budget and run_summary.budget.max_parallel_workers is not None:
        effective_parallel_workers = max(1, min(effective_parallel_workers, run_summary.budget.max_parallel_workers))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_parallel_workers) as executor:
            inflight: dict[concurrent.futures.Future, dict[str, Any]] = {}
            last_housekeeping_at = 0.0
            while True:
                now_monotonic = time.monotonic()
                if now_monotonic - last_housekeeping_at >= 2.0:
                    with session_scope(session_factory) as session:
                        run_control = _new_run_control_service(session)
                        run_execution = _new_run_execution_service(session)
                        reclaim_result = run_execution.reclaim_expired_leases(run_id=run_id)
                        budget_result = run_execution.enforce_budget_guardrails(run_id=run_id)
                        run_summary = run_execution.reconcile_run_terminal_state(run_id=run_id)
                        report["run_housekeeping"] = {
                            "expired_lease_count": reclaim_result.expired_lease_count,
                            "reclaimed_work_item_ids": reclaim_result.reclaimed_work_item_ids,
                            "budget_exceeded": budget_result.budget_exceeded,
                            "budget_stop_reason": budget_result.stop_reason,
                        }
                    _refresh_run_report(
                        report=report,
                        report_path=report_path,
                        run_summary=run_summary,
                        total_packet_count=len(packet_ids),
                        recent_results=recent_results,
                    )
                    last_housekeeping_at = now_monotonic

                while len(inflight) < effective_parallel_workers:
                    with session_scope(session_factory) as session:
                        run_execution = _new_run_execution_service(session)
                        claimed = run_execution.claim_next_translate_work_item(
                            run_id=run_id,
                            worker_name="real-book-live.translate",
                            worker_instance_id=f"real-book-live:{uuid4()}",
                            lease_seconds=args.lease_seconds,
                        )
                    if claimed is None:
                        break
                    future = executor.submit(
                        _execute_controlled_translate_work_item,
                        session_factory=session_factory,
                        export_root=str(export_root),
                        claimed_work_item=claimed,
                        lease_seconds=args.lease_seconds,
                        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
                    )
                    inflight[future] = {
                        "work_item_id": claimed.work_item_id,
                        "packet_id": claimed.scope_id,
                        "attempt": claimed.attempt,
                    }

                if not inflight:
                    with session_scope(session_factory) as session:
                        run_execution = _new_run_execution_service(session)
                        run_summary = run_execution.reconcile_run_terminal_state(run_id=run_id)
                    _refresh_run_report(
                        report=report,
                        report_path=report_path,
                        run_summary=run_summary,
                        total_packet_count=len(packet_ids),
                        recent_results=recent_results,
                    )
                    if run_summary.status in {"succeeded", "failed", "paused", "cancelled"}:
                        break
                    time.sleep(1.0)
                    continue

                done, _ = concurrent.futures.wait(
                    inflight.keys(),
                    timeout=1.0,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    continue

                for future in done:
                    inflight_meta = inflight.pop(future)
                    result = future.result()
                    recent_results.append(result)
                    with session_scope(session_factory) as session:
                        run_execution = _new_run_execution_service(session)
                        run_execution.enforce_budget_guardrails(run_id=run_id)
                        run_summary = run_execution.reconcile_run_terminal_state(run_id=run_id)
                    _refresh_run_report(
                        report=report,
                        report_path=report_path,
                        run_summary=run_summary,
                        total_packet_count=len(packet_ids),
                        recent_results=recent_results,
                    )
                    print(
                        json.dumps(
                            {
                                "event": "work_item_completed",
                                "run_id": run_id,
                                "work_item_id": inflight_meta["work_item_id"],
                                "packet_id": inflight_meta["packet_id"],
                                "status": result["status"],
                                "translated_packet_count": report["translate"]["translated_packet_count"],
                                "remaining_packet_count": report["translate"]["remaining_packet_count"],
                                "run_status": run_summary.status,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

                    if run_summary.status in {"failed", "paused", "cancelled", "succeeded"} and not inflight:
                        break

                if run_summary.status in {"failed", "paused", "cancelled", "succeeded"} and not inflight:
                    break
    except KeyboardInterrupt:
        with session_scope(session_factory) as session:
            run_control = _new_run_control_service(session)
            run_summary = run_control.pause_run_system(
                run_id,
                stop_reason="operator.keyboard_interrupt",
                detail_json={"requested_by": args.requested_by},
            )
        report["interrupted"] = True
        report["interrupted_at"] = _utcnow().isoformat()
        _refresh_run_report(
            report=report,
            report_path=report_path,
            run_summary=run_summary,
            total_packet_count=len(packet_ids),
            recent_results=recent_results,
        )
        print(
            json.dumps(
                {
                    "event": "translate_interrupted",
                    "run_id": run_id,
                    "translated_packet_count": report["translate"]["translated_packet_count"],
                    "total_packet_count": len(packet_ids),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 130

    with session_scope(session_factory) as session:
        run_control = _new_run_control_service(session)
        final_run_summary = run_control.get_run_summary(run_id)
    _refresh_run_report(
        report=report,
        report_path=report_path,
        run_summary=final_run_summary,
        total_packet_count=len(packet_ids),
        recent_results=recent_results,
    )

    if final_run_summary.status != "succeeded":
        finished_at = _utcnow()
        report["finished_at"] = finished_at.isoformat()
        report["duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
        _write_report(report_path, report)
        print(
            json.dumps(
                {
                    "event": "translate_run_stopped",
                    "run_id": run_id,
                    "run_status": final_run_summary.status,
                    "stop_reason": final_run_summary.stop_reason,
                    "report_path": str(report_path.resolve()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if final_run_summary.status == "paused" else 1

    with session_scope(session_factory) as session:
        service = _new_service(session, export_root=str(export_root))
        review = service.review_document(document_id)
        report["review"] = asdict(review)
    _write_report(report_path, report)

    with session_scope(session_factory) as session:
        service = _new_service(session, export_root=str(export_root))
        review_export = service.export_document(document_id, ExportType.REVIEW_PACKAGE)
        report["review_package_export"] = asdict(review_export)
    _write_report(report_path, report)

    if not args.skip_final_export:
        try:
            with session_scope(session_factory) as session:
                service = _new_service(session, export_root=str(export_root))
                final_export = service.export_document(
                    document_id,
                    ExportType.BILINGUAL_HTML,
                    auto_execute_followup_on_gate=args.auto_followup_on_gate,
                    max_auto_followup_attempts=args.max_auto_followup_attempts,
                )
                report["bilingual_export"] = asdict(final_export)
                _write_report(report_path, report)
        except ExportGateError as exc:
            report["bilingual_export_error"] = {
                "message": str(exc),
                "detail": exc.to_http_detail(),
            }
            _write_report(report_path, report)

    with session_scope(session_factory) as session:
        service = _new_service(session, export_root=str(export_root))
        final_summary = service.get_document_summary(document_id)
        report["document_summary_final"] = asdict(final_summary)
        report["translation_samples"] = _summarize_samples(session, document_id, limit=args.sample_count)

    finished_at = _utcnow()
    report["finished_at"] = finished_at.isoformat()
    report["duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    _write_report(report_path, report)
    print(json.dumps({"report_path": str(report_path.resolve()), "document_id": document_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
