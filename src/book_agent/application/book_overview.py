"""Where a book stands, for a reader: the one place the UI reads a book's state from.

Built on the run control projection (``RunControlService.get_run_summary`` re-derives stage
statuses from packets and work items on every read) and the run plan, so the frontend no longer
derives stages, badges or next actions itself (docs/specs/ui-ux-overhaul/SPEC.md, decision 3).

Four independent parts, because one book can be readable while a reader-started export fails:

- ``readable``: is there a Chinese version to read, in which formats;
- ``translation``: the current *full translation* run only (exports and reviews say nothing about it);
- ``activity``: exports running in the background;
- ``needs_you``: what waits on a person (pending approvals; pauses only a person can fix).

``parse_health`` summarises the latest parse health report (services/parse_health.py): a blocking
finding is shown as worth fixing before translation, but does not stop a translation from starting.

``pilot`` is the free two-chapter trial (services/pilot.py): not started, running, ready for the
reader to review with the problems it left, or followed by the whole book (paid) - or skipped, or
not offered for this book. ``translation`` is the current translation run, the whole book's once it
exists and the pilot's before (``translation.scope`` says which), so progress, stop, resume and
retry work the same for both. ``readable`` is about the whole book; the pilot's reading copy is
``pilot.readable``.

``inbox_items`` lists ``needs_you`` across the caller's books with the same code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.core.config import Settings
from book_agent.domain.enums import (
    ApprovalStatus,
    DocumentRunStatus,
    DocumentRunType,
    ExportStatus,
    ExportType,
    IssueStatus,
    PacketStatus,
    SourceType,
)
from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Chapter, Document, Event
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import Export, ReviewIssue
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.orchestrator.pipeline_stage_cache import read_cached_stages
from book_agent.orchestrator.run_plan import RUN_REQUEST_KEY, plan_for_run
from book_agent.services.parse_health import latest_report, overview_summary
from book_agent.services.pilot import UNAVAILABLE_TEXT, latest_book_run, latest_pilot_run, select_pilot_chapters
from book_agent.services.run_control import RunControlService

SETTINGS_LINK = {"target": "/settings", "label": "去设置"}

# Why a run stopped, in words a reader can act on; ``needs_person`` pauses go to needs_you.
# Other reasons (provider.transport, malformed_response, output_truncated...) retry by themselves
# (workers/failures.py) and are never shown as something to do.
STOP_REASONS: dict[str, dict[str, Any]] = {
    "provider.not_configured": {
        "text": "还没有可用的模型服务商。在设置里添加并启用一个服务商后，翻译会自动接着进行。",
        "link": SETTINGS_LINK,
        "needs_person": True,
    },
    "provider.insufficient_balance": {
        "text": "模型服务商账户余额不足。充值后点「继续翻译」，会从停下的地方接着翻译，已完成的部分不会重做。",
        "needs_person": True,
    },
    "provider.authentication_failed": {
        "text": "模型服务商拒绝了 API key（无效、过期或没有权限）。在设置里更新 key 后点「继续翻译」。",
        "link": SETTINGS_LINK,
        "needs_person": True,
    },
    "provider.reasoning_exhausted_output": {
        "text": "思考模型把输出额度都用在了思考上，没有写出译文。在设置里关闭思考模式或调大最大输出 token，然后点「继续翻译」。",
        "link": SETTINGS_LINK,
        "needs_person": True,
    },
    "billing.credit_exhausted": {
        "text": "预付余额已用完，翻译已暂停。充值后点「继续翻译」，已完成的部分不会重复扣费。",
        "needs_person": True,
    },
    "budget.org_monthly_exhausted": {
        "text": "本组织本月的模型花费已达上限。提高月度预算或等到下个月后点「继续翻译」。",
        "needs_person": True,
    },
    "budget.cost_exceeded": {"text": "这次翻译的花费达到了设定的上限。调高上限后点「继续翻译」。", "needs_person": True},
    "budget.token_in_exceeded": {"text": "这次翻译的输入 token 达到了设定的上限。调高上限后点「继续翻译」。", "needs_person": True},
    "budget.token_out_exceeded": {"text": "这次翻译的输出 token 达到了设定的上限。调高上限后点「继续翻译」。", "needs_person": True},
    "budget.wall_clock_exceeded": {"text": "这次翻译超过了设定的最长时长。点「继续翻译」接着跑。", "needs_person": True},
    "budget.no_progress_exceeded": {
        "text": "翻译长时间没有进展，已自动暂停。通常是模型服务暂时不可用，稍后点「继续翻译」重试。",
        "needs_person": True,
    },
    "budget.consecutive_failures_exceeded": {
        "text": "连续多次调用失败，已自动暂停，避免继续花钱。检查服务商状态后点「继续翻译」。",
        "link": SETTINGS_LINK,
        "needs_person": True,
    },
    "stage.evidence_failed": {"text": "有一个必需步骤没有完成。点「重试」只重做失败的部分。"},
    "runner.unhandled_exception": {"text": "翻译遇到了内部错误。点「重试」；如果反复出现，请联系管理员。"},
}


def stop_reason_text(stop_reason: str | None, status: str | None) -> str | None:
    if not stop_reason or status not in {DocumentRunStatus.PAUSED.value, DocumentRunStatus.FAILED.value}:
        return None
    known = STOP_REASONS.get(stop_reason)
    if known:
        return known["text"]
    return f"翻译已{'暂停' if status == DocumentRunStatus.PAUSED.value else '停止'}（{stop_reason}）。"


def pause_needs_person(stop_reason: str | None) -> bool:
    if not stop_reason:
        return False
    known = STOP_REASONS.get(stop_reason)
    if known is not None:
        return bool(known.get("needs_person"))
    return stop_reason.startswith(("budget.", "billing."))


# The steps a reader sees, in order. ``web`` steps exist for web pages only.
STEPS: tuple[tuple[str, str], ...] = (
    ("structure_review", "检查版面"),
    ("terminology", "确定术语"),
    ("translate", "翻译"),
    ("model_review", "模型审校"),
    ("review", "自动复核"),
    ("repair", "自动修复"),
    ("bilingual_html", "生成中英对照"),
    ("merged_html", "生成中文版"),
    ("web_html", "生成中文网页"),
    ("export_review", "检查导出"),
    ("pilot_qa", "试译质检"),
    ("pilot_repair", "自动修复试译"),
    ("book_qa", "全书质检"),
    ("book_repair", "自动修复全书"),
)
STEP_LABELS = dict(STEPS)

_ACTIVE = {DocumentRunStatus.QUEUED.value, DocumentRunStatus.RUNNING.value, DocumentRunStatus.DRAINING.value}
_READING_FORMATS = (ExportType.MERGED_HTML, ExportType.BILINGUAL_HTML, ExportType.MERGED_MARKDOWN)


@dataclass(slots=True)
class OverviewContext:
    settings: Settings
    pdf_renderer: bool


def _succeeded_export_types(session: Session, document_id: str) -> set[str]:
    """Export types that exist for the whole book: a pilot's merged export holds two chapters only."""
    rows = session.execute(
        select(Export.export_type, Export.input_version_bundle_json).where(
            Export.document_id == document_id, Export.status == ExportStatus.SUCCEEDED
        )
    ).all()
    return {
        export_type.value if hasattr(export_type, "value") else str(export_type)
        for export_type, bundle in rows
        if not (bundle or {}).get("chapter_scope")
    }


