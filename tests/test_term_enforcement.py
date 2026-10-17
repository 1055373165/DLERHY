"""Shared locked-term matcher: one decision for the translation guardrail, violation events and review."""

import unittest

from book_agent.domain.terminology.enforcement import LockedTerm, find_term_violations
from book_agent.translation.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationWorkerOutput
from book_agent.translation.output_validation import LOCKED_TERM_VIOLATED, OutputValidator

RSI = LockedTerm("RSI divergence", ("RSI 背离", "RSI背离现象"))
BULL = LockedTerm("bull", ("多头",))
HEAD = LockedTerm("Head & Shoulders", ("头肩形态",))
INVERTED = LockedTerm("Inverted Head and Shoulders", ("反转头肩形态",))


class FindTermViolationsTests(unittest.TestCase):
    def test_a_term_inside_a_quoted_english_title_is_not_required(self) -> None:
        """The Illustrated Guide's reference "Fully Autonomous AI Agents Should Not Be Developed." was
        forced into "Fully Autonomous AI 智能体 Should Not Be Developed." by the agent lock."""
        agent = LockedTerm(source_term="agent", renderings=("智能体",))
        units = [
            ("s3", "“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv, *2502.02649.",
             "“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv,* 2502.02649。"),
            ("s4", "4 Gabriel, Iason et al. 2024. \"The Agent Ethics of Advanced AI Assistants.\" *arXiv,*2404.16244.",
             "4 Gabriel, Iason 等，2024。\"The Agent Ethics of Advanced AI Assistants.\" *arXiv,* 2404.16244。"),
        ]
        self.assertEqual(find_term_violations(units, [agent]), [])

    def test_the_term_outside_the_title_and_in_prose_quotes_is_still_required(self) -> None:
        agent = LockedTerm(source_term="agent", renderings=("智能体",))
        units = [
            ("s1", "The paper “Fully Autonomous AI Agents Should Not Be Developed” warns every agent builder.",
             "论文“Fully Autonomous AI Agents Should Not Be Developed”提醒每一位 agent 开发者。"),
            ("s2", "As we said, an “agent” acts on its environment.", "如前所述，“agent”会作用于环境。"),
        ]
        self.assertEqual([violation.unit_id for violation in find_term_violations(units, [agent])], ["s1", "s2"])

    def test_accepted_rendering_ignoring_spacing_and_case_is_honoured(self) -> None:
        units = [("s1", "Watch for RSI divergences.", "注意 rsi背离。"), ("s2", "RSI divergence again.", "再次出现RSI背离现象")]
        self.assertEqual(find_term_violations(units, [RSI]), [])

    def test_missing_rendering_is_a_violation_with_the_expected_term_first(self) -> None:
        violations = find_term_violations([("s1", "An RSI divergence appeared.", "出现了相对强弱背驰。")], [RSI])
        self.assertEqual(len(violations), 1)
        violation = violations[0]
        self.assertEqual((violation.unit_id, violation.term_index), ("s1", 0))
        self.assertEqual(violation.expected_target_term, "RSI 背离")
        self.assertEqual(violation.accepted_renderings, ("RSI 背离", "RSI背离现象"))
        self.assertEqual(violation.source_occurrences, 1)

    def test_whole_tokens_only_and_longest_term_owns_the_position(self) -> None:
        self.assertEqual(find_term_violations([("s1", "A bullish close.", "收盘偏强。")], [BULL]), [])
        units = [("s1", "An Inverted Head & Shoulders formed.", "形成了反转头肩形态。")]
        self.assertEqual(find_term_violations(units, [HEAD, INVERTED]), [])

    def test_a_term_inside_a_code_identifier_is_not_the_term(self) -> None:
        agent = LockedTerm(source_term="agent", renderings=("智能体",))
        tool_call = LockedTerm(source_term="tool call", renderings=("工具调用",))
        mcp = LockedTerm(source_term="Model Context Protocol", renderings=("模型上下文协议",))
        units = [
            ("s1", "The package is called scratch_agents.", "这个包叫 scratch_agents。"),
            ("s2", "It carries the matching tool_call_id.", "它带有匹配的 tool_call_id。"),
            ("s3", "Run npx @modelcontextprotocol/inspector first.", "先运行 npx @modelcontextprotocol/inspector。"),
            ("s4", "Then call agent.run() again.", "然后再次调用 agent.run()。"),
        ]
        self.assertEqual(find_term_violations(units, [agent, tool_call, mcp]), [])
        # The same words in prose are still the term.
        self.assertEqual(len(find_term_violations([("s5", "The agent decides.", "它来决定。")], [agent])), 1)

    def test_empty_targets_are_skipped_only_on_request(self) -> None:
        units = [("s1", "The bull ran.", "")]
        self.assertEqual(len(find_term_violations(units, [BULL])), 1)
        self.assertEqual(find_term_violations(units, [BULL], skip_empty_targets=True), [])

    def test_terms_without_a_rendering_are_ignored(self) -> None:
        self.assertEqual(find_term_violations([("s1", "The bull ran.", "牛跑了")], [LockedTerm("bull", ("",))]), [])


