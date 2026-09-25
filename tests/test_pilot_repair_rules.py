"""The pilot repair's pure decisions: classification, path choice, judging, questions, text fixes.

docs/agent-upgrade/08-pilot-quality-gate.md §7 (domain/quality/pilot_repair.py, zh_text_fixes.py).
"""

from __future__ import annotations

import unittest

from book_agent.domain.quality import pilot_repair as rules
from book_agent.domain.quality import zh_text_fixes as fixes
from book_agent.domain.quality.pilot_qa import CHECKS


def finding(check_id: str, key: str, *, severity: str = "blocking", packet: str | None = None, detail: str | None = None, page: int | None = None) -> dict:
    return {
        "check_id": check_id,
        "layer": check_id.split(".")[0],
        "severity": severity,
        "key": key,
        "target": {"kind": "packet" if packet else "block", "id": packet or key},
        "location": {key: value for key, value in (("packet_id", packet), ("page", page)) if value is not None},
        "evidence": {"detail": detail} if detail else {},
        "message": CHECKS[check_id].title,
    }


def layers(*findings: dict, counts: dict[str, int] | None = None) -> list[dict]:
    by_check: dict[str, int] = {}
    severity: dict[str, int] = {"blocking": 0, "major": 0, "minor": 0}
    for item in findings:
        by_check[item["check_id"]] = by_check.get(item["check_id"], 0) + 1
        severity[item["severity"]] += 1
    return [
        {
            "layer": "L1",
            "counts": severity,
            "checks": [{"check_id": check_id, "count": count} for check_id, count in {**by_check, **(counts or {})}.items()],
            "findings": list(findings),
        }
    ]


class _Ledger:
    def __init__(self, fixed: str | None = None, books: int = 0) -> None:
        self.fixed, self.books = fixed, books

    def fixed_systemic_defect(self, issue_class: str) -> str | None:
        return self.fixed

    def books_with_issue(self, issue_class: str, *, exclude_document_id: str | None = None) -> int:
        return self.books


class ClassificationTests(unittest.TestCase):
    def test_a_cause_a_generic_rule_states_is_a_systemic_candidate(self) -> None:
        result = rules.classify(finding("L7.leaked_marker", "a"))
        self.assertEqual((result.kind, result.rule, result.cause.component), ("systemic", 3, "export"))

    def test_a_figure_crop_is_systemic_only_for_a_generic_problem_kind(self) -> None:
        header = rules.classify(finding("L2.figure_regions", "a", detail="page_furniture"))
        self.assertEqual((header.kind, header.issue_class), ("systemic", "L2.figure_regions:page_furniture"))
        cut = rules.classify(finding("L2.figure_regions", "b", detail="cut_path"))
        self.assertEqual((cut.kind, cut.rule), ("book_specific", 4))

    def test_the_book_own_problems_are_book_specific(self) -> None:
        for check_id in ("L5.term_variants", "L6.untranslated_sentence", "L6.model_review"):
            self.assertEqual(rules.classify(finding(check_id, "a")).rule, 4, check_id)

    def test_the_ledger_rules_come_first(self) -> None:
        recurred = rules.classify(finding("L6.untranslated_sentence", "a"), _Ledger(fixed="ticket-1"))
        self.assertEqual((recurred.kind, recurred.rule, recurred.ledger_ref), ("systemic", 1, "ticket-1"))
        seen = rules.classify(finding("L5.term_variants", "a"), _Ledger(books=2))
        self.assertEqual((seen.kind, seen.rule), ("systemic", 2))
        once = rules.classify(finding("L5.term_variants", "a"), _Ledger(books=1))
        self.assertEqual(once.rule, 4)


class PlanTests(unittest.TestCase):
    def test_text_fixes_take_every_finding_of_their_check_at_once(self) -> None:
        work = rules.plan_work(
            [finding("L7.cjk_latin_spacing", "s1", severity="major"), finding("L7.cjk_latin_spacing", "s2", severity="major")], set()
        )
        self.assertEqual([(item.path.name, item.keys) for item in work], [("segment_edit.cjk_latin_spacing", ["s1", "s2"])])

    def test_retranslation_goes_packet_by_packet_across_checks(self) -> None:
        work = rules.plan_work(
            [
                finding("L6.untranslated_sentence", "u1", packet="p1"),
                finding("L6.number_mismatch", "n1", severity="major", packet="p1"),
                finding("L6.untranslated_sentence", "u2", packet="p2"),
            ],
            set(),
        )
        self.assertEqual(sorted(tuple(item.keys) for item in work), [("u1", "n1"), ("u2",)])

    def test_structure_comes_before_retranslation_and_tried_paths_are_not_tried_again(self) -> None:
        items = [finding("L6.untranslated_sentence", "u1", packet="p1"), finding("L1.heading_is_prose", "h1", severity="major")]
        work = rules.plan_work(items, set())
        self.assertEqual([item.path.tool for item in work], ["structure_edit", "retranslate_packets"])
        tried = {("h1", "structure_edit.relabel_as_paragraph")}
        self.assertEqual([item.path.tool for item in rules.plan_work(items, tried)], ["retranslate_packets"])

    def test_the_next_path_after_one_failed(self) -> None:
        item = finding("L2.caption_swallows_body", "c1")
        first = rules.plan_work([item], set())[0].path.path
        second = rules.plan_work([item], {("c1", f"structure_edit.{first}")})[0].path.path
        self.assertEqual((first, second), ("split_caption", "unfold_caption_context"))

    def test_a_check_without_a_path_is_not_planned(self) -> None:
        self.assertEqual(rules.plan_work([finding("L7.stray_glyph", "g1")], set()), [])