def _pilot_readable(session: Session, document_id: str) -> bool:
    """The pilot's reading copy (its merged export, or the book's once the whole book is exported)."""
    return (
        session.scalar(
            select(func.count(Export.id)).where(
                Export.document_id == document_id,
                Export.export_type == ExportType.MERGED_HTML,
                Export.status == ExportStatus.SUCCEEDED,
            )
        )
        or 0
    ) > 0


def current_translation_run(session: Session, document_id: str) -> DocumentRun | None:
    """The whole book's latest translation run, else the pilot's."""
    return latest_book_run(session, document_id) or latest_pilot_run(session, document_id)


def _readable(exported: set[str], web: bool) -> bool:
    return (ExportType.WEB_HTML.value if web else ExportType.MERGED_HTML.value) in exported


def _packet_progress(session: Session, document_id: str, packet_ids: frozenset[str] | None = None) -> dict[str, int]:
    """Translated packets of the book, or of a pilot's scope."""
    statement = (
        select(TranslationPacket.status, func.count(TranslationPacket.id))
        .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
        .where(Chapter.document_id == document_id)
        .group_by(TranslationPacket.status)
    )
    if packet_ids is not None:
        statement = statement.where(TranslationPacket.id.in_(sorted(packet_ids)))
    rows = session.execute(statement).all()
    counts = {status.value if hasattr(status, "value") else str(status): int(count) for status, count in rows}
    counts.pop(PacketStatus.INVALIDATED.value, None)
    return {"done": counts.get(PacketStatus.TRANSLATED.value, 0), "total": sum(counts.values())}


