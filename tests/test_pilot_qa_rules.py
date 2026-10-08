"""The pilot's layered QA rules (domain/quality/pilot_qa.py) on hand-built pilots.

docs/agent-upgrade/08-pilot-quality-gate.md §6 and the Q4 row of §11: every known issue the two
Manning books had is a fixture here, and each must be found at its layer and severity (100%
detection). A clean pilot, built the way the export renders it, must pass every check.
"""

from __future__ import annotations

import dataclasses
import unittest
from types import SimpleNamespace
from typing import Any

from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import (
    BLOCKING,
    MAJOR,
    MINOR,
    ExportedChapter,
    GlossaryTerm,
    HealthInputs,
    PilotQaInputs,
    QaBlock,
    QaChapter,
    QaSentence,
    ReviewBlocker,
    ReviewFinding,
)
from book_agent.domain.structure.parse_health import HealthBlock, HealthChapter, OutlineEntry
from book_agent.export import markup
from book_agent.export.models import MergedRenderBlock

C1 = "00000000-0000-0000-0000-0000000000c1"
C2 = "00000000-0000-0000-0000-0000000000c2"
P1 = "00000000-0000-0000-0000-0000000000a1"
P2 = "00000000-0000-0000-0000-0000000000a2"
PAGE = (612.0, 792.0)


def rendered(
    block_id: str,
    chapter_id: str,
    block_type: str,
    source: str,
    target: str,
    *,
    mode: str = "zh_primary_with_optional_source",
    kind: str | None = None,
    page: int = 21,
    has_image: bool | None = None,
    caption: str | None = None,
    packet_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    html: str | None = None,
) -> QaBlock:
    """A block as the service builds it: its HTML is what the merged reading copy renders."""
    source_metadata = dict(metadata or {})
    if caption:
        source_metadata["linked_caption_text"] = caption
    render_block = MergedRenderBlock(
        block_id=block_id,
        chapter_id=chapter_id,
        block_type=block_type,
        render_mode=mode,
        artifact_kind=kind,
        title=None,
        source_text=source,
        target_text=target or None,
        source_metadata=source_metadata,
        source_sentence_ids=[],
        target_segment_ids=[],
        is_expected_source_only=mode != "zh_primary_with_optional_source",
        notice=None,
    )
    assets = {block_id: f"assets/{block_id}.png"} if has_image else {}
    return QaBlock(
        block_id=block_id,
        chapter_id=chapter_id,
        block_type=block_type,
        render_mode=mode,
        artifact_kind=kind,
        source_text=source,
        target_text=target,
        html=html
        if html is not None
        else markup.render_block_html(
            render_block, assets, include_source_toggle=False, include_notice=False, include_source_caption=False
        ),
        page=page,
        has_image=has_image,
        packet_id=packet_id or (P1 if chapter_id == C1 else P2),
        caption_text=caption,
    )


def sentence_for(block: QaBlock, *, target: str | None = None, sentence_id: str | None = None) -> QaSentence:
    return QaSentence(
        id=sentence_id or f"s-{block.block_id}",
        chapter_id=block.chapter_id,
        block_id=block.block_id,
        source_text=block.source_text,
        target_text=block.target_text if target is None else target,
        packet_id=block.packet_id,
        page=block.page,
        rendered_as_translation=block.render_mode != "source_artifact_full_width",
    )


def clean_blocks() -> list[QaBlock]:
    return [
        rendered("b-h1", C1, "heading", "1 Why agents", "1 为什么需要智能体", page=20),
        rendered(
            "b-p1",
            C1,
            "paragraph",
            "An agent decides which tool to call next. The loop ends when the task is done.",
            "智能体决定下一步调用哪个工具。任务完成后，循环就结束了。",
        ),
        rendered(
            "b-p2",
            C1,
            "paragraph",
            "Call load_dotenv() before you create the client.",
            "在创建客户端之前调用 `load_dotenv()`。",
        ),
        rendered(
            "b-p3",
            C1,
            "paragraph",
            "The LLM reads 128,000 tokens at most, and the agent keeps a short memory of what it has done.",
            "LLM 最多读取 128,000 个 token，智能体会对自己做过的事保留一段简短的记忆。",
        ),
        rendered(
            "b-fig",
            C1,
            "image",
            "Figure 1.1 The agent loop: the model decides, a tool acts, and the result comes back.",
            "图 1.1 智能体循环：模型做出决定，工具执行，结果再返回给模型。",
            mode="image_anchor_with_translated_caption",
            kind="image",
            page=22,
            has_image=True,
        ),
        rendered("b-li", C1, "list_item", "• Tools extend what the agent can do.", "• 工具扩展了智能体能做的事。", page=22),
        rendered(
            "b-code",
            C1,
            "code",
            "import os\nprint(os.getenv('OPENAI_API_KEY'))",
            "",
            mode="source_artifact_full_width",
            kind="code",
            page=23,
        ),
        rendered(
            "b-table",
            C1,
            "table",
            "| Model | Context window |\n| GPT-5 | 400K |\n| Claude Sonnet 4.5 | 200K |",
            "表 1.1 模型与上下文窗口",
            mode="translated_wrapper_with_preserved_artifact",
            kind="table",
            page=23,
            caption="Table 1.1 Models and their context windows",
        ),
        rendered("b-p4", C1, "paragraph", "Listing 1.1 shows the whole loop.", "代码清单 1.1 展示了完整的循环。", page=24),
        rendered("b-h2", C2, "heading", "2 The loop", "2 循环", page=31),
        rendered("b-p5", C2, "paragraph", "Listing 2.1 calls the model once per step.", "代码清单 2.1 每一步调用一次模型。", page=32),
        rendered("b-fn", C2, "footnote", "1 See the appendix for the full code.", "1 完整代码见附录。", page=32),
    ]


def health_blocks(blocks: list[QaBlock]) -> list[HealthBlock]:
    out = []
    for ordinal, block in enumerate(blocks):
        span: dict[str, Any] = {"source_page_start": block.page}
        if block.block_type == "image":
            span["source_bbox_json"] = {"regions": [{"page_number": block.page, "bbox": [100.0, 200.0, 500.0, 500.0]}]}
            span["pdf_block_role"] = "figure"
        out.append(
            HealthBlock(
                id=block.block_id,
                chapter_id=block.chapter_id,
                ordinal=ordinal,
                block_type=block.block_type,
                source_text=block.source_text,
                source_span_json=span,
            )
        )
    return out


def build(
    blocks: list[QaBlock] | None = None,
    *,
    sentences: list[QaSentence] | None = None,
    chapters: list[QaChapter] | None = None,
    exported: list[ExportedChapter] | None | str = "default",
    glossary: list[GlossaryTerm] | None = None,
    model_issues: list[ReviewFinding] | None = None,
    review_blockers: list[ReviewBlocker] | None = None,
    figure_problems: dict[str, list[Any]] | None = None,
    health_extra: list[HealthBlock] | None = None,
    outline: list[OutlineEntry] | None = None,
) -> PilotQaInputs:
    blocks = blocks if blocks is not None else clean_blocks()
    chapters = chapters or [
        QaChapter(C1, 10, "1 Why agents", "1 为什么需要智能体", 20, 30),
        QaChapter(C2, 11, "2 The loop", "2 循环", 31, 40),
    ]
    if sentences is None:
        sentences = [sentence_for(block) for block in blocks if block.render_mode != "source_artifact_full_width"]
    problems = figure_problems or {}

    def find(page: int, bbox, furniture):
        return problems.get(str(page), [])

    health = HealthInputs(
        chapters=[HealthChapter(c.id, c.ordinal, c.title_src, c.page_start, c.page_end) for c in chapters],
        blocks=[*health_blocks(blocks), *(health_extra or [])],
        is_pdf=True,
        outline=outline
        if outline is not None
        else [OutlineEntry(1, "1 Why agents", 20), OutlineEntry(1, "2 The loop", 31)],
        document_title="Build an Agent",
        page_sizes={page: PAGE for page in range(1, 60)},
        find_figure_problems=find,
    )
    return PilotQaInputs(
        chapters=chapters,
        blocks=blocks,
        sentences=sentences,
        glossary=glossary
        if glossary is not None
        else [GlossaryTerm("agent", ("智能体",), locked=True), GlossaryTerm("tool", ("工具",))],
        model_issues=model_issues or [],
        review_blockers=review_blockers or [],
        exported=[ExportedChapter(C1, "第 1 章 为什么需要智能体", ""), ExportedChapter(C2, "第 2 章 循环", "")]
        if exported == "default"
        else exported,  # type: ignore[arg-type]
        health=health,
    )


