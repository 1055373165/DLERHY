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
        exported=[ExportedChapter(C1, "1 为什么需要智能体", ""), ExportedChapter(C2, "2 循环", "")]
        if exported == "default"
        else exported,  # type: ignore[arg-type]
        health=health,
    )


def findings(inputs: PilotQaInputs) -> list[dict[str, Any]]:
    return [finding for layer in pilot_qa.run_layers(inputs) for finding in layer["findings"]]


def replace_block(blocks: list[QaBlock], block_id: str, **changes: Any) -> list[QaBlock]:
    return [dataclasses.replace(block, **changes) if block.block_id == block_id else block for block in blocks]


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
        inputs = build(exported=[ExportedChapter(C1, "1 为什么需要智能体", "")])
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


if __name__ == "__main__":
    unittest.main()