def _steps(summary, run: DocumentRun, source_type: SourceType) -> list[dict[str, Any]]:
    plan = plan_for_run(run.run_type, run.status_detail_json)
    stages = read_cached_stages((summary.status_detail_json or {}).get("pipeline")) or {}
    finished = summary.status in {DocumentRunStatus.SUCCEEDED.value, DocumentRunStatus.SUCCEEDED_WITH_WARNINGS.value}
    steps: list[dict[str, Any]] = []
    for key, label in STEPS:
        if key == "web_html" and source_type != SourceType.WEB:
            continue
        if key not in plan.stages:
            # Optional steps this run did not include stay out of a reader's way.
            continue
        detail = stages.get(key) or {}
        status = str(detail.get("status") or ("succeeded" if finished else "pending"))
        if status == "running" and detail.get("turn_status") == "awaiting_approval":
            status = "awaiting_approval"
        # When the step last changed: the progress tab shows it as the step's time.
        steps.append({"key": key, "label": label, "status": status, "at": detail.get("updated_at")})
    return steps


def _approval_title(approval: Approval) -> str:
    arguments = (approval.payload_json or {}).get("arguments") or {}
    if approval.kind == "lock_term":
        return f"锁定术语：{arguments.get('source_term', '')} → {arguments.get('target_term', '')}"
    if approval.kind == "budget_extension":
        return "代理用完了预算，是否继续"
    return str((approval.payload_json or {}).get("tool") or approval.kind)


def _question_item(document: Document, approval: Approval) -> dict[str, Any]:
    """A problem the pilot repair could not fix (services/pilot_repair.py): one yes/no question, with its evidence."""
    payload = approval.payload_json or {}
    evidence = payload.get("evidence") or []
    pages = sorted({item["page"] for item in evidence if isinstance(item.get("page"), int)})
    where = f"第 {'、'.join(str(page) for page in pages[:4])} 页，" if pages else ""
    sample = next((str(item.get("source")) for item in evidence if item.get("source")), "")
    detail = f"{payload.get('title') or ''}：{where}共 {payload.get('finding_count') or len(evidence)} 处，自动修复没有解决。"
    if sample:
        detail += f"原文：「{sample[:80]}」"
    return {
        "kind": "question",
        "id": approval.id,
        "title": str(payload.get("question") or payload.get("title") or "试译里有一个问题需要你决定"),
        "detail": detail,
        "images": [
            f"/documents/{document.id}/pilot-repair/evidence/{name}" for name in (payload.get("images") or [])
        ],
        "action": {
            "kind": "approve",
            "approve_label": (payload.get("answers") or {}).get("yes") or "是",
            "reject_label": (payload.get("answers") or {}).get("no") or "否",
            "target": f"/books/{document.id}/progress#pilot-quality",
            "label": "看质检报告",
        },
    }