def findings(inputs: PilotQaInputs) -> list[dict[str, Any]]:
    return [finding for layer in pilot_qa.run_layers(inputs) for finding in layer["findings"]]


def replace_block(blocks: list[QaBlock], block_id: str, **changes: Any) -> list[QaBlock]:
    return [dataclasses.replace(block, **changes) if block.block_id == block_id else block for block in blocks]


def blocker(issue_type: str, *, detector: str = "rule", root: str = "structure", **fields: Any) -> ReviewBlocker:
    fields.setdefault("chapter_id", C2)
    return ReviewBlocker(
        issue_id=fields.pop("issue_id", f"issue-{issue_type.lower()}"),
        issue_type=issue_type,
        severity=fields.pop("severity", "critical"),
        root_cause_layer=root,
        detector=detector,
        **fields,
    )


LISTING = (
    'from litellm import completion\nresponse = completion(\nmodel="gpt-5-mini",\n'
    'messages=[{"role": "user", "content": "Hello!"}]\n)'
)


def gate_issue(block_id: str, code: str, excerpt: str = "") -> ReviewBlocker:
    """The export layout gate's issue (services/export.py sync_gate_issues) for one block."""
    return blocker(
        "LAYOUT_VALIDATION_FAILURE",
        root="export",
        issue_id=f"gate-{block_id}",
        severity="high",
        evidence={
            "reason": "export_layout_validation",
            "layout_issues": [
                {
                    "block_id": block_id,
                    "issue_code": code,
                    "blocking": True,
                    "severity": "high",
                    "evidence": {"artifact_kind": "code", "source_excerpt": excerpt},
                }
            ],
        },
    )


class ReviewBlockerTests(unittest.TestCase):
    """The pilot gate is the one verdict on "no blocking problems": every open blocking review
    issue of the pilot chapters is a finding in its layer, with a repair path or a question."""

    def found(self, inputs: PilotQaInputs, check_id: str) -> list[dict[str, Any]]:
        return [finding for finding in findings(inputs) if finding["check_id"] == check_id]

    def test_a_blocking_reading_order_issue_is_a_blocking_structure_finding(self) -> None:
        # The RAG book: MISORDERING, review_policy default_blocking_layout_risk, pages 42 and 45.
        issue = blocker(
            "MISORDERING",
            issue_id="i-mis",
            evidence={"layout_risk": "high", "page_layout_risk_pages": [42, 45], "review_policy": "default_blocking_layout_risk"},
        )
        layers = pilot_qa.run_layers(build(review_blockers=[issue]))
        [finding] = [f for layer in layers for f in layer["findings"] if f["check_id"] == "L1.review_blocking"]
        self.assertEqual(finding["severity"], BLOCKING)
        self.assertEqual(finding["repair"], "review_recheck")
        self.assertEqual(finding["key"], "L1.review_blocking:issue:i-mis")
        self.assertEqual(finding["location"], {"chapter_id": C2, "page": 42})
        self.assertIn("阅读顺序", finding["message"])
        self.assertIn("42、45", finding["message"])
        self.assertEqual(finding["evidence"]["review_policy"], "default_blocking_layout_risk")
        self.assertEqual(pilot_qa.summarize(layers)["counts"]["blocking"], 1)

    def test_the_export_gate_blocking_a_code_listing_typed_as_a_table_is_one_blocking_table_is_code(self) -> None:
        # The Agent book: listing 2.4 parsed as a table, rendered as code; the layout gate blocks it.
        listing = rendered("b-lst", C2, "table", LISTING, "", mode="source_artifact_full_width", kind="code", page=36)
        inputs = build([*clean_blocks(), listing], review_blockers=[gate_issue("b-lst", "TABLE_STRUCTURE_UNRENDERABLE")])
        hits = self.found(inputs, "L3.table_is_code")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], BLOCKING)
        self.assertEqual(hits[0]["key"], "L3.table_is_code:block:b-lst")
        self.assertEqual(hits[0]["evidence"]["issue_code"], "TABLE_STRUCTURE_UNRENDERABLE")
        self.assertEqual(self.found(inputs, "L3.review_blocking"), [])

    def test_a_gate_issue_on_a_block_the_pilot_does_not_render_still_counts(self) -> None:
        inputs = build(review_blockers=[gate_issue("b-gone", "TABLE_STRUCTURE_UNRENDERABLE", excerpt=LISTING)])
        [hit] = self.found(inputs, "L3.table_is_code")
        self.assertEqual((hit["severity"], hit["target"]), (BLOCKING, {"kind": "block", "id": "b-gone"}))

    def test_other_gate_codes_land_in_their_layer(self) -> None:
        inputs = build(
            review_blockers=[
                gate_issue("b-fig", "FIGURE_ASSET_MISSING"),
                gate_issue("b-h2", "HEADING_LEVEL_SKIP"),
                gate_issue("b-t", "TABLE_EMPTY"),
            ]
        )
        self.assertEqual([f["severity"] for f in self.found(inputs, "L2.figure_without_image")], [BLOCKING])
        self.assertEqual([f["location"].get("block_id") for f in self.found(inputs, "L1.review_blocking")], ["b-h2"])
        self.assertEqual([f["location"].get("block_id") for f in self.found(inputs, "L3.review_blocking")], ["b-t"])

    def test_issues_without_a_layer_of_their_own_go_by_root_cause(self) -> None:
        inputs = build(
            review_blockers=[
                blocker("OMISSION", root="translation", packet_id=P2, sentence_id="s-x"),
                blocker("SOMETHING_NEW", root="export", detector="human"),
            ]
        )
        [omission] = self.found(inputs, "L6.review_blocking")
        self.assertEqual(omission["target"], {"kind": "packet", "id": P2})
        [other] = self.found(inputs, "L7.review_blocking")
        self.assertIn("人工审校", other["message"])

    def test_the_reviewer_agents_translation_findings_are_not_counted_twice(self) -> None:
        issue = blocker("MISTRANSLATION_SEMANTIC", detector="model", root="translation")
        self.assertEqual(self.found(build(review_blockers=[issue]), "L6.review_blocking"), [])

    def test_a_blocking_model_finding_is_blocking_whatever_its_severity(self) -> None:
        issue = ReviewFinding(
            issue_id="i-m", issue_type="OMISSION", severity="medium", chapter_id=C1, packet_id=P1, sentence_id="s-b-p1", blocking=True
        )
        [finding] = self.found(build(model_issues=[issue]), "L6.model_review")
        self.assertEqual(finding["severity"], BLOCKING)


class CleanPilotTests(unittest.TestCase):
    def test_a_clean_pilot_passes_every_layer(self) -> None:
        layers = pilot_qa.run_layers(build())
        self.assertEqual([layer["layer"] for layer in layers], ["L1", "L2", "L3", "L4", "L5", "L6", "L7"])
        leftovers = [(f["check_id"], f["message"]) for layer in layers for f in layer["findings"]]
        self.assertEqual(leftovers, [])
        self.assertTrue(all(layer["status"] == "passed" and layer["passed"] for layer in layers))
        self.assertEqual(pilot_qa.summarize(layers), {"status": "passed", "counts": {"blocking": 0, "major": 0, "minor": 0}})
        # Every check of the design ran (the parse health ones included) and says it passed.
        ran = {check["check_id"] for layer in layers for check in layer["checks"]}
        self.assertEqual(ran, set(pilot_qa.CHECKS))


