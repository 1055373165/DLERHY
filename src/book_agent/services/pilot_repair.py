"""The pilot repair loop: repair what the pilot's layered QA found, verify each repair, stop in bounds.

docs/agent-upgrade/08-pilot-quality-gate.md §7 (G4 / Q5). The pilot run's last stage,
``pilot_repair``, after ``pilot_qa``:

1. Take the QA's blocking and major findings (the loop's own view: the reading copy as the export
   would write it now, ``PilotQualityService.evaluate(use_export_file=False)``).
2. For each, classify it (§7.1, ``domain/quality/pilot_repair.classify``): systemic or this book's
   own. Every systemic issue class gets a defect ticket with its evidence (§7.3) for the dev side;
   the book still gets a workaround from the toolbox. The loop never changes code.
3. Choose a path from the closed toolbox (§7.2, ``REPAIR_PATHS``): rules decide, the pilot repair
   agent is asked only where a structure edit needs judgment.
4. Apply it in a savepoint, retranslating only the packets it affects, run the QA again, and keep it
   only when the targeted findings improved and no other check got worse; otherwise roll the
   savepoint back (the model calls it paid for stay on the books) and try the next path.
5. Bounds (§7.4): three rounds (a round takes the findings present when it starts; findings a kept
   repair uncovers wait for the next), a budget of twice the pilot's cost, a time limit.
6. Re-export the pilot's reading copy if anything was kept, run the QA on it (the report the pilot
   card shows), and turn what is still blocking into one "需要你处理" question per issue class, with
   evidence (pages, a page image, the text) and one concrete yes/no question.

Every attempt is a ``pilot_repair_attempts`` row: the findings, classification and rule, tool, path
and parameters, packets and blocks touched, metrics before and after, kept or reverted and why, and
its cost. The lessons ledger (Q6) will learn from them.

A repair failure never fails the pilot: the stage records the error and the pilot goes to the
reader with the QA's findings listed (the product rule).
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ApprovalStatus, ExportType, SourceType
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED, LLM_CALL_FAILED
from book_agent.domain.models import Block, Chapter, Document, Event
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import PilotDefectTicket, PilotQualityReport, PilotRepair, PilotRepairAttempt
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.events import PENDING_LLM_EVENTS
from book_agent.domain.quality import pilot_repair as rules
from book_agent.domain.quality.pilot_qa import CHECKS
from book_agent.domain.quality.pilot_repair import (
    Classification,
    LessonLedger,
    NoLedger,
    QaSnapshot,
    Verdict,
    WorkItem,
    classify,
    escalation_question,
    findings_of,
    judge,
    needs_reexport,
    repairable_findings,
    plan_work,
    restored_content_followups,
    self_inflicted_packets,
)
from book_agent.domain.structure.parse_health import BLOCKING, MAJOR
from book_agent.orchestrator.run_plan import CHAPTER_IDS_REQUEST_KEY, PACKET_IDS_REQUEST_KEY, RUN_REQUEST_KEY
from book_agent.services.billing_category import paid_by_us
from book_agent.services.pilot_quality import PilotQualityService, pilot_chapter_ids, report_for_run
from book_agent.services.pilot_repair_tools import (
    BudgetExhausted,
    NotApplicable,
    PilotRepairToolbox,
    RepairBudget,
    RepairContext,
    StructureJudge,
    ToolResult,
)

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3
# §7.4: the repair may spend up to twice what the pilot cost.
BUDGET_MULTIPLIER = 2.0
MAX_SECONDS = 30 * 60
MAX_ATTEMPTS = 150
# The whole book's repair has more chapters to cover: more time and attempts, a budget of its own
# (services/book_quality.py: book_repair_budget).
BOOK_MAX_SECONDS = 120 * 60
BOOK_MAX_ATTEMPTS = 600
SCOPE_PILOT = "pilot"
SCOPE_BOOK = "book"
BOOK_TRIGGER = "book_repair"
QUESTION_KIND = "pilot_question"
RETRY_HINT = "上一次重译有句子没有译成中文，或混进了程序提示。这次每一句都要完整译成中文，只输出译文。"
TRIGGER = "pilot_repair"
EVIDENCE_DIR = "pilot-repair"
MAX_EVIDENCE_SAMPLES = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class _AttemptOutcome:
    outcome: str
    reason: str | None
    layers: list[dict[str, Any]] | None = None
    resolved: tuple[str, ...] = ()
    budget_stop: bool = False


@dataclass(slots=True)
class _ClassState:
    """What the loop learned about one issue class, for the summary, the ticket and the question."""

    issue_class: str
    check_id: str
    classification: Classification
    findings_before: int = 0
    attempts: list[str] = field(default_factory=list)
    kept: int = 0
    paths: list[str] = field(default_factory=list)
    ticket_id: str | None = None


class PilotRepairService:
    def __init__(
        self,
        session: Session,
        *,
        workflow_factory: Callable[[Session], Any],
        judge: StructureJudge | None = None,
        ledger: LessonLedger | None = None,
        max_rounds: int = MAX_ROUNDS,
        budget_multiplier: float = BUDGET_MULTIPLIER,
        budget_usd: float | None = None,
        max_seconds: float | None = None,
        max_attempts: int | None = None,
        scope: str = SCOPE_PILOT,
    ) -> None:
        self.session = session
        # "pilot": the pilot's chapters; "book": every translated chapter, after the whole-book run.
        self.scope = scope
        self.workflow_factory = workflow_factory
        self.judge = judge
        self.ledger = ledger or NoLedger()
        self.max_rounds = max_rounds
        self.budget_multiplier = budget_multiplier
        # An explicit cap (tests, a person); otherwise twice the pilot's cost.
        self.budget_override = budget_usd
        book = scope == SCOPE_BOOK
        self.max_seconds = max_seconds if max_seconds is not None else (BOOK_MAX_SECONDS if book else MAX_SECONDS)
        self.max_attempts = max_attempts if max_attempts is not None else (BOOK_MAX_ATTEMPTS if book else MAX_ATTEMPTS)
        self._pages = None

    # --- entry points ---------------------------------------------------------------------------

    def start(self, document_id: str, *, run: DocumentRun | None = None) -> PilotRepair:
        """The repair's row, running, before the loop starts (the stage's state from the first moment)."""
        before_report = report_for_run(self.session, run.id) if run is not None else None
        repair = PilotRepair(
            document_id=document_id,
            run_id=run.id if run is not None else None,
            scope=self.scope,
            status="running",
            rounds=0,
            before_report_id=before_report.id if before_report is not None else None,
            repaired_count=0,
            escalated_count=0,
            remaining_blocking=0,
            remaining_major=0,
            cost_usd=0,
            started_at=_utcnow(),
            created_at=_utcnow(),
        )
        self.session.add(repair)
        self.session.commit()
        return repair

    def run_for_pilot(self, run: DocumentRun, *, repair_id: str | None = None) -> PilotRepair:
        """The pilot run's ``pilot_repair`` stage: an error is recorded on the repair row, never raised."""
        try:
            return self.run(run.document_id, run=run, repair_id=repair_id)
        except Exception as exc:
            logger.exception("pilot repair failed for run %s", run.id)
            self.session.rollback()
            repair = self.session.scalars(
                select(PilotRepair).where(PilotRepair.run_id == run.id, PilotRepair.status == "running").order_by(PilotRepair.created_at.desc())
            ).first()
            if repair is None:
                repair = PilotRepair(
                    document_id=run.document_id,
                    run_id=run.id,
                    scope=self.scope,
                    status="error",
                    rounds=0,
                    repaired_count=0,
                    escalated_count=0,
                    remaining_blocking=0,
                    remaining_major=0,
                    cost_usd=0,
                    created_at=_utcnow(),
                )
                self.session.add(repair)
            repair.status = "error"
            repair.outcome = "error"
            repair.error_text = f"{type(exc).__name__}: {exc}"[:2000]
            repair.finished_at = _utcnow()
            self.session.commit()
            return repair

    def run(self, document_id: str, *, run: DocumentRun | None = None, repair_id: str | None = None) -> PilotRepair:
        started = time.monotonic()
        if self.scope == SCOPE_BOOK:
            from book_agent.services.book_quality import book_chapter_ids

            chapter_ids = book_chapter_ids(self.session, document_id)
        else:
            chapter_ids = pilot_chapter_ids(self.session, document_id, run)
        repair = self.session.get(PilotRepair, repair_id) if repair_id else None
        if repair is None:
            repair = self.start(document_id, run=run)
        before_report = self.session.get(PilotQualityReport, repair.before_report_id) if repair.before_report_id else None

        budget = self._budget(run, chapter_ids)
        repair.budget_usd = budget.budget_usd
        ctx = RepairContext(
            session=self.session,
            document_id=document_id,
            run=run,
            chapter_ids=list(chapter_ids),
            workflow_factory=self.workflow_factory,
            budget=budget,
            judge=self.judge,
        )
        ctx.spacing_convention = spacing_convention(self.session, ctx.chapter_ids)
        pages = self._open_pdf(document_id)
        # Every QA pass reads the PDF through it: its tables and figures are read once per repair.
        self._pages = pages
        if pages is not None:
            ctx.figure_problems = pages.figure_problems
        toolbox = PilotRepairToolbox(ctx)
        qa = PilotQualityService(self.session)
        try:
            layers, _source = qa.evaluate(document_id, ctx.chapter_ids, use_export_file=False, scope=self.scope, pages=pages)
            # The reading copy on disk, judged now (the before report may predate a check): a copy the
            # export would write differently (literal emphasis markers) is written again at the end.
            file_layers, file_source = qa.evaluate(document_id, ctx.chapter_ids, use_export_file=True, scope=self.scope, pages=pages)
            stale_copy = file_source == "file" and needs_reexport(file_layers)
            initial = repairable_findings(layers)
            classes = self._classes(initial)
            self.session.commit()
            self._begin_write()
            tickets = self._open_tickets(repair, classes, initial)
            self.session.commit()

            tried: set[tuple[str, str]] = set()
            attempts = 0
            kept_any = False
            stop_reason: str | None = None
            round_no = 0
            while round_no < self.max_rounds:
                round_no += 1
                round_keys = {finding["key"] for finding in repairable_findings(layers)}
                kept_in_round = 0
                while True:
                    current = [finding for finding in repairable_findings(layers) if finding["key"] in round_keys]
                    work = plan_work(current, tried)
                    if not work:
                        break
                    if time.monotonic() - started > self.max_seconds:
                        stop_reason = "time"
                        break
                    if attempts >= self.max_attempts:
                        stop_reason = "attempts"
                        break
                    item = work[0]
                    for finding in item.findings:
                        tried.add((finding["key"], item.path.name))
                    attempts += 1
                    classification = classify(item.findings[0], self.ledger, document_id=document_id)
                    state = classes.setdefault(
                        classification.issue_class,
                        _ClassState(classification.issue_class, item.check_id, classification),
                    )
                    if classification.systemic and state.ticket_id is None:
                        state.ticket_id = self._ticket(repair, state, item.findings).id
                        tickets[state.issue_class] = state.ticket_id
                    result = self._attempt(repair, toolbox, qa, ctx, item, classification, state, layers, round_no, attempts)
                    if result.outcome == "kept":
                        layers = result.layers or layers
                        kept_in_round += 1
                        kept_any = True
                    if result.budget_stop:
                        stop_reason = "budget"
                        break
                repair.rounds = round_no
                self.session.commit()
                if stop_reason is not None:
                    break
                remaining = repairable_findings(layers)
                if not remaining:
                    stop_reason = "clean"
                    break
                if kept_in_round == 0 or not plan_work(remaining, tried):
                    stop_reason = "exhausted"
                    break
            else:
                stop_reason = "max_rounds"

            final_report = before_report
            # A stale reading copy: writing it again is a repair even when nothing else was kept.
            if kept_any or stale_copy:
                if kept_any:
                    self._persist_chapter_scope(run, ctx.chapter_ids, chapter_ids)
                self.session.commit()
                self._reexport(ctx)
                self._begin_write()
                final_report = qa.run(document_id, run=run, trigger=self._trigger, chapter_ids=ctx.chapter_ids, scope=self.scope)
                self.session.commit()
            elif final_report is None:
                self._begin_write()
                final_report = qa.run(document_id, run=run, trigger=self._trigger, chapter_ids=ctx.chapter_ids, scope=self.scope)
                self.session.commit()
            self._begin_write()
            final_layers = list(final_report.layers_json or [])
            escalated = self._escalate(repair, final_layers, classes, pages, ctx)
            self._close_tickets(repair, classes, final_layers)
            self._finish(repair, final_report, final_layers, initial, classes, stop_reason, escalated, budget)
            self.session.commit()
            return repair
        finally:
            if pages is not None:
                pages.close()

    @property
    def _trigger(self) -> str:
        return BOOK_TRIGGER if self.scope == SCOPE_BOOK else TRIGGER

    # --- budget ----------------------------------------------------------------------------------

    def _budget(self, run: DocumentRun | None, chapter_ids: Sequence[str]) -> RepairBudget:
        if self.scope == SCOPE_BOOK:
            from book_agent.services.book_quality import book_repair_budget

            return book_repair_budget(self.session, chapter_ids, override_usd=self.budget_override)
        packets = int(
            self.session.scalar(select(func.count(TranslationPacket.id)).where(TranslationPacket.chapter_id.in_(list(chapter_ids) or ["-"])))
            or 0
        )
        pilot_cost = 0.0
        if run is not None:
            pilot_cost = float(
                self.session.scalar(
                    select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                        Event.kind == LLM_CALL_COMPLETED, paid_by_us([str(run.id)])
                    )
                )
                or 0.0
            )
        # A pilot that reused translations made earlier cost next to nothing; the budget follows what
        # translating these chapters costs (§7.4: twice the pilot's estimated cost), whichever is more.
        pilot_cost = max(pilot_cost, self._estimated_pilot_cost(packets, chapter_ids))
        budget_usd = self.budget_override
        if budget_usd is None and pilot_cost > 0:
            budget_usd = round(self.budget_multiplier * pilot_cost, 6)
        per_packet = (pilot_cost / packets) if packets and pilot_cost > 0 else 0.0
        return RepairBudget(
            budget_usd=budget_usd,
            max_packets=max(1, int(self.budget_multiplier * max(packets, 1))),
            cost_per_packet_usd=per_packet,
        )

    def _estimated_pilot_cost(self, packets: int, chapter_ids: Sequence[str]) -> float:
        """The book's estimate scaled to the pilot chapters' share of its packets; 0 when unpriced."""
        from book_agent.core.config import get_settings
        from book_agent.services.cost_estimate import estimate_document_cost

        if not packets or not chapter_ids:
            return 0.0
        document_id = self.session.scalar(select(Chapter.document_id).where(Chapter.id == chapter_ids[0]))
        if document_id is None:
            return 0.0
        estimate = estimate_document_cost(self.session, str(document_id), get_settings())
        if not estimate.cost_usd or not estimate.packet_count:
            return 0.0
        return estimate.cost_usd * min(1.0, packets / estimate.packet_count)

    # --- one attempt -------------------------------------------------------------------------------

    def _attempt(
        self,
        repair: PilotRepair,
        toolbox: PilotRepairToolbox,
        qa: PilotQualityService,
        ctx: RepairContext,
        item: WorkItem,
        classification: Classification,
        state: _ClassState,
        layers: list[dict[str, Any]],
        round_no: int,
        ordinal: int,
    ) -> _AttemptOutcome:
        started = time.monotonic()
        # A path that needs the repair agent asks it now, before the savepoint opens; the agent's
        # turn and its model calls are written on their own connection.
        agent_watermark = int(self.session.scalar(select(func.coalesce(func.max(Event.id), 0))) or 0)
        try:
            toolbox.consult(item)
        except Exception:
            logger.warning("pilot repair agent could not be consulted", exc_info=True)
        agent_events = self._llm_events_since(agent_watermark) if item.path.needs_agent else []
        self.session.commit()
        self._begin_write()
        before = QaSnapshot.from_layers(layers)
        misaligned_before = alignment_anomalies(self.session, ctx.chapter_ids)
        chapter_ids_before = list(ctx.chapter_ids)
        spent_before = ctx.budget.spent_usd
        result: ToolResult | None = None
        after: QaSnapshot | None = None
        new_layers: list[dict[str, Any]] | None = None
        outcome = _AttemptOutcome("failed", None)
        savepoint = self.session.begin_nested()
        try:
            result = toolbox.apply(item)
            self.session.flush()
            new_layers, _source = qa.evaluate(ctx.document_id, ctx.chapter_ids, use_export_file=False, scope=self.scope, pages=self._pages)
            after = QaSnapshot.from_layers(new_layers)
            verdict = judge(before, after, item.keys, {finding["check_id"] for finding in item.findings})
            # Reverted only for the retranslation's own flake (a line back in English, a leaked note):
            # retranslate those packets once more before giving up on a good repair.
            flaky = self_inflicted_packets(verdict, before, new_layers, result.packet_ids)
            if flaky:
                toolbox.retranslate(flaky, [RETRY_HINT])
                self.session.flush()
                result.params = {**result.params, "retried_packet_ids": flaky, "retry_reason": verdict.reason}
                new_layers, _source = qa.evaluate(ctx.document_id, ctx.chapter_ids, use_export_file=False, scope=self.scope, pages=self._pages)
                after = QaSnapshot.from_layers(new_layers)
                verdict = judge(before, after, item.keys, {finding["check_id"] for finding in item.findings})
            if item.path.restores_content and restored_content_followups(verdict, before, new_layers, result.packet_ids):
                # The restored content stays; its fresh translation's problems are the next round's work.
                result.params = {**result.params, "kept_despite": verdict.reason}
                verdict = Verdict(True, "improved", verdict.resolved_keys)
            outcome = _AttemptOutcome("kept" if verdict.keep else "reverted", None if verdict.keep else verdict.reason, new_layers, verdict.resolved_keys)
            # A repair must not leave translations the export cannot align (ALIGNMENT_FAILURE).
            misaligned_after = alignment_anomalies(self.session, ctx.chapter_ids)
            if verdict.keep and misaligned_after > misaligned_before:
                outcome = _AttemptOutcome(
                    "reverted", f"worse:alignment ({misaligned_before} -> {misaligned_after} misaligned targets)", new_layers
                )
        except NotApplicable as exc:
            outcome = _AttemptOutcome("skipped", f"not_applicable: {exc}"[:500])
        except BudgetExhausted as exc:
            outcome = _AttemptOutcome("skipped", f"budget: {exc}"[:500], budget_stop=True)
        except Exception as exc:  # a tool's refusal (StructureEditRejected, EditRejected) or a bug: the attempt fails, the loop goes on
            logger.warning("pilot repair attempt %s (%s) failed", item.path.name, item.keys[:3], exc_info=True)
            outcome = _AttemptOutcome("failed", f"{type(exc).__name__}: {exc}"[:500])
        # The model calls this attempt made, as emit_event keeps them for a rollback (infra/repositories/events.py).
        calls = [dict(event) for event in self.session.info.get(PENDING_LLM_EVENTS) or []]
        if outcome.outcome == "kept":
            savepoint.commit()
        else:
            # Paid for all the same: written again here, in this transaction, instead of by the
            # rollback hook on another connection.
            self.session.info.pop(PENDING_LLM_EVENTS, None)
            savepoint.rollback()
            ctx.chapter_ids[:] = chapter_ids_before
            self._restore_events(calls)
        events = [*agent_events, *calls]
        cost = sum(float((event["payload"] or {}).get("cost_usd") or 0.0) for event in events if event["kind"] == LLM_CALL_COMPLETED)
        token_in = sum(int((event["payload"] or {}).get("token_in") or 0) for event in events)
        token_out = sum(int((event["payload"] or {}).get("token_out") or 0) for event in events)
        # Retranslation charged the budget as it went; the attempt's events are the full account.
        ctx.budget.spent_usd = spent_before + cost
        attempt = PilotRepairAttempt(
            repair_id=repair.id,
            document_id=ctx.document_id,
            round=round_no,
            ordinal=ordinal,
            check_id=item.check_id,
            layer=item.check_id.split(".", 1)[0],
            severity=item.severity,
            finding_keys_json=item.keys,
            classification=classification.kind,
            classification_rule=classification.rule,
            classification_reason=classification.reason,
            tool=item.path.tool,
            path=item.path.path,
            params_json={
                **(result.params if result is not None else {}),
                "checks": sorted({finding["check_id"] for finding in item.findings}),
                "issue_class": classification.issue_class,
                # Packets whose text was corrected without a model call.
                "edited_packet_ids": list(result.edited_packet_ids) if result is not None else [],
            },
            decided_by=result.decided_by if result is not None else ("agent" if item.path.needs_agent else "rule"),
            packet_ids_json=list(result.packet_ids) if result is not None else [],
            block_ids_json=list(result.block_ids) if result is not None else [],
            before_json=before.to_json(item.keys),
            after_json=after.to_json(item.keys) if after is not None else {},
            resolved_keys_json=list(outcome.resolved) if outcome.outcome == "kept" else [],
            outcome=outcome.outcome,
            reason=outcome.reason,
            ticket_id=state.ticket_id,
            token_in=token_in,
            token_out=token_out,
            cost_usd=round(cost, 6),
            duration_ms=int((time.monotonic() - started) * 1000),
            created_at=_utcnow(),
        )
        self.session.add(attempt)
        repair.cost_usd = round(float(repair.cost_usd or 0) + cost, 6)
        self.session.flush()
        state.attempts.append(attempt.id)
        state.paths.append(f"{item.path.name}:{outcome.outcome}")
        if outcome.outcome == "kept":
            state.kept += 1
        self.session.commit()
        return outcome

    def _llm_events_since(self, watermark: int) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(Event).where(Event.id > watermark, Event.kind.in_((LLM_CALL_COMPLETED, LLM_CALL_FAILED))).order_by(Event.id)
        ).all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "occurred_at": row.occurred_at,
                "run_id": row.run_id,
                "chapter_id": row.chapter_id,
                "packet_id": row.packet_id,
                "actor_kind": row.actor_kind,
                "actor_id": row.actor_id,
                "org_id": row.org_id,
                "correlation_id": row.correlation_id,
                "payload": dict(row.payload or {}),
            }
            for row in rows
        ]

    def _restore_events(self, events: list[dict[str, Any]]) -> None:
        """A reverted repair's model calls were still paid for: their events stay in the ledger."""
        for event in events:
            self.session.add(
                Event(
                    kind=event["kind"],
                    run_id=event.get("run_id"),
                    chapter_id=event.get("chapter_id"),
                    packet_id=event.get("packet_id"),
                    actor_kind=event.get("actor_kind") or "agent",
                    actor_id=event.get("actor_id") or "agent.pilot_repair",
                    org_id=event.get("org_id"),
                    correlation_id=event.get("correlation_id"),
                    payload={**(event.get("payload") or {}), "reverted_repair": True},
                )
            )
        if events:
            self.session.flush()

    def _begin_write(self) -> None:
        """On SQLite, take the write lock before the attempt reads anything.

        An attempt reads, then writes, in one transaction. On SQLite a transaction that read first
        cannot upgrade to a writer while another connection commits (the run loop's ticks): SQLite
        answers "database is locked" at once. Taking the lock first makes the others wait instead.
        PostgreSQL needs nothing: rows are locked as they are written.
        """
        bind = self.session.get_bind()
        if bind.dialect.name != "sqlite":
            return
        try:
            self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except Exception:
            logger.debug("BEGIN IMMEDIATE not taken", exc_info=True)

    # --- classes and tickets -----------------------------------------------------------------------

    def _classes(self, findings: Sequence[dict[str, Any]]) -> dict[str, _ClassState]:
        classes: dict[str, _ClassState] = {}
        for finding in findings:
            classification = classify(finding, self.ledger)
            state = classes.setdefault(
                classification.issue_class, _ClassState(classification.issue_class, str(finding["check_id"]), classification)
            )
            state.findings_before += 1
        return classes

    def _open_tickets(self, repair: PilotRepair, classes: dict[str, _ClassState], findings: Sequence[dict[str, Any]]) -> dict[str, str]:
        by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for finding in findings:
            by_class[rules.issue_class(finding)].append(finding)
        tickets: dict[str, str] = {}
        for issue_class, state in classes.items():
            if not state.classification.systemic:
                continue
            state.ticket_id = self._ticket(repair, state, by_class.get(issue_class, [])).id
            tickets[issue_class] = state.ticket_id
        return tickets

    def _ticket(self, repair: PilotRepair, state: _ClassState, findings: Sequence[dict[str, Any]]) -> PilotDefectTicket:
        """The defect ticket of this issue class: the book's open one updated, else a new one.

        One ticket per issue class, document and suspected cause: a repair run again (by hand, or the
        whole book's after the pilot's) adds its evidence and counts to the ticket already open.
        """
        cause = state.classification.cause
        spec = CHECKS.get(state.check_id)
        document = self.session.get(Document, repair.document_id)
        evidence = [self._evidence(finding) for finding in list(findings)[:MAX_EVIDENCE_SAMPLES]]
        pages = sorted({item["page"] for item in evidence if isinstance(item.get("page"), int)})
        suspected = cause.cause if cause is not None else state.classification.reason
        existing = self.session.scalars(
            select(PilotDefectTicket)
            .where(
                PilotDefectTicket.document_id == repair.document_id,
                PilotDefectTicket.issue_class == state.issue_class,
                PilotDefectTicket.suspected_cause == suspected,
                PilotDefectTicket.status == "open",
            )
            .order_by(PilotDefectTicket.created_at)
            .limit(1)
        ).first()
        if existing is not None:
            impact = dict(existing.impact_json or {})
            repair_ids = list(impact.get("repair_ids") or ([existing.repair_id] if existing.repair_id else []))
            if repair.id not in repair_ids:
                repair_ids.append(repair.id)
                impact["occurrences"] = int(impact.get("occurrences") or 1) + 1
            impact["repair_ids"] = repair_ids
            impact["findings_before"] = len(findings)
            impact["findings_seen_total"] = int(impact.get("findings_seen_total") or 0) + len(findings)
            impact["pages"] = sorted({*(impact.get("pages") or []), *pages})
            impact["last_seen_at"] = _utcnow().isoformat()
            seen = {(item.get("key"), item.get("block_id")) for item in existing.evidence_json or []}
            fresh = [item for item in evidence if (item.get("key"), item.get("block_id")) not in seen]
            existing.evidence_json = [*fresh, *(existing.evidence_json or [])][: MAX_EVIDENCE_SAMPLES * 2]
            existing.impact_json = impact
            existing.updated_at = _utcnow()
            self.session.flush()
            return existing
        ticket = PilotDefectTicket(
            document_id=repair.document_id,
            repair_id=repair.id,
            check_id=state.check_id,
            layer=state.check_id.split(".", 1)[0],
            issue_class=state.issue_class,
            classification_rule=state.classification.rule,
            component=cause.component if cause is not None else "pipeline",
            title=f"{spec.title if spec else state.check_id}（{document.title_src or document.title or repair.document_id if document else repair.document_id}）",
            suspected_cause=suspected,
            reproduction=(
                f"导入《{(document.title_src or document.title) if document else ''}》（{document.source_type.value if document else ''}），"
                f"试译后运行分层质检，{state.check_id} 在第 {', '.join(str(page) for page in pages[:8]) or '?'} 页报告 "
                f"{len(findings)} 处；样例见 evidence（块 id、原文、译文）。"
            ),
            evidence_json=evidence,
            impact_json={
                "findings_before": len(findings),
                "findings_seen_total": len(findings),
                "pages": pages,
                "severity": spec.severity if spec else None,
                "occurrences": 1,
                "repair_ids": [repair.id],
                "last_seen_at": _utcnow().isoformat(),
            },
            workaround_json={},
            status="open",
        )
        self.session.add(ticket)
        self.session.flush()
        return ticket

    def _evidence(self, finding: dict[str, Any]) -> dict[str, Any]:
        location = finding.get("location") or {}
        evidence = finding.get("evidence") or {}
        item = {
            "key": finding.get("key"),
            "check_id": finding.get("check_id"),
            "severity": finding.get("severity"),
            "message": finding.get("message"),
            "page": location.get("page"),
            "chapter_id": location.get("chapter_id"),
            "block_id": location.get("block_id"),
            "sentence_id": location.get("sentence_id"),
            "packet_id": location.get("packet_id"),
            "source": evidence.get("source"),
            "target": evidence.get("target"),
            "detail": evidence.get("detail"),
        }
        return {key: value for key, value in item.items() if value not in (None, "")}

    def _close_tickets(self, repair: PilotRepair, classes: dict[str, _ClassState], final_layers: list[dict[str, Any]]) -> None:
        remaining = Counter(rules.issue_class(finding) for finding in findings_of(final_layers))
        for state in classes.values():
            if state.ticket_id is None:
                continue
            ticket = self.session.get(PilotDefectTicket, state.ticket_id)
            if ticket is None:
                continue
            ticket.impact_json = {**(ticket.impact_json or {}), "findings_after": int(remaining.get(state.issue_class, 0))}
            ticket.workaround_json = {
                "paths": list(state.paths),
                "kept": state.kept,
                "attempt_ids": list(state.attempts),
                "worked": state.kept > 0 and not remaining.get(state.issue_class),
            }
            ticket.updated_at = _utcnow()

    # --- after the loop -----------------------------------------------------------------------------

    def _persist_chapter_scope(self, run: DocumentRun | None, chapter_ids: Sequence[str], original: Sequence[str]) -> None:
        """A chapter split out of a pilot chapter joins the pilot: its request names it (the overview, the QA)."""
        if run is None or self.scope != SCOPE_PILOT or list(chapter_ids) == list(original):
            return
        # The run loop writes this row too (its stage cache): read it fresh, locked.
        self._begin_write()
        run = self.session.get(DocumentRun, run.id, with_for_update=True, populate_existing=True) or run
        detail = dict(run.status_detail_json or {})
        request = dict(detail.get(RUN_REQUEST_KEY) or {})
        request[CHAPTER_IDS_REQUEST_KEY] = list(chapter_ids)
        request[PACKET_IDS_REQUEST_KEY] = [
            str(packet_id)
            for packet_id in self.session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(list(chapter_ids))).order_by(TranslationPacket.id)
            ).all()
        ]
        detail[RUN_REQUEST_KEY] = request
        run.status_detail_json = detail

    def _reexport(self, ctx: RepairContext) -> None:
        """The reading copy the reader opens, written again with the repairs."""
        workflow = self.workflow_factory(self.session)
        for export_type in (ExportType.MERGED_HTML, ExportType.BILINGUAL_HTML):
            try:
                self.session.commit()
                self._begin_write()
                result = workflow.export_document(
                    ctx.document_id,
                    export_type,
                    auto_execute_followup_on_gate=False,
                    # The whole book's reading copy is the book's, not a list of its chapters.
                    chapter_ids=None if self.scope == SCOPE_BOOK else list(ctx.chapter_ids),
                    despite_gate=True,
                )
                self.session.commit()
                # The export QA judges the new reading copy (on the pilot's chapters only).
                from book_agent.services.export_qa import ExportQaService

                artifacts = (
                    [(chapter.chapter_id, Path(chapter.file_path)) for chapter in result.chapter_results]
                    if result.chapter_results
                    else ([(None, Path(result.file_path))] if result.file_path else [])
                )
                ExportQaService(self.session).audit(ctx.document_id, export_type, artifacts, chapter_ids=list(ctx.chapter_ids))
                self.session.commit()
            except Exception:
                logger.exception("pilot repair: re-export %s of %s failed", export_type.value, ctx.document_id)
                self.session.rollback()

    def _escalate(
        self,
        repair: PilotRepair,
        final_layers: list[dict[str, Any]],
        classes: dict[str, _ClassState],
        pages: Any,
        ctx: RepairContext,
    ) -> list[str]:
        """What is still blocking: one question per issue class in the reader's inbox."""
        blocking = [finding for finding in findings_of(final_layers, (BLOCKING,))]
        by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for finding in blocking:
            by_check[str(finding["check_id"])].append(finding)
        approval_ids: list[str] = []
        # A question whose problem a later repair solved is no longer asked.
        for stale in self.session.scalars(
            select(Approval).where(
                Approval.document_id == ctx.document_id, Approval.kind == QUESTION_KIND, Approval.status == ApprovalStatus.PENDING
            )
        ).all():
            if (stale.payload_json or {}).get("check_id") not in by_check:
                stale.status = ApprovalStatus.EXPIRED
                stale.decided_by = "agent.pilot_repair"
                stale.decided_at = _utcnow()
                stale.decision_note = "a later repair solved it"
        export_root = self._evidence_root(ctx)
        for check_id, findings in by_check.items():
            title, question = escalation_question(check_id, findings)
            issue_class = rules.issue_class(findings[0])
            state = classes.get(issue_class)
            images = self._page_images(ctx.document_id, check_id, findings, pages, export_root)
            payload = {
                "question": question,
                "title": title,
                "check_id": check_id,
                "layer": check_id.split(".", 1)[0],
                "issue_class": issue_class,
                "classification": state.classification.to_json() if state is not None else None,
                "repair_id": repair.id,
                "run_id": repair.run_id,
                "finding_count": len(findings),
                "finding_keys": [finding["key"] for finding in findings][:50],
                "tried": list(state.paths) if state is not None else [],
                "ticket_id": state.ticket_id if state is not None else None,
                "evidence": [self._evidence(finding) for finding in findings[:MAX_EVIDENCE_SAMPLES]],
                "images": images,
                "answers": {"yes": "是", "no": "否"},
            }
            existing = self.session.scalars(
                select(Approval).where(
                    Approval.document_id == ctx.document_id,
                    Approval.kind == QUESTION_KIND,
                    Approval.status == ApprovalStatus.PENDING,
                )
            ).all()
            match = next((approval for approval in existing if (approval.payload_json or {}).get("check_id") == check_id), None)
            if match is not None:
                match.payload_json = payload
                approval_ids.append(match.id)
                continue
            approval = Approval(
                document_id=ctx.document_id,
                turn_id=None,
                kind=QUESTION_KIND,
                payload_json=payload,
                status=ApprovalStatus.PENDING,
                policy_id="pilot_repair.escalation",
            )
            self.session.add(approval)
            self.session.flush()
            approval_ids.append(approval.id)
        return approval_ids

    def _evidence_root(self, ctx: RepairContext) -> Path | None:
        try:
            workflow = ctx.workflow()
            return Path(workflow.export_service.output_root) / ctx.document_id / EVIDENCE_DIR
        except Exception:
            return None

    def _page_images(self, document_id: str, check_id: str, findings: Sequence[dict[str, Any]], pages: Any, root: Path | None) -> list[str]:
        """A picture of the page for the reader: the problem's block outlined in red (PDF books only)."""
        if pages is None or root is None:
            return []
        import fitz

        images: list[str] = []
        seen: set[int] = set()
        for finding in findings:
            location = finding.get("location") or {}
            page = location.get("page")
            if not isinstance(page, int) or page in seen or not 1 <= page <= pages.document.page_count or len(seen) >= 2:
                continue
            seen.add(page)
            try:
                root.mkdir(parents=True, exist_ok=True)
                pdf_page = pages.document[page - 1]
                block = self.session.get(Block, location.get("block_id")) if location.get("block_id") else None
                regions = (((block.source_span_json or {}).get("source_bbox_json") or {}).get("regions") or []) if block is not None else []
                shape = pdf_page.new_shape()
                for region in regions:
                    if isinstance(region, dict) and region.get("page_number") == page and len(region.get("bbox") or []) == 4:
                        shape.draw_rect(fitz.Rect(*region["bbox"]))
                shape.finish(color=(0.85, 0.1, 0.1), width=2)
                pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                shape_commit = getattr(shape, "commit", None)
                if regions and shape_commit is not None:
                    shape_commit()
                    pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                name = f"{check_id.replace('.', '-')}-p{page}.png"
                pixmap.save(str(root / name))
                images.append(name)
            except Exception:
                logger.warning("pilot repair: no page image for %s p%s", document_id, page, exc_info=True)
        return images

    def _open_pdf(self, document_id: str):
        from book_agent.services.parse_health import PDF_SOURCE_TYPES, _PdfPages

        document = self.session.get(Document, document_id)
        if document is None or document.source_type not in PDF_SOURCE_TYPES or not document.source_path:
            return None
        if not Path(document.source_path).exists():
            return None
        try:
            return _PdfPages(Path(document.source_path))
        except Exception:
            logger.warning("pilot repair: cannot open %s", document.source_path, exc_info=True)
            return None

    def _finish(
        self,
        repair: PilotRepair,
        final_report: PilotQualityReport | None,
        final_layers: list[dict[str, Any]],
        initial: Sequence[dict[str, Any]],
        classes: dict[str, _ClassState],
        stop_reason: str | None,
        escalated: list[str],
        budget: RepairBudget,
    ) -> None:
        attempts = self.session.scalars(select(PilotRepairAttempt).where(PilotRepairAttempt.repair_id == repair.id)).all()
        outcomes = Counter(attempt.outcome for attempt in attempts)
        final_keys = {finding["key"] for finding in findings_of(final_layers)}
        repaired = sorted({key for attempt in attempts if attempt.outcome == "kept" for key in attempt.resolved_keys_json or []} - final_keys)
        counts = Counter(finding["severity"] for finding in findings_of(final_layers))
        initial_counts = Counter(finding["severity"] for finding in initial)
        repair.status = "finished"
        repair.after_report_id = final_report.id if final_report is not None else None
        repair.stop_reason = stop_reason if initial else "clean"
        repair.repaired_count = len(repaired)
        repair.escalated_count = len(escalated)
        repair.remaining_blocking = int(counts.get(BLOCKING, 0))
        repair.remaining_major = int(counts.get(MAJOR, 0))
        repair.outcome = (
            "nothing_to_do" if not initial else "passed" if not counts.get(BLOCKING) else "escalated"
        )
        repair.finished_at = _utcnow()
        repair.summary_json = {
            "attempts": len(attempts),
            "outcomes": dict(outcomes),
            "before": {"blocking": int(initial_counts.get(BLOCKING, 0)), "major": int(initial_counts.get(MAJOR, 0))},
            "after": {"blocking": int(counts.get(BLOCKING, 0)), "major": int(counts.get(MAJOR, 0))},
            "repaired_keys": repaired[:200],
            "approval_ids": escalated,
            "budget": {
                "budget_usd": budget.budget_usd,
                "spent_usd": round(budget.spent_usd, 6),
                "retranslated_packets": budget.packets,
                "max_packets": budget.max_packets,
            },
            "classes": [
                {
                    "issue_class": state.issue_class,
                    "check_id": state.check_id,
                    "title": CHECKS[state.check_id].title if state.check_id in CHECKS else state.check_id,
                    "classification": state.classification.to_json(),
                    "findings_before": state.findings_before,
                    "kept": state.kept,
                    "paths": list(state.paths),
                    "ticket_id": state.ticket_id,
                }
                for state in classes.values()
            ],
        }