def _needs_you(session: Session, document: Document, translation_run: DocumentRun | None) -> list[dict[str, Any]]:
    from book_agent.services.pilot_repair import QUESTION_KIND

    items: list[dict[str, Any]] = [
        _question_item(document, approval)
        if approval.kind == QUESTION_KIND
        else {
            "kind": "approval",
            "id": approval.id,
            "title": _approval_title(approval),
            "detail": "批准后翻译会继续。",
            "action": {"kind": "approve"},
        }
        for approval in session.scalars(
            select(Approval)
            .where(Approval.document_id == document.id, Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at)
        ).all()
    ]
    if (
        translation_run is not None
        and translation_run.status == DocumentRunStatus.PAUSED
        and pause_needs_person(translation_run.stop_reason)
    ):
        known = STOP_REASONS.get(translation_run.stop_reason or "", {})
        items.append(
            {
                "kind": "paused",
                "id": translation_run.id,
                "title": "翻译暂停了，需要你处理",
                "detail": stop_reason_text(translation_run.stop_reason, DocumentRunStatus.PAUSED.value),
                "action": {"kind": "link", **known["link"]} if known.get("link") else {"kind": "resume"},
            }
        )
    return items


def _spent_usd(session: Session, run_ids: list[str]) -> float:
    if not run_ids:
        return 0.0
    return float(
        session.scalar(
            select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                Event.kind == LLM_CALL_COMPLETED, Event.run_id.in_(run_ids)
            )
        )
        or 0.0
    )


_PILOT_ACTIVE_OR_STOPPED = {
    DocumentRunStatus.QUEUED.value: "queued",
    DocumentRunStatus.RUNNING.value: "running",
    DocumentRunStatus.DRAINING.value: "draining",
    DocumentRunStatus.PAUSED.value: "paused",
    DocumentRunStatus.FAILED.value: "failed",
    DocumentRunStatus.CANCELLED.value: "cancelled",
}


def _pilot_problems(session: Session, document_id: str, chapter_ids: list[str]) -> dict[str, Any]:
    """Open review issues in the pilot's chapters (rule and model review), by type, blocking first."""
    if not chapter_ids:
        return {"open": 0, "blocking": 0, "by_type": []}
    rows = session.execute(
        select(ReviewIssue.issue_type, ReviewIssue.blocking, func.count(ReviewIssue.id))
        .where(
            ReviewIssue.document_id == document_id,
            ReviewIssue.chapter_id.in_(chapter_ids),
            ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED)),
        )
        .group_by(ReviewIssue.issue_type, ReviewIssue.blocking)
    ).all()
    by_type: dict[str, dict[str, Any]] = {}
    for issue_type, blocking, count in rows:
        entry = by_type.setdefault(str(issue_type), {"type": str(issue_type), "count": 0, "blocking": 0})
        entry["count"] += int(count)
        if blocking:
            entry["blocking"] += int(count)
    ordered = sorted(by_type.values(), key=lambda entry: (-entry["blocking"], -entry["count"], entry["type"]))
    return {
        "open": sum(entry["count"] for entry in ordered),
        "blocking": sum(entry["blocking"] for entry in ordered),
        "by_type": ordered,
    }