class GuardrailLockedTermTests(unittest.TestCase):
    def _output(self, text: str) -> TranslationWorkerOutput:
        return TranslationWorkerOutput(
            packet_id="p1",
            target_segments=[TranslationTargetSegment(temp_id="t1", text_zh=text, segment_type="sentence", source_sentence_ids=["s1"])],
            alignment_suggestions=[AlignmentSuggestion(source_sentence_ids=["s1"], target_temp_ids=["t1"], relation_type="1:1")],
        )

    def test_violation_rejects_the_output_with_an_alias_finding(self) -> None:
        report = OutputValidator().validate(
            ["s1"],
            self._output("出现了相对强弱背驰。"),
            source_texts={"s1": "An RSI divergence appeared."},
            locked_terms=[RSI],
        )
        self.assertFalse(report.ok)
        self.assertEqual(report.error_code, LOCKED_TERM_VIOLATED)
        finding = report.findings({"s1": "S1"})[0]
        self.assertIn('S1 contains the locked term "RSI divergence"', finding)
        self.assertIn('"RSI 背离"', finding)
        self.assertEqual(report.to_json()["term_violations"][0]["sentence_id"], "s1")

    def test_honoured_term_passes(self) -> None:
        report = OutputValidator().validate(
            ["s1"], self._output("出现了 RSI 背离。"), source_texts={"s1": "An RSI divergence appeared."}, locked_terms=[RSI]
        )
        self.assertTrue(report.ok)


class CapitalizedWordTermTests(unittest.TestCase):
    """How Large Language Models Work: the repair locked "Figure" -> 图 and every "figure out" failed it."""

    TERMS = [LockedTerm("Figure", ("图",)), LockedTerm("Gray", ("Gray",)), LockedTerm("model", ("模型",))]

    def violated(self, source: str, target: str) -> list[str]:
        return [item.source_term for item in find_term_violations([("u", source, target)], self.TERMS)]

    def test_one_capitalized_word_applies_only_where_the_source_capitalizes_it(self) -> None:
        self.assertEqual(self.violated("Can you figure out what STOP represents?", "你能推断出 STOP 代表什么吗？"), [])
        self.assertEqual(self.violated("The gray area is small.", "灰色区域很小。"), [])
        self.assertEqual(self.violated("Figure 2.8 shows the curves.", "Figure 2.8 展示了曲线。"), ["Figure"])
        self.assertEqual(self.violated("Figure 2.8 shows the curves.", "图 2.8 展示了曲线。"), [])
        # A lowercase word still folds case: "Model" at a sentence start is the locked "model".
        self.assertEqual(self.violated("Models learn.", "它们会学习。"), ["model"])