class KnownIssueTests(unittest.TestCase):
    """One fixture per issue the two real books had; each is found at its layer and severity."""

    def assertFound(self, inputs: PilotQaInputs, check_id: str, severity: str, *, target_id: str | None = None) -> dict[str, Any]:
        hits = [finding for finding in findings(inputs) if finding["check_id"] == check_id]
        self.assertTrue(hits, f"{check_id} not found; got {[f['check_id'] for f in findings(inputs)]}")
        finding = hits[0]
        self.assertEqual(finding["severity"], severity)
        self.assertEqual(finding["layer"], check_id.split(".")[0])
        self.assertTrue(finding["key"].startswith(check_id + ":"))
        self.assertTrue(finding["message"])
        self.assertIn(finding["repair"], {spec.repair for spec in pilot_qa.CHECKS.values()})
        if target_id is not None:
            self.assertIn(target_id, (finding["target"].get("id"), finding["location"].get("block_id"), finding["location"].get("chapter_id")))
        return finding

    def test_caption_swallowing_body_text(self) -> None:
        # Figure 1.2 of the RAG book: the paragraph after the caption was exported inside the caption.
        swallowed = (
            "Figure 1.2 ChatGPT (GPT 3.5) response to the question, “Who won the 2023 Cricket World Cup?” "
            "Source: Screenshot of the author’s account. This is problematic. Despite not having any memory of the "
            "2023 Cricket World Cup, ChatGPT still generates the answer in a seemingly confident tone. This is called "
            "hallucination, and it has become a well-known problem of large language models. It is also why this "
            "book exists, and why retrieval matters so much."
        )
        blocks = replace_block(clean_blocks(), "b-fig", source_text=swallowed)
        finding = self.assertFound(build(blocks), "L2.caption_swallows_body", BLOCKING, target_id="b-fig")
        self.assertEqual(finding["location"]["page"], 22)
        self.assertEqual(finding["repair"], "structure_edit")

    def test_body_text_folded_into_a_caption_by_the_export_grouping(self) -> None:
        blocks = clean_blocks()
        folded = dataclasses.replace(
            blocks[4],
            context_texts=(
                "The agent reads the result. It decides again. Then it calls another tool. Finally it answers the user.",
            ),
        )
        blocks = [folded if block.block_id == "b-fig" else block for block in blocks]
        self.assertFound(build(blocks), "L2.caption_swallows_body", BLOCKING, target_id="b-fig")

    def test_figure_crop_into_the_header_band_and_a_cut_off_figure(self) -> None:
        # Figures 1.4–1.6: crops that took in the running header, or cut through the drawing.
        header = SimpleNamespace(kind="page_furniture", text="1.2 The novelty of RAG 11")
        cut = SimpleNamespace(kind="cut_path", text="")
        finding = self.assertFound(build(figure_problems={"22": [header]}), "L2.figure_regions", BLOCKING, target_id="b-fig")
        self.assertIn("page_furniture", finding["evidence"]["detail"])
        self.assertEqual(finding["repair"], "figure_region_override")
        finding = self.assertFound(build(figure_problems={"22": [cut]}), "L2.figure_regions", BLOCKING)
        self.assertIn("cut_path", finding["evidence"]["detail"])

    def test_a_figure_without_its_picture(self) -> None:
        blocks = replace_block(clean_blocks(), "b-fig", has_image=False)
        self.assertFound(build(blocks), "L2.figure_without_image", BLOCKING, target_id="b-fig")

    def test_translated_code(self) -> None:
        blocks = replace_block(
            clean_blocks(),
            "b-code",
            html="<section class='block artifact code'><pre><code>import os\n# 读取密钥\nprint(os.getenv('OPENAI_API_KEY'))</code></pre></section>",
        )
        self.assertFound(build(blocks), "L4.code_translated", BLOCKING, target_id="b-code")

    def test_a_listing_title_on_the_code_box_is_not_translated_code(self) -> None:
        # The Agent book's whole-book QA reported 186 listings as translated code: their Chinese
        # titles (代码清单 3.1 …) are drawn as the code box's caption.
        blocks = replace_block(
            clean_blocks(),
            "b-code",
            html=(
                "<section class='block artifact code'><div class='zh listing-caption'>代码清单 2.1 加载环境变量</div>"
                "<pre><code>import os\nprint(os.getenv('OPENAI_API_KEY'))</code></pre></section>"
            ),
        )
        self.assertNotIn("L4.code_translated", [finding["check_id"] for finding in findings(build(blocks))])

    def test_python_none_is_not_a_negation(self) -> None:
        from book_agent.domain.quality.pilot_qa import _CODE_LITERAL_RE, _NEGATION_EN_RE

        self.assertIsNone(_NEGATION_EN_RE.search(_CODE_LITERAL_RE.sub(" ", "If the callback returns None, the tool executes.")))
        self.assertIsNotNone(_NEGATION_EN_RE.search(_CODE_LITERAL_RE.sub(" ", "None of these tools are needed.")))

    def test_a_code_comment_outside_its_code_block(self) -> None:
        # The RAG book: bold "# comments" in listings came out as headings between code blocks.
        blocks = clean_blocks()
        comment = rendered("b-cm", C1, "heading", "# Load the API key from the environment", "# 从环境变量加载 API 密钥", page=23)
        at = [block.block_id for block in blocks].index("b-code")
        blocks = [*blocks[:at], comment, *blocks[at:]]
        self.assertFound(build(blocks), "L4.code_comment_as_text", MAJOR, target_id="b-cm")
        # Parse health sees the same block as a pseudo heading.
        self.assertFound(build(blocks), "L4.pseudo_headings", MAJOR, target_id="b-cm")

    def test_inline_code_lost_in_the_translation(self) -> None:
        blocks = replace_block(clean_blocks(), "b-p2", target_text="在创建客户端之前加载环境变量。")
        blocks = replace_block(blocks, "b-p2", html=rendered("x", C1, "paragraph", "", "在创建客户端之前加载环境变量。").html)
        finding = self.assertFound(build(blocks), "L4.inline_code_lost", MAJOR, target_id="b-p2")
        self.assertEqual(finding["evidence"]["tokens"], ["load_dotenv()"])

    def test_inline_code_shown_with_its_backticks(self) -> None:
        # Chapter 2 of the agent book before inline code rendered: "`find_dotenv()` 函数" printed as is.
        target = "在创建客户端之前调用 `load_dotenv()`。"
        blocks = replace_block(
            clean_blocks(), "b-p2", html=f"<section class='block paragraph'><div class='zh'>{target}</div></section>"
        )
        self.assertFound(build(blocks), "L4.inline_code_markup", MAJOR, target_id="b-p2")

    def test_a_locked_term_not_followed(self) -> None:
        blocks = replace_block(
            clean_blocks(), "b-p1", target_text="代理决定下一步调用哪个工具。任务完成后，循环就结束了。"
        )
        finding = self.assertFound(build(blocks), "L5.locked_term", MAJOR)
        self.assertEqual((finding["evidence"]["source_term"], finding["evidence"]["expected"]), ("agent", "智能体"))
        self.assertEqual(finding["target"], {"kind": "packet", "id": P1})

    def test_one_term_rendered_two_ways(self) -> None:
        # The agent book: "Listing 2.3" came out as 清单 2.3 while every other listing is 代码清单.
        blocks = replace_block(clean_blocks(), "b-p5", target_text="清单 2.1 每一步调用一次模型。")
        finding = self.assertFound(build(blocks), "L5.label_rendering", MAJOR)
        self.assertEqual(finding["evidence"]["renderings"], {"代码清单": 1, "清单": 1})
        # A glossary term with two accepted renderings, both in use (the RAG book's 生成流水线 / 生成流程).
        glossary = [
            GlossaryTerm("agent", ("智能体",), locked=True),
            GlossaryTerm("tool", ("工具", "工具函数")),
        ]
        blocks = replace_block(clean_blocks(), "b-li", target_text="• 工具函数扩展了智能体能做的事。")
        finding = self.assertFound(build(blocks, glossary=glossary), "L5.term_variants", MAJOR)
        self.assertEqual(finding["target"], {"kind": "term", "id": "tool"})

    def test_a_term_sometimes_left_in_english(self) -> None:
        blocks = replace_block(clean_blocks(), "b-li", target_text="• Tools 扩展了智能体能做的事。")
        self.assertFound(build(blocks), "L5.term_left_in_english", MAJOR)

    def test_a_leaked_program_note(self) -> None:
        # Before fix/no-export-hints the bilingual export printed 图片锚点保留 next to every figure.
        blocks = replace_block(
            clean_blocks(),
            "b-fig",
            html=(
                "<section class='block artifact image-anchor image'><div class='artifact-body'><figure><img src='x.png'/>"
                "</figure></div><div class='zh'>图 1.1 智能体循环</div><div class='artifact-note'>图片锚点保留</div></section>"
            ),
        )
        finding = self.assertFound(build(blocks), "L7.leaked_marker", BLOCKING, target_id="b-fig")
        self.assertEqual(finding["evidence"]["marker"], "图片锚点保留")
        # The same note written by the model into the translation itself.
        blocks = replace_block(clean_blocks(), "b-p1", target_text="译文：智能体决定下一步调用哪个工具。")
        self.assertFound(build(blocks), "L7.leaked_marker", BLOCKING, target_id="b-p1")

    def test_missing_cjk_latin_spacing(self) -> None:
        blocks = replace_block(
            clean_blocks(), "b-p3", target_text="LLM最多读取128,000个token，智能体会对自己做过的事保留一段简短的记忆。"
        )
        finding = self.assertFound(build(blocks), "L7.cjk_latin_spacing", MAJOR, target_id="b-p3")
        self.assertEqual(finding["evidence"]["convention"], "spaced")

    def test_half_width_punctuation_in_chinese(self) -> None:
        blocks = replace_block(
            clean_blocks(), "b-p1", target_text="智能体决定下一步调用哪个工具,任务完成后,循环就结束了."
        )
        self.assertFound(build(blocks), "L7.halfwidth_punctuation", MAJOR, target_id="b-p1")

    def test_a_contents_entry_taken_for_a_chapter(self) -> None:
        # The RAG book: lines of the contents page became chapters, with their page numbers in the title.
        chapters = [
            QaChapter(C1, 3, "1 LLMs and the need for RAG ........ 3", None, 8, 8),
            QaChapter(C2, 11, "2 The loop", "2 循环", 31, 40),
        ]
        finding = self.assertFound(build(chapters=chapters), "L1.chapter_is_contents", BLOCKING, target_id=C1)
        self.assertEqual(finding["repair"], "resplit_chapters")
        # Parse health's outline check agrees: the chapter is not where the outline has it.
        self.assertFound(build(chapters=chapters), "L1.chapters_vs_outline", BLOCKING, target_id=C1)

    def test_chapters_folded_together(self) -> None:
        # The RAG book: the chapters under "Part 1" were one chapter; the pilot's chapter 1 held chapter 2.
        blocks = replace_block(clean_blocks(), "b-h2", chapter_id=C1)
        chapters = [QaChapter(C1, 10, "1 Why agents", "1 为什么需要智能体", 20, 40), QaChapter(C2, 11, "3 Tools", "3 工具", 41, 50)]
        outline = [OutlineEntry(1, "1 Why agents", 20), OutlineEntry(1, "2 The loop", 31), OutlineEntry(1, "3 Tools", 41)]
        inputs = build(blocks, chapters=chapters, outline=outline)
        self.assertFound(inputs, "L1.chapter_folded", BLOCKING, target_id="b-h2")
        self.assertFound(inputs, "L1.chapters_vs_outline", BLOCKING, target_id=C1)

    def test_a_pilot_chapter_missing_from_the_reading_copy(self) -> None:
        # Both books' pilots: the export folded chapter 2 into chapter 1's section.
        inputs = build(exported=[ExportedChapter(C1, "第 1 章 为什么需要智能体", "")])
        self.assertFound(inputs, "L1.export_chapter_missing", BLOCKING, target_id=C2)

    def test_a_stray_symbol_font_bullet(self) -> None:
        # The RAG book: 502 list items began with "¡", the symbol font's bullet decoded as text.
        blocks = replace_block(clean_blocks(), "b-li", source_text="¡ Tools extend what the agent can do.", target_text="¡ 工具扩展了智能体能做的事。")
        blocks = replace_block(blocks, "b-li", html=rendered("x", C1, "list_item", "", "¡ 工具扩展了智能体能做的事。").html)
        finding = self.assertFound(build(blocks), "L7.stray_glyph", BLOCKING, target_id="b-li")
        self.assertEqual(finding["evidence"]["glyph"], "U+00A1")

    def test_an_omitted_segment(self) -> None:
        blocks = clean_blocks()
        sentences = [sentence_for(block) for block in blocks if block.render_mode != "source_artifact_full_width"]
        sentences = [dataclasses.replace(s, target_text="") if s.block_id == "b-p3" else s for s in sentences]
        finding = self.assertFound(build(blocks, sentences=sentences), "L6.untranslated_sentence", BLOCKING)
        self.assertEqual(finding["location"]["sentence_id"], "s-b-p3")
        self.assertEqual(finding["target"], {"kind": "packet", "id": P1})
        # A translation far shorter than its source lost content.
        blocks = replace_block(clean_blocks(), "b-p3", target_text="LLM 有记忆。")
        self.assertFound(build(blocks), "L6.short_translation", MAJOR, target_id="b-p3")

    def test_a_number_that_disappears(self) -> None:
        blocks = replace_block(clean_blocks(), "b-p3", target_text="LLM 最多读取十几万个 token，智能体会对自己做过的事保留一段简短的记忆。")
        finding = self.assertFound(build(blocks), "L6.number_mismatch", MAJOR, target_id="b-p3")
        self.assertEqual(finding["evidence"]["numbers"], ["128000"])

    def test_a_flipped_negation(self) -> None:
        blocks = clean_blocks()
        extra = rendered("b-neg", C2, "paragraph", "The agent does not call a tool it has never seen.", "智能体会调用它见过的工具。", page=33)
        self.assertFound(build([*blocks, extra]), "L6.negation_lost", MAJOR)

    def test_the_reviewer_agents_findings(self) -> None:
        issue = ReviewFinding(
            issue_id="i-1",
            issue_type="MISTRANSLATION_SEMANTIC",
            severity="high",
            chapter_id=C1,
            block_id="b-p1",
            sentence_id="s-b-p1",
            packet_id=P1,
            source_text="The loop ends when the task is done.",
            target_text="任务开始时循环结束。",
            explanation="意思译反了",
        )
        finding = self.assertFound(build(model_issues=[issue]), "L6.model_review", BLOCKING)
        self.assertEqual(finding["key"], "L6.model_review:issue:i-1")

    def test_tables_flattened_or_garbled(self) -> None:
        # The agent book: table 1.1 printed as rows of "|", a chart read as a table.
        flattened = rendered(
            "b-t2", C1, "paragraph", "| Strategy | Description |\n| Generate | Constructing context |", "| 策略 | 描述 |", page=35
        )
        self.assertFound(build([*clean_blocks(), flattened]), "L3.table_flattened", BLOCKING, target_id="b-t2")
        garbled = rendered(
            "b-t3",
            C1,
            "table",
            "| | | C | | laude Sonnet 4 |\n| | | G Q G | G Q G | PT-4.1 wen3-32B |",
            "",
            mode="translated_wrapper_with_preserved_artifact",
            kind="table",
            page=34,
        )
        self.assertFound(build([*clean_blocks(), garbled]), "L3.table_sparse", MAJOR, target_id="b-t3")

    def test_prose_set_as_code(self) -> None:
        prose = rendered(
            "b-pc",
            C1,
            "code",
            "Context engineering can be broadly categorized into five strategies, each of which is covered in this book:",
            "上下文工程大致可分为五种策略：",
            mode="source_artifact_full_width",
            kind="code",
            page=35,
        )
        self.assertFound(build([*clean_blocks(), prose]), "L4.prose_in_code", MAJOR, target_id="b-pc")

    def test_a_run_in_heading_glued_to_its_paragraph(self) -> None:
        glued = rendered(
            "b-g",
            C2,
            "paragraph",
            "Open LLMs become worth considering.cost optimization When you handle heavy traffic, self-hosting is cheaper.",
            "开源 LLM 就值得考虑了。成本优化 处理高并发流量时，自托管往往更便宜。",
            page=34,
        )
        self.assertFound(build([*clean_blocks(), glued]), "L1.heading_merged_into_text", MAJOR, target_id="b-g")

    def test_a_run_in_heading_the_parser_kept_as_the_paragraph_first_line(self) -> None:
        """The Agent book: "how the agent loop works" in bold, then the paragraph; the reader sees one paragraph."""
        import dataclasses

        from book_agent.domain.quality.pilot_qa import run_in_heading_lines

        text = "how the agent loop works\nThe loop is necessary because it is difficult to know in advance\nhow many steps a task will take."
        self.assertEqual(run_in_heading_lines(text), ["how the agent loop works"])
        # A wrapped line after a comma, or a long first line, is body text.
        self.assertEqual(run_in_heading_lines("A workflow is a system,\nwith LLMs executing steps.\nThe end."), [])
        self.assertEqual(run_in_heading_lines("most models answer from what they saw in training and cannot know more\nThe end."), [])
        block = dataclasses.replace(
            rendered("b-ri", C2, "paragraph", " ".join(text.split()), "循环之所以必要，是因为很难提前知道一个任务需要多少步。", page=34),
            parsed_text=text,
        )
        found = self.assertFound(build([*clean_blocks(), block]), "L1.heading_merged_into_text", MAJOR, target_id="b-ri")
        self.assertEqual(found["evidence"]["headings"], ["how the agent loop works"])

    def test_markdown_bold_left_in_the_translation_is_a_leak(self) -> None:
        # The Agent book: the model set run-in headings in markdown bold; the reader saw "**核心要点**".
        bold = rendered("b-md", C2, "heading", "Key takeaways", "**核心要点**", page=40)
        found = self.assertFound(build([*clean_blocks(), bold]), "L7.leaked_marker", BLOCKING, target_id="b-md")
        self.assertEqual(found["evidence"]["marker"], "**")

    def test_code_parsed_as_a_table_is_flagged_even_when_the_reading_copy_sets_it_as_code(self) -> None:
        code = "| from litellm import completion |\n| response = completion( |\n| model=\"gpt-5-mini\", |"
        as_table = rendered("b-tc", C2, "table", code, "", mode="translated_wrapper_with_preserved_artifact", kind="table", page=36)
        self.assertFound(build([*clean_blocks(), as_table]), "L3.table_is_code", MAJOR, target_id="b-tc")
        # The reader sees code, but the block is still a table to the export's layout gate.
        as_code = rendered("b-tc", C2, "table", code, "", mode="source_artifact_full_width", kind="code", page=36)
        found = self.assertFound(build([*clean_blocks(), as_code]), "L3.table_is_code", MAJOR, target_id="b-tc")
        self.assertEqual(found["repair"], "structure_edit")

    def test_a_running_header_kept_as_a_footnote(self) -> None:
        header = rendered("b-rh", C2, "footnote", "11\nThe loop", "11 循环", page=33)
        self.assertFound(build([*clean_blocks(), header]), "L1.running_header_as_text", MAJOR, target_id="b-rh")


