"""Layered QA of a pilot: rule checks on the translation a reader of the two pilot chapters gets.

docs/agent-upgrade/08-pilot-quality-gate.md §6. Seven layers, checked in the order in which a
problem hurts reading; each finding has a severity (阻断 blocking / 一般 major / 轻微 minor):

=====  ==========  ==================================================================================
layer  name        what the rules look at
=====  ==========  ==================================================================================
L1     结构        parse health's chapter checks on the pilot chapters; a pilot chapter that is front
                   matter, a contents page or holds another chapter; the exported reading copy's
                   chapters and their titles; sentences set as headings; running headers kept as text
L2     图表        figure crops (``figure_region_problems`` through parse health), image fragments,
                   figure labels kept as text, body text folded into or swallowed by a caption, a
                   figure without its picture or caption, a caption without its figure
L3     表格        the rendered table: flattened to text, cells still holding the source's "|", rows of
                   different widths, no header, mostly empty (a chart read as a table), code set as a
                   table, English cells left untranslated
L4     代码与格式  code translated, prose set as code, code comments as headings or outside the code,
                   inline code lost or its backticks shown, list and footnote numbers, equations
L5     术语        locked terms (``find_term_violations``), a glossary term rendered several ways or
                   sometimes left in English, "Listing/Figure/Table N" named two ways, a word the
                   translation sometimes keeps in English
L6     忠实度      the Reviewer Agent's open findings (the pilot's model review), sentences without a
                   translation, translations much shorter than their source, numbers that disappear,
                   negations and comparisons that disappear
L7     排版与纯净  program notes leaked into the text ("图片锚点保留"), stray glyphs ("¡", U+FFFD),
                   English left where Chinese should be, CJK–Latin spacing against the book's own
                   convention, half-width punctuation in Chinese, odd whitespace
=====  ==========  ==================================================================================

Every finding carries a stable ``check_id`` ("L2.caption_swallows_body"), its layer and severity,
where it is (chapter, packet, block, sentence, page), evidence, and a ``key`` naming what a repair
would act on ("L2.figure_without_image:block:<id>"), so the repair agent (Q5) can take the report
as its work list and the same checks can judge its repairs.

Pure functions over plain data: the service (services/pilot_quality.py) loads the pilot's render
blocks, sentences, glossary and review findings, and the PDF pages for figure crops. No model is
called here; a vision or model reviewer can be plugged in as an extra check.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from book_agent.domain.structure import parse_health
from book_agent.domain.structure.parse_health import (
    BLOCKING,
    MAJOR,
    MINOR,
    SEVERITY_ORDER,
    CheckResult,
    FigureProblemFinder,
    HealthBlock,
    HealthChapter,
    OutlineEntry,
)
from book_agent.domain.terminology.enforcement import LockedTerm, find_term_violations
from book_agent.domain.terminology.matching import SourceTermIndex, normalize_target, source_term_key

LAYERS: tuple[tuple[str, str], ...] = (
    ("L1", "结构"),
    ("L2", "图表"),
    ("L3", "表格"),
    ("L4", "代码与格式"),
    ("L5", "术语"),
    ("L6", "忠实度"),
    ("L7", "排版与纯净"),
)
LAYER_NAMES = dict(LAYERS)

# Findings kept per check: enough for a repair agent's work list, bounded for storage.
MAX_FINDINGS_PER_CHECK = 200

# What a repair would use (docs/agent-upgrade/08 §7.2); "export_code" means a systemic fix in code.
REPAIR_STRUCTURE = "structure_edit"
REPAIR_RESPLIT = "resplit_chapters"
REPAIR_FIGURE_REGION = "figure_region_override"
REPAIR_RETRANSLATE = "retranslate_packet"
REPAIR_GLOSSARY = "glossary"
REPAIR_PARSE = "parse_recovery"
REPAIR_EXPORT_CODE = "export_code"
REPAIR_PILOT = "pilot_selection"


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    severity: str
    title: str
    repair: str

    @property
    def layer(self) -> str:
        return self.check_id.split(".", 1)[0]


_SPECS = (
    CheckSpec("L1.not_body_chapter", BLOCKING, "试译的章节不是正文章节", REPAIR_PILOT),
    CheckSpec("L1.chapters_vs_outline", BLOCKING, "章节切分与书签不一致", REPAIR_RESPLIT),
    CheckSpec("L1.chapter_titles", BLOCKING, "章节标题异常（带页码，或标题与内容不符）", REPAIR_RESPLIT),
    CheckSpec("L1.chapter_is_contents", BLOCKING, "这一章其实是目录", REPAIR_RESPLIT),
    CheckSpec("L1.chapter_folded", BLOCKING, "一章里混进了另一章", REPAIR_RESPLIT),
    CheckSpec("L1.export_chapter_missing", BLOCKING, "中文版里这一章没有单独成章", REPAIR_EXPORT_CODE),
    CheckSpec("L1.export_title", MAJOR, "中文版目录里的章节标题有问题", REPAIR_RETRANSLATE),
    CheckSpec("L1.heading_is_prose", MAJOR, "正文句子被当成了标题", REPAIR_STRUCTURE),
    CheckSpec("L1.running_header_as_text", MAJOR, "页眉被当成了正文或脚注", REPAIR_STRUCTURE),
    CheckSpec("L1.heading_merged_into_text", MAJOR, "小标题和正文粘在了一起", REPAIR_STRUCTURE),
    CheckSpec("L2.figure_regions", BLOCKING, "图片裁剪不对（切掉了图，或框进了页眉、图注）", REPAIR_FIGURE_REGION),
    CheckSpec("L2.figure_without_image", BLOCKING, "图没有图片", REPAIR_FIGURE_REGION),
    CheckSpec("L2.caption_swallows_body", BLOCKING, "图注里混进了正文", REPAIR_STRUCTURE),
    CheckSpec("L2.caption_folds", BLOCKING, "正文被并进了图注", REPAIR_STRUCTURE),
    CheckSpec("L2.orphan_caption", MAJOR, "图注没有配到图", REPAIR_STRUCTURE),
    CheckSpec("L2.duplicated_labels", MAJOR, "图中的标注又作为正文出现", REPAIR_STRUCTURE),
    CheckSpec("L2.image_fragments", MAJOR, "碎片图片或空白图片", REPAIR_PARSE),
    CheckSpec("L2.figure_without_caption", MINOR, "图没有图注", REPAIR_STRUCTURE),
    CheckSpec("L3.table_flattened", BLOCKING, "表格被压成了文字", REPAIR_STRUCTURE),
    CheckSpec("L3.table_garbled", BLOCKING, "表格没有拆开（单元格里还有竖线）", REPAIR_STRUCTURE),
    CheckSpec("L3.table_ragged", MAJOR, "表格各行列数不一致", REPAIR_STRUCTURE),
    CheckSpec("L3.table_no_header", MAJOR, "表格没有表头", REPAIR_STRUCTURE),
    CheckSpec("L3.table_sparse", MAJOR, "表格大半是空格子（多半是图被当成了表格）", REPAIR_STRUCTURE),
    CheckSpec("L3.table_is_code", MAJOR, "代码被当成了表格", REPAIR_STRUCTURE),
    CheckSpec("L3.table_untranslated", MAJOR, "表格内容没有翻译", REPAIR_RETRANSLATE),
    CheckSpec("L4.code_translated", BLOCKING, "代码被翻译了", REPAIR_RETRANSLATE),
    CheckSpec("L4.prose_in_code", MAJOR, "正文被当成代码，没有翻译", REPAIR_STRUCTURE),
    CheckSpec("L4.pseudo_headings", MAJOR, "代码注释被当成了标题", REPAIR_STRUCTURE),
    CheckSpec("L4.code_comment_as_text", MAJOR, "代码注释跑到了代码块外面", REPAIR_STRUCTURE),
    CheckSpec("L4.inline_code_lost", MAJOR, "行内代码在译文里丢了或被改了", REPAIR_RETRANSLATE),
    CheckSpec("L4.inline_code_markup", MAJOR, "行内代码没有显示成代码（露出了反引号）", REPAIR_EXPORT_CODE),
    CheckSpec("L4.list_marker_lost", MAJOR, "列表编号丢了", REPAIR_RETRANSLATE),
    CheckSpec("L4.footnote_marker_lost", MAJOR, "脚注编号丢了", REPAIR_RETRANSLATE),
    CheckSpec("L4.equation_altered", MAJOR, "公式被改动了", REPAIR_RETRANSLATE),
    CheckSpec("L5.locked_term", MAJOR, "没有遵守锁定的术语", REPAIR_RETRANSLATE),
    CheckSpec("L5.term_variants", MAJOR, "同一术语用了几种译法", REPAIR_GLOSSARY),
    CheckSpec("L5.term_left_in_english", MAJOR, "术语有时翻译、有时留英文", REPAIR_GLOSSARY),
    CheckSpec("L5.label_rendering", MAJOR, "图、表、代码清单的编号叫法前后不一", REPAIR_GLOSSARY),
    CheckSpec("L5.mixed_rendering", MINOR, "同一个词有时翻译、有时留英文", REPAIR_GLOSSARY),
    CheckSpec("L6.model_review", MAJOR, "模型审校发现的错译、漏译", REPAIR_RETRANSLATE),
    CheckSpec("L6.untranslated_sentence", BLOCKING, "有句子没有译文（漏译）", REPAIR_RETRANSLATE),
    CheckSpec("L6.short_translation", MAJOR, "译文比原文短得多，可能漏译", REPAIR_RETRANSLATE),
    CheckSpec("L6.number_mismatch", MAJOR, "原文里的数字在译文里不见了", REPAIR_RETRANSLATE),
    CheckSpec("L6.negation_lost", MAJOR, "原文是否定，译文里没有否定", REPAIR_RETRANSLATE),
    CheckSpec("L6.comparative_lost", MINOR, "原文的比较关系在译文里不见了", REPAIR_RETRANSLATE),
    CheckSpec("L7.leaked_marker", BLOCKING, "译文里混进了程序提示", REPAIR_EXPORT_CODE),
    CheckSpec("L7.stray_glyph", BLOCKING, "异常字符（如 ¡、乱码）", REPAIR_PARSE),
    CheckSpec("L7.untranslated_prose", MAJOR, "整段没有翻译，读者看到的是英文", REPAIR_RETRANSLATE),
    CheckSpec("L7.cjk_latin_spacing", MAJOR, "中英文之间的空格与全书习惯不一致", REPAIR_RETRANSLATE),
    CheckSpec("L7.halfwidth_punctuation", MAJOR, "中文里用了半角标点", REPAIR_RETRANSLATE),
    CheckSpec("L7.english_residue", MINOR, "译文里残留整句英文", REPAIR_RETRANSLATE),
    CheckSpec("L7.odd_whitespace", MINOR, "异常空白（全角空格、连续空格）", REPAIR_RETRANSLATE),
)
CHECKS: dict[str, CheckSpec] = {spec.check_id: spec for spec in _SPECS}


# ---------------------------------------------------------------------------
# Inputs


@dataclass(frozen=True, slots=True)
class QaChapter:
    id: str
    ordinal: int
    title_src: str
    title_tgt: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class QaBlock:
    """A block as the reading copy renders it (an export ``MergedRenderBlock``)."""

    block_id: str
    chapter_id: str
    block_type: str
    render_mode: str
    artifact_kind: str | None
    source_text: str
    target_text: str
    # The block's HTML in the Chinese reading copy.
    html: str
    page: int | None = None
    # Figures: whether the reading copy has a picture for it (None: not known).
    has_image: bool | None = None
    packet_id: str | None = None
    # Tables and equations: their linked caption (a figure's caption is its source_text).
    caption_text: str | None = None
    # Blocks the export folds into this artifact's caption.
    context_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QaSentence:
    id: str
    chapter_id: str
    block_id: str
    source_text: str
    # The current translation ("" when there is none).
    target_text: str
    packet_id: str | None = None
    page: int | None = None
    # Sentences of code, tables and other kept-as-is blocks are not translated by design.
    rendered_as_translation: bool = True


@dataclass(frozen=True, slots=True)
class ExportedChapter:
    """A chapter section of the exported reading copy (merged HTML)."""

    chapter_id: str | None
    title: str
    html: str


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    source_term: str
    # First the expected rendering, then accepted variants.
    renderings: tuple[str, ...]
    locked: bool = False


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """An open finding of the Reviewer Agent (a ``review_issues`` row with detector=model)."""

    issue_id: str
    issue_type: str
    severity: str  # low | medium | high | critical
    chapter_id: str | None
    block_id: str | None = None
    sentence_id: str | None = None
    packet_id: str | None = None
    source_text: str = ""
    target_text: str = ""
    explanation: str = ""
    suggested_target_text: str = ""


@dataclass(slots=True)
class HealthInputs:
    """What the parse health checks need, for the pilot chapters' blocks."""

    chapters: Sequence[HealthChapter]  # every chapter of the book (the outline check needs them)
    blocks: Sequence[HealthBlock]  # the pilot chapters' blocks only
    is_pdf: bool = False
    outline: Sequence[OutlineEntry] = ()
    document_title: str | None = None
    page_sizes: dict[int, tuple[float, float]] | None = None
    find_figure_problems: FigureProblemFinder | None = None
    academic_paper: bool = False


