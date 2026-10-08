"""The pilot repair loop's decisions: classify a finding, choose a repair path, judge the result.

docs/agent-upgrade/08-pilot-quality-gate.md §7. Pure functions over the layered QA's report
(``pilot_qa.run_layers``); the service (services/pilot_repair.py) applies what they decide.

Classification (§7.1), first rule that holds wins:

1. the lessons ledger has this issue class as a systemic defect already fixed in code: it recurred;
2. the ledger has seen the issue class in two books or more;
3. the cause can be said as a generic rule, independent of this book's words (``GENERIC_CAUSES``);
4. otherwise the problem is this book's own.

The ledger (Q6) does not exist yet: rules 1 and 2 ask a ``LessonLedger``, and ``NoLedger`` answers
"never seen". A systemic finding still gets a workaround for this book from the toolbox; a defect
ticket carries it to the dev side (§7.3).

Repair paths (§7.2): each check maps to an ordered list of paths from the closed toolbox. The loop
tries them in order and keeps the first that helps. A path's parameters come from the finding
(deterministic, ``decided_by="rule"``); the few paths that need judgment (where to split a block
the rules cannot place) ask the pilot repair agent (``decided_by="agent"``).

Judging (§7.2, last line): a repair is kept only when the targeted findings improved and no other
check got worse, by the same checks run again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from book_agent.domain.quality.pilot_qa import CHECKS, LAYER_NAMES
from book_agent.domain.structure.parse_health import BLOCKING, MAJOR, MINOR

SYSTEMIC = "systemic"
BOOK_SPECIFIC = "book_specific"

# The toolbox (§7.2). Every repair is one of these; nothing here changes code.
TOOL_RECOVERY_SKILLS = "recovery_skills"
TOOL_RESPLIT = "resplit_chapters"
TOOL_STRUCTURE = "structure_edit"
TOOL_FIGURE_REGION = "figure_region_override"
TOOL_BOOK_MD = "book_instructions"
TOOL_GLOSSARY = "glossary"
TOOL_RETRANSLATE = "retranslate_packets"
# A reversible correction of the translation text (a new attempt through the segment edit service).
TOOL_SEGMENT_EDIT = "segment_edit"
# Run the detector behind a review issue again (the review pass, or the export gate): an issue a
# repair or a fixed rule no longer finds is resolved; nothing is changed but the issue ledger.
TOOL_REVIEW_RECHECK = "review_recheck"
TOOLS: tuple[str, ...] = (
    TOOL_RECOVERY_SKILLS,
    TOOL_RESPLIT,
    TOOL_STRUCTURE,
    TOOL_FIGURE_REGION,
    TOOL_BOOK_MD,
    TOOL_GLOSSARY,
    TOOL_RETRANSLATE,
    TOOL_SEGMENT_EDIT,
    TOOL_REVIEW_RECHECK,
)

# How an attempt covers findings: one finding, every finding of the check at once, or the findings
# of one packet (a packet is retranslated once for all its problems).
SCOPE_FINDING = "finding"
SCOPE_CHECK = "check"
SCOPE_PACKET = "packet"


@dataclass(frozen=True, slots=True)
class RepairPath:
    tool: str
    path: str
    scope: str = SCOPE_FINDING
    # Calls the translation model (costs money, counted against the budget).
    costs_model: bool = False
    # The parameters need a model's judgment (the pilot repair agent proposes them).
    needs_agent: bool = False

    @property
    def name(self) -> str:
        return f"{self.tool}.{self.path}"


_RETRANSLATE = RepairPath(TOOL_RETRANSLATE, "with_hints", SCOPE_PACKET, costs_model=True)
_STRUCTURE_AGENT = RepairPath(TOOL_STRUCTURE, "agent_proposal", SCOPE_FINDING, needs_agent=True)
_RECHECK = RepairPath(TOOL_REVIEW_RECHECK, "rerun_detectors")

REPAIR_PATHS: dict[str, tuple[RepairPath, ...]] = {
    # L1 结构
    "L1.chapters_vs_outline": (RepairPath(TOOL_RESPLIT, "split_at_outline_entry"),),
    "L1.chapter_folded": (RepairPath(TOOL_RESPLIT, "split_at_chapter_heading"),),
    "L1.heading_is_prose": (RepairPath(TOOL_STRUCTURE, "relabel_as_paragraph"),),
    "L1.running_header_as_text": (RepairPath(TOOL_STRUCTURE, "hide_running_header"),),
    "L1.heading_merged_into_text": (RepairPath(TOOL_STRUCTURE, "split_run_in_heading"), _STRUCTURE_AGENT),
    "L1.heading_as_text": (RepairPath(TOOL_STRUCTURE, "relabel_as_heading"),),
    # The icon and its lines become one callout block; its packets are retranslated line by line.
    "L1.icon_callout_split": (RepairPath(TOOL_STRUCTURE, "merge_icon_callout", costs_model=True),),
    # A cut-short copy of the block beside it (an older parse clipped a listing's box into an annotation).
    "L1.clipped_text_copy": (RepairPath(TOOL_STRUCTURE, "hide_clipped_copy"),),
    # The part's name read again from its page, split out of the introduction and set as a heading.
    "L1.part_title_unnamed": (RepairPath(TOOL_STRUCTURE, "split_part_name", costs_model=True),),
    "L1.review_blocking": (_RECHECK,),
    # The chapters read again by the current EPUB parser (structure refresh): blocks keep their
    # translations where their text is unchanged; only new blocks (definition terms) are translated.
    "L1.epub_structure_outdated": (RepairPath(TOOL_STRUCTURE, "refresh_epub_structure", SCOPE_CHECK, costs_model=True),),
    # Structure the reading copy draws (lists, sidebars, split paragraphs, listing captions): the parser
    # side's QA rules name these paths in their repair hints; the findings carry the block ids.
    "L1.sublist_as_text": (RepairPath(TOOL_STRUCTURE, "nest_list_items", costs_model=True),),
    "L1.sidebar_as_text": (RepairPath(TOOL_STRUCTURE, "relabel_as_sidebar", costs_model=True),),
    "L1.paragraph_split_midsentence": (RepairPath(TOOL_STRUCTURE, "join_continuation", costs_model=True),),
    "L2.listing_caption_unlinked": (RepairPath(TOOL_STRUCTURE, "link_listing_caption"),),
    # L2 图表
    "L2.figure_regions": (RepairPath(TOOL_FIGURE_REGION, "fit_to_figure"),),
    "L2.caption_swallows_body": (
        RepairPath(TOOL_STRUCTURE, "split_caption"),
        RepairPath(TOOL_STRUCTURE, "unfold_caption_context"),
        _STRUCTURE_AGENT,
    ),
    "L2.caption_folds": (RepairPath(TOOL_STRUCTURE, "unfold_caption_context"),),
    "L2.orphan_caption": (RepairPath(TOOL_STRUCTURE, "link_caption"),),
    "L2.duplicated_labels": (RepairPath(TOOL_STRUCTURE, "hide_duplicated_label"),),
    "L2.figure_residue": (RepairPath(TOOL_STRUCTURE, "hide_figure_residue"),),
    # Minor: link the caption set right next to it, else accept a figure its page gives no caption.
    "L2.figure_without_caption": (
        RepairPath(TOOL_STRUCTURE, "link_adjacent_caption"),
        RepairPath(TOOL_STRUCTURE, "accept_uncaptioned_figure"),
    ),
    # An icon drawn as a figure card: when it leads a callout, the callout takes it.
    "L2.tiny_icon_figure": (RepairPath(TOOL_STRUCTURE, "merge_icon_callout", costs_model=True),),
    # The export gate's "no picture" is stale once the figure has one: its recheck resolves it.
    "L2.figure_without_image": (_RECHECK,),
    "L2.review_blocking": (_RECHECK,),
    # L3 表格
    # The table read again from its page (rows that share one cell box split apart); only its cells
    # are translated again. Cells set from the "|" text is the fallback without the PDF.
    "L3.table_garbled": (
        RepairPath(TOOL_STRUCTURE, "reextract_table", costs_model=True),
        RepairPath(TOOL_STRUCTURE, "rebuild_table_cells"),
    ),
    "L3.table_rows_merged": (RepairPath(TOOL_STRUCTURE, "reextract_table", costs_model=True),),
    "L3.table_flattened": (RepairPath(TOOL_STRUCTURE, "rebuild_table_cells"),),
    "L3.table_untranslated": (_RETRANSLATE,),
    # The block becomes code (what the reading copy already shows) and the export gate checks it again.
    "L3.table_is_code": (RepairPath(TOOL_STRUCTURE, "relabel_table_as_code"), _RECHECK),
    # A mostly empty "table" inside or next to a figure is the figure's text: the figure takes it.
    "L3.table_sparse": (RepairPath(TOOL_STRUCTURE, "absorb_sparse_table_into_figure"),),
    "L3.review_blocking": (_RECHECK,),
    # L4 代码与格式
    "L4.code_translated": (_RETRANSLATE,),
    "L4.prose_in_code": (RepairPath(TOOL_STRUCTURE, "relabel_as_paragraph"),),
    "L4.pseudo_headings": (RepairPath(TOOL_STRUCTURE, "relabel_as_code"),),
    "L4.code_comment_as_text": (RepairPath(TOOL_STRUCTURE, "relabel_as_code"),),
    "L4.inline_code_lost": (_RETRANSLATE,),
    "L4.list_marker_lost": (_RETRANSLATE,),
    "L4.list_marker_doubled": (RepairPath(TOOL_SEGMENT_EDIT, "strip_doubled_list_marker", SCOPE_CHECK), _RETRANSLATE),
    # Each line of the callout translated on its own (the output guardrail holds the model to it).
    "L4.callout_lines_collapsed": (RepairPath(TOOL_RETRANSLATE, "callout_line_by_line", costs_model=True),),
    "L4.footnote_marker_lost": (_RETRANSLATE,),
    "L4.equation_altered": (_RETRANSLATE,),
    "L4.review_blocking": (_RECHECK,),
    # L5 术语: settle the rendering in the glossary (it stays for the whole book), then fix the pilot's text.
    "L5.locked_term": (RepairPath(TOOL_SEGMENT_EDIT, "use_locked_rendering"), _RETRANSLATE),
    # The model first: a rendering can be right in its context ("嵌入向量" for "embedding vector");
    # the mechanical swap only when retranslating did not settle it.
    "L5.term_variants": (
        RepairPath(TOOL_GLOSSARY, "lock_majority_and_retranslate", costs_model=True),
        RepairPath(TOOL_GLOSSARY, "lock_majority_and_unify"),
    ),
    "L5.term_left_in_english": (RepairPath(TOOL_GLOSSARY, "lock_and_retranslate", costs_model=True),),
    "L5.review_blocking": (_RECHECK,),
    # The source's English title put back in place of the altered one; retranslation only if that fails.
    "L5.cited_title_altered": (RepairPath(TOOL_SEGMENT_EDIT, "restore_cited_title", SCOPE_CHECK), _RETRANSLATE),
    "L5.label_rendering": (
        RepairPath(TOOL_GLOSSARY, "unify_label_rendering"),
        RepairPath(TOOL_GLOSSARY, "lock_label_and_retranslate", costs_model=True),
    ),
    # L6 忠实度
    "L6.model_review": (RepairPath(TOOL_SEGMENT_EDIT, "apply_reviewer_suggestion"), _RETRANSLATE),
    "L6.untranslated_sentence": (_RETRANSLATE,),
    "L6.short_translation": (_RETRANSLATE,),
    "L6.number_mismatch": (_RETRANSLATE,),
    "L6.negation_lost": (_RETRANSLATE,),
    "L6.review_blocking": (_RECHECK,),
    # L7 排版与纯净: deterministic text fixes over every finding of the check; the book's convention
    # goes into BOOK.md so the whole-book run writes it that way from the start.
    "L7.leaked_marker": (RepairPath(TOOL_SEGMENT_EDIT, "strip_leaked_markers", SCOPE_CHECK), _RETRANSLATE),
    # Set wholly in the code face (program output, code parsed as prose): code, not translated.
    "L7.untranslated_prose": (RepairPath(TOOL_STRUCTURE, "relabel_code_face_as_code"), _RETRANSLATE),
    "L7.cjk_latin_spacing": (RepairPath(TOOL_SEGMENT_EDIT, "cjk_latin_spacing", SCOPE_CHECK),),
    "L7.halfwidth_punctuation": (RepairPath(TOOL_SEGMENT_EDIT, "fullwidth_punctuation", SCOPE_CHECK),),
    # An unpaired emphasis marker in a translation: dropped where the source has no such marker.
    # (A reading copy written before markers were rendered is written again: ``needs_reexport``.)
    "L7.emphasis_marker": (RepairPath(TOOL_SEGMENT_EDIT, "balance_emphasis_markers", SCOPE_CHECK), _RETRANSLATE),
    "L7.review_blocking": (_RECHECK,),
}


def needs_reexport(layers: Sequence[Mapping[str, Any]]) -> bool:
    """The reading copy on disk shows a problem the export no longer writes (``"reexport": true`` in
    a finding's evidence): writing it again is the repair, whatever else the loop keeps."""
    return any(
        bool((finding.get("evidence") or {}).get("reexport"))
        for layer in layers or ()
        for finding in layer.get("findings") or ()
    )


STRUCTURE_HINT_PREFIX = "structure_edit."


def paths_for(check_id: str, finding: Mapping[str, Any] | None = None) -> tuple[RepairPath, ...]:
    """The check's paths; a check this table does not know may name its path in its finding's repair
    hint ("structure_edit.nest_list_items"), taken when the toolbox has that structure edit."""
    paths = REPAIR_PATHS.get(check_id, ())
    hint = str((finding or {}).get("repair") or ((finding or {}).get("evidence") or {}).get("repair_path") or "")
    if hint.startswith(STRUCTURE_HINT_PREFIX) and hint[len(STRUCTURE_HINT_PREFIX) :] in HINTED_STRUCTURE_EDITS:
        hinted = RepairPath(TOOL_STRUCTURE, hint[len(STRUCTURE_HINT_PREFIX) :], costs_model=True)
        if all(path.name != hinted.name for path in paths):
            paths = (hinted, *paths)
    return paths


# Structure edits a finding's repair hint may ask for by name.
HINTED_STRUCTURE_EDITS = frozenset({"nest_list_items", "relabel_as_sidebar", "join_continuation", "link_listing_caption"})


# Minor checks the loop still takes on: their repair is cheap and deterministic (no model call).
MINOR_REPAIRABLE_CHECKS = frozenset({"L2.figure_without_caption"})


# ---------------------------------------------------------------------------
# Classification (§7.1)


@dataclass(frozen=True, slots=True)
class GenericCause:
    """A cause stated as a rule that holds for any book: a finding it explains is a systemic defect."""

    issue_class: str
    check_id: str
    component: str  # parser | export | translation
    cause: str
    # Only findings whose evidence says so ("page_furniture" in the figure problem kinds).
    detail: str | None = None


GENERIC_CAUSES: tuple[GenericCause, ...] = (
    GenericCause("L1.chapters_vs_outline", "L1.chapters_vs_outline", "parser", "有 PDF 书签时，章节没有按书签的章级条目切分"),
    GenericCause("L1.chapter_folded", "L1.chapter_folded", "parser", "另一章的章标题出现在这一章里：章节切分漏掉了一个章起点"),
    GenericCause("L1.export_chapter_missing", "L1.export_chapter_missing", "export", "导出没有为这一章生成独立的章节"),
    GenericCause("L1.running_header_as_text", "L1.running_header_as_text", "parser", "页眉（页码 + 章节标题）没有识别为页眉，当成了正文或脚注"),
    GenericCause(
        "L1.heading_merged_into_text",
        "L1.heading_merged_into_text",
        "parser",
        "行内小标题（小写词组紧跟在句点后）没有从段落里拆出来",
    ),
    GenericCause(
        "L1.epub_structure_outdated",
        "L1.epub_structure_outdated",
        "parser",
        "EPUB 解析器旧版本没有保留列表、术语表（dt）和注意/侧栏框的结构",
    ),
    GenericCause("L1.heading_is_prose", "L1.heading_is_prose", "parser", "以句号结尾的整句被识别成了标题"),
    GenericCause(
        "L1.icon_callout_split",
        "L1.icon_callout_split",
        "parser",
        "小图标引出的提示框（图标 + 非正文字体的几行）没有识别成一个整体，图标成了图片、每行成了段落",
    ),
    GenericCause("L2.tiny_icon_figure", "L2.tiny_icon_figure", "export", "一行字大小的小图标被当成整幅插图排版"),
    GenericCause(
        "L2.figure_regions:page_furniture",
        "L2.figure_regions",
        "parser",
        "图片区域没有排除页眉、页码、页脚",
        detail="page_furniture",
    ),
    GenericCause("L2.figure_regions:caption", "L2.figure_regions", "parser", "图片区域把图注框了进去", detail="caption"),
    GenericCause("L2.caption_swallows_body", "L2.caption_swallows_body", "export", "图注后面的正文被并进了图注"),
    GenericCause("L2.caption_folds", "L2.caption_folds", "export", "导出把图注附近的正文当成了图的说明"),
    GenericCause("L2.duplicated_labels", "L2.duplicated_labels", "parser", "图中的文字标注又被当成正文段落保留"),
    GenericCause("L3.table_garbled", "L3.table_garbled", "parser", "表格的行列没有拆开，单元格里还是整行原文（带竖线）"),
    GenericCause("L3.table_flattened", "L3.table_flattened", "parser", "表格被识别成了正文段落"),
    GenericCause(
        "L3.table_rows_merged",
        "L3.table_rows_merged",
        "parser",
        "只有表头和底部有横线的表格，几行正文挤在一个单元格框里，没有按行距拆开",
    ),
    GenericCause("L1.clipped_text_copy", "L1.clipped_text_copy", "parser", "代码框的裁剪把旁边注释的半截文字也读了进来，成了一段"),
    GenericCause("L1.part_title_unnamed", "L1.part_title_unnamed", "parser", "部分页的大字号名称并进了下面的正文段落"),
    GenericCause("L4.list_marker_doubled", "L4.list_marker_doubled", "translation", "编号列表项的译文开头多了圆点"),
    GenericCause("L4.callout_lines_collapsed", "L4.callout_lines_collapsed", "translation", "重译时几行提示词被合成一个译文，分行丢了"),
    GenericCause("L4.pseudo_headings", "L4.pseudo_headings", "parser", "等宽字体或 # 开头的代码注释被识别成了标题"),
    GenericCause("L4.code_comment_as_text", "L4.code_comment_as_text", "parser", "代码注释被拆到了代码块外面"),
    GenericCause("L4.inline_code_markup", "L4.inline_code_markup", "export", "行内代码的反引号没有渲染成代码样式"),
    GenericCause("L7.leaked_marker", "L7.leaked_marker", "export", "程序提示（如「图片锚点保留」）写进了读者看到的译文"),
    GenericCause("L7.emphasis_marker", "L7.emphasis_marker", "translation", "译文里的强调标记（*、**）没有成对，读者看到了星号"),
    GenericCause("L7.stray_glyph", "L7.stray_glyph", "parser", "符号字体的字符被解码成了 ¡ 或乱码"),
    GenericCause(
        "L7.cjk_latin_spacing",
        "L7.cjk_latin_spacing",
        "translation",
        "译文中英文之间的空格没有统一成全书的习惯（输出没有做规范化）",
    ),
    GenericCause("L7.halfwidth_punctuation", "L7.halfwidth_punctuation", "translation", "中文里用了半角标点（输出没有做规范化）"),
)


class LessonLedger(Protocol):
    """What the classification asks of the lessons ledger (Q6). Rules 1 and 2 of §7.1."""

    def fixed_systemic_defect(self, issue_class: str) -> str | None:
        """The ledger record of a systemic defect of this class already fixed in code, if any (rule 1)."""

    def books_with_issue(self, issue_class: str, *, exclude_document_id: str | None = None) -> int:
        """How many other books had this issue class (rule 2)."""


class NoLedger:
    """Until the ledger exists: nothing has been seen before."""

    def fixed_systemic_defect(self, issue_class: str) -> str | None:
        return None

    def books_with_issue(self, issue_class: str, *, exclude_document_id: str | None = None) -> int:
        return 0


LEDGER_BOOKS_FOR_SYSTEMIC = 2


@dataclass(frozen=True, slots=True)
class Classification:
    kind: str  # systemic | book_specific
    rule: int  # which rule of §7.1 decided it
    reason: str
    issue_class: str
    cause: GenericCause | None = None
    ledger_ref: str | None = None

    @property
    def systemic(self) -> bool:
        return self.kind == SYSTEMIC

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "rule": self.rule,
            "reason": self.reason,
            "issue_class": self.issue_class,
            "component": self.cause.component if self.cause else None,
            "ledger_ref": self.ledger_ref,
        }