def alignment_anomalies(session: Session, chapter_ids: Sequence[str]) -> int:
    """Alignment breakage the export's check would flag in these chapters (export/alignment.py):
    translation segments no sentence renders, and sentences whose only translations are inactive.

    Sentences with no translation at all are left out: that is the QA's untranslated check, and
    retranslating them is what a repair does.
    """
    from book_agent.export.alignment import build_export_misalignment_evidence
    from book_agent.infra.repositories.export import ExportRepository

    repository = ExportRepository(session)
    total = 0
    for chapter_id in chapter_ids:
        evidence = build_export_misalignment_evidence(repository.load_chapter_bundle(chapter_id))
        total += len(evidence.sentence_ids_with_only_inactive_targets) + len(evidence.orphan_target_segment_ids)
    return total


def spacing_convention(session: Session, chapter_ids: Sequence[str]) -> str | None:
    """The pilot's own CJK–Latin spacing, by majority: "spaced", "unspaced", or None without any."""
    from book_agent.domain.models import Sentence
    from book_agent.domain.quality.pilot_qa import _boundaries
    from book_agent.infra.repositories.review import active_target_texts

    sentence_ids = list(
        session.scalars(
            select(Sentence.id).where(
                Sentence.chapter_id.in_(list(chapter_ids) or ["-"]),
                Sentence.translatable.is_(True),
                Sentence.retired_by_revision_id.is_(None),
            )
        ).all()
    )
    spaced = unspaced = 0
    for text in active_target_texts(session, sentence_ids).values():
        with_space, without = _boundaries(text or "")
        spaced += with_space
        unspaced += without
    if not spaced and not unspaced:
        return None
    return "spaced" if spaced >= unspaced else "unspaced"