def _pilot(session: Session, document: Document, pilot_run: DocumentRun | None, book_run: DocumentRun | None) -> dict[str, Any]:
    """The free pilot: whether it is offered, where it stands, its chapters and the problems it left."""
    from book_agent.services.billing_category import paid_by_us
    from book_agent.services.pilot_quality import latest_report as latest_quality_report
    from book_agent.services.pilot_quality import overview_summary as quality_summary
    from book_agent.services.pilot_repair import latest_repair
    from book_agent.services.pilot_repair import overview_summary as repair_summary

    if pilot_run is not None:
        request = (pilot_run.status_detail_json or {}).get(RUN_REQUEST_KEY) or {}
        chapter_ids = [str(chapter_id) for chapter_id in request.get("chapter_ids") or []]
        available, reason = True, None
    else:
        selection = select_pilot_chapters(session, document.id)
        chapter_ids = selection.chapter_ids
        available, reason = selection.available, selection.unavailable_reason
    if book_run is not None:
        state = "book_started" if pilot_run is not None else "skipped"
    elif pilot_run is not None:
        status = pilot_run.status.value
        state = "ready" if status in {DocumentRunStatus.SUCCEEDED.value, DocumentRunStatus.SUCCEEDED_WITH_WARNINGS.value} else _PILOT_ACTIVE_OR_STOPPED.get(status, status)
    else:
        state = "not_started" if available else "unavailable"
    chapters = {
        str(chapter.id): chapter
        for chapter in (session.scalars(select(Chapter).where(Chapter.id.in_(chapter_ids))).all() if chapter_ids else [])
    }
    cost_usd = 0.0
    if pilot_run is not None:
        cost_usd = float(
            session.scalar(
                select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                    Event.kind == LLM_CALL_COMPLETED, paid_by_us([str(pilot_run.id)])
                )
            )
            or 0.0
        )
    return {
        "available": available,
        "unavailable_reason": reason,
        "unavailable_text": UNAVAILABLE_TEXT.get(reason or "") if reason else None,
        "state": state,
        "run_id": pilot_run.id if pilot_run is not None else None,
        "chapters": [
            {
                "chapter_id": chapter_id,
                "ordinal": chapters[chapter_id].ordinal,
                "title": chapters[chapter_id].title_tgt or chapters[chapter_id].title_src,
            }
            for chapter_id in chapter_ids
            if chapter_id in chapters
        ],
        "readable": pilot_run is not None and state in {"ready", "book_started"} and _pilot_readable(session, document.id),
        # Only once there is something to judge: the review issues of the pilot's chapters.
        "problems": _pilot_problems(session, document.id, chapter_ids) if pilot_run is not None else None,
        # The layered QA (L1–L7) of the pilot's chapters: its verdict and each layer's counts.
        "quality": quality_summary(latest_quality_report(session, document.id)),
        # The repair loop after the QA: what it repaired, what is left, the questions it asked.
        "repair": repair_summary(session, latest_repair(session, document.id)) if pilot_run is not None else None,
        # What the pilot's model calls cost us; the reader is never charged for them.
        "cost_usd": round(cost_usd, 4),
        "finished_at": pilot_run.finished_at.isoformat() if pilot_run is not None and pilot_run.finished_at else None,
    }


def _cost(session: Session, document: Document, context: OverviewContext, *, after_pilot: bool = False) -> dict[str, Any]:
    from book_agent.domain.models.auth import DEFAULT_ORG_ID
    from book_agent.services.billing_category import pilot_run_ids
    from book_agent.services.cost_estimate import estimate_document_cost
    from book_agent.services.prepaid_credit import credit_status

    estimate_range: list[float] | None = None
    try:
        # After a pilot the whole book costs what is left: its chapters are done, its terminology too.
        estimate = (
            estimate_document_cost(session, document.id, context.settings, terminology="skip", pending_only=True)
            if after_pilot
            else estimate_document_cost(session, document.id, context.settings)
        )
        if estimate.cost_usd_range is not None:
            estimate_range = [float(estimate.cost_usd_range[0]), float(estimate.cost_usd_range[1])]
    except LookupError:
        pass
    multiplier = 1.0
    prepaid = False
    try:
        credit = credit_status(session, document.org_id or DEFAULT_ORG_ID, price_multiplier=context.settings.billing_price_multiplier)
        prepaid = credit.prepaid
        multiplier = credit.price_multiplier if credit.prepaid else 1.0
    except LookupError:
        pass
    pilots = set(pilot_run_ids(session, document.org_id or DEFAULT_ORG_ID))
    run_ids = [
        str(run_id)
        for run_id in session.scalars(select(DocumentRun.id).where(DocumentRun.document_id == document.id)).all()
        if str(run_id) not in pilots
    ]
    return {
        "estimate_usd_range": estimate_range,
        # The most this book may be charged: what an auto-start cap is compared with.
        "max_charge_usd": round(estimate_range[1] * multiplier, 4) if estimate_range else None,
        "prepaid": prepaid,
        # What this book's paid runs cost so far; the free pilot is not in it.
        "spent_usd": round(_spent_usd(session, run_ids), 4),
    }