def _details(finding: Mapping[str, Any]) -> set[str]:
    detail = str((finding.get("evidence") or {}).get("detail") or "")
    return {part.strip() for part in detail.split(",") if part.strip()}


def generic_cause(finding: Mapping[str, Any]) -> GenericCause | None:
    check_id = str(finding.get("check_id") or "")
    details = _details(finding)
    for cause in GENERIC_CAUSES:
        if cause.check_id != check_id:
            continue
        if cause.detail is None or cause.detail in details:
            return cause
    return None


def issue_class(finding: Mapping[str, Any]) -> str:
    """The finding's class: its generic cause's id when it has one, else its check."""
    cause = generic_cause(finding)
    return cause.issue_class if cause else str(finding.get("check_id") or "")


def classify(
    finding: Mapping[str, Any], ledger: LessonLedger | None = None, *, document_id: str | None = None
) -> Classification:
    ledger = ledger or NoLedger()
    cls = issue_class(finding)
    cause = generic_cause(finding)
    ref = ledger.fixed_systemic_defect(cls)
    if ref:
        return Classification(SYSTEMIC, 1, f"经验账本里已判为系统性并在代码中修复过（{ref}），这次复发说明修复不完整", cls, cause, ref)
    books = ledger.books_with_issue(cls, exclude_document_id=document_id)
    if books >= LEDGER_BOOKS_FOR_SYSTEMIC:
        return Classification(SYSTEMIC, 2, f"经验账本里已有 {books} 本书出现同类问题", cls, cause)
    if cause is not None:
        return Classification(SYSTEMIC, 3, f"系统性（候选）：{cause.cause}", cls, cause)
    return Classification(BOOK_SPECIFIC, 4, "这本书特有：原因取决于这本书的内容或版式", cls)