class ReportShapeTests(unittest.TestCase):
    def test_layer_verdicts_and_counts(self) -> None:
        blocks = replace_block(clean_blocks(), "b-fig", has_image=False)
        blocks = replace_block(blocks, "b-p5", target_text="清单 2.1 每一步调用一次模型。")
        layers = {layer["layer"]: layer for layer in pilot_qa.run_layers(build(blocks))}
        self.assertEqual((layers["L2"]["status"], layers["L2"]["passed"]), ("blocking", False))
        self.assertEqual(layers["L2"]["counts"], {"blocking": 1, "major": 0, "minor": 0})
        self.assertEqual((layers["L5"]["status"], layers["L5"]["passed"]), ("warnings", True))
        self.assertEqual(layers["L1"]["status"], "passed")
        summary = pilot_qa.summarize(list(layers.values()))
        self.assertEqual(summary, {"status": "blocking", "counts": {"blocking": 1, "major": 1, "minor": 0}})

    def test_findings_are_stable_repair_targets(self) -> None:
        blocks = replace_block(clean_blocks(), "b-fig", has_image=False)
        first = findings(build(blocks))
        second = findings(build(blocks))
        self.assertEqual([f["key"] for f in first], [f["key"] for f in second])
        self.assertEqual(first[0]["key"], "L2.figure_without_image:block:b-fig")
        self.assertEqual(first[0]["target"], {"kind": "block", "id": "b-fig"})
        self.assertEqual(first[0]["location"], {"chapter_id": C1, "packet_id": P1, "block_id": "b-fig", "page": 22})

    def test_an_extra_reviewer_plugs_in(self) -> None:
        def vision(inputs: PilotQaInputs):
            figure = next(block for block in inputs.blocks if block.block_id == "b-fig")
            finding = pilot_qa._block_finding("L2.figure_regions", figure, "看图：图的右边被切掉了", detail="vision")
            return [pilot_qa.CheckRun("L2.figure_regions", [finding])]

        layers = pilot_qa.run_layers(build(), [vision])
        l2 = next(layer for layer in layers if layer["layer"] == "L2")
        self.assertEqual([f["evidence"].get("detail") for f in l2["findings"]], ["vision"])
        self.assertEqual(l2["counts"]["blocking"], 1)

    def test_minor_only_layers_pass(self) -> None:
        figure = rendered(
            "b-img2", C2, "image", "[Image]", "", mode="source_artifact_full_width", kind="image", page=33, has_image=True
        )
        layers = {layer["layer"]: layer for layer in pilot_qa.run_layers(build([*clean_blocks(), figure]))}
        self.assertEqual(layers["L2"]["counts"], {"blocking": 0, "major": 0, "minor": 1})
        self.assertEqual((layers["L2"]["status"], layers["L2"]["passed"]), ("passed", True))
        self.assertEqual(MINOR, "minor")