# --- reading ------------------------------------------------------------------------------------------


def latest_repair(session: Session, document_id: str, *, scope: str = SCOPE_PILOT) -> PilotRepair | None:
    return session.scalars(
        select(PilotRepair).where(PilotRepair.document_id == document_id, PilotRepair.scope == scope).order_by(PilotRepair.created_at.desc(), PilotRepair.id.desc()).limit(1)
    ).first()


def repair_for_run(session: Session, run_id: str) -> PilotRepair | None:
    return session.scalars(
        select(PilotRepair).where(PilotRepair.run_id == run_id).order_by(PilotRepair.created_at.desc(), PilotRepair.id.desc()).limit(1)
    ).first()


def open_questions(session: Session, document_id: str) -> list[Approval]:
    return list(
        session.scalars(
            select(Approval)
            .where(Approval.document_id == document_id, Approval.kind == QUESTION_KIND, Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at)
        ).all()
    )


def overview_summary(session: Session, repair: PilotRepair | None) -> dict[str, Any] | None:
    """What the pilot card says of the repair: what was repaired, what is left, what waits on the reader.

    ``repair`` is the latest; what was repaired adds up every repair of the book (a repair run again
    by hand after a fix builds on the first), what is left is the latest's.
    """
    if repair is None:
        return None
    summary = repair.summary_json or {}
    finished = list(
        session.scalars(
            select(PilotRepair)
            .where(
                PilotRepair.document_id == repair.document_id,
                PilotRepair.status == "finished",
                PilotRepair.scope == repair.scope,
            )
            .order_by(PilotRepair.created_at)
        ).all()
    )
    repaired = sum(int(item.repaired_count or 0) for item in finished)
    kept: Counter[str] = Counter()
    for item in finished:
        for entry in (item.summary_json or {}).get("classes") or []:
            if int(entry.get("kept") or 0) > 0:
                kept[str(entry.get("title") or entry.get("check_id"))] += int(entry.get("kept") or 0)
    questions = open_questions(session, repair.document_id)
    left = int(repair.remaining_blocking or 0) + int(repair.remaining_major or 0)
    if repair.status == "running":
        text = "正在自动修复全书里的问题…" if repair.scope == SCOPE_BOOK else "正在自动修复试译里的问题…"
    elif repair.status == "error":
        text = f"自动修复没能完成，{'全书' if repair.scope == SCOPE_BOOK else '试译'}照常给你看，问题都列在下面。"
    elif repair.outcome == "nothing_to_do" and not repaired:
        text = "质检没有发现需要修复的严重或一般问题。"
    else:
        parts = [f"自动修复了 {repaired} 个问题" if repaired else "自动修复没能解决这些问题"]
        if repair.remaining_blocking:
            parts.append(
                f"还有 {repair.remaining_blocking} 个严重问题需要你看" + (f"（{len(questions)} 个问题等你回答）" if questions else "")
            )
        elif left:
            parts.append(f"还有 {left} 个一般问题，不影响阅读")
        else:
            parts.append("没有留下严重或一般问题")
        text = "，".join(parts) + "。"
    return {
        "repair_id": repair.id,
        "scope": repair.scope,
        "status": repair.status,
        "outcome": repair.outcome,
        # What kinds of problem were repaired, most first ("同一术语用了几种译法", ...).
        "repaired_kinds": [kind for kind, _count in kept.most_common(5)],
        "stop_reason": repair.stop_reason,
        "rounds": repair.rounds,
        "repaired": repaired,
        "remaining": {"blocking": int(repair.remaining_blocking or 0), "major": int(repair.remaining_major or 0)},
        "questions": len(questions),
        "attempts": int(summary.get("attempts") or 0),
        "before": summary.get("before"),
        "after": summary.get("after"),
        "cost_usd": round(sum(float(item.cost_usd or 0) for item in finished) or float(repair.cost_usd or 0), 4),
        "text": text,
        "finished_at": repair.finished_at.isoformat() if repair.finished_at else None,
    }