# ---------------------------------------------------------------------------
# Work list


def findings_of(layers: Sequence[Mapping[str, Any]], severities: Iterable[str] = (BLOCKING, MAJOR)) -> list[dict[str, Any]]:
    """The report's findings of these severities, blocking first, then layer by layer."""
    wanted = set(severities)
    order = {BLOCKING: 0, MAJOR: 1, MINOR: 2}
    findings = [dict(finding) for layer in layers for finding in layer.get("findings") or [] if finding.get("severity") in wanted]
    return sorted(findings, key=lambda finding: (order.get(finding.get("severity"), 9), str(finding.get("layer")), str(finding.get("check_id"))))


def repairable_findings(layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The loop's work: blocking and major findings, then the minor ones it repairs cheaply."""
    return [
        *findings_of(layers),
        *[finding for finding in findings_of(layers, (MINOR,)) if finding.get("check_id") in MINOR_REPAIRABLE_CHECKS],
    ]


@dataclass(slots=True)
class WorkItem:
    """Findings one attempt repairs with one path."""

    check_id: str
    path: RepairPath
    findings: list[dict[str, Any]]

    @property
    def keys(self) -> list[str]:
        return [str(finding["key"]) for finding in self.findings]

    @property
    def severity(self) -> str:
        severities = {finding.get("severity") for finding in self.findings}
        return BLOCKING if BLOCKING in severities else MAJOR if MAJOR in severities else MINOR


def plan_work(findings: Sequence[Mapping[str, Any]], tried: set[tuple[str, str]]) -> list[WorkItem]:
    """The next attempts: for each finding its first path not tried yet, grouped by the path's scope.

    ``tried`` holds (finding key, path name) pairs already attempted. Structure and chapter repairs
    come first (they change sentences and packets), then terms, retranslation, and text fixes last.
    """
    tool_order = {
        TOOL_RESPLIT: 0,
        TOOL_STRUCTURE: 1,
        TOOL_FIGURE_REGION: 2,
        TOOL_RECOVERY_SKILLS: 3,
        TOOL_GLOSSARY: 4,
        TOOL_BOOK_MD: 5,
        TOOL_RETRANSLATE: 6,
        TOOL_SEGMENT_EDIT: 7,
        # After the repairs that change the book, so the detectors see them.
        TOOL_REVIEW_RECHECK: 8,
    }
    groups: dict[tuple[str, str, str], WorkItem] = {}
    for finding in findings:
        check_id = str(finding.get("check_id") or "")
        path = next((path for path in paths_for(check_id, finding) if (str(finding.get("key")), path.name) not in tried), None)
        if path is None:
            continue
        if path.scope == SCOPE_CHECK:
            group = (path.name, check_id, "")
        elif path.scope == SCOPE_PACKET:
            packet = str((finding.get("location") or {}).get("packet_id") or (finding.get("target") or {}).get("id") or finding.get("key"))
            # Several checks' findings on one packet: one retranslation fixes them together.
            group = (path.name, "", packet)
        else:
            group = (path.name, check_id, str(finding.get("key")))
        item = groups.get(group)
        if item is None:
            groups[group] = WorkItem(check_id=check_id, path=path, findings=[dict(finding)])
        else:
            item.findings.append(dict(finding))
    severity_order = {BLOCKING: 0, MAJOR: 1, MINOR: 2}
    return sorted(
        groups.values(),
        key=lambda item: (tool_order.get(item.path.tool, 9), severity_order.get(item.severity, 9), item.check_id),
    )


# ---------------------------------------------------------------------------
# Judging a repair


@dataclass(slots=True)
class QaSnapshot:
    """What a judgment needs of a QA report: severity counts, each check's count, the finding keys."""

    counts: dict[str, int]
    check_counts: dict[str, int]
    keys: set[str]
    severity_by_key: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_layers(cls, layers: Sequence[Mapping[str, Any]]) -> "QaSnapshot":
        counts: Counter[str] = Counter()
        check_counts: dict[str, int] = {}
        keys: set[str] = set()
        severity_by_key: dict[str, str] = {}
        for layer in layers:
            for severity, count in (layer.get("counts") or {}).items():
                counts[severity] += int(count or 0)
            for check in layer.get("checks") or []:
                check_counts[str(check["check_id"])] = int(check.get("count") or 0)
            for finding in layer.get("findings") or []:
                keys.add(str(finding["key"]))
                severity_by_key[str(finding["key"])] = str(finding.get("severity"))
        return cls(
            counts={severity: int(counts.get(severity, 0)) for severity in (BLOCKING, MAJOR, MINOR)},
            check_counts=check_counts,
            keys=keys,
            severity_by_key=severity_by_key,
        )

    def to_json(self, target_keys: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "checks": {check_id: count for check_id, count in sorted(self.check_counts.items()) if count},
            "targets_present": [key for key in target_keys if key in self.keys],
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    keep: bool
    reason: str
    resolved_keys: tuple[str, ...] = ()
    worse_checks: tuple[str, ...] = ()


def judge(before: QaSnapshot, after: QaSnapshot, target_keys: Sequence[str], target_checks: Iterable[str]) -> Verdict:
    """Keep a repair when a targeted finding went away (or got less severe), its check did not grow,
    and no other blocking or major check got worse. Minor checks are reported, never decisive.
    """
    order = {BLOCKING: 0, MAJOR: 1, MINOR: 2}
    resolved = tuple(
        key
        for key in target_keys
        if key not in after.keys
        or order.get(after.severity_by_key.get(key, MINOR), 2) > order.get(before.severity_by_key.get(key, MINOR), 2)
    )
    targets = set(target_checks)
    worse = []
    for check_id in sorted(set(before.check_counts) | set(after.check_counts)):
        spec = CHECKS.get(check_id)
        if spec is None or spec.severity == MINOR:
            continue
        if after.check_counts.get(check_id, 0) > before.check_counts.get(check_id, 0):
            worse.append(check_id)
    if after.counts.get(BLOCKING, 0) > before.counts.get(BLOCKING, 0) and not worse:
        worse.append("blocking")
    if worse:
        return Verdict(False, "worse:" + ",".join(worse), resolved, tuple(worse))
    if not resolved:
        return Verdict(False, "not_improved", resolved)
    if any(after.check_counts.get(check_id, 0) > before.check_counts.get(check_id, 0) for check_id in targets):
        return Verdict(False, "not_improved", resolved)
    return Verdict(True, "improved", resolved)


# Checks a retranslation can fail by itself (a line left in English, a program note leaked in):
# the model's own flake, not a sign the repair was wrong.
RETRANSLATION_FLAKE_CHECKS = frozenset({"L6.untranslated_sentence", "L7.untranslated_prose", "L7.leaked_marker"})


def self_inflicted_packets(
    verdict: Verdict, before: QaSnapshot, after_layers: Sequence[Mapping[str, Any]], retranslated: Iterable[str]
) -> list[str]:
    """The packets to retranslate once more when a repair was judged worse only for its retranslation's
    own output: every check that got worse is one a flaky model output explains, and every new finding
    of those checks is in a packet the attempt just retranslated. Empty otherwise (the repair is reverted).
    """
    retranslated = set(retranslated)
    if verdict.keep or not verdict.worse_checks or not retranslated:
        return []
    worse = set(verdict.worse_checks) - {"blocking"}
    if not worse or not worse <= RETRANSLATION_FLAKE_CHECKS:
        return []
    fresh = [
        finding
        for finding in findings_of(after_layers, (BLOCKING, MAJOR, MINOR))
        if finding.get("check_id") in RETRANSLATION_FLAKE_CHECKS and str(finding.get("key")) not in before.keys
    ]
    packets = [str((finding.get("location") or {}).get("packet_id") or "") for finding in fresh]
    if not fresh or not all(packet in retranslated for packet in packets):
        return []
    return sorted(set(packets))


# ---------------------------------------------------------------------------
# Escalation: one question per issue class (§7.4)


def _pages(findings: Sequence[Mapping[str, Any]]) -> list[int]:
    return sorted({int(page) for finding in findings if isinstance((page := (finding.get("location") or {}).get("page")), int)})


def _page_text(pages: Sequence[int]) -> str:
    if not pages:
        return ""
    shown = "、".join(str(page) for page in pages[:4])
    return f"第 {shown}{' 等' if len(pages) > 4 else ''} 页"


def escalation_question(check_id: str, findings: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """(title, question) for the reader's inbox: what could not be repaired, and one concrete yes/no question."""
    spec = CHECKS.get(check_id)
    title = spec.title if spec else check_id
    where = _page_text(_pages(findings))
    count = len(findings)
    sample = next((str((finding.get("evidence") or {}).get("source") or "") for finding in findings if (finding.get("evidence") or {}).get("source")), "")
    sample = sample if len(sample) <= 40 else sample[:39] + "…"
    lead = f"{where}{'的' if where else ''}"
    markers = sorted({str((finding.get("evidence") or {}).get("marker") or "") for finding in findings} - {""})
    marker_text = "程序提示" + (f"（{'、'.join(f'「{marker}」' for marker in markers[:3])}）" if markers else "")
    questions = {
        "L2.figure_regions": f"{lead}图片自动裁剪不干净，修正了几次没有成功。要把这张图所在的页面区域原样截取、保留成图片吗？",
        "L2.figure_without_image": f"{lead}图在中文版里没有图片，自动修复没有成功。要把原书这一页的图直接截图放进中文版吗？",
        "L2.caption_swallows_body": f"{lead}图注里混进了正文，自动拆分没有成功。要把图注只保留「图 N.N」开头的第一句、其余作为正文吗？",
        "L2.caption_folds": f"{lead}正文被并进了图注，自动拆分没有成功。要把这些段落移回正文吗？",
        "L3.table_garbled": f"{lead}的表格没能拆成单元格。要先把这张表按原书的样子截图保留吗？",
        "L3.table_rows_merged": f"{lead}的表格有几行挤在一行里，按原书重新读取没有成功。要先把这张表按原书的样子截图保留吗？",
        "L3.table_flattened": f"{lead}的表格被排成了文字，自动重建没有成功。要先把这张表按原书的样子截图保留吗？",
        "L1.chapters_vs_outline": f"{lead}章节切分和书签不一致，自动按书签拆分没有成功。要以书签为准重新切分章节吗？",
        "L1.chapter_folded": f"「{sample}」应当是单独的一章，自动拆分没有成功。要按书签把它拆成单独一章吗？",
        "L1.export_chapter_missing": "中文版里有一章没有单独成章。要在全书翻译前先由我们修复导出再继续吗？",
        "L6.untranslated_sentence": f"{lead}有 {count} 句没有译文，重译后仍然缺失。要在中文版里先显示这几句的原文吗？",
        "L7.leaked_marker": f"{lead}译文里混进了{marker_text}，自动清除没有成功。要在全书翻译前先由我们修复再继续吗？",
        "L7.stray_glyph": f"{lead}有异常字符（如 ¡），是原书字体解码的问题。要在全书翻译前先由我们修复解析再继续吗？",
        "L4.code_translated": f"{lead}有代码被翻译了，重译后仍然如此。要把这些代码块改为原样保留、不翻译吗？",
        "L3.table_is_code": f"{lead}有一段代码被识别成了表格，自动改为代码没有成功。要把它按代码原样保留吗？",
    }
    if check_id.endswith(".review_blocking"):
        labels = sorted({str(finding.get("message") or "") for finding in findings} - {""})
        what = "；".join(labels[:2]) + (f" 等 {count} 项" if count > 2 else "")
        questions[check_id] = f"{what}。重新检查后仍然存在，没有可用的自动修复。要由我们按原书页面核对、修复后再继续吗？"
    question = questions.get(
        check_id,
        f"{lead}「{title}」共 {count} 处，自动修复没有解决。要按现在的样子继续、在全书翻译时再处理吗？",
    )
    return title, question


def layer_name(check_id: str) -> str:
    return LAYER_NAMES.get(check_id.split(".", 1)[0], "")