def _headline(
    translation: dict[str, Any],
    readable: bool,
    needs_you: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    pilot: dict[str, Any],
) -> str:
    state = translation["state"]
    on_pilot = translation.get("scope") == "pilot"
    # A question the pilot repair left does not hold anything up: the pilot is there to read.
    blocking = [item for item in needs_you if item.get("kind") != "question"]
    if blocking:
        return f"有 {len(blocking)} 件事等你处理，处理后{'试译' if on_pilot else '翻译'}会继续。"
    if state in {"queued", "running", "draining"}:
        current = next((step for step in steps if step["status"] in {"running", "pending"}), None)
        progress = translation["progress"]
        doing = "免费试译前两章" if on_pilot else "翻译"
        if current and current["key"] == "translate" and progress["total"]:
            return f"正在{doing} · 已完成 {progress['done']}/{progress['total']} 段。"
        if on_pilot:
            return f"正在免费试译前两章：{current['label']}…" if current else "正在免费试译前两章…"
        return f"正在{current['label']}…" if current else "正在处理…"
    if state == "paused":
        return "试译已暂停。" if on_pilot else "翻译已暂停。"
    if state in {"failed", "cancelled"}:
        if on_pilot:
            return translation.get("stop_reason_text") or ("试译没有完成。" if state == "failed" else "试译已取消。")
        return translation.get("stop_reason_text") or ("翻译失败了。" if state == "failed" else "翻译已取消。")
    if pilot["state"] == "ready":
        repair = pilot.get("repair") or {}
        if repair.get("status") == "finished":
            # The card below says what was repaired and what is left; the headline says only what needs the reader.
            blocking = int((repair.get("remaining") or {}).get("blocking") or 0)
            return "试译完成：前两章可以阅读了。" + (f"还有 {blocking} 个严重问题需要你看。" if blocking else "")
        problems = pilot.get("problems") or {}
        left = int(problems.get("open") or 0)
        return "试译完成：前两章可以阅读了。" + (f"还有 {left} 个问题没有解决，列在下面。" if left else "")
    if readable:
        return "中文版已就绪。" if state != "succeeded_with_warnings" else "中文版已就绪（有些可选检查没有完成）。"
    if state == "never_started":
        if pilot["state"] == "not_started":
            return "还没开始翻译。可以先免费试译前两章，看过效果再决定是否翻译全书。"
        return "还没开始翻译。"
    return "翻译完成，正在准备中文版…"