@dataclass(slots=True)
class PilotQaInputs:
    chapters: Sequence[QaChapter]
    blocks: Sequence[QaBlock]
    sentences: Sequence[QaSentence]
    glossary: Sequence[GlossaryTerm] = ()
    model_issues: Sequence[ReviewFinding] = ()
    # The exported reading copy's chapter sections; None when the pilot has no export to read.
    exported: Sequence[ExportedChapter] | None = None
    health: HealthInputs | None = None


# ---------------------------------------------------------------------------
# Findings and the report


@dataclass(slots=True)
class Location:
    chapter_id: str | None = None
    packet_id: str | None = None
    block_id: str | None = None
    sentence_id: str | None = None
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in (
            ("chapter_id", self.chapter_id),
            ("packet_id", self.packet_id),
            ("block_id", self.block_id),
            ("sentence_id", self.sentence_id),
            ("page", self.page),
        ) if value is not None}


@dataclass(slots=True)
class Finding:
    check_id: str
    severity: str
    # What a repair acts on: {"kind": "block" | "packet" | "sentence" | "chapter" | "term" | "document", "id": ...}
    target: dict[str, str]
    location: Location
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # What the problem is about when that is narrower than the repair target (a model issue's id).
    subject: str | None = None

    @property
    def layer(self) -> str:
        return self.check_id.split(".", 1)[0]

    @property
    def key(self) -> str:
        """Stable across runs for the same problem: the repair agent's handle and the ledger's.

        The problem's own unit: a sentence or block even when the repair retranslates its whole packet.
        """
        if self.subject:
            return f"{self.check_id}:{self.subject}"
        kind = self.target.get("kind", "document")
        if kind == "packet":
            if self.location.sentence_id:
                return f"{self.check_id}:sentence:{self.location.sentence_id}"
            if self.location.block_id:
                return f"{self.check_id}:block:{self.location.block_id}"
        return f"{self.check_id}:{kind}:{self.target.get('id', '')}"

    def to_dict(self) -> dict[str, Any]:
        spec = CHECKS.get(self.check_id)
        return {
            "check_id": self.check_id,
            "layer": self.layer,
            "severity": self.severity,
            "key": self.key,
            "target": dict(self.target),
            "repair": spec.repair if spec else None,
            "location": self.location.to_dict(),
            "message": self.message,
            "evidence": {key: value for key, value in self.evidence.items() if value not in (None, "", [], {})},
        }


@dataclass(slots=True)
class CheckRun:
    """One check's outcome: its findings and, for parse health checks, the full count behind the samples."""

    check_id: str
    findings: list[Finding] = field(default_factory=list)
    count: int | None = None

    @property
    def total(self) -> int:
        return max(self.count or 0, len(self.findings))


