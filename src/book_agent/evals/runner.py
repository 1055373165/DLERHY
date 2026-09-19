"""Run eval suites and write a report with metrics, thresholds and harness configuration."""

from __future__ import annotations

import json
import subprocess
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

from book_agent.core.config import Settings, get_settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS = REPO_ROOT / "evals" / "datasets"


@dataclass(slots=True)
class Threshold:
    metric: str
    minimum: float


@dataclass(slots=True)
class SuiteResult:
    suite: str
    metrics: dict[str, float | None]
    thresholds: list[Threshold]
    cases: list[dict[str, Any]] = field(default_factory=list)
    skipped_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def failed_thresholds(self) -> list[str]:
        if self.skipped_reason:
            return []
        failures = []
        for threshold in self.thresholds:
            value = self.metrics.get(threshold.metric)
            if value is None or value < threshold.minimum:
                failures.append(f"{threshold.metric}={value} < {threshold.minimum}")
        return failures

    def to_json(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "passed": not self.failed_thresholds,
            "skipped_reason": self.skipped_reason,
            "metrics": self.metrics,
            "thresholds": [asdict(t) for t in self.thresholds],
            "failed_thresholds": self.failed_thresholds,
            "notes": self.notes,
            "cases": self.cases,
        }


@dataclass(slots=True)
class EvalContext:
    settings: Settings
    workdir: Path
    session_factory: sessionmaker
    # Built from settings unless a caller (tests) injects them.
    translation_worker: Any = None
    agent_model: Any = None
    # Filled by suites that share state (the export suite reuses the translated terminology book).
    shared: dict[str, Any] = field(default_factory=dict)


SuiteFn = Callable[[EvalContext], SuiteResult]


def harness_config(settings: Settings) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {
        "git_commit": commit,
        "translation_backend": settings.translation_backend,
        "translation_model": settings.translation_model,
        "prompt_version": settings.translation_prompt_version,
        "prompt_profile": settings.translation_prompt_profile,
        "structured_output_mode": settings.translation_openai_structured_output_mode,
        "max_output_repairs": settings.translation_max_output_repairs,
    }


def run_evals(
    suite_names: list[str],
    *,
    output_dir: Path,
    settings: Settings | None = None,
    translation_worker: Any = None,
    agent_model: Any = None,
) -> dict[str, Any]:
    from book_agent.evals.suites import SUITES

    unknown = [name for name in suite_names if name not in SUITES]
    if unknown:
        raise ValueError(f"unknown suites: {', '.join(unknown)}; known: {', '.join(SUITES)}")
    output_dir = output_dir.resolve()
    settings = settings or get_settings()
    if translation_worker is None:
        from book_agent.workers.factory import build_translation_worker

        translation_worker = build_translation_worker(settings)
    if agent_model is None:
        from book_agent.harness.kernel.openai_model import agent_model_for_worker

        agent_model = agent_model_for_worker(translation_worker)
    names = suite_names or list(SUITES)
    # The export suite needs the translated terminology book.
    if "export" in names and "terminology" not in names:
        names = ["terminology", *names]
    started = datetime.now(timezone.utc)
    results: list[SuiteResult] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    # Inside the report directory, not the system temp dir: export records refuse temp paths,
    # and the rendered exports are worth keeping next to the report.
    workdir = output_dir / "work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    engine = build_engine(f"sqlite+pysqlite:///{workdir / 'eval.db'}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        context = EvalContext(
            settings=settings,
            workdir=workdir,
            session_factory=build_session_factory(engine=engine),
            translation_worker=translation_worker,
            agent_model=agent_model,
        )
        for name in names:
            try:
                results.append(SUITES[name](context))
            except Exception as exc:  # a crashing suite fails the run but not the report
                results.append(
                    SuiteResult(
                        suite=name,
                        metrics={},
                        thresholds=[Threshold("completed", 1.0)],
                        notes=[f"suite raised {type(exc).__name__}: {exc}"],
                    )
                )
    finally:
        engine.dispose()
        (workdir / "eval.db").unlink(missing_ok=True)
    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "harness": {**harness_config(settings), "worker": translation_worker.metadata().model_name, "agent_model": type(agent_model).__name__},
        "passed": all(not result.failed_thresholds for result in results),
        "suites": [result.to_json() for result in results],
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(_summary(report), encoding="utf-8")
    return report


def _summary(report: dict[str, Any]) -> str:
    lines = [
        f"# Eval report ({report['started_at']})",
        "",
        f"- Result: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Backend / model: {report['harness']['translation_backend']} / {report['harness']['translation_model']}",
        f"- Prompt: {report['harness']['prompt_version']} ({report['harness']['prompt_profile']})",
        f"- Commit: {report['harness']['git_commit']}",
        "",
        "| Suite | Result | Metrics |",
        "|---|---|---|",
    ]
    for suite in report["suites"]:
        result = "skipped" if suite["skipped_reason"] else ("pass" if suite["passed"] else "FAIL")
        metrics = ", ".join(f"{k}={v if not isinstance(v, float) else round(v, 3)}" for k, v in suite["metrics"].items())
        lines.append(f"| {suite['suite']} | {result} | {metrics} |")
    details = [
        f"- {suite['suite']}: {note}"
        for suite in report["suites"]
        for note in suite["notes"] + ([suite["skipped_reason"]] if suite["skipped_reason"] else []) + suite["failed_thresholds"]
    ]
    if details:
        lines += ["", *details]
    return "\n".join(lines) + "\n"