def repair_payload(session: Session, repair: PilotRepair) -> dict[str, Any]:
    attempts = session.scalars(
        select(PilotRepairAttempt).where(PilotRepairAttempt.repair_id == repair.id).order_by(PilotRepairAttempt.ordinal)
    ).all()
    # A ticket stays with the repair that filed it; later repairs that saw the defect again are in its impact.
    tickets = [
        ticket
        for ticket in session.scalars(
            select(PilotDefectTicket).where(PilotDefectTicket.document_id == repair.document_id).order_by(PilotDefectTicket.created_at)
        ).all()
        if ticket.repair_id == repair.id or repair.id in ((ticket.impact_json or {}).get("repair_ids") or [])
    ]
    return {
        **(overview_summary(session, repair) or {}),
        "document_id": repair.document_id,
        "run_id": repair.run_id,
        "before_report_id": repair.before_report_id,
        "after_report_id": repair.after_report_id,
        "budget_usd": float(repair.budget_usd) if repair.budget_usd is not None else None,
        "error": repair.error_text,
        "summary": repair.summary_json or {},
        "attempts": [
            {
                "attempt_id": attempt.id,
                "round": attempt.round,
                "ordinal": attempt.ordinal,
                "check_id": attempt.check_id,
                "severity": attempt.severity,
                "finding_keys": attempt.finding_keys_json,
                "classification": attempt.classification,
                "classification_rule": attempt.classification_rule,
                "classification_reason": attempt.classification_reason,
                "tool": attempt.tool,
                "path": attempt.path,
                "params": attempt.params_json,
                "decided_by": attempt.decided_by,
                "packet_ids": attempt.packet_ids_json,
                "block_ids": attempt.block_ids_json,
                "before": attempt.before_json,
                "after": attempt.after_json,
                "resolved_keys": attempt.resolved_keys_json,
                "outcome": attempt.outcome,
                "reason": attempt.reason,
                "ticket_id": attempt.ticket_id,
                "cost_usd": float(attempt.cost_usd or 0),
                "token_in": attempt.token_in,
                "token_out": attempt.token_out,
                "duration_ms": attempt.duration_ms,
            }
            for attempt in attempts
        ],
        "tickets": [ticket_payload(ticket) for ticket in tickets],
        "questions": [question_payload(approval) for approval in open_questions(session, repair.document_id)],
    }