def _primary_action(
    translation: dict[str, Any], readable: bool, needs_you: list[dict[str, Any]], pilot: dict[str, Any]
) -> dict[str, Any]:
    state = translation["state"]
    run_id = translation.get("run_id")
    on_pilot = translation.get("scope") == "pilot"
    if any(item.get("kind") != "question" for item in needs_you):
        return {"kind": "handle", "label": "去处理"}
    if state in {"queued", "running", "draining"} and translation.get("stalled"):
        return {"kind": "retry", "label": "重试", "run_id": run_id}
    if state in {"queued", "running", "draining"}:
        return {"kind": "read", "label": "阅读中文版"} if readable else {"kind": "stop", "label": "停止", "run_id": run_id}
    if state == "paused":
        return {"kind": "resume", "label": "继续试译" if on_pilot else "继续翻译", "run_id": run_id}
    if pilot["state"] == "ready":
        # The reader has read (or can read) the pilot: the choice now is the whole book.
        return {"kind": "start_book", "label": "翻译全书"}
    if readable:
        return {"kind": "read", "label": "阅读中文版"}
    if state in {"failed", "cancelled"}:
        return {"kind": "retry", "label": "重试", "run_id": run_id}
    if state == "never_started":
        if pilot["state"] == "not_started":
            return {"kind": "pilot", "label": "免费试译前两章"}
        return {"kind": "start", "label": "开始翻译"}
    return {"kind": "none", "label": ""}


def build_overview(session: Session, document_id: str, context: OverviewContext, *, include_cost: bool = True) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document not found: {document_id}")
    service = RunControlService(RunControlRepository(session))

    exported = _succeeded_export_types(session, document.id)
    web = document.source_type == SourceType.WEB
    # Readable means the reader's first format exists: the Chinese page for a web page (it is
    # generated after the merged copy; opening the reader in between would ask for it too early).
    readable_ready = _readable(exported, web)
    formats = ([ExportType.WEB_HTML.value] if web else []) + [fmt.value for fmt in _READING_FORMATS]
    if context.pdf_renderer:
        formats.append(ExportType.REBUILT_PDF.value)

    book_run = latest_book_run(session, document.id)
    pilot_run = latest_pilot_run(session, document.id)
    run = book_run or pilot_run
    if run is None:
        translation: dict[str, Any] = {
            "state": "never_started",
            "scope": None,
            "run_id": None,
            "stop_reason": None,
            "stop_reason_text": None,
            "steps": [],
        }
        progress = _packet_progress(session, document.id)
    else:
        summary = service.get_run_summary(run.id)
        request = (run.status_detail_json or {}).get(RUN_REQUEST_KEY) or {}
        plan = plan_for_run(run.run_type, run.status_detail_json)
        translation = {
            "state": summary.status,
            "scope": "pilot" if plan.is_pilot else "book",
            "run_id": run.id,
            "stop_reason": summary.stop_reason,
            "stop_reason_text": stop_reason_text(summary.stop_reason, summary.status),
            "steps": _steps(summary, run, document.source_type),
            "fast_reading": str(request.get("fast_reading") or "off").lower() in {"on", "true", "1", "yes"},
            "auto_started": bool(request.get("auto_started")),
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            # Still marked running, but a step failed and nothing has moved for a while: offer a retry.
            "stalled": service._is_stale_failed_stage_run(run.id),
        }
        progress = _packet_progress(session, document.id, plan.packet_ids if plan.is_pilot else None)
    translation["progress"] = progress
    pilot = _pilot(session, document, pilot_run, book_run)

    exporting = sorted(
        {
            str(((export_run.status_detail_json or {}).get(RUN_REQUEST_KEY) or {}).get("export_type") or ExportType.MERGED_HTML.value)
            for export_run in session.scalars(
                select(DocumentRun).where(
                    DocumentRun.document_id == document.id,
                    DocumentRun.run_type == DocumentRunType.EXPORT_FULL,
                    DocumentRun.status.in_(tuple(_ACTIVE)),
                )
            ).all()
        }
    )
    needs_you = _needs_you(session, document, run)
    issues = session.execute(
        select(func.count(ReviewIssue.id), func.count(ReviewIssue.id).filter(ReviewIssue.blocking.is_(True))).where(
            ReviewIssue.document_id == document.id, ReviewIssue.status.in_((IssueStatus.OPEN, IssueStatus.TRIAGED))
        )
    ).one()
    return {
        "document_id": document.id,
        "title": document.title_tgt or document.title or document.title_src,
        "source_type": document.source_type.value,
        "source_url": (document.metadata_json or {}).get("source_url"),
        "headline": _headline(translation, readable_ready, needs_you, translation.get("steps", []), pilot),
        "primary_action": _primary_action(translation, readable_ready, needs_you, pilot),
        "readable": {"ready": readable_ready, "formats": formats},
        "translation": translation,
        "pilot": pilot,
        "activity": {"exporting": exporting},
        "needs_you": needs_you,
        "quality": {"open_issues": int(issues[0] or 0), "blocking": int(issues[1] or 0)},
        "parse_health": overview_summary(latest_report(session, document.id)),
        # The whole book's layered QA and repair (services/book_quality.py); null before the first.
        "book_quality": _book_quality(session, document.id),
        # The estimate reads the whole book: skipped for lists of books (the home page's cards).
        # After a pilot it is what the rest of the book costs.
        "cost": _cost(session, document, context, after_pilot=pilot["state"] == "ready") if include_cost else None,
    }