class JudgeTests(unittest.TestCase):
    def test_kept_when_the_target_went_away_and_nothing_got_worse(self) -> None:
        before = rules.QaSnapshot.from_layers(layers(finding("L6.untranslated_sentence", "u1")))
        after = rules.QaSnapshot.from_layers(layers(counts={"L6.untranslated_sentence": 0}))
        verdict = rules.judge(before, after, ["u1"], {"L6.untranslated_sentence"})
        self.assertEqual((verdict.keep, verdict.resolved_keys), (True, ("u1",)))

    def test_reverted_when_another_check_got_worse(self) -> None:
        before = rules.QaSnapshot.from_layers(layers(finding("L6.untranslated_sentence", "u1")))
        after = rules.QaSnapshot.from_layers(layers(finding("L7.leaked_marker", "l1"), counts={"L6.untranslated_sentence": 0}))
        verdict = rules.judge(before, after, ["u1"], {"L6.untranslated_sentence"})
        self.assertEqual((verdict.keep, verdict.reason), (False, "worse:L7.leaked_marker"))

    def test_reverted_when_nothing_improved(self) -> None:
        snapshot = rules.QaSnapshot.from_layers(layers(finding("L6.untranslated_sentence", "u1")))
        self.assertEqual(rules.judge(snapshot, snapshot, ["u1"], {"L6.untranslated_sentence"}).reason, "not_improved")

    def test_a_minor_check_does_not_decide(self) -> None:
        before = rules.QaSnapshot.from_layers(layers(finding("L6.untranslated_sentence", "u1")))
        after = rules.QaSnapshot.from_layers(
            layers(finding("L7.odd_whitespace", "w1", severity="minor"), counts={"L6.untranslated_sentence": 0})
        )
        self.assertTrue(rules.judge(before, after, ["u1"], {"L6.untranslated_sentence"}).keep)


class QuestionTests(unittest.TestCase):
    def test_one_concrete_yes_no_question_with_the_pages(self) -> None:
        title, question = rules.escalation_question(
            "L2.figure_regions", [finding("L2.figure_regions", "f1", page=138), finding("L2.figure_regions", "f2", page=140)]
        )
        self.assertEqual(title, CHECKS["L2.figure_regions"].title)
        self.assertIn("第 138、140 页", question)
        self.assertTrue(question.endswith("吗？"))

    def test_every_blocking_check_has_a_question(self) -> None:
        for check_id, spec in CHECKS.items():
            if spec.severity != "blocking":
                continue
            _title, question = rules.escalation_question(check_id, [finding(check_id, "k", page=3)])
            self.assertTrue(question.endswith("？"), check_id)


class TextFixTests(unittest.TestCase):
    def test_spacing_follows_the_book_and_leaves_code_alone(self) -> None:
        self.assertEqual(fixes.fix_cjk_latin_spacing("使用RAG系统的2个例子，见 `foo_bar`调用", "spaced"), "使用 RAG 系统的 2 个例子，见 `foo_bar`调用")
        self.assertEqual(fixes.fix_cjk_latin_spacing("使用 RAG 系统", "unspaced"), "使用RAG系统")
        self.assertEqual(fixes.fix_cjk_latin_spacing("见 https://example.com/a页", "spaced"), "见 https://example.com/a页")

    def test_half_width_punctuation_between_chinese_becomes_full_width(self) -> None:
        self.assertEqual(fixes.fix_halfwidth_punctuation("这是例子,很好;对吗?是的:好"), "这是例子，很好；对吗？是的：好")
        self.assertEqual(fixes.fix_halfwidth_punctuation("调用 `f(a, b)`，然后"), "调用 `f(a, b)`，然后")

    def test_leaked_notes_are_taken_out(self) -> None:
        self.assertEqual(fixes.strip_leaked_markers("图片锚点保留这是图注"), "这是图注")
        self.assertEqual(fixes.strip_leaked_markers("译文：这是译文"), "这是译文")
        self.assertEqual(fixes.strip_leaked_markers("正常的译文"), "正常的译文")

    def test_one_name_for_a_numbered_label(self) -> None:
        text = "见清单 2.1 和代码清单 2.2，还有清单2.3"
        self.assertEqual(fixes.unify_label(text, ["2.1", "2.2", "2.3"], "代码清单", ["清单"]), "见代码清单 2.1 和代码清单 2.2，还有代码清单2.3")

    def test_one_rendering_of_a_term(self) -> None:
        self.assertEqual(fixes.replace_rendering("大型语言模型和大语言模型", "大型语言模型", "大语言模型"), "大语言模型和大语言模型")
        self.assertEqual(fixes.replace_rendering("嵌入向量和嵌入", "嵌入", "嵌入向量"), "嵌入向量和嵌入向量")

    def test_markdown_bold_around_a_heading_is_taken_out(self) -> None:
        self.assertEqual(fixes.strip_markdown_emphasis("**核心要点**"), "核心要点")
        self.assertEqual(fixes.strip_markdown_emphasis("见 `a**b**c`，**重点**"), "见 `a**b**c`，重点")
        self.assertEqual(fixes.strip_leaked_markers("**明确知识边界**"), "明确知识边界")

    def test_whitespace_after_chinese_punctuation(self) -> None:
        self.assertEqual(fixes.fix_odd_whitespace("第一句。 第二句  还有　"), "第一句。第二句 还有")


if __name__ == "__main__":
    unittest.main()
