"""Translate a whole book with the configured (paid) provider through the real run executor and write a report.

Usage:
    uv run python scripts/run_paid_book_eval.py --source path/to/book.pdf --workdir .test-tmp/paid-book \
        --max-token-in 8000000 --max-token-out 1500000

The run goes through the product path: bootstrap, a TRANSLATE_FULL run with
the default agent stages (terminology and model review sampled), the run
executor with leases and budgets, rule review and repair, HTML exports and
export QA. Token caps are enforced as a run budget (the executor pauses the
run when one is hit). The report (report.json + report.md in the workdir)
covers stage outcomes, time, tokens and cost by call kind, output guardrail
rejections, issues, locked-term consistency, export QA and sample pairs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import func, select  # noqa: E402

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor  # noqa: E402
from book_agent.core.config import get_settings  # noqa: E402
from book_agent.domain.enums import DocumentRunType  # noqa: E402
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED  # noqa: E402
from book_agent.domain.models import Chapter, Sentence  # noqa: E402
from book_agent.domain.models.ops import DocumentRun, Event  # noqa: E402
from book_agent.domain.models.review import Export, ReviewIssue  # noqa: E402
from book_agent.domain.models.translation import TranslationRun  # noqa: E402
from book_agent.domain.terminology.enforcement import find_term_violations  # noqa: E402
from book_agent.domain.terminology.matching import SourceTermIndex, source_term_key  # noqa: E402
from book_agent.infra.db.base import Base  # noqa: E402
from book_agent.infra.db.session import build_engine, build_session_factory  # noqa: E402
from book_agent.infra.repositories.review import active_target_texts  # noqa: E402
from book_agent.infra.repositories.run_control import RunControlRepository  # noqa: E402
from book_agent.orchestrator.run_plan import RUN_REQUEST_KEY  # noqa: E402
from book_agent.services.glossary_service import GlossaryService  # noqa: E402
from book_agent.services.run_control import RunBudgetSummary, RunControlService  # noqa: E402
from book_agent.services.workflows import DocumentWorkflowService  # noqa: E402
from book_agent.workers.factory import build_translation_worker  # noqa: E402

TERMINAL = {"succeeded", "succeeded_with_warnings", "failed", "paused", "cancelled"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--max-token-in", type=int, default=8_000_000)
    parser.add_argument("--max-token-out", type=int, default=1_500_000)
    parser.add_argument("--max-wall-clock-seconds", type=int, default=4 * 3600)
    parser.add_argument("--parallel-workers", type=int, default=8)
    parser.add_argument("--terminology", default="sampled", choices=["sampled", "thorough", "skip"])
    parser.add_argument("--model-review", default="sampled", choices=["sampled", "full", "skip"])
    parser.add_argument("--repair-agent", default="off", choices=["on", "off"])
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    return parser.parse_args()


def _log(workdir: Path, message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    with (workdir / "progress.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    if settings.translation_backend.strip().lower() == "echo":
        print("The configured backend is echo; this script is for a real provider.", file=sys.stderr)
        return 2
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    database = workdir / "book.db"
    engine = build_engine(f"sqlite+pysqlite:///{database}", connect_args={"check_same_thread": False, "timeout": 60})
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine=engine)
    worker = build_translation_worker(settings)
    started = time.monotonic()

    with session_factory() as session:
        existing = session.scalar(select(DocumentRun).order_by(DocumentRun.created_at.desc()).limit(1))
    if existing is not None:
        run_id, document_id = existing.id, existing.document_id
        _log(workdir, f"resuming run {run_id}")
        with session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            if control.get_run_summary(run_id).status == "paused":
                control.resume_run(run_id, actor_id="paid-eval", note="resume")
            session.commit()
    else:
        with session_factory() as session:
            summary = DocumentWorkflowService(session, export_root=workdir / "exports").bootstrap_document(args.source)
            session.commit()
            document_id = summary.document_id
            control = RunControlService(RunControlRepository(session))
            run = control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="paid-eval",
                backend=settings.translation_backend,
                model_name=settings.translation_model,
                status_detail_json={
                    RUN_REQUEST_KEY: {
                        "terminology": args.terminology,
                        "model_review": args.model_review,
                        "repair_agent": args.repair_agent,
                    }
                },
                budget=RunBudgetSummary(
                    max_wall_clock_seconds=args.max_wall_clock_seconds,
                    max_total_cost_usd=None,
                    max_total_token_in=args.max_token_in,
                    max_total_token_out=args.max_token_out,
                    max_retry_count_per_work_item=None,
                    max_consecutive_failures=None,
                    max_parallel_workers=args.parallel_workers,
                    max_parallel_requests_per_provider=None,
                    max_auto_followup_attempts=None,
                ),
            )
            control.resume_run(run.run_id, actor_id="paid-eval", note="start")
            session.commit()
            run_id = run.run_id
        _log(workdir, f"document {document_id} run {run_id} started")

    executor = DocumentRunExecutor(
        session_factory=session_factory,
        export_root=workdir / "exports",
        translation_worker=worker,
        translation_max_output_repairs=settings.translation_max_output_repairs,
        poll_interval_seconds=1.0,
    )
    executor.start()
    status = "running"
    try:
        while True:
            time.sleep(args.poll_seconds)
            with session_factory() as session:
                repository = RunControlRepository(session)
                summary = RunControlService(repository).get_run_summary(run_id)
                usage = repository.usage_from_events(run_id)
            status = summary.status
            pipeline = (summary.status_detail_json or {}).get("pipeline") or {}
            _log(
                workdir,
                f"status={status} stage={pipeline.get('current_stage')} work_items={summary.work_items.status_counts} "
                f"calls={usage['call_count']} tokens_in={usage['token_in']} tokens_out={usage['token_out']}",
            )
            if status in TERMINAL:
                break
    finally:
        executor.stop(work_timeout_seconds=120)

    report = _report(session_factory, document_id, run_id, workdir, elapsed=time.monotonic() - started, args=args)
    (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (workdir / "report.md").write_text(_markdown(report), encoding="utf-8")
    _log(workdir, f"finished with status={status}; report at {workdir / 'report.md'}")
    engine.dispose()
    return 0 if status in {"succeeded", "succeeded_with_warnings"} else 1


def _report(session_factory, document_id: str, run_id: str, workdir: Path, *, elapsed: float, args) -> dict[str, Any]:
    settings = get_settings()
    with session_factory() as session:
        repository = RunControlRepository(session)
        summary = RunControlService(repository).get_run_summary(run_id)
        detail = summary.status_detail_json or {}
        usage = repository.usage_from_events(run_id)
        by_kind: dict[str, dict[str, float]] = {}
        for event in session.scalars(select(Event).where(Event.kind.in_([LLM_CALL_COMPLETED, LLM_CALL_FAILED]))):
            payload = event.payload or {}
            bucket = by_kind.setdefault(
                str(payload.get("call_kind") or "translate"), {"calls": 0, "failed": 0, "token_in": 0, "token_out": 0, "latency_ms": 0}
            )
            bucket["calls"] += 1
            bucket["failed"] += int(event.kind == LLM_CALL_FAILED)
            bucket["token_in"] += int(payload.get("token_in") or 0)
            bucket["token_out"] += int(payload.get("token_out") or 0)
            bucket["latency_ms"] += int(payload.get("latency_ms") or 0)

        runs = session.scalars(select(TranslationRun)).all()
        error_codes = Counter(run.error_code for run in runs if run.error_code)
        repairs = sum(int((run.model_config_json or {}).get("output_repairs") or 0) for run in runs)

        issues = session.scalars(select(ReviewIssue).where(ReviewIssue.document_id == document_id)).all()
        issue_counts = Counter((issue.issue_type, issue.status.value, issue.detector.value) for issue in issues)

        sentences = session.scalars(
            select(Sentence).where(
                Sentence.document_id == document_id,
                Sentence.translatable.is_(True),
                Sentence.retired_by_revision_id.is_(None),
            )
        ).all()
        targets = active_target_texts(session, [sentence.id for sentence in sentences])
        covered = sum(1 for sentence in sentences if targets.get(sentence.id, "").strip())

        glossary = GlossaryService(session)
        chapter_ids = session.scalars(select(Chapter.id).where(Chapter.document_id == document_id)).all()
        term_units = 0
        violations = 0
        locked_count = 0
        for chapter_id in chapter_ids:
            terms = glossary.locked_terms_for_chapter(document_id, chapter_id)
            locked_count = max(locked_count, len(terms))
            units = [(s.id, s.source_text, targets.get(s.id, "")) for s in sentences if s.chapter_id == chapter_id]
            index = SourceTermIndex(term.source_term for term in terms)
            keys = {source_term_key(term.source_term) for term in terms}
            term_units += sum(len({o.key for o in index.find(source) if o.key in keys}) for _, source, _ in units)
            violations += len(find_term_violations(units, terms, skip_empty_targets=True))

        exports = [
            {"type": export.export_type.value, "status": export.status.value, "path": export.file_path}
            for export in session.scalars(select(Export).where(Export.document_id == document_id))
        ]
        qa_reports = []
        for path in sorted((workdir / "exports").rglob("*.qa.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            qa_reports.append(
                {
                    "path": str(path.relative_to(workdir)),
                    "all_ok": data.get("all_ok"),
                    "failed": [check["name"] for check in data.get("checks", []) if not check.get("ok")],
                }
            )
        step = max(1, len(sentences) // 12)
        samples = [
            {"source": sentence.source_text, "target": targets.get(sentence.id, "")}
            for sentence in list(sentences)[::step][:12]
        ]
        pipeline = detail.get("pipeline") or {}
        stages = {
            name: {key: value for key, value in (stage or {}).items() if key in {"status", "stop_reason", "turn_status"}}
            for name, stage in (pipeline.get("stages") or {}).items()
        }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": args.source,
            "provider": {"backend": settings.translation_backend, "model": settings.translation_model, "prompt_version": settings.translation_prompt_version},
            "run": {
                "run_id": run_id,
                "status": summary.status,
                "stop_reason": summary.stop_reason,
                "elapsed_seconds": round(elapsed, 1),
                "work_items": summary.work_items.status_counts,
                "stages": stages,
                "request": detail.get(RUN_REQUEST_KEY),
            },
            "usage": usage,
            "usage_by_call_kind": by_kind,
            "priced": usage.get("cost_usd", 0) > 0,
            "translation": {
                "translatable_sentences": len(sentences),
                "covered_sentences": covered,
                "coverage": round(covered / len(sentences), 4) if sentences else None,
                "translation_runs": len(runs),
                "guardrail_error_codes": dict(error_codes),
                "output_repairs": repairs,
            },
            "terminology": {
                "locked_terms": locked_count,
                "term_occurrences": term_units,
                "violations": violations,
                "consistency": round((term_units - violations) / term_units, 4) if term_units else None,
            },
            "issues": [
                {"issue_type": issue_type, "status": status, "detector": detector, "count": count}
                for (issue_type, status, detector), count in sorted(issue_counts.items())
            ],
            "blocking_open_issues": session.scalar(
                select(func.count(ReviewIssue.id)).where(
                    ReviewIssue.document_id == document_id, ReviewIssue.blocking.is_(True), ReviewIssue.status.in_(["open", "triaged"])
                )
            ),
            "exports": exports,
            "export_qa": qa_reports,
            "samples": samples,
        }


def _markdown(report: dict[str, Any]) -> str:
    run, usage, translation, terminology = report["run"], report["usage"], report["translation"], report["terminology"]
    lines = [
        f"# Paid book eval: {Path(report['source']).name}",
        "",
        f"- Provider: {report['provider']['backend']} / {report['provider']['model']}",
        f"- Run: {run['status']} ({run['stop_reason'] or 'no stop reason'}), {run['elapsed_seconds']} s",
        f"- Tokens: {usage['token_in']} in / {usage['token_out']} out over {usage['call_count']} calls"
        + (f", ${usage['cost_usd']}" if report["priced"] else " (no pricing configured)"),
        f"- Coverage: {translation['covered_sentences']}/{translation['translatable_sentences']} sentences",
        f"- Guardrail: {translation['guardrail_error_codes'] or 'no rejected outputs kept'}; {translation['output_repairs']} repair calls",
        f"- Terminology: {terminology['locked_terms']} locked terms, consistency {terminology['consistency']}",
        f"- Blocking open issues: {report['blocking_open_issues']}",
        "",
        "| Stage | Status |",
        "|---|---|",
        *[f"| {name} | {stage.get('status')} |" for name, stage in run["stages"].items()],
        "",
        "| Call kind | Calls | Failed | Tokens in | Tokens out |",
        "|---|---|---|---|---|",
        *[
            f"| {kind} | {int(b['calls'])} | {int(b['failed'])} | {int(b['token_in'])} | {int(b['token_out'])} |"
            for kind, b in sorted(report["usage_by_call_kind"].items())
        ],
        "",
        "| Issue type | Status | Detector | Count |",
        "|---|---|---|---|",
        *[f"| {i['issue_type']} | {i['status']} | {i['detector']} | {i['count']} |" for i in report["issues"]],
        "",
        "Export QA: " + "; ".join(f"{q['path']}: {'ok' if q['all_ok'] else ', '.join(q['failed'])}" for q in report["export_qa"]),
        "",
        "## Samples",
        "",
        *[f"- {sample['source']}\n  - {sample['target']}" for sample in report["samples"]],
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