def _book_quality(session: Session, document_id: str) -> dict[str, Any] | None:
    from book_agent.services.book_quality import overview

    return overview(session, document_id)


def inbox_items(session: Session, *, org_id: str | None) -> list[dict[str, Any]]:
    """Everything waiting on a person across the caller's books, oldest book first."""
    document_ids = set(
        session.scalars(select(Approval.document_id).where(Approval.status == ApprovalStatus.PENDING)).all()
    ) | set(
        session.scalars(
            select(DocumentRun.document_id).where(
                DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL, DocumentRun.status == DocumentRunStatus.PAUSED
            )
        ).all()
    )
    items: list[dict[str, Any]] = []
    for document_id in sorted(str(value) for value in document_ids):
        document = session.get(Document, document_id)
        if document is None or (org_id is not None and document.org_id != org_id):
            continue
        run = current_translation_run(session, document.id)
        title = document.title_tgt or document.title or document.title_src
        for item in _needs_you(session, document, run):
            items.append({**item, "document_id": document.id, "document_title": title})
    return items


_FINISHED = (DocumentRunStatus.SUCCEEDED, DocumentRunStatus.SUCCEEDED_WITH_WARNINGS, DocumentRunStatus.FAILED)


def inbox_updates(session: Session, *, org_id: str | None, since) -> list[dict[str, Any]]:
    """Translations that ended since ``since``: ready to read, a pilot ready to review, or failed.

    What a notification says.
    """
    from book_agent.services.billing_category import PILOT

    runs = session.scalars(
        select(DocumentRun)
        .where(
            DocumentRun.run_type == DocumentRunType.TRANSLATE_FULL,
            DocumentRun.status.in_(_FINISHED),
            DocumentRun.finished_at.is_not(None),
            DocumentRun.finished_at >= since,
        )
        .order_by(DocumentRun.finished_at)
    ).all()
    updates: list[dict[str, Any]] = []
    for run in runs:
        document = session.get(Document, run.document_id)
        if document is None or (org_id is not None and document.org_id != org_id):
            continue
        title = document.title_tgt or document.title or document.title_src
        pilot = run.billing_category == PILOT
        if run.status == DocumentRunStatus.FAILED:
            kind = "failed"
            text = stop_reason_text(run.stop_reason, DocumentRunStatus.FAILED.value) or ("试译没有完成。" if pilot else "翻译失败了。")
        elif pilot and _pilot_readable(session, document.id):
            kind, text = "pilot_ready", "免费试译完成：前两章可以阅读了，看过再决定是否翻译全书。"
        elif not pilot and _readable(_succeeded_export_types(session, document.id), document.source_type == SourceType.WEB):
            kind, text = "readable", "中文版已就绪，可以阅读了。"
        else:
            continue
        updates.append(
            {
                "kind": kind,
                "run_id": run.id,
                "document_id": document.id,
                "document_title": title,
                "text": text,
                "at": run.finished_at.isoformat(),
            }
        )
    return updates