DEM = "ITCFranklinGothicStd-Dem"
BODY = "NewBaskervilleStd-Roman"
BODY_TEXT = (
    "An agent decides which tool to call next, reads what the tool returns and decides again, until the "
    "task is done or it gives up and says why."
)


def styled(block: QaBlock, *fonts: str, regions: tuple = ()) -> QaBlock:
    return dataclasses.replace(block, font_names=tuple(fonts), regions=regions)


def styled_pilot(*extra: QaBlock) -> list[QaBlock]:
    """A pilot whose headings are set in the heading face and its prose in the body face (Manning)."""
    return [
        styled(rendered("b-h11", C1, "heading", "1.1 The age of agents", "1.1 智能体时代", page=21), DEM),
        styled(rendered("b-body1", C1, "paragraph", BODY_TEXT, "智能体决定下一步调用哪个工具，读取工具的返回结果后再次决定，直到任务完成。", page=21), BODY),
        styled(rendered("b-h12", C1, "heading", "1.2 Understanding agents", "1.2 理解智能体", page=22), DEM),
        styled(rendered("b-body2", C1, "paragraph", BODY_TEXT, "智能体决定下一步调用哪个工具，读取工具的返回结果后再次决定，直到任务完成。", page=22), BODY),
        *extra,
    ]


