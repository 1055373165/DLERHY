"""Where a book stands, for a reader: the one place the UI reads a book's state from.

Built on the run control projection (``RunControlService.get_run_summary`` re-derives stage
statuses from packets and work items on every read) and the run plan, so the frontend no longer
derives stages, badges or next actions itself (docs/specs/ui-ux-overhaul/SPEC.md, decision 3).

Four independent parts, because one book can be readable while a reader-started export fails:

- ``readable``: is there a Chinese version to read, in which formats;
- ``translation``: the latest *full translation* run only (exports and reviews say nothing about it);
- ``activity``: exports running in the background;
- ``needs_you``: what waits on a person (pending approvals; pauses only a person can fix).

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
)
STEP_LABELS = dict(STEPS)

_ACTIVE = {DocumentRunStatus.QUEUED.value, DocumentRunStatus.RUNNING.value, DocumentRunStatus.DRAINING.value}
_READING_FORMATS = (ExportType.MERGED_HTML, ExportType.BILINGUAL_HTML, ExportType.MERGED_MARKDOWN)


@dataclass(slots=True)
class OverviewContext:
    settings: Settings
    pdf_renderer: bool


def _latest_run(session: Session, document_id: str, run_type: DocumentRunType) -> DocumentRun | None:
    return session.scalars(
        select(DocumentRun)
        .where(DocumentRun.document_id == document_id, DocumentRun.run_type == run_type)
        .order_by(DocumentRun.created_at.desc(), DocumentRun.id.desc())
        .limit(1)
    ).first()


def _succeeded_export_types(session: Session, document_id: str) -> set[str]:
    rows = session.scalars(
        select(Export.export_type).where(Export.document_id == document_id, Export.status == ExportStatus.SUCCEEDED).distinct()
    ).all()
    return {row.value if hasattr(row, "value") else str(row) for row in rows}


def _packet_progress(session: Session, document_id: str) -> dict[str, int]:
    rows = session.execute(
        select(TranslationPacket.status, func.count(TranslationPacket.id))
        .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
        .where(Chapter.document_id == document_id)
        .group_by(TranslationPacket.status)
    ).all()
    counts = {status.value if hasattr(status, "value") else str(status): int(count) for status, count in rows}
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
        steps.append({"key": key, "label": label, "status": status})
    return steps


def _approval_title(approval: Approval) -> str:
    arguments = (approval.payload_json or {}).get("arguments") or {}
    if approval.kind == "lock_term":
        return f"锁定术语：{arguments.get('source_term', '')} → {arguments.get('target_term', '')}"
    if approval.kind == "budget_extension":
        return "代理用完了预算，是否继续"
    return str((approval.payload_json or {}).get("tool") or approval.kind)


def _needs_you(session: Session, document: Document, translation_run: DocumentRun | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {
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


def _cost(session: Session, document: Document, context: OverviewContext) -> dict[str, Any]:
    from book_agent.domain.models.auth import DEFAULT_ORG_ID
    from book_agent.services.cost_estimate import estimate_document_cost
    from book_agent.services.prepaid_credit import credit_status

    estimate_range: list[float] | None = None
    try:
        estimate = estimate_document_cost(session, document.id, context.settings)
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
    run_ids = list(session.scalars(select(DocumentRun.id).where(DocumentRun.document_id == document.id)).all())
    return {
        "estimate_usd_range": estimate_range,
        # The most this book may be charged: what an auto-start cap is compared with.
        "max_charge_usd": round(estimate_range[1] * multiplier, 4) if estimate_range else None,
        "prepaid": prepaid,
        "spent_usd": round(_spent_usd(session, [str(run_id) for run_id in run_ids]), 4),
    }


def _headline(translation: dict[str, Any], readable: bool, needs_you: list[dict[str, Any]], steps: list[dict[str, Any]]) -> str:
    state = translation["state"]
    if needs_you:
        return f"有 {len(needs_you)} 件事等你处理，处理后翻译会继续。"
    if state in {"queued", "running", "draining"}:
        current = next((step for step in steps if step["status"] in {"running", "pending"}), None)
        progress = translation["progress"]
        if current and current["key"] == "translate" and progress["total"]:
            return f"正在翻译 · 已完成 {progress['done']}/{progress['total']} 段。"
        return f"正在{current['label']}…" if current else "正在处理…"
    if state == "paused":
        return "翻译已暂停。"
    if state in {"failed", "cancelled"}:
        return translation.get("stop_reason_text") or ("翻译失败了。" if state == "failed" else "翻译已取消。")
    if readable:
        return "中文版已就绪。" if state != "succeeded_with_warnings" else "中文版已就绪（有些可选检查没有完成）。"
    if state == "never_started":
        return "还没开始翻译。"
    return "翻译完成，正在准备中文版…"


def _primary_action(translation: dict[str, Any], readable: bool, needs_you: list[dict[str, Any]]) -> dict[str, Any]:
    state = translation["state"]
    run_id = translation.get("run_id")
    if needs_you:
        return {"kind": "handle", "label": "去处理"}
    if state in {"queued", "running", "draining"}:
        return {"kind": "read", "label": "阅读中文版"} if readable else {"kind": "stop", "label": "停止", "run_id": run_id}
    if state == "paused":
        return {"kind": "resume", "label": "继续翻译", "run_id": run_id}
    if readable:
        return {"kind": "read", "label": "阅读中文版"}
    if state in {"failed", "cancelled"}:
        return {"kind": "retry", "label": "重试", "run_id": run_id}
    if state == "never_started":
        return {"kind": "start", "label": "开始翻译"}
    return {"kind": "none", "label": ""}


def build_overview(session: Session, document_id: str, context: OverviewContext) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document not found: {document_id}")
    service = RunControlService(RunControlRepository(session))

    exported = _succeeded_export_types(session, document.id)
    web = document.source_type == SourceType.WEB
    readable_ready = ExportType.MERGED_HTML.value in exported or (web and ExportType.WEB_HTML.value in exported)
    formats = ([ExportType.WEB_HTML.value] if web else []) + [fmt.value for fmt in _READING_FORMATS]
    if context.pdf_renderer:
        formats.append(ExportType.REBUILT_PDF.value)

    run = _latest_run(session, document.id, DocumentRunType.TRANSLATE_FULL)
    progress = _packet_progress(session, document.id)
    if run is None:
        translation: dict[str, Any] = {"state": "never_started", "run_id": None, "stop_reason": None, "stop_reason_text": None, "steps": []}
    else:
        summary = service.get_run_summary(run.id)
        request = (run.status_detail_json or {}).get(RUN_REQUEST_KEY) or {}
        translation = {
            "state": summary.status,
            "run_id": run.id,
            "stop_reason": summary.stop_reason,
            "stop_reason_text": stop_reason_text(summary.stop_reason, summary.status),
            "steps": _steps(summary, run, document.source_type),
            "fast_reading": str(request.get("fast_reading") or "off").lower() in {"on", "true", "1", "yes"},
            "auto_started": bool(request.get("auto_started")),
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
        }
    translation["progress"] = progress

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
        "headline": _headline(translation, readable_ready, needs_you, translation.get("steps", [])),
        "primary_action": _primary_action(translation, readable_ready, needs_you),
        "readable": {"ready": readable_ready, "formats": formats},
        "translation": translation,
        "activity": {"exporting": exporting},
        "needs_you": needs_you,
        "quality": {"open_issues": int(issues[0] or 0), "blocking": int(issues[1] or 0)},
        "cost": _cost(session, document, context),
    }


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
        run = _latest_run(session, document.id, DocumentRunType.TRANSLATE_FULL)
        title = document.title_tgt or document.title or document.title_src
        for item in _needs_you(session, document, run):
            items.append({**item, "document_id": document.id, "document_title": title})
    return items