def _severity_counts(runs: Iterable[CheckRun]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for run in runs:
        for finding in run.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        # Parse health reports a count with at most a few samples: the rest at the check's severity.
        unsampled = run.total - len(run.findings)
        if unsampled > 0:
            counts[CHECKS[run.check_id].severity] += unsampled
    return counts


def status_for(counts: dict[str, int]) -> str:
    if counts.get(BLOCKING):
        return "blocking"
    if counts.get(MAJOR):
        return "warnings"
    return "passed"


def layer_report(layer: str, runs: Sequence[CheckRun]) -> dict[str, Any]:
    counts = _severity_counts(runs)
    order = {severity: index for index, severity in enumerate(SEVERITY_ORDER)}
    findings = sorted(
        (finding for run in runs for finding in run.findings),
        key=lambda finding: (order.get(finding.severity, 9), finding.check_id),
    )
    checks = []
    for run in runs:
        spec = CHECKS[run.check_id]
        checks.append(
            {
                "check_id": run.check_id,
                "title": spec.title,
                "severity": spec.severity,
                "count": run.total,
                "passed": run.total == 0,
            }
        )
    return {
        "layer": layer,
        "name": LAYER_NAMES[layer],
        "status": status_for(counts),
        # The pilot may go to the reader only when no layer has a blocking finding.
        "passed": counts[BLOCKING] == 0,
        "counts": counts,
        "checks": checks,
        "findings": [finding.to_dict() for finding in findings],
    }


def summarize(layers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counts = {severity: sum(int(layer["counts"].get(severity, 0)) for layer in layers) for severity in SEVERITY_ORDER}
    return {"status": status_for(counts), "counts": counts}


# ---------------------------------------------------------------------------
# Text helpers

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RE = re.compile(f"[{_CJK}]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z“\"(])")
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.*?[^`\s].*?)(?<!`)\1(?!`)")
# A URL ends at whitespace, a Chinese character or full-width punctuation (Chinese text has no spaces).
_URL_RE = re.compile(
    rf"(?:https?://|www\.)[^\s{_CJK}\u3000-\u303f\uff00-\uffef]+|\b[A-Za-z0-9.-]+\.(?:com|org|net|io|ai|dev|edu|gov|co|bz)(?:/[^\s{_CJK}\u3000-\u303f\uff00-\uffef]*)?\b",
    re.IGNORECASE,
)


def _snippet(text: str | None, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def visible_text(fragment: str) -> str:
    """What a reader sees of an HTML fragment: tags dropped, entities decoded, whitespace collapsed."""
    text = _TAG_RE.sub(" ", (fragment or "").replace("<br/>", "\n"))
    return " ".join(html_lib.unescape(text).split())


def cjk_count(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def english_words(text: str) -> list[str]:
    return _LATIN_WORD_RE.findall(_URL_RE.sub(" ", text or ""))


def _without_code_and_urls(text: str) -> str:
    return _URL_RE.sub(" ", _CODE_SPAN_RE.sub(" ", text or ""))


def _loc(block: QaBlock) -> Location:
    return Location(chapter_id=block.chapter_id, packet_id=block.packet_id, block_id=block.block_id, page=block.page)


def _block_finding(check_id: str, block: QaBlock, message: str, *, severity: str | None = None, **evidence: Any) -> Finding:
    evidence.setdefault("source", _snippet(block.source_text))
    evidence.setdefault("target", _snippet(block.target_text))
    return Finding(
        check_id=check_id,
        severity=severity or CHECKS[check_id].severity,
        target={"kind": "block", "id": block.block_id},
        location=_loc(block),
        message=message,
        evidence=evidence,
    )


def _sentence_finding(check_id: str, sentence: QaSentence, message: str, *, severity: str | None = None, **evidence: Any) -> Finding:
    evidence.setdefault("source", _snippet(sentence.source_text))
    evidence.setdefault("target", _snippet(sentence.target_text))
    return Finding(
        check_id=check_id,
        severity=severity or CHECKS[check_id].severity,
        # A sentence is repaired by retranslating its packet.
        target={"kind": "packet", "id": sentence.packet_id} if sentence.packet_id else {"kind": "sentence", "id": sentence.id},
        location=Location(
            chapter_id=sentence.chapter_id,
            packet_id=sentence.packet_id,
            block_id=sentence.block_id,
            sentence_id=sentence.id,
            page=sentence.page,
        ),
        message=message,
        evidence=evidence,
    )


_PROSE_TYPES = frozenset({"paragraph", "list_item", "heading", "caption", "footnote", "quote"})
_TRANSLATED_MODES = frozenset({"zh_primary_with_optional_source"})
_FIGURE_KINDS = frozenset({"image", "figure"})


def _is_prose_block(block: QaBlock) -> bool:
    return block.block_type in _PROSE_TYPES and block.render_mode in _TRANSLATED_MODES


def _is_figure(block: QaBlock) -> bool:
    return block.artifact_kind in _FIGURE_KINDS or block.block_type in _FIGURE_KINDS


def _is_table(block: QaBlock) -> bool:
    return block.block_type == "table" or block.artifact_kind == "table"


def _is_code(block: QaBlock) -> bool:
    return block.artifact_kind == "code" or block.block_type == "code"


# ---------------------------------------------------------------------------
# Parse health on the pilot chapters


def _samples_to_findings(check_id: str, result: CheckResult | None, chapter_by_block: dict[str, str]) -> CheckRun:
    run = CheckRun(check_id)
    if result is None:
        return run
    for sample in result.samples:
        block_id = sample.block_id
        chapter_id = sample.chapter_id or (chapter_by_block.get(block_id) if block_id else None)
        target = {"kind": "block", "id": block_id} if block_id else {"kind": "chapter", "id": chapter_id or ""}
        run.findings.append(
            Finding(
                check_id=check_id,
                severity=CHECKS[check_id].severity,
                target=target,
                location=Location(chapter_id=chapter_id, block_id=block_id, page=sample.page),
                message=CHECKS[check_id].title,
                evidence={"source": sample.text, "detail": sample.detail},
            )
        )
    run.count = result.count
    return run


def _chapters_vs_outline(health: HealthInputs, pilot: Sequence[QaChapter]) -> CheckRun:
    """The outline's chapter-level entries that fall inside a pilot chapter, and pilot chapters the outline lacks."""
    run = CheckRun("L1.chapters_vs_outline")
    if not health.is_pdf or not health.outline:
        return run
    _front, expected = parse_health.split_outline(health.outline, health.document_title)
    pilot_ids = {chapter.id for chapter in pilot}
    pilot_health = [chapter for chapter in health.chapters if chapter.id in pilot_ids]
    for entry in expected:
        if parse_health._chapter_for(entry, health.chapters) is not None:
            continue
        holder = next(
            (
                chapter
                for chapter in pilot_health
                if chapter.page_start is not None
                and chapter.page_end is not None
                and chapter.page_start <= entry.page <= chapter.page_end
            ),
            None,
        )
        if holder is None:
            continue
        run.findings.append(
            Finding(
                check_id=run.check_id,
                severity=BLOCKING,
                target={"kind": "chapter", "id": holder.id},
                location=Location(chapter_id=holder.id, page=entry.page),
                message=f"书签里的「{_snippet(entry.title, 60)}」没有单独成章，并进了「{_snippet(holder.title, 40)}」",
                evidence={"source": entry.title, "detail": "missing_chapter"},
            )
        )
    outline_pages = {entry.page for entry in health.outline}
    for chapter in pilot_health:
        if chapter.page_start in outline_pages or any(parse_health._title_matches(chapter, entry) for entry in health.outline):
            continue
        run.findings.append(
            Finding(
                check_id=run.check_id,
                severity=BLOCKING,
                target={"kind": "chapter", "id": chapter.id},
                location=Location(chapter_id=chapter.id, page=chapter.page_start),
                message=f"「{_snippet(chapter.title, 60)}」在书签里没有对应的章",
                evidence={"source": chapter.title, "detail": "extra_chapter"},
            )
        )
    return run


def parse_health_runs(health: HealthInputs | None, pilot: Sequence[QaChapter]) -> dict[str, CheckRun]:
    """Parse health's checks on the pilot chapters, under the layer each belongs to."""
    if health is None:
        return {}
    blocks = list(health.blocks)
    chapter_by_block = {block.id: block.chapter_id for block in blocks}
    pilot_ids = {chapter.id for chapter in pilot}
    pilot_health = [chapter for chapter in health.chapters if chapter.id in pilot_ids]
    runs = {
        "L1.chapters_vs_outline": _chapters_vs_outline(health, pilot),
        "L1.chapter_titles": _samples_to_findings(
            "L1.chapter_titles", parse_health.check_chapter_titles(pilot_health, blocks), chapter_by_block
        ),
        "L4.pseudo_headings": _samples_to_findings(
            "L4.pseudo_headings", parse_health.check_pseudo_headings(blocks), chapter_by_block
        ),
        "L2.caption_folds": _samples_to_findings(
            "L2.caption_folds", parse_health.check_caption_folds(blocks, academic_paper=health.academic_paper), chapter_by_block
        ),
    }
    if health.is_pdf:
        if health.find_figure_problems is not None:
            runs["L2.figure_regions"] = _samples_to_findings(
                "L2.figure_regions",
                parse_health.check_figure_regions(blocks, health.find_figure_problems, health.page_sizes),
                chapter_by_block,
            )
        runs["L2.image_fragments"] = _samples_to_findings(
            "L2.image_fragments", parse_health.check_image_fragments(blocks, health.page_sizes), chapter_by_block
        )
        runs["L2.duplicated_labels"] = _samples_to_findings(
            "L2.duplicated_labels", parse_health.check_duplicated_labels(blocks, health.page_sizes), chapter_by_block
        )
    return runs


# ---------------------------------------------------------------------------
# L1 结构

_FRONT_OR_BACK = re.compile(
    r"^\s*(?:front matter|cover|title page|copyright|dedication|(?:brief |table of )?contents|foreword|preface|"
    r"acknowledg(?:e)?ments?|about (?:this book|the authors?|the cover(?: illustration)?)|index|references|"
    r"bibliography|glossary|part\s+(?:\d+|[ivxlc]+)\b|appendix|目录|前言|致谢|索引|参考文献|附录)",
    re.IGNORECASE,
)
_TOC_LINE_RE = re.compile(r"(?:\.{3,}|…{2,}|\s{2,})\s*\d{1,4}\s*$|\s\d{1,4}\s*$")
_CHAPTER_OPENING_RE = re.compile(r"^\s*(?:[Cc]hapter\s+|CHAPTER\s+)?(\d{1,2})\s+(?=[A-Z])")
_SECTION_NUMBER_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s+")
# A run-in heading glued to the paragraph after it: "...worth considering.cost optimization When you're ...".
_GLUED_HEADING_RE = re.compile(r"(?<![\w.@/])([a-z]{3,})([.?!])([a-z]{2,})(?=\s)")
_NOT_GLUED = frozenset({"com", "org", "net", "io", "ai", "py", "js", "md", "txt", "json", "html", "csv", "pdf", "env", "yaml", "yml", "toml", "sh", "exe", "zip"})
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d{1,4})\s+(.+)$", re.DOTALL)


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _chapter_number(title: str | None) -> int | None:
    match = _CHAPTER_OPENING_RE.match(title or "")
    return int(match.group(1)) if match else None


def _is_sentence(text: str) -> bool:
    """A heading that reads as a sentence of body text ("Chapter 7 explores the layers of ...")."""
    return len(text.split()) >= 8 and text.rstrip().endswith((".", "?", "!"))


def check_pilot_chapters(inputs: PilotQaInputs) -> list[CheckRun]:
    not_body = CheckRun("L1.not_body_chapter")
    contents = CheckRun("L1.chapter_is_contents")
    folded = CheckRun("L1.chapter_folded")
    blocks_by_chapter: dict[str, list[QaBlock]] = defaultdict(list)
    for block in inputs.blocks:
        blocks_by_chapter[block.chapter_id].append(block)
    for chapter in inputs.chapters:
        title = chapter.title_src or ""
        if _FRONT_OR_BACK.match(title):
            not_body.findings.append(
                Finding(
                    "L1.not_body_chapter",
                    BLOCKING,
                    {"kind": "chapter", "id": chapter.id},
                    Location(chapter_id=chapter.id, page=chapter.page_start),
                    f"试译选中了「{_snippet(title, 60)}」，它不是正文章节，看不出正文的翻译质量",
                    {"source": title},
                )
            )
        text_blocks = [block for block in blocks_by_chapter.get(chapter.id, []) if block.block_type in _PROSE_TYPES]
        toc_lines = [block for block in text_blocks if _TOC_LINE_RE.search(" ".join(block.source_text.split()))]
        if parse_health.title_page_number(title) is not None or (len(text_blocks) >= 3 and len(toc_lines) * 2 >= len(text_blocks)):
            contents.findings.append(
                Finding(
                    "L1.chapter_is_contents",
                    BLOCKING,
                    {"kind": "chapter", "id": chapter.id},
                    Location(chapter_id=chapter.id, page=chapter.page_start),
                    f"「{_snippet(title, 60)}」是目录里的一行或一页目录，不是一章",
                    {"source": title, "sample": [_snippet(block.source_text, 80) for block in toc_lines[:3]]},
                )
            )
        own_number = _chapter_number(title)
        for block in blocks_by_chapter.get(chapter.id, []):
            if block.block_type != "heading":
                continue
            number = _chapter_number(block.source_text)
            heading = " ".join(block.source_text.split())
            if number is None or number == own_number or _norm(heading) == _norm(title) or _is_sentence(heading):
                continue
            if parse_health.title_page_number(heading) is not None or len(heading.split()) > 14:
                continue
            folded.findings.append(
                _block_finding(
                    "L1.chapter_folded",
                    block,
                    f"「{_snippet(title, 40)}」里出现了另一章的开头「{_snippet(heading, 60)}」",
                    detail="another_chapter_heading",
                )
            )
    return [not_body, contents, folded]


def check_exported_chapters(inputs: PilotQaInputs) -> list[CheckRun]:
    """The reading copy: every pilot chapter is its own chapter, under a Chinese title without a page number."""
    missing = CheckRun("L1.export_chapter_missing")
    titles = CheckRun("L1.export_title")
    if inputs.exported is None:
        return [missing, titles]
    sections = {section.chapter_id: section for section in inputs.exported if section.chapter_id}
    for chapter in inputs.chapters:
        section = sections.get(chapter.id)
        if section is None:
            missing.findings.append(
                Finding(
                    "L1.export_chapter_missing",
                    BLOCKING,
                    {"kind": "chapter", "id": chapter.id},
                    Location(chapter_id=chapter.id, page=chapter.page_start),
                    f"中文版里「{_snippet(chapter.title_tgt or chapter.title_src, 40)}」没有自己的一章，"
                    "内容并进了前一章，目录里找不到它",
                    {"source": chapter.title_src, "detail": "no_section"},
                )
            )
            continue
        title = " ".join(section.title.split())
        problem = None
        if not title:
            problem = "没有标题"
        elif parse_health.title_page_number(title) is not None:
            problem = "标题后面带着页码"
        elif not cjk_count(title) and len(english_words(title)) >= 2:
            problem = "标题没有翻译"
        if problem:
            titles.findings.append(
                Finding(
                    "L1.export_title",
                    MAJOR,
                    {"kind": "chapter", "id": chapter.id},
                    Location(chapter_id=chapter.id, page=chapter.page_start),
                    f"中文版目录里这一章{problem}：「{_snippet(title, 60)}」",
                    {"source": chapter.title_src, "target": title},
                )
            )
    return [missing, titles]


def check_headings_and_headers(inputs: PilotQaInputs) -> list[CheckRun]:
    prose_headings = CheckRun("L1.heading_is_prose")
    headers = CheckRun("L1.running_header_as_text")
    glued = CheckRun("L1.heading_merged_into_text")
    # Running headers repeat a chapter or section title, with or without its number ("11 / The novelty of RAG").
    titles = [block.source_text for block in inputs.blocks if block.block_type == "heading" and block.source_text.strip()]
    titles.extend(chapter.title_src for chapter in inputs.chapters)
    heading_titles = {_norm(title) for title in titles} | {_norm(_SECTION_NUMBER_RE.sub("", title)) for title in titles}
    heading_titles = {title for title in heading_titles if len(title) >= 6}
    for block in inputs.blocks:
        text = " ".join(block.source_text.split())
        if block.block_type == "heading":
            if _is_sentence(text):
                prose_headings.findings.append(
                    _block_finding("L1.heading_is_prose", block, f"这句正文被排成了标题：「{_snippet(text, 80)}」")
                )
            continue
        if block.block_type not in {"paragraph", "footnote", "list_item"}:
            continue
        if _is_prose_block(block):
            hits = [
                match.group(0)
                for match in _GLUED_HEADING_RE.finditer(_without_code_and_urls(block.source_text))
                # An identifier the translation keeps as it is ("asyncio.gather") is code, not a glued heading.
                if match.group(3) not in _NOT_GLUED and match.group(1) not in {"etc"} and match.group(0) not in block.target_text
            ]
            if hits:
                glued.findings.append(
                    _block_finding(
                        "L1.heading_merged_into_text",
                        block,
                        f"小标题和正文粘在了一起（{'、'.join(hits[:3])}），译文里小标题成了正文的一部分",
                        hits=hits[:5],
                    )
                )
        match = _LEADING_NUMBER_RE.match(block.source_text.strip())
        if not match:
            continue
        rest = _norm(match.group(2))
        header = next((title for title in heading_titles if rest == title or (len(title) >= 8 and rest.startswith(title + " "))), None)
        if header is None:
            continue
        headers.findings.append(
            _block_finding(
                "L1.running_header_as_text",
                block,
                f"页眉「{match.group(1)} {_snippet(match.group(2).splitlines()[0], 40)}」被当成了{'脚注' if block.block_type == 'footnote' else '正文'}",
                detail=block.block_type,
            )
        )
    return [prose_headings, headers, glued]


# ---------------------------------------------------------------------------
# L2 图表

_FIGURE_CAPTION_RE = re.compile(r"^\s*(?:fig(?:ure|\.)|图)\s*[A-Z]?\d+(?:[.\-]\d+)*", re.IGNORECASE)
# A caption longer than this, or of more sentences, has taken in the text after it.
CAPTION_MAX_WORDS = 70
CAPTION_MAX_SENTENCES = 3
CAPTION_MAX_CJK = 150


def _sentence_count(text: str) -> int:
    text = " ".join((text or "").split())
    return len([part for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]) if text else 0


def _zh_sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？]", text or "") if cjk_count(part) >= 4])


def check_figures(inputs: PilotQaInputs) -> list[CheckRun]:
    swallowed = CheckRun("L2.caption_swallows_body")
    no_image = CheckRun("L2.figure_without_image")
    no_caption = CheckRun("L2.figure_without_caption")
    orphan = CheckRun("L2.orphan_caption")
    for block in inputs.blocks:
        if _is_figure(block):
            captioned = block.render_mode == "image_anchor_with_translated_caption"
            if captioned:
                caption = " ".join(block.source_text.split())
                words = len(caption.split())
                sentences = _sentence_count(caption)
                zh = block.target_text or ""
                if words > CAPTION_MAX_WORDS or sentences > CAPTION_MAX_SENTENCES or (
                    cjk_count(zh) > CAPTION_MAX_CJK and _zh_sentence_count(zh) > CAPTION_MAX_SENTENCES
                ):
                    swallowed.findings.append(
                        _block_finding(
                            "L2.caption_swallows_body",
                            block,
                            f"图注有 {words} 个词、{sentences} 句，后面的正文被并进了图注",
                            words=words,
                            sentences=sentences,
                        )
                    )
                for context in block.context_texts:
                    context = " ".join(context.split())
                    if _sentence_count(context) > 2 or len(context.split()) > 60:
                        swallowed.findings.append(
                            _block_finding(
                                "L2.caption_swallows_body",
                                block,
                                "图注下面的正文被并进了图注",
                                folded=_snippet(context),
                            )
                        )
                        break
            if block.has_image is False:
                no_image.findings.append(
                    _block_finding(
                        "L2.figure_without_image",
                        block,
                        f"{_snippet(block.source_text, 40) if captioned else '这张图'}在中文版里没有图片",
                    )
                )
            if not captioned and block.has_image is not False:
                no_caption.findings.append(_block_finding("L2.figure_without_caption", block, "这张图没有图注"))
            continue
        label = _FIGURE_CAPTION_RE.match(block.source_text or "") if block.block_type in {"caption", "paragraph"} else None
        # "Figure 1.4 shows the two techniques ..." is a body sentence about the figure, not its caption.
        if label and not re.match(r"\s*[a-z]", (block.source_text or "")[label.end() :]):
            if len(block.source_text.split()) <= CAPTION_MAX_WORDS:
                orphan.findings.append(
                    _block_finding(
                        "L2.orphan_caption",
                        block,
                        f"图注「{_snippet(block.source_text, 50)}」没有配到图，作为正文单独出现",
                    )
                )
    return [swallowed, no_image, orphan, no_caption]


# ---------------------------------------------------------------------------
# L3 表格

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
_CELL_RE = re.compile(r"<t([hd])[^>]*>(.*?)</t[hd]>", re.DOTALL)
_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|.*\|\s*$|^[^|\n]*\|[^|\n]*\|[^|\n]*$", re.MULTILINE)
_CODE_LINE_RE = re.compile(
    r"^\s*(?:from\s+\w[\w.]*\s+import\b|import\s+\w|def\s+\w+\(|class\s+\w+[(:]|return\b|print\(|async\s+def\b|await\b|"
    r"[A-Za-z_][\w.\[\]]*\s*=\s*\S|[A-Za-z_][\w.]*\(.*\)\s*$|[{}\[\]()]\s*,?\s*$|#\s|//\s|\$\s|pip\s+install|curl\s|git\s)"
)


def _table_rows(fragment: str) -> list[list[tuple[str, str]]]:
    rows = []
    for row in _ROW_RE.findall(fragment or ""):
        rows.append([(kind, visible_text(cell)) for kind, cell in _CELL_RE.findall(row)])
    return [row for row in rows if row]


def looks_like_code(text: str) -> bool:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    code_lines = sum(1 for line in lines if _CODE_LINE_RE.match(line.strip("| ")))
    return code_lines * 2 >= len(lines)


def check_tables(inputs: PilotQaInputs) -> list[CheckRun]:
    flattened = CheckRun("L3.table_flattened")
    garbled = CheckRun("L3.table_garbled")
    ragged = CheckRun("L3.table_ragged")
    no_header = CheckRun("L3.table_no_header")
    sparse = CheckRun("L3.table_sparse")
    is_code = CheckRun("L3.table_is_code")
    untranslated = CheckRun("L3.table_untranslated")
    for block in inputs.blocks:
        if _is_table(block):
            label = _snippet(block.caption_text or block.target_text or "这个表格", 40)
            if looks_like_code(block.source_text):
                is_code.findings.append(_block_finding("L3.table_is_code", block, f"{label}其实是一段代码"))
                continue
            rows = _table_rows(block.html)
            if not rows:
                if len(_PIPE_ROW_RE.findall(block.source_text or "")) >= 2 or "|" in visible_text(block.html):
                    flattened.findings.append(
                        _block_finding("L3.table_flattened", block, f"{label}没有排成表格，读者看到的是一串竖线和文字")
                    )
                continue
            cells = [text for row in rows for _kind, text in row]
            piped = [text for text in cells if text.count("|") >= 2]
            if piped:
                garbled.findings.append(
                    _block_finding(
                        "L3.table_garbled", block, f"{label}的单元格里还是整行的原始文字（带竖线），表格没有拆开", cell=_snippet(piped[0], 80)
                    )
                )
                continue
            widths = Counter(len(row) for row in rows)
            if len(widths) > 1:
                ragged.findings.append(
                    _block_finding("L3.table_ragged", block, f"{label}各行的列数不一样：{sorted(widths)}", widths=sorted(widths))
                )
            header = rows[0]
            if all(not text.strip() for _kind, text in header):
                no_header.findings.append(_block_finding("L3.table_no_header", block, f"{label}没有表头"))
            empty = sum(1 for text in cells if not text.strip())
            if cells and empty / len(cells) > 0.4:
                sparse.findings.append(
                    _block_finding(
                        "L3.table_sparse",
                        block,
                        f"{label}有 {empty}/{len(cells)} 个空格子，多半是图里的文字被当成了表格",
                        empty=empty,
                        cells=len(cells),
                    )
                )
                continue
            prose_cells = [text for text in cells if len(english_words(text)) >= 3 and not cjk_count(text)]
            if prose_cells and not any(cjk_count(text) for text in cells) and len(prose_cells) * 3 >= len([c for c in cells if c.strip()]):
                untranslated.findings.append(
                    _block_finding(
                        "L3.table_untranslated", block, f"{label}的内容还是英文", cell=_snippet(prose_cells[0], 80)
                    )
                )
        elif block.block_type in {"paragraph", "list_item"} and len(_PIPE_ROW_RE.findall(block.source_text or "")) >= 2:
            flattened.findings.append(
                _block_finding("L3.table_flattened", block, "一张表格被当成了正文段落，读者看到的是一串竖线和文字")
            )
    return [flattened, garbled, ragged, no_header, sparse, is_code, untranslated]


# ---------------------------------------------------------------------------
# L4 代码与格式

_PROSE_END_RE = re.compile(r"[.:?!]\s*$")
_CODE_CHAR_RE = re.compile(r"[{}()\[\]=<>;_#/\\$\"']")
_COMMENT_RE = re.compile(r"^\s*(?:#|//)\s*\S")
_BACKTICK_RE = re.compile(r"`")
_INLINE_CODE_TOKEN_RE = re.compile(
    r"(?<![\w./-])(?:"
    r"[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*\(\)"  # call: load_dotenv(), client.chat()
    r"|[a-z][a-z0-9]*(?:_[a-z0-9]+)+"  # snake_case
    r"|[a-z]+[A-Z][A-Za-z0-9]*"  # camelCase
    r")(?![\w-])"
)
_LIST_MARKER_RE = re.compile(r"^\s*(\(?(?:\d{1,2}|[a-z])[.)])\s+")
_FOOTNOTE_MARKER_RE = re.compile(r"^\s*(\d{1,3}|[*†‡])\s")


def check_code_and_format(inputs: PilotQaInputs) -> list[CheckRun]:
    translated_code = CheckRun("L4.code_translated")
    prose_code = CheckRun("L4.prose_in_code")
    comment_text = CheckRun("L4.code_comment_as_text")
    lost = CheckRun("L4.inline_code_lost")
    markup = CheckRun("L4.inline_code_markup")
    list_marker = CheckRun("L4.list_marker_lost")
    footnote = CheckRun("L4.footnote_marker_lost")
    equation = CheckRun("L4.equation_altered")
    by_chapter: dict[str, list[QaBlock]] = defaultdict(list)
    for block in inputs.blocks:
        by_chapter[block.chapter_id].append(block)
    for chapter_blocks in by_chapter.values():
        for index, block in enumerate(chapter_blocks):
            if _is_code(block) and not _is_table(block):
                rendered = visible_text(block.html)
                if cjk_count(rendered) > cjk_count(block.source_text):
                    translated_code.findings.append(
                        _block_finding("L4.code_translated", block, "代码块里出现了中文：代码被翻译了", rendered=_snippet(rendered))
                    )
                    continue
                text = " ".join(block.source_text.split())
                words = english_words(text)
                code_chars = len(_CODE_CHAR_RE.findall(text))
                if (
                    len(words) >= 8
                    and _PROSE_END_RE.search(text)
                    and code_chars <= max(1, len(text) // 40)
                    and not looks_like_code(block.source_text)
                ):
                    prose_code.findings.append(
                        _block_finding(
                            "L4.prose_in_code",
                            block,
                            "这段正文被排成了代码，读者看到的是英文原文",
                            has_translation=bool(block.target_text.strip()),
                        )
                    )
                continue
            if block.artifact_kind == "equation" or block.block_type == "equation":
                rendered = visible_text(block.html)
                if cjk_count(rendered) > cjk_count(block.source_text):
                    equation.findings.append(_block_finding("L4.equation_altered", block, "公式里出现了中文：公式被翻译了"))
                continue
            if not _is_prose_block(block):
                continue
            source = block.source_text or ""
            target = block.target_text or ""
            neighbours = chapter_blocks[max(0, index - 1) : index + 2]
            if (
                _COMMENT_RE.match(source)
                and len(source.split()) <= 16
                and any(_is_code(other) for other in neighbours if other is not block)
            ):
                comment_text.findings.append(
                    _block_finding(
                        "L4.code_comment_as_text",
                        block,
                        f"代码注释「{_snippet(source, 50)}」被当成了{'标题' if block.block_type == 'heading' else '正文'}，跑到了代码块外面",
                    )
                )
                continue
            if not target.strip():
                continue
            # Backtick spans the renderer turns into <code>; a backtick still visible is broken markup.
            visible = visible_text(block.html)
            if _BACKTICK_RE.search(target) and visible.count("`") >= 2:
                markup.findings.append(
                    _block_finding("L4.inline_code_markup", block, "行内代码显示成了带反引号的文字", rendered=_snippet(visible))
                )
            plain_target = target.replace("`", "")
            missing = [
                token
                for token in dict.fromkeys(match.group(0) for match in _INLINE_CODE_TOKEN_RE.finditer(_URL_RE.sub(" ", source)))
                if token not in plain_target and token.rstrip("()") not in plain_target
            ]
            if missing:
                lost.findings.append(
                    _block_finding(
                        "L4.inline_code_lost",
                        block,
                        f"原文里的代码 {', '.join(missing[:3])} 在译文里不见了（丢了或被翻译了）",
                        tokens=missing[:10],
                    )
                )
            marker = _LIST_MARKER_RE.match(source)
            if block.block_type == "list_item" and marker:
                wanted = marker.group(1)
                if not target.lstrip().startswith(wanted) and not target.lstrip().startswith(wanted.rstrip(".)")):
                    list_marker.findings.append(
                        _block_finding("L4.list_marker_lost", block, f"列表项的编号「{wanted}」在译文里丢了", marker=wanted)
                    )
            note = _FOOTNOTE_MARKER_RE.match(source)
            if block.block_type == "footnote" and note and not target.lstrip().startswith(note.group(1)):
                footnote.findings.append(
                    _block_finding("L4.footnote_marker_lost", block, f"脚注编号「{note.group(1)}」在译文里丢了", marker=note.group(1))
                )
    return [translated_code, prose_code, comment_text, lost, markup, list_marker, footnote, equation]


# ---------------------------------------------------------------------------
# L5 术语


def _translated_sentences(inputs: PilotQaInputs) -> list[QaSentence]:
    return [sentence for sentence in inputs.sentences if sentence.rendered_as_translation and sentence.target_text.strip()]


def check_locked_terms(inputs: PilotQaInputs) -> CheckRun:
    run = CheckRun("L5.locked_term")
    locked = [LockedTerm(source_term=term.source_term, renderings=term.renderings) for term in inputs.glossary if term.locked]
    if not locked:
        return run
    sentences = {sentence.id: sentence for sentence in _translated_sentences(inputs)}
    violations = find_term_violations(
        ((sentence.id, sentence.source_text, sentence.target_text) for sentence in sentences.values()),
        locked,
        skip_empty_targets=True,
    )
    for violation in violations:
        sentence = sentences[violation.unit_id]
        term = violation.source_term.strip()
        # The shared matcher folds case and plurals: "ARES" would match "are". An acronym needs its capitals.
        if sum(1 for char in term if char.isupper()) >= 2 and " " not in term and not re.search(
            rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", sentence.source_text
        ):
            continue
        # A title kept in the original (a paper's, in 《》) is not a term to translate.
        if re.search(rf"《[^》]*{re.escape(term.split()[0])}", sentence.target_text, re.IGNORECASE):
            continue
        run.findings.append(
            _sentence_finding(
                "L5.locked_term",
                sentence,
                f"「{violation.source_term}」锁定译为「{violation.expected_target_term}」，这里没有用",
                source_term=violation.source_term,
                expected=violation.expected_target_term,
            )
        )
    return run


def _renderings_used(target: str, renderings: Sequence[str]) -> list[str]:
    """Which renderings a translation uses; a longer rendering owns the characters it covers ("代码清单" is not also "清单")."""
    text = normalize_target(target)
    used: list[str] = []
    for rendering in sorted({r for r in renderings if r.strip()}, key=lambda r: -len(normalize_target(r))):
        key = normalize_target(rendering)
        if key and key in text:
            used.append(rendering)
            text = text.replace(key, "\x00")
    return used


def _english_kept(target: str, source_term: str) -> bool:
    plain = _CODE_SPAN_RE.sub(" ", target)
    return bool(re.search(rf"(?<![A-Za-z]){re.escape(source_term)}(?:e?s)?(?![A-Za-z])", plain, re.IGNORECASE))


def check_term_consistency(inputs: PilotQaInputs) -> list[CheckRun]:
    """Glossary terms: several renderings in use, or the rendering here and the English there."""
    variants = CheckRun("L5.term_variants")
    left_english = CheckRun("L5.term_left_in_english")
    terms = [term for term in inputs.glossary if term.source_term.strip() and any(r.strip() for r in term.renderings)]
    if not terms:
        return [variants, left_english]
    by_key: dict[str, list[GlossaryTerm]] = defaultdict(list)
    for term in terms:
        by_key[source_term_key(term.source_term)].append(term)
    index = SourceTermIndex(term.source_term for term in terms)
    uses: dict[str, dict[str, list[QaSentence]]] = defaultdict(lambda: defaultdict(list))
    english: dict[str, list[QaSentence]] = defaultdict(list)
    for sentence in _translated_sentences(inputs):
        keys = {occurrence.key for occurrence in index.find(sentence.source_text)}
        for key in keys:
            entries = by_key.get(key) or []
            renderings = [
                rendering
                for entry in entries
                for rendering in entry.renderings
                if normalize_target(rendering) != normalize_target(entry.source_term)
            ]
            for rendering in _renderings_used(sentence.target_text, renderings)[:1]:
                uses[key][rendering].append(sentence)
            # Terms whose English form is itself accepted (acronyms) are not "left in English"; locked ones are L5.locked_term's.
            accepts_english = any(
                normalize_target(r) == normalize_target(entry.source_term) for entry in entries for r in entry.renderings
            )
            if (
                entries
                and not accepts_english
                and not any(entry.locked for entry in entries)
                and any(cjk_count(r) for r in renderings)
                and _english_kept(sentence.target_text, entries[0].source_term)
            ):
                if not _renderings_used(sentence.target_text, renderings):
                    english[key].append(sentence)
    for key, by_rendering in uses.items():
        term = by_key[key][0]
        if len(by_rendering) >= 2:
            forms = sorted(by_rendering.items(), key=lambda item: -len(item[1]))
            minority = forms[1][1][0]
            variants.findings.append(
                Finding(
                    "L5.term_variants",
                    MAJOR,
                    {"kind": "term", "id": key},
                    Location(chapter_id=minority.chapter_id, packet_id=minority.packet_id, block_id=minority.block_id, sentence_id=minority.id, page=minority.page),
                    f"「{term.source_term}」译成了 {'、'.join(f'「{rendering}」{len(items)} 处' for rendering, items in forms)}",
                    {
                        "source_term": term.source_term,
                        "renderings": {rendering: len(items) for rendering, items in forms},
                        "samples": [
                            {"rendering": rendering, "target": _snippet(items[0].target_text, 80), "sentence_id": items[0].id}
                            for rendering, items in forms
                        ],
                    },
                )
            )
        kept = english.get(key) or []
        translated = sum(len(items) for items in by_rendering.values())
        if kept and translated:
            sample = kept[0]
            left_english.findings.append(
                Finding(
                    "L5.term_left_in_english",
                    MAJOR,
                    {"kind": "term", "id": key},
                    Location(chapter_id=sample.chapter_id, packet_id=sample.packet_id, block_id=sample.block_id, sentence_id=sample.id, page=sample.page),
                    f"「{term.source_term}」有 {translated} 处译成「{term.renderings[0]}」，{len(kept)} 处留着英文",
                    {"source_term": term.source_term, "kept": len(kept), "translated": translated, "target": _snippet(sample.target_text, 80)},
                )
            )
    return [variants, left_english]


# Numbered labels and the ways a translation may name them.
_LABELS: dict[str, tuple[str, ...]] = {
    "listing": ("代码清单", "程序清单", "清单", "列表", "代码"),
    "figure": ("插图", "图"),
    "table": ("表格", "表"),
    "example": ("示例", "例子", "例"),
}
_LABEL_RE = re.compile(r"\b(Listing|Figure|Fig\.|Table|Example)s?\s+([A-Z]?\d+(?:\.\d+)+|\d+)\b")


def check_label_rendering(inputs: PilotQaInputs) -> CheckRun:
    run = CheckRun("L5.label_rendering")
    forms: dict[str, dict[str, list[QaSentence]]] = defaultdict(lambda: defaultdict(list))
    for sentence in _translated_sentences(inputs):
        for match in _LABEL_RE.finditer(sentence.source_text):
            label = "figure" if match.group(1).lower().startswith("fig") else match.group(1).lower()
            number = match.group(2)
            found = re.search(rf"([{_CJK}]{{1,4}})\s*{re.escape(number)}(?![\d.])", sentence.target_text)
            if not found:
                continue
            before = found.group(1)
            rendering = next((name for name in _LABELS[label] if before.endswith(name)), None)
            if rendering is not None:
                forms[label][rendering].append(sentence)
    for label, by_rendering in forms.items():
        if len(by_rendering) < 2:
            continue
        ordered = sorted(by_rendering.items(), key=lambda item: -len(item[1]))
        minority = ordered[1][1][0]
        run.findings.append(
            Finding(
                "L5.label_rendering",
                MAJOR,
                {"kind": "term", "id": f"label:{label}"},
                Location(chapter_id=minority.chapter_id, packet_id=minority.packet_id, block_id=minority.block_id, sentence_id=minority.id, page=minority.page),
                f"「{label.capitalize()} N」译成了 {'、'.join(f'「{name}」{len(items)} 处' for name, items in ordered)}",
                {
                    "label": label,
                    "renderings": {name: len(items) for name, items in ordered},
                    "samples": [{"rendering": name, "target": _snippet(items[0].target_text, 80)} for name, items in ordered],
                },
            )
        )
    return run


_STOPWORDS = frozenset(
    """the and for with that this from into over than then they them their there these those have has had
    were was are been being will would could should about which while when where what your you our its
    also only just more most such each other some many much very can may might must not all any both
    between through under after before using used use how why who whom one two three first second new""".split()
)


_GLOSS_RE = re.compile(r"[（(][^（）()]{0,60}[）)]")
# Identifiers and quoted strings are code and names, not words a translation should translate.
_IDENTIFIER_RE = re.compile(r"[\w.]*[_.][\w.]*\w|[\"“”'‘’][^\"“”'‘’]{0,40}[\"“”'‘’]")


def check_mixed_rendering(inputs: PilotQaInputs) -> CheckRun:
    """A common word the translation keeps in English in some sentences and translates in most others ("Agent" / 智能体)."""
    run = CheckRun("L5.mixed_rendering")
    sentences = _translated_sentences(inputs)
    glossary_keys = {source_term_key(term.source_term) for term in inputs.glossary}
    kept: dict[str, list[QaSentence]] = defaultdict(list)
    translated: Counter[str] = Counter()
    proper: set[str] = set()
    for sentence in sentences:
        source = _IDENTIFIER_RE.sub(" ", _without_code_and_urls(sentence.source_text))
        target = _IDENTIFIER_RE.sub(" ", _GLOSS_RE.sub(" ", _without_code_and_urls(sentence.target_text)))
        target_words = {word.casefold() for word in _LATIN_WORD_RE.findall(target)}
        seen: set[str] = set()
        for position, match in enumerate(_LATIN_WORD_RE.finditer(source)):
            word = match.group(0)
            if not word.isalpha() or len(word) < 4:
                continue
            if word[0].isupper():
                if position > 0:
                    proper.add(word.casefold().rstrip("s"))
                continue
            stem = word.casefold()
            stem = stem[:-1] if stem.endswith("s") and not stem.endswith("ss") else stem
            if stem in _STOPWORDS or stem in seen:
                continue
            seen.add(stem)
            if stem in target_words or stem + "s" in target_words:
                kept[stem].append(sentence)
            else:
                translated[stem] += 1
    for stem, kept_sentences in kept.items():
        done = translated.get(stem, 0)
        if stem in proper or source_term_key(stem) in glossary_keys:
            continue
        # Kept twice or more: one English word in one sentence is often a name or a quoted string.
        if done >= 3 and len(kept_sentences) >= 2 and len(kept_sentences) * 3 <= done + len(kept_sentences):
            sample = kept_sentences[0]
            run.findings.append(
                Finding(
                    "L5.mixed_rendering",
                    MINOR,
                    {"kind": "term", "id": stem},
                    Location(chapter_id=sample.chapter_id, packet_id=sample.packet_id, block_id=sample.block_id, sentence_id=sample.id, page=sample.page),
                    f"「{stem}」有 {done} 处译成了中文，{len(kept_sentences)} 处留着英文",
                    {"word": stem, "kept": len(kept_sentences), "translated": done, "target": _snippet(sample.target_text, 80)},
                )
            )
    return run


# ---------------------------------------------------------------------------
# L6 忠实度

_MODEL_ISSUE_TYPES = {
    "MISTRANSLATION_SEMANTIC": "意思译错了",
    "MISTRANSLATION_LOGIC": "逻辑关系译错了",
    "MISTRANSLATION_REFERENCE": "指代译错了",
    "OMISSION": "漏译",
    "STYLE_DRIFT": "译文生硬",
}
_MODEL_SEVERITY = {"critical": BLOCKING, "high": BLOCKING, "medium": MAJOR, "low": MINOR}


def check_model_review(inputs: PilotQaInputs) -> CheckRun:
    run = CheckRun("L6.model_review")
    for issue in inputs.model_issues:
        label = _MODEL_ISSUE_TYPES.get(issue.issue_type)
        if label is None:
            continue
        severity = MINOR if issue.issue_type == "STYLE_DRIFT" else _MODEL_SEVERITY.get(issue.severity, MAJOR)
        target = (
            {"kind": "packet", "id": issue.packet_id}
            if issue.packet_id
            else {"kind": "sentence", "id": issue.sentence_id or issue.issue_id}
        )
        run.findings.append(
            Finding(
                "L6.model_review",
                severity,
                target,
                Location(chapter_id=issue.chapter_id, packet_id=issue.packet_id, block_id=issue.block_id, sentence_id=issue.sentence_id),
                f"模型审校：{label}" + (f"。{_snippet(issue.explanation, 80)}" if issue.explanation else ""),
                {
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "source": _snippet(issue.source_text),
                    "target": _snippet(issue.target_text),
                    "suggested": _snippet(issue.suggested_target_text),
                },
                subject=f"issue:{issue.issue_id}",
            )
        )
    return run


# A translation shorter than this share of its source's length (Chinese characters count double) has lost content.
SHORT_RATIO = 0.2
SHORT_MIN_SOURCE_CHARS = 80
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)*)(%?)(?![A-Za-z0-9_]|\.\d)")
# "405 billion" is 4050 亿 in Chinese: a number with a scale word is written another way.
_SCALED_RE = re.compile(r"^\s*(?:thousand|million|billion|trillion)\b", re.IGNORECASE)
_NEGATION_EN_RE = re.compile(
    r"\b(?:not|no|never|none|nothing|neither|nor|cannot|without|n't|unable|fail(?:s|ed)? to|lack(?:s|ing)?)\b|n't\b",
    re.IGNORECASE,
)
_NEGATION_ZH_RE = re.compile(r"[不没无非未别勿否莫免缺难]|失败|拒绝|除非|除了|毫无|并非|仅|只有|才|缺乏|忽略|避免|防止|而非|取代")
_COMPARATIVE_EN_RE = re.compile(
    r"\b(?:more|less|fewer|better|worse|greater|smaller|larger|higher|lower|faster|slower)\b(?:\s+\w+){0,6}?\s+than\b"
    r"(?!\s+(?:\d|one\b|just\b|simply\b|merely\b|a\s+few\b|ever\b))",
    re.IGNORECASE,
)
_COMPARATIVE_ZH_RE = re.compile(r"比|更|较|不如|超过|少于|多于|优于|胜过|相比|高于|低于|大于|小于|快于|慢于|不及|甚于|不止|不只|远非")


def _weighted_length(text: str) -> int:
    """Length where a Chinese character counts as two Latin ones (a Chinese sentence is about half as long)."""
    cjk = cjk_count(text)
    return len("".join((text or "").split())) + cjk


def _numbers(text: str) -> set[str]:
    values = set()
    text = _URL_RE.sub(" ", text or "")
    for match in _NUMBER_RE.finditer(text):
        if _SCALED_RE.match(text[match.end() :]):
            continue
        number = match.group(1).replace(",", "")
        if "." not in number and len(number) <= 2 and int(number) <= 10 and not match.group(2):
            continue  # small counts are often written out in Chinese (三个、两种)
        values.add(number + match.group(2))
    return values


def check_faithfulness(inputs: PilotQaInputs) -> list[CheckRun]:
    untranslated = CheckRun("L6.untranslated_sentence")
    short = CheckRun("L6.short_translation")
    numbers = CheckRun("L6.number_mismatch")
    negation = CheckRun("L6.negation_lost")
    comparative = CheckRun("L6.comparative_lost")
    sentences_by_block: dict[str, list[QaSentence]] = defaultdict(list)
    for sentence in inputs.sentences:
        sentences_by_block[sentence.block_id].append(sentence)
        if not sentence.rendered_as_translation:
            continue
        if not sentence.target_text.strip() and len(english_words(sentence.source_text)) >= 2:
            untranslated.findings.append(
                _sentence_finding("L6.untranslated_sentence", sentence, "这句没有译文，中文版里缺了这句（或显示成英文）")
            )
            continue
        target = sentence.target_text
        if not target.strip():
            continue
        if _NEGATION_EN_RE.search(_without_code_and_urls(sentence.source_text)) and not _NEGATION_ZH_RE.search(target):
            negation.findings.append(_sentence_finding("L6.negation_lost", sentence, "原文是否定的，译文里找不到否定"))
        if _COMPARATIVE_EN_RE.search(sentence.source_text) and not _COMPARATIVE_ZH_RE.search(target):
            comparative.findings.append(_sentence_finding("L6.comparative_lost", sentence, "原文有比较（more … than），译文里没有"))
    for block in inputs.blocks:
        if not _is_prose_block(block) or not block.target_text.strip():
            continue
        source = block.source_text
        if len(source) >= SHORT_MIN_SOURCE_CHARS and _weighted_length(block.target_text) < SHORT_RATIO * len("".join(source.split())):
            short.findings.append(
                _block_finding(
                    "L6.short_translation",
                    block,
                    f"译文只有原文长度的 {_weighted_length(block.target_text) / max(1, len(''.join(source.split()))):.0%}，可能漏了内容",
                )
            )
        missing = sorted(_numbers(source) - _numbers(block.target_text))
        if missing:
            numbers.findings.append(
                _block_finding("L6.number_mismatch", block, f"原文里的 {', '.join(missing[:3])} 在译文里找不到", numbers=missing[:10])
            )
    return [untranslated, short, numbers, negation, comparative]


# ---------------------------------------------------------------------------
# L7 排版与纯净

# Notes the export or the pipeline writes for reviewers, never part of a book's translation.
LEAKED_MARKERS: tuple[str, ...] = (
    "图片锚点保留",
    "插图原样保留",
    "图片原样保留",
    "代码保持原样",
    "公式保持原样",
    "表格原结构保留",
    "原文工件保留",
    "尾部资料页保留原样",
    "保留原始结构，优先保证可复制与结构保真",
    "参考标识保留",
    "ZH::",
    "[待译]",
    "[未翻译]",
    "（此处省略）",
    "（译文略）",
    "PLACEHOLDER",
    "TODO:",
    "<translation>",
    "</translation>",
)
_LEAK_PREFIX_RE = re.compile(r"^\s*(?:译文|翻译|中文翻译|Translation)\s*[:：]")
_GLYPH_RE = re.compile("[\u00a1\ufffd\ue000-\uf8ff]")
_SPACING_RE = re.compile(rf"(?:(?<=[{_CJK}])( ?)(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])( ?)(?=[{_CJK}]))")
_HALFWIDTH_RE = re.compile(rf"[{_CJK}][,;!?]|[,;!?](?=[{_CJK}])|[{_CJK}]:(?=\s?[{_CJK}])|[{_CJK}]\((?=[^)]*[{_CJK}])")
_ODD_SPACE_RE = re.compile(rf"\u3000|(?<=[{_CJK}])\s{{2,}}|\s{{2,}}(?=[{_CJK}])|(?<=[\u3001\u3002\uff01\uff0c\uff1a\uff1b\uff1f])[ \t]+(?=[{_CJK}\u201c\u300c])")
_ENGLISH_RUN_RE = re.compile(r"(?:\b[A-Za-z][a-z'’]+\b[\s,;]+){7,}[A-Za-z][a-z'’]+[.!?]")


def _reader_text(block: QaBlock) -> str:
    return visible_text(block.html) if block.html else (block.target_text or block.source_text)


def check_purity(inputs: PilotQaInputs) -> list[CheckRun]:
    leaked = CheckRun("L7.leaked_marker")
    glyphs = CheckRun("L7.stray_glyph")
    english = CheckRun("L7.untranslated_prose")
    residue = CheckRun("L7.english_residue")
    halfwidth = CheckRun("L7.halfwidth_punctuation")
    odd = CheckRun("L7.odd_whitespace")
    for block in inputs.blocks:
        reader = _reader_text(block)
        target = block.target_text or ""
        marker = next((marker for marker in LEAKED_MARKERS if marker in reader or marker in target), None)
        if marker is None and _LEAK_PREFIX_RE.match(target):
            marker = _LEAK_PREFIX_RE.match(target).group(0).strip()
        if marker is not None:
            leaked.findings.append(
                _block_finding("L7.leaked_marker", block, f"译文里出现了程序提示「{marker}」，读者只应看到原文的译文", marker=marker)
            )
        if not _is_code(block):
            glyph = _GLYPH_RE.search(reader)
            if glyph:
                glyphs.findings.append(
                    _block_finding(
                        "L7.stray_glyph",
                        block,
                        f"读者看到了异常字符「{glyph.group(0)}」（U+{ord(glyph.group(0)):04X}）",
                        glyph=f"U+{ord(glyph.group(0)):04X}",
                    )
                )
        if not _is_prose_block(block) or not target.strip():
            continue
        plain = _without_code_and_urls(target)
        words = english_words(plain)
        if len(words) >= 6 and cjk_count(plain) * 4 < len(" ".join(words)):
            english.findings.append(_block_finding("L7.untranslated_prose", block, "这段的“译文”还是英文"))
            continue
        run = _ENGLISH_RUN_RE.search(plain.replace("“", " ").replace("”", " "))
        if run and not re.search(r"[\"“”'‘’]", target[max(0, target.find(run.group(0)[:10]) - 2) : max(0, target.find(run.group(0)[:10])) + 1]):
            residue.findings.append(
                _block_finding("L7.english_residue", block, f"译文里留着整句英文：「{_snippet(run.group(0), 60)}」")
            )
        if _HALFWIDTH_RE.search(_without_code_and_urls(target)):
            hit = _HALFWIDTH_RE.search(_without_code_and_urls(target)).group(0)
            halfwidth.findings.append(
                _block_finding("L7.halfwidth_punctuation", block, f"中文里用了半角标点「{hit}」", hit=hit)
            )
        if _ODD_SPACE_RE.search(target):
            odd.findings.append(_block_finding("L7.odd_whitespace", block, "译文里有全角空格或连续空格"))
    return [leaked, glyphs, english, residue, halfwidth, odd, check_spacing(inputs)]


# The share of blocks that keep the book's convention; below it the book mixes both ways and a block is a minor matter.
SPACING_CONVENTION_SHARE = 0.8


def _boundaries(text: str) -> tuple[int, int]:
    """(spaced, unspaced) boundaries between a Chinese character and a Latin letter or digit."""
    spaced = unspaced = 0
    for match in _SPACING_RE.finditer(_without_code_and_urls(text)):
        if (match.group(1) or match.group(2) or "") == " ":
            spaced += 1
        else:
            unspaced += 1
    return spaced, unspaced


def check_spacing(inputs: PilotQaInputs) -> CheckRun:
    """CJK–Latin spacing against the pilot's own majority: a block that goes against it is flagged."""
    run = CheckRun("L7.cjk_latin_spacing")
    counts: list[tuple[QaBlock, int, int]] = []
    for block in inputs.blocks:
        if _is_prose_block(block) and block.target_text.strip():
            spaced, unspaced = _boundaries(block.target_text)
            if spaced or unspaced:
                counts.append((block, spaced, unspaced))
    total_spaced = sum(spaced for _block, spaced, _ in counts)
    total_unspaced = sum(unspaced for _block, _, unspaced in counts)
    total = total_spaced + total_unspaced
    if not total:
        return run
    convention = "spaced" if total_spaced >= total_unspaced else "unspaced"
    conforming = sum(1 for _block, spaced, unspaced in counts if not (unspaced if convention == "spaced" else spaced))
    share = conforming / len(counts)
    for block, spaced, unspaced in counts:
        against = unspaced if convention == "spaced" else spaced
        if not against:
            continue
        run.findings.append(
            _block_finding(
                "L7.cjk_latin_spacing",
                block,
                (
                    f"全书中英文之间{'加空格' if convention == 'spaced' else '不加空格'}，这段有 {against} 处"
                    f"{'没加' if convention == 'spaced' else '加了'}"
                ),
                # A book that already mixes both ways is a minor matter per block.
                severity=MAJOR if share >= SPACING_CONVENTION_SHARE else MINOR,
                convention=convention,
                against=against,
            )
        )
    return run


# ---------------------------------------------------------------------------
# The report

ExtraCheck = Callable[[PilotQaInputs], Iterable[CheckRun]]


def run_layers(inputs: PilotQaInputs, extra_checks: Sequence[ExtraCheck] = ()) -> list[dict[str, Any]]:
    """Every check on the pilot, as one report per layer in L1–L7 order.

    ``extra_checks`` are pluggable reviewers (a vision pass over figures, a sampled model read of
    faithfulness) returning ``CheckRun``s whose check ids are registered in ``CHECKS``.
    """
    health = parse_health_runs(inputs.health, inputs.chapters)
    runs: list[CheckRun] = [
        *check_pilot_chapters(inputs),
        *check_exported_chapters(inputs),
        *check_headings_and_headers(inputs),
        *check_figures(inputs),
        *check_tables(inputs),
        *check_code_and_format(inputs),
        check_locked_terms(inputs),
        *check_term_consistency(inputs),
        check_label_rendering(inputs),
        check_mixed_rendering(inputs),
        check_model_review(inputs),
        *check_faithfulness(inputs),
        *check_purity(inputs),
        *health.values(),
    ]
    for extra in extra_checks:
        runs.extend(extra(inputs))
    merged: dict[str, CheckRun] = {}
    for run in runs:
        existing = merged.get(run.check_id)
        if existing is None:
            merged[run.check_id] = run
            continue
        existing.findings.extend(run.findings)
        if run.count is not None:
            existing.count = (existing.count or 0) + run.count
    by_layer: dict[str, list[CheckRun]] = defaultdict(list)
    for spec in _SPECS:
        run = merged.get(spec.check_id)
        if run is None:
            continue
        # One finding per repair target and check: the same block found twice is one problem.
        unique: dict[str, Finding] = {}
        for finding in run.findings:
            unique.setdefault(finding.key, finding)
        run.findings = list(unique.values())[:MAX_FINDINGS_PER_CHECK]
        by_layer[spec.layer].append(run)
    return [layer_report(layer, by_layer.get(layer, [])) for layer, _name in LAYERS]