def ticket_payload(ticket: PilotDefectTicket) -> dict[str, Any]:
    return {
        "ticket_id": ticket.id,
        "document_id": ticket.document_id,
        "repair_id": ticket.repair_id,
        "check_id": ticket.check_id,
        "layer": ticket.layer,
        "issue_class": ticket.issue_class,
        "classification_rule": ticket.classification_rule,
        "component": ticket.component,
        "title": ticket.title,
        "suspected_cause": ticket.suspected_cause,
        "reproduction": ticket.reproduction,
        "evidence": ticket.evidence_json,
        "impact": ticket.impact_json,
        "workaround": ticket.workaround_json,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
    }


def question_payload(approval: Approval) -> dict[str, Any]:
    payload = approval.payload_json or {}
    return {
        "approval_id": approval.id,
        "question": payload.get("question"),
        "title": payload.get("title"),
        "check_id": payload.get("check_id"),
        "finding_count": payload.get("finding_count"),
        "evidence": payload.get("evidence") or [],
        "images": payload.get("images") or [],
        "tried": payload.get("tried") or [],
    }


def document_is_pdf(document: Document | None) -> bool:
    return document is not None and document.source_type in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}


def heal_alignment(session: Session, workflow: Any, document_id: str) -> dict[str, Any]:
    """Heal translations left misaligned (ALIGNMENT_FAILURE, export_time_misalignment) and re-check.

    For each packet an open export ALIGNMENT_FAILURE names: when its latest successful attempt
    translates every current sentence of the packet, the other attempts' active segments are
    superseded (what the repair and the fork now do as they go). Then each chapter's export gate
    runs again, which resolves the issues it no longer finds. A packet whose latest attempt does
    not cover it is left alone and reported: it needs a retranslation.
    """
    from book_agent.domain.enums import IssueStatus, PacketSentenceRole, RootCauseLayer, RunStatus, TargetSegmentStatus
    from book_agent.domain.models import Sentence
    from book_agent.domain.models.review import ReviewIssue
    from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TargetSegment, TranslationRun
    from book_agent.infra.repositories.translation import TranslationRepository

    from book_agent.services.parse_revision_fork import ParseRevisionForkService

    # Packets an earlier merge left on the same block: one stays, the others stop claiming its sentences.
    collapsed = ParseRevisionForkService(session).collapse_document_duplicates(document_id)
    issues = session.scalars(
        select(ReviewIssue).where(
            ReviewIssue.document_id == document_id,
            ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
            ReviewIssue.root_cause_layer == RootCauseLayer.EXPORT,
            ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED)),
        )
    ).all()
    healed: list[dict[str, Any]] = []
    skipped: list[str] = []
    chapters: set[str] = set()
    for issue in issues:
        if not issue.packet_id:
            continue
        chapters.add(str(issue.chapter_id))
        latest = session.scalars(
            select(TranslationRun)
            .where(TranslationRun.packet_id == issue.packet_id, TranslationRun.status == RunStatus.SUCCEEDED)
            .order_by(TranslationRun.attempt.desc())
            .limit(1)
        ).first()
        current = set(
            session.scalars(
                select(Sentence.id)
                .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
                .where(
                    PacketSentenceMap.packet_id == issue.packet_id,
                    PacketSentenceMap.role == PacketSentenceRole.CURRENT,
                    Sentence.translatable.is_(True),
                    Sentence.retired_by_revision_id.is_(None),
                )
            ).all()
        )
        covered = (
            set(
                session.scalars(
                    select(AlignmentEdge.sentence_id)
                    .join(TargetSegment, TargetSegment.id == AlignmentEdge.target_segment_id)
                    .where(TargetSegment.translation_run_id == latest.id, TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
                ).all()
            )
            if latest is not None
            else set()
        )
        if latest is None or not current <= covered:
            skipped.append(str(issue.packet_id))
            continue
        superseded = TranslationRepository(session).supersede_other_attempts(str(issue.packet_id), latest.id)
        healed.append({"packet_id": str(issue.packet_id), "kept_attempt": latest.attempt, "superseded_segments": superseded})
    session.flush()
    for chapter_id in sorted(chapters):
        workflow._revalidate_export_gate(chapter_id)
    session.flush()
    still_open = [
        str(issue.id)
        for issue in session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.issue_type == "ALIGNMENT_FAILURE",
                ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED)),
            )
        ).all()
    ]
    return {"healed": healed, "needs_retranslation": skipped, "open_alignment_issues": still_open, "collapsed_duplicate_packets": collapsed}