class HeadingAsTextTests(unittest.TestCase):
    """Section headings the reading copy sets as body paragraphs (both Manning books' pilots)."""

    def found(self, blocks: list[QaBlock]) -> list[dict[str, Any]]:
        return [finding for finding in findings(build(blocks)) if finding["check_id"] == "L1.heading_as_text"]

    def test_a_numbered_subsection_set_in_the_heading_face(self) -> None:
        heading = styled(rendered("b-121", C1, "paragraph", "1.2.1 What is an LLM?", "1.2.1 什么是 LLM？", page=22), DEM)
        hits = self.found(styled_pilot(heading))
        self.assertEqual([(hit["target"]["id"], hit["severity"], hit["repair"]) for hit in hits], [("b-121", MAJOR, "structure_edit")])
        self.assertEqual(hits[0]["evidence"]["heading_level"], 3)

    def test_section_labels_in_the_heading_face(self) -> None:
        summary = styled(rendered("b-sum", C1, "paragraph", "Summary", "小结", page=29), DEM)
        covers = styled(rendered("b-cov", C1, "paragraph", "This chapter covers", "本章涵盖", page=20), DEM)
        hits = self.found(styled_pilot(summary, covers))
        self.assertEqual(sorted((hit["target"]["id"], hit["evidence"]["heading_level"]) for hit in hits), [("b-cov", 2), ("b-sum", 2)])

    def test_numbers_and_labels_in_body_type_are_not_headings(self) -> None:
        blocks = styled_pilot(
            # A sentence starting with a number, a numbered list item, another chapter's number, body type.
            styled(rendered("b-n1", C1, "paragraph", "1.5 million users signed up", "150 万用户注册", page=23), BODY),
            styled(rendered("b-n2", C1, "list_item", "1. Install the package", "1. 安装软件包", page=23), DEM),
            styled(rendered("b-n3", C1, "paragraph", "2.1 Choosing a model", "2.1 选择模型", page=23), DEM),
            styled(rendered("b-n4", C1, "paragraph", "Summary", "小结", page=23), BODY),
            styled(rendered("b-n5", C1, "paragraph", "1.2.2 What is an agent? It is a loop.", "1.2.2 什么是智能体？", page=23), DEM, BODY),
        )
        self.assertEqual(self.found(blocks), [])

    def test_without_font_evidence_nothing_is_found(self) -> None:
        # An EPUB pilot has no fonts: a pattern alone is not evidence.
        heading = rendered("b-121", C1, "paragraph", "1.2.1 What is an LLM?", "1.2.1 什么是 LLM？", page=22)
        self.assertEqual(self.found([*clean_blocks(), heading]), [])


FIGURE_BOX = (99.0, 51.0, 465.0, 305.0)


def figure_block(block_id: str = "b-f27", page: int = 27) -> QaBlock:
    return styled(
        rendered(
            block_id,
            C1,
            "figure",
            "Figure 1.3 The teacher teaches the student.",
            "图 1.3 老师教学生。",
            mode="image_anchor_with_translated_caption",
            kind="figure",
            page=page,
            has_image=True,
        ),
        regions=((page, FIGURE_BOX),),
    )


def artifact(block_id: str, block_type: str, text: str, target: str, bbox, *fonts: str, caption: str | None = None, page: int = 27) -> QaBlock:
    mode = "source_artifact_full_width" if block_type == "code" else "translated_wrapper_with_preserved_artifact"
    return styled(
        rendered(block_id, C1, block_type, text, target, mode=mode, kind=block_type, page=page, caption=caption),
        *fonts,
        regions=((page, bbox),),
    )


class FigureResidueTests(unittest.TestCase):
    """A figure's own text kept as a table or code block next to it (RAG figure 1.3, Agent figure 1.6)."""

    def found(self, *extra: QaBlock) -> list[dict[str, Any]]:
        blocks = [*clean_blocks(), figure_block(), *extra]
        return [finding for finding in findings(build(blocks)) if finding["check_id"] == "L2.figure_residue"]

    def test_repeated_labels_parsed_as_a_table_are_blocking(self) -> None:
        table = artifact("b-t", "table", "| The | teacher |\n| The | teacher |", "老师老师老师老师", (102.0, 132.0, 165.0, 158.0), "Arial-BoldMT")
        hits = self.found(table)
        self.assertEqual([(hit["target"]["id"], hit["severity"], hit["repair"]) for hit in hits], [("b-t", BLOCKING, "structure_edit")])

    def test_a_legend_with_split_words_is_blocking(self) -> None:
        legend = artifact("b-l", "table", "| | | C | | laude Sonnet 4 |\n| G Q G | PT-4.1 |", "", (121.0, 150.0, 443.0, 290.0), "ArialMT")
        self.assertEqual([hit["severity"] for hit in self.found(legend)], [BLOCKING])

    def test_diagram_labels_parsed_as_code_are_major(self) -> None:
        code = artifact("b-c", "code", "{Query + Information} Retrieve", "", (234.0, 208.0, 301.0, 217.0), "ArialMT")
        self.assertEqual([(hit["target"]["id"], hit["severity"]) for hit in self.found(code)], [("b-c", MAJOR)])

    def test_real_tables_and_listings_are_left_alone(self) -> None:
        blocks = [
            # A listing in the book's code font inside the figure's box, a captioned table inside it, a data
            # grid inside it, and label-like text outside every figure.
            artifact("b-k1", "code", "from openai import OpenAI\nclient = OpenAI()", "", (110.0, 60.0, 300.0, 90.0), "JetBrainsMonoNL-Regular"),
            artifact("b-k2", "table", "| Model | Accuracy |\n| GPT-5 | 0.8 |", "", (110.0, 100.0, 300.0, 140.0), "ArialMT", caption="Table 1.1 Accuracy"),
            artifact(
                "b-k3",
                "table",
                "| Use case | Needs data? | RAG? |\n| Chat bot | Yes: product data | Yes |\n| Translation | No: all in the prompt | No |",
                "",
                (110.0, 150.0, 400.0, 290.0),
                "ArialMT",
            ),
            artifact("b-k4", "table", "| The | teacher |\n| The | teacher |", "", (102.0, 400.0, 165.0, 430.0), "ArialMT"),
        ]
        self.assertEqual(self.found(*blocks), [])



class HeadingIsProseTests(unittest.TestCase):
    def found(self, text: str) -> list[dict[str, Any]]:
        heading = rendered("b-hq", C2, "heading", text, "标题", page=33)
        return [f for f in findings(build([*clean_blocks(), heading])) if f["check_id"] == "L1.heading_is_prose"]

    def test_a_sentence_set_as_a_heading_is_found(self) -> None:
        self.assertTrue(self.found("Chapter 7 explores the various layers of infrastructure that support RAG systems."))

    def test_a_listing_title_set_as_a_heading_is_found(self) -> None:
        # Build an AI Agent: "Listing 2.4 Unified API calls with LiteLLM" was promoted to a section heading.
        hits = self.found("Listing 2.4 Unified API calls with LiteLLM")
        self.assertEqual([(hit["target"]["id"], hit["repair"]) for hit in hits], [("b-hq", "structure_edit")])

    def test_a_numbered_section_title_that_asks_a_question_is_a_title(self) -> None:
        # The RAG book's "2.1": relabelling it as a paragraph would lose the section.
        self.assertEqual(self.found("2.1 What does a RAG system look like?"), [])



class ProseParsedAsCodeTests(unittest.TestCase):
    """Agent p49: prose typed as code, joined by the export with the listing before it, then split off untranslated."""

    PROSE = "The call_llm function wraps acompletion with both protections. The semaphore ensures that only ten calls run."

    def test_the_parsed_code_block_behind_untranslated_prose_is_the_target(self) -> None:
        split = dataclasses.replace(
            rendered("b-code", C2, "paragraph", self.PROSE, "", page=33),
            merged_blocks=(("b-listing", "import asyncio\nsemaphore = asyncio.Semaphore(10)"), ("b-prose", self.PROSE)),
        )
        hits = [f for f in findings(build([*clean_blocks(), split])) if f["check_id"] == "L4.prose_in_code"]
        self.assertEqual([(hit["target"]["id"], hit["location"]["block_id"], hit["severity"]) for hit in hits], [("b-prose", "b-prose", MAJOR)])

    def test_translated_prose_after_a_listing_is_fine(self) -> None:
        split = dataclasses.replace(
            rendered("b-code", C2, "paragraph", self.PROSE, "call_llm 函数用两种保护包装了 acompletion。", page=33),
            merged_blocks=(("b-listing", "import asyncio"), ("b-prose", self.PROSE)),
        )
        self.assertEqual([f for f in findings(build([*clean_blocks(), split])) if f["check_id"] == "L4.prose_in_code"], [])



