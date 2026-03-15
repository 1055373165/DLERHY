from __future__ import annotations

import re
from dataclasses import dataclass

from book_agent.domain.structure.models import ParsedBlock, ParsedChapter

_ABBREVIATIONS = [
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "Sr.",
    "Jr.",
    "St.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
    "Inc.",
    "Ltd.",
    "Co.",
    "No.",
    "Fig.",
    "Eq.",
    "U.S.",
    "U.K.",
    "a.m.",
    "p.m.",
]
_SENTINEL = "<DOT>"


@dataclass(slots=True, frozen=True)
class SegmentedSentence:
    ordinal_in_block: int
    text: str


@dataclass(slots=True, frozen=True)
class SegmentedBlock:
    block: ParsedBlock
    sentences: list[SegmentedSentence]


class EnglishSentenceSegmenter:
    """Lightweight English sentence segmenter for P0."""

    def segment_text(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []

        protected = normalized
        for abbr in _ABBREVIATIONS:
            protected = protected.replace(abbr, abbr.replace(".", _SENTINEL))
        protected = re.sub(r"(\d)\.(\d)", rf"\1{_SENTINEL}\2", protected)

        raw_parts = re.split(r'(?<=[.!?])\s+(?=(?:"|\'|“|‘|\()?[A-Z0-9])', protected)
        sentences = [
            part.replace(_SENTINEL, ".").strip()
            for part in raw_parts
            if part and part.strip()
        ]
        expanded: list[str] = []
        for sentence in sentences or [normalized]:
            expanded.extend(self._split_terminal_time_abbreviations(sentence))
        return expanded

    def _split_terminal_time_abbreviations(self, sentence: str) -> list[str]:
        parts = re.split(r'(?<=\b(?:a\.m\.|p\.m\.))\s+(?=(?:"|\'|“|‘|\()?[A-Z0-9])', sentence)
        return [part.strip() for part in parts if part.strip()]

    def segment_block(self, block: ParsedBlock) -> SegmentedBlock:
        if block.block_type in {"heading", "code", "footnote", "caption"}:
            sentences = [SegmentedSentence(ordinal_in_block=1, text=block.text)]
        else:
            sentences = [
                SegmentedSentence(ordinal_in_block=index, text=sentence)
                for index, sentence in enumerate(self.segment_text(block.text), start=1)
            ]
        return SegmentedBlock(block=block, sentences=sentences)

    def segment_chapter(self, chapter: ParsedChapter) -> list[SegmentedBlock]:
        return [self.segment_block(block) for block in chapter.blocks]
