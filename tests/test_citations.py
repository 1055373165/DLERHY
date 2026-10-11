"""Citation and reference entries, and the English titles of cited works.

Chapter-end notes of "An Illustrated Guide to AI Agents" keep their paper titles in English; the
pilot QA flagged them as untranslated prose, a lost negation ("Should Not Be Developed") and a term
left in English.
"""

from __future__ import annotations

import unittest

from book_agent.domain.structure.citations import altered_titles, is_citation_entry, is_english_title, title_spans

ENTRIES = (
    "1 Russell, Stuart and Peter Norvig, *Artificial Intelligence: A Modern Approach*, 4th ed. (Pearson, 2020).",
    "3 Mitchell, Margaret et al. 2025. “Fully Autonomous AI Agents Should Not Be Developed.” *arXiv, *2502.02649.",
    "4 Gabriel, Iason et al. 2024. “The Ethics of Advanced AI Assistants.” *arXiv,*2404.16244.",
    "8 Yang, An et al. 2025. “Qwen3 Technical Report,” *arXiv, *2505.09388.",
    "3 Guo, Daya et al. 2025. “DeepSeek-R1 incentivizes reasoning in LLMs through reinforcement learning.” *Nature* 645: 633–638.",
    "[12] Vaswani, Ashish et al. 2017. \"Attention Is All You Need.\" NeurIPS.",
    "“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv, *2502.02649.",
    # Art of AI: IEEE author lists, bulleted entries, a letter note marker, an author line on its own.
    "[1] T. Mikolov, K. Chen, G. Corrado, and J. Dean, “Efficient Estimation of Word Representations in Vector Space,” arXiv, 2013.",
    "• S. Russell and P. Norvig, Artificial Intelligence: A Modern Approach, 4th ed. Pearson, 2020.",
    "aDastin, Jeffrey. “Amazon Scraps Secret AI Recruiting Tool That Showed Bias Against Women.” Reuters, 2018, https://mng.bz/rZmZ.",
    "• J. Kirkpatrick, R. Pascanu, N. Rabinowitz, J. Veness, G. Desjardins, A. A. Rusu, K.",
)
PROSE = (
    "Gabriel et al. (2024) showed that agents in arXiv papers are risky.",
    "Arguably, the most famous example is OpenClaw, which gained an astonishing 300,000 stars on GitHub in only a couple of months.",
    "However, Python and Java are both used in 2020 by many teams.",
    "The model was asked “What is the weather in Paris today?” and answered.",
    "As we discussed in Chapter 1, “agents” are systems that act.",
    "Chen, Zaharia, and Zou showed that cascades cut costs in 2023.",
    "• A. Users want answers, and the system gives them in 2024 at scale.",
)


class CitationEntryTest(unittest.TestCase):
    def test_reference_entries_are_recognised(self) -> None:
        for text in ENTRIES:
            with self.subTest(text=text):
                self.assertTrue(is_citation_entry(text))

    def test_prose_that_mentions_a_paper_is_not_an_entry(self) -> None:
        for text in PROSE:
            with self.subTest(text=text):
                self.assertFalse(is_citation_entry(text))


class TitleTest(unittest.TestCase):
    def test_quoted_title_case_words_are_a_title(self) -> None:
        text = "3 Mitchell, Margaret et al. 2025. “Fully Autonomous AI Agents Should Not Be Developed.” *arXiv*"
        self.assertEqual([text[a:b] for a, b in title_spans(text)], ["“Fully Autonomous AI Agents Should Not Be Developed.”"])

    def test_quoted_prose_and_short_quotes_are_not(self) -> None:
        self.assertFalse(is_english_title("What is the weather in Paris today?"))
        self.assertFalse(is_english_title("agents"))
        self.assertFalse(is_english_title("完全自主的 AI 智能体"))
        self.assertEqual(title_spans("an “agent” acts"), [])

    def test_a_title_with_a_term_forced_into_it_is_altered(self) -> None:
        source = "“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv, *2502.02649."
        forced = "“Fully Autonomous AI 智能体 Should Not Be Developed.” *arXiv,* 2502.02649。"
        self.assertEqual(
            altered_titles(source, forced),
            [("“Fully Autonomous AI Agents Should Not Be Developed.”", "“Fully Autonomous AI 智能体 Should Not Be Developed.”")],
        )
        # Kept as it is, or translated as a whole: fine.
        self.assertEqual(altered_titles(source, "“Fully Autonomous AI Agents Should Not Be Developed.” *arXiv,* 2502.02649。"), [])
        self.assertEqual(altered_titles(source, "《不应开发完全自主的 AI 智能体》，*arXiv,* 2502.02649。"), [])
        self.assertEqual(altered_titles(source, "“不应开发完全自主的 AI 智能体。”"), [])


if __name__ == "__main__":
    unittest.main()