class HiddenPageTopHeadingTests(unittest.TestCase):
    """RAG chapter 2: "Summary" opens page 49 and was taken for the running head "29 Summary"."""

    def _header(self, fonts: list[str]) -> HealthBlock:
        return HealthBlock(
            id="b-hidden",
            chapter_id=C2,
            ordinal=90,
            block_type="paragraph",
            source_text="Summary",
            source_span_json={"pdf_block_role": "header", "pdf_font_names": fonts, "source_page_start": 38},
        )

    def found(self, header: HealthBlock) -> list[dict[str, Any]]:
        inputs = build(styled_pilot(), health_extra=[header])
        return [finding for finding in findings(inputs) if finding["check_id"] == "L1.heading_as_text"]

    def test_a_section_label_in_the_heading_face_hidden_as_a_header_is_found(self) -> None:
        hits = self.found(self._header([DEM]))
        self.assertEqual([(hit["target"]["id"], hit["evidence"]["heading_level"], hit["evidence"]["detail"]) for hit in hits], [("b-hidden", 2, "hidden_as_header")])

    def test_the_real_running_head_is_left_hidden(self) -> None:
        self.assertEqual(self.found(self._header(["NewBaskervilleStd-Bold"])), [])


SIDEBAR_FACE = "ITCFranklinGothicStd-Boo"


class ReadingStructureTests(unittest.TestCase):
    """Structure the reading copies flattened: sub-lists, sidebars, split sentences, listing titles."""

    def hits(self, blocks: list[QaBlock], check_id: str) -> list[dict[str, Any]]:
        return [finding for finding in findings(build(blocks)) if finding["check_id"] == check_id]

    def test_loose_dash_items_after_a_bullet(self) -> None:
        # Build an AI Agent 1.6: "– OpenAI API key ..." left after the list as a paragraph.
        blocks = styled_pilot(
            rendered("b-li", C1, "list_item", "• Development-environment setup—you need:", "• 开发环境搭建：", page=38),
            rendered("b-d1", C1, "paragraph", "– OpenAI API key for accessing GPT models", "– 用于访问 GPT 模型的 OpenAI API 密钥", page=38),
            rendered("b-d2", C1, "paragraph", "– Tavily API key for web search", "– 用于网页搜索的 Tavily API 密钥", page=38),
        )
        [hit] = self.hits(blocks, "L1.sublist_as_text")
        self.assertEqual((hit["target"], hit["severity"], hit["repair"]), ({"kind": "block", "id": "b-li"}, MAJOR, "structure_edit.nest_list_items"))
        self.assertEqual(hit["evidence"]["parent_block_id"], "b-li")
        self.assertEqual(hit["evidence"]["item_block_ids"], ["b-d1", "b-d2"])

    def test_dash_items_run_into_their_bullet(self) -> None:
        # Build an AI Agent 1.4.3: "构建智能体的流程如下：– 判断… – 观察…" in one item.
        item = rendered(
            "b-li",
            C1,
            "list_item",
            "• The process goes like this:\n– Determine whether an agent would help.\n– Observe when the agent fails.",
            "• 流程如下：– 判断智能体是否有帮助。– 观察智能体何时失败。",
            page=32,
        )
        [hit] = self.hits(styled_pilot(item), "L1.sublist_as_text")
        self.assertEqual(hit["evidence"]["inline_items"], ["– Determine whether an agent would help.", "– Observe when the agent fails."])

    def test_nested_sub_items_are_not_found(self) -> None:
        blocks = styled_pilot(
            rendered("b-li", C1, "list_item", "• Development-environment setup—you need:", "• 开发环境搭建：", page=38),
            rendered(
                "b-d1",
                C1,
                "list_item",
                "– OpenAI API key for accessing GPT models",
                "– 用于访问 GPT 模型的 OpenAI API 密钥",
                page=38,
                metadata={"list_kind": "dash", "list_level": 2},
            ),
        )
        self.assertEqual(self.hits(blocks, "L1.sublist_as_text"), [])

    def test_sidebar_title_set_as_a_heading(self) -> None:
        # A Simple Guide to RAG 2.3: "Do you always need an indexing pipeline?" over sans-set paragraphs.
        title = styled(rendered("b-st", C1, "heading", "Do you always need an indexing pipeline?", "你总是需要索引流水线吗？", page=44), DEM)
        member = styled(
            rendered("b-sm", C1, "paragraph", "Offline indexing pipelines are typically used for large knowledge bases.", "离线索引流水线通常用于大型知识库。", page=44),
            SIDEBAR_FACE,
        )
        after = styled(rendered("b-after", C1, "paragraph", BODY_TEXT, "智能体决定下一步调用哪个工具。", page=45), BODY)
        [hit] = self.hits(styled_pilot(title, member, after), "L1.sidebar_as_text")
        self.assertEqual((hit["target"]["id"], hit["repair"]), ("b-st", "structure_edit.relabel_as_sidebar"))
        self.assertEqual(hit["evidence"]["member_block_ids"], ["b-sm"])
        # Drawn as a sidebar, it is not a finding.
        titled = styled(
            rendered(
                "b-st", C1, "paragraph", "Do you always need an indexing pipeline?", "你总是需要索引流水线吗？", page=44,
                metadata={"sidebar_id": "s1", "sidebar_title": True},
            ),
            DEM,
        )
        self.assertEqual(self.hits(styled_pilot(titled, member, after), "L1.sidebar_as_text"), [])

    def test_section_heading_over_body_text_is_not_a_sidebar(self) -> None:
        self.assertEqual(self.hits(styled_pilot(), "L1.sidebar_as_text"), [])

    def test_paragraph_starting_mid_sentence_after_a_figure(self) -> None:
        # Build an AI Agent 1.2.1: "... thinking tokens starting" / figure 1.1 / "with <think>. This advance ...".
        first = rendered("b-a", C1, "paragraph", "The reasoning model first generates thinking tokens starting", "推理模型首先生成以此开头的思考 token", page=24)
        second = rendered("b-b", C1, "paragraph", "with <think>. This advance enables agents to tackle complex problems.", "有了 <think>，这一进展使智能体能够处理复杂问题。", page=25)
        blocks = [*clean_blocks()[:4], first, figure_block("b-fig24", page=24), second, *clean_blocks()[4:]]
        [hit] = self.hits(blocks, "L1.paragraph_split_midsentence")
        self.assertEqual((hit["target"]["id"], hit["repair"]), ("b-b", "structure_edit.join_continuation"))
        self.assertEqual(hit["evidence"]["join_into_block_id"], "b-a")
        self.assertEqual(hit["evidence"]["between_block_ids"], ["b-fig24"])

    def test_a_paragraph_after_a_finished_sentence_is_not_split(self) -> None:
        first = rendered("b-a", C1, "paragraph", "The model answers.", "模型给出回答。", page=24)
        second = rendered("b-b", C1, "paragraph", "iPhone owners use the app too.", "iPhone 用户也在使用这个应用。", page=25)
        self.assertEqual(self.hits([*clean_blocks(), first, second], "L1.paragraph_split_midsentence"), [])

    def test_listing_title_left_as_a_paragraph_over_its_code(self) -> None:
        title = rendered("b-lt", C1, "paragraph", "Listing 2.1 Loading environment variables", "代码清单 2.1 加载环境变量", page=44)
        code = rendered("b-lc", C1, "code", "load_dotenv(find_dotenv())", "", mode="source_artifact_full_width", kind="code", page=44)
        [hit] = self.hits([*clean_blocks(), title, code], "L2.listing_caption_unlinked")
        self.assertEqual((hit["target"]["id"], hit["repair"]), ("b-lt", "structure_edit.link_listing_caption"))
        self.assertEqual((hit["evidence"]["caption_block_id"], hit["evidence"]["code_block_id"]), ("b-lt", "b-lc"))
        # The code box with its title as the caption is not a finding.
        linked = rendered(
            "b-lc", C1, "code", "load_dotenv(find_dotenv())", "代码清单 2.1 加载环境变量",
            mode="translated_wrapper_with_preserved_artifact", kind="code", page=44,
            metadata={"linked_caption_text": "Listing 2.1 Loading environment variables"},
        )
        self.assertEqual(self.hits([*clean_blocks(), linked], "L2.listing_caption_unlinked"), [])