class LiteralTermTests(unittest.TestCase):
    """The Agent book's terminology locked identifiers as-is ("@tool", "step()", "Event"): 200 false violations."""

    TERMS = [
        LockedTerm("@tool", ("@tool",)),
        LockedTerm("step()", ("step()",)),
        LockedTerm("Event", ("Event",)),
        LockedTerm("LLM", ("LLM",)),
        LockedTerm("agent", ("智能体",)),
    ]

    def violated(self, source: str, target: str) -> list[str]:
        return [item.source_term for item in find_term_violations([("u", source, target)], self.TERMS)]

    def test_an_identifier_or_a_word_kept_as_it_is_applies_only_where_the_source_spells_it(self) -> None:
        self.assertEqual(self.violated("Did a tool return an event at each step?", "工具在每一步都返回事件吗？"), [])
        self.assertEqual(
            self.violated("Decorate it with @tool and emit an Event from step().", "用装饰器标记它，并发出事件。"),
            ["@tool", "step()", "Event"],
        )
        self.assertEqual(self.violated("Two LLMs disagree.", "两个大模型意见不一。"), ["LLM"])

    def test_a_lowercase_word_kept_as_it_is_is_a_name_in_code(self) -> None:
        terms = [LockedTerm("execute", ("execute",))]
        check = lambda source, target: [item.source_term for item in find_term_violations([("u", source, target)], terms)]  # noqa: E731
        self.assertEqual(check("The LLM executes each step.", "大语言模型逐步执行。"), [])
        self.assertEqual(check("Call `execute` on the tool.", "对工具调用执行。"), ["execute"])

    def test_words_still_match_by_their_forms(self) -> None:
        self.assertEqual(self.violated("Agents act on their own.", "代理自主行动。"), ["agent"])
        self.assertEqual(self.violated("Agents act on their own.", "智能体自主行动。"), [])


class CitationBlockGuardrailTests(unittest.TestCase):
    """How Large Language Models Work: a reference split into sentences failed the "language processing"
    lock on its English title, three times, because the title sentence alone is not a citation."""

    def test_the_pieces_of_a_citation_entry_are_not_held_to_locked_terms(self) -> None:
        from types import SimpleNamespace

        from book_agent.translation.contracts import PacketBlock

        sentences = [
            SimpleNamespace(id="s1", source_text="[1] Friederici, A. D."),
            SimpleNamespace(id="s2", source_text="(2011)."),
            SimpleNamespace(id="s3", source_text="The brain basis of language processing: From structure to function."),
        ]
        block = PacketBlock(
            block_id="b1",
            block_type="list_item",
            sentence_ids=["s1", "s2", "s3"],
            text="[1] Friederici, A. D. (2011). The brain basis of language processing: From structure to function. "
            "Physiology Review, 91, 1357-1392. https://doi.org/10.1152/physrev.00006.2011",
        )
        task = SimpleNamespace(current_sentences=sentences, context_packet=SimpleNamespace(current_blocks=[block]))
        output = TranslationWorkerOutput(
            packet_id="p",
            target_segments=[TranslationTargetSegment(temp_id=f"T{i}", text_zh=s.source_text, segment_type="sentence", source_sentence_ids=[s.id]) for i, s in enumerate(sentences, 1)],
            alignment_suggestions=[AlignmentSuggestion(source_sentence_ids=[s.id], target_temp_ids=[f"T{i}"], relation_type="1:1") for i, s in enumerate(sentences, 1)],
        )
        report = OutputValidator().validate_task(task, output, locked_terms=[LockedTerm("language processing", ("语言处理",))])
        self.assertEqual(report.term_violations, ())
        # The same title in prose is held to the lock.
        prose = SimpleNamespace(current_sentences=[sentences[2]], context_packet=SimpleNamespace(current_blocks=[
            PacketBlock(block_id="b2", block_type="paragraph", sentence_ids=["s3"], text=sentences[2].source_text)]))
        prose_output = TranslationWorkerOutput(packet_id="p", target_segments=[output.target_segments[2]], alignment_suggestions=[output.alignment_suggestions[2]])
        self.assertTrue(OutputValidator().validate_task(prose, prose_output, locked_terms=[LockedTerm("language processing", ("语言处理",))]).term_violations)


if __name__ == "__main__":
    unittest.main()