def reaudit_pilot_exports(session: Session, document_id: str) -> dict[str, Any]:
    """Audit the pilot's reading copies again on the pilot's chapters (resolves whole-book false issues).

    The latest merged export and the pilot chapters' bilingual files next to it; nothing is exported.
    """
    from book_agent.domain.enums import ExportStatus
    from book_agent.domain.models.review import Export
    from book_agent.services.export_qa import ExportQaService

    chapter_ids = pilot_chapter_ids(session, document_id)
    merged = session.scalars(
        select(Export)
        .where(Export.document_id == document_id, Export.export_type == ExportType.MERGED_HTML, Export.status == ExportStatus.SUCCEEDED)
        .order_by(Export.updated_at.desc(), Export.id.desc())
        .limit(1)
    ).first()
    if merged is None or not merged.file_path or not Path(merged.file_path).is_file():
        return {"error": "no merged export file to audit", "chapter_ids": chapter_ids}
    merged_path = Path(merged.file_path)
    service = ExportQaService(session)
    results = [
        service.audit(document_id, ExportType.MERGED_HTML, [(None, merged_path)], chapter_ids=chapter_ids).to_json(),
        service.audit(
            document_id,
            ExportType.BILINGUAL_HTML,
            [(chapter_id, merged_path.parent / f"bilingual-{chapter_id}.html") for chapter_id in chapter_ids],
            chapter_ids=chapter_ids,
        ).to_json(),
    ]
    return {"chapter_ids": chapter_ids, "audits": results}