# The Illustrated Guide's chapter-end notes (EPUB footnotes, parsed as paragraphs): the book keeps the
# English paper titles there.
REFERENCES = (
    (
        "1 Russell, Stuart and Peter Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed. (Pearson, 2020).",
        "1 Russell, Stuart 和 Peter Norvig，*Artificial Intelligence: A Modern Approach*，第 4 版（Pearson，2020）。",
    ),
    (
        "3 Mitchell, Margaret et al. 2025. “Fully Autonomous AI Agents Should Not Be Developed.” *arXiv, *2502.02649.",
        "3 Mitchell, Margaret 等，2025。“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv,* 2502.02649。",
    ),
    (
        "4 Gabriel, Iason et al. 2024. “The Ethics of Advanced AI Assistants.” *arXiv,*2404.16244.",
        "4 Gabriel, Iason 等，2024。“The Ethics of Advanced AI Assistants.” *arXiv,* 2404.16244。",
    ),
    (
        "7 Shazeer, Noam et al. 2017. “Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer,” "
        "*International Conference on Learning Representations*, poster.",
        "7 Shazeer, Noam 等，2017。“Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer”，"
        "*International Conference on Learning Representations*，海报。",
    ),
)


def _by_check(inputs: PilotQaInputs) -> dict[str, list[dict[str, Any]]]:
    by_check: dict[str, list[dict[str, Any]]] = {}
    for finding in findings(inputs):
        by_check.setdefault(finding["check_id"], []).append(finding)
    return by_check


class CitationEntryTests(unittest.TestCase):
    """Reference entries keep their English titles by the book's convention: not untranslated prose,
    a lost negation, a locked term or a term left in English."""

    def test_reference_entries_are_not_findings(self) -> None:
        notes = [
            rendered(f"b-ref{index}", C2, "paragraph", source, target, page=39, metadata={"has_inline_formatting": True})
            for index, (source, target) in enumerate(REFERENCES)
        ]
        # The book translates "mixture of experts" and "agent" elsewhere; the titles keep the English.
        moe = rendered("b-moe", C2, "paragraph", "A mixture-of-experts layer routes each token.", "混合专家层为每个词元选择路由。", page=33)
        glossary = [
            GlossaryTerm("agent", ("智能体",), locked=True),
            GlossaryTerm("tool", ("工具",)),
            GlossaryTerm("mixture-of-experts", ("混合专家",)),
        ]
        hits = _by_check(build([*clean_blocks(), moe, *notes], glossary=glossary))
        for check_id in (
            "L7.untranslated_prose",
            "L7.english_residue",
            "L6.negation_lost",
            "L5.locked_term",
            "L5.term_left_in_english",
            "L7.emphasis_marker",
        ):
            self.assertNotIn(check_id, hits, check_id)

    def test_a_negation_inside_a_quoted_title_in_prose_is_not_one(self) -> None:
        prose = rendered(
            "b-quote",
            C2,
            "paragraph",
            "The paper “Fully Autonomous AI Agents Should Not Be Developed” argues for oversight.",
            "论文“Fully Autonomous AI Agents Should Not Be Developed”主张保留人的监督。",
            page=33,
        )
        hits = _by_check(build([*clean_blocks(), prose]))
        self.assertNotIn("L6.negation_lost", hits)
        self.assertNotIn("L7.untranslated_prose", hits)
        self.assertNotIn("L5.locked_term", hits)

    def test_english_prose_left_untranslated_is_still_found(self) -> None:
        text = "Gabriel et al. (2024) showed that agents in production need careful oversight and evaluation."
        prose = rendered("b-en", C2, "paragraph", text, text, page=33)
        self.assertIn("L7.untranslated_prose", _by_check(build([*clean_blocks(), prose])))


class LargeNumberTests(unittest.TestCase):
    def test_a_number_written_with_wan_or_yi_is_the_same_number(self) -> None:
        stars = rendered(
            "b-stars",
            C2,
            "paragraph",
            "OpenClaw gained an astonishing 300,000 stars on GitHub, and Gemma 3 has a 12-billion-parameter model.",
            "OpenClaw 在 GitHub 上收获了惊人的 30 万颗星，Gemma 3 有一个 120 亿参数的模型。",
            page=33,
        )
        self.assertNotIn("L6.number_mismatch", _by_check(build([*clean_blocks(), stars])))

    def test_a_wrong_wan_rendering_is_still_a_mismatch(self) -> None:
        stars = rendered("b-stars", C2, "paragraph", "It gained 300,000 stars on GitHub.", "它在 GitHub 上收获了 3 万颗星。", page=33)
        hits = _by_check(build([*clean_blocks(), stars])).get("L6.number_mismatch", [])
        self.assertEqual([finding["evidence"]["numbers"] for finding in hits], [["300000"]])

    def test_wan_and_yi_values(self) -> None:
        self.assertEqual(
            pilot_qa._zh_scaled_numbers("30 万颗、1.5 万、4050 亿、2 万亿"),
            {"300000", "15000", "405000000000", "2000000000000"},
        )


class EmphasisMarkerTests(unittest.TestCase):
    def test_an_unpaired_marker_in_a_translation_is_found(self) -> None:
        block = rendered(
            "b-em",
            C2,
            "paragraph",
            "In practice, its *agent program* is a reasoning LLM.",
            "在实践中，其*智能体程序（agent program）是一个推理 LLM。",
            page=33,
            metadata={"has_inline_formatting": True},
        )
        hits = _by_check(build([*clean_blocks(), block])).get("L7.emphasis_marker", [])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], MAJOR)
        self.assertEqual(hits[0]["target"], {"kind": "block", "id": "b-em"})
        self.assertEqual(hits[0]["evidence"]["cause"], "translation")

    def test_paired_markers_and_text_stars_are_not(self) -> None:
        blocks = [
            rendered(
                "b-em", C2, "paragraph", "its *agent program*", "其*智能体程序*（agent program）",
                page=33, metadata={"has_inline_formatting": True},
            ),
            rendered("b-args", C2, "paragraph", "Call f(*args, **kwargs) with 2*3.", "用 2*3 调用 f(*args, **kwargs)。", page=33),
        ]
        self.assertNotIn("L7.emphasis_marker", _by_check(build([*clean_blocks(), *blocks])))

    def test_a_reading_copy_written_before_markers_were_rendered_is_written_again(self) -> None:
        from book_agent.domain.quality.pilot_repair import needs_reexport

        block = rendered("b-em", C2, "paragraph", "its *agent program*", "其*智能体程序*（agent program）", page=33)
        stale = "<section class='block paragraph'><div class='zh'>其*智能体程序*（agent program）</div></section>"
        exported = [ExportedChapter(C1, "第 1 章 为什么需要智能体", ""), ExportedChapter(C2, "第 2 章 循环", stale)]
        inputs = build([*clean_blocks(), block], exported=exported)
        hits = _by_check(inputs).get("L7.emphasis_marker", [])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["target"], {"kind": "chapter", "id": C2})
        self.assertTrue(hits[0]["evidence"]["reexport"])
        self.assertTrue(needs_reexport(pilot_qa.run_layers(inputs)))
        self.assertFalse(needs_reexport(pilot_qa.run_layers(build([*clean_blocks(), block]))))


if __name__ == "__main__":
    unittest.main()
