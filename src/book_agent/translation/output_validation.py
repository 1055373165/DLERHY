"""Structural checks on a worker's translation output before it is persisted.

The validator is a rejecting guardrail: ``TranslationService.call_worker``
validates every worker output and, when the report is not ok, sends the
findings back to the model in the same turn (``TranslationTask.correction``)
for a bounded number of repair calls. Only when the repair budget is spent
does the last output land with its ``error_code``, so review still sees the
problem as OMISSION / ALIGNMENT issues.

Checks, in error-code priority order:

- coverage: every current sentence is aligned to a returned target segment;
- echo: a target segment repeats its source text instead of translating it;
- empty: a target segment has no text;
- length ratio: the translated length of an aligned group is implausible
  for the amount of source text (truncation or runaway generation).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from book_agent.translation.contracts import TranslationWorkerOutput

COVERAGE_INCOMPLETE = "output_sentence_coverage_incomplete"
SOURCE_ECHOED = "output_source_echoed"
TARGET_EMPTY = "output_target_empty"
LENGTH_RATIO_ABNORMAL = "output_length_ratio_abnormal"

_WHITESPACE = re.compile(r"\s+")
_LETTERS = re.compile(r"[A-Za-z一-鿿]")


def _normalize(text: str | None) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip()).casefold()


@dataclass(frozen=True, slots=True)
class LengthRatioFinding:
    source_sentence_ids: tuple[str, ...]
    target_temp_ids: tuple[str, ...]
    source_chars: int
    target_chars: int

    @property
    def ratio(self) -> float:
        return self.target_chars / self.source_chars if self.source_chars else 0.0


@dataclass(frozen=True, slots=True)
class OutputValidationReport:
    uncovered_sentence_ids: tuple[str, ...] = ()
    unknown_source_sentence_ids: tuple[str, ...] = ()
    unknown_target_temp_ids: tuple[str, ...] = ()
    echoed_sentence_ids: tuple[str, ...] = ()
    empty_target_temp_ids: tuple[str, ...] = ()
    length_ratio_findings: tuple[LengthRatioFinding, ...] = ()

    @property
    def error_code(self) -> str | None:
        if self.uncovered_sentence_ids:
            return COVERAGE_INCOMPLETE
        if self.echoed_sentence_ids:
            return SOURCE_ECHOED
        if self.empty_target_temp_ids:
            return TARGET_EMPTY
        if self.length_ratio_findings:
            return LENGTH_RATIO_ABNORMAL
        return None

    @property
    def error_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        if self.uncovered_sentence_ids:
            codes.append(COVERAGE_INCOMPLETE)
        if self.echoed_sentence_ids:
            codes.append(SOURCE_ECHOED)
        if self.empty_target_temp_ids:
            codes.append(TARGET_EMPTY)
        if self.length_ratio_findings:
            codes.append(LENGTH_RATIO_ABNORMAL)
        return tuple(codes)

    @property
    def ok(self) -> bool:
        return not (
            self.uncovered_sentence_ids
            or self.unknown_source_sentence_ids
            or self.unknown_target_temp_ids
            or self.echoed_sentence_ids
            or self.empty_target_temp_ids
            or self.length_ratio_findings
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "error_codes": list(self.error_codes),
            "uncovered_sentence_ids": list(self.uncovered_sentence_ids),
            "unknown_source_sentence_ids": list(self.unknown_source_sentence_ids),
            "unknown_target_temp_ids": list(self.unknown_target_temp_ids),
            "echoed_sentence_ids": list(self.echoed_sentence_ids),
            "empty_target_temp_ids": list(self.empty_target_temp_ids),
            "length_ratio_findings": [
                {
                    "source_sentence_ids": list(item.source_sentence_ids),
                    "target_temp_ids": list(item.target_temp_ids),
                    "source_chars": item.source_chars,
                    "target_chars": item.target_chars,
                    "ratio": round(item.ratio, 3),
                }
                for item in self.length_ratio_findings
            ],
        }

    def findings(self, alias_of: Mapping[str, str] | None = None) -> list[str]:
        """Human-readable findings; sentence ids are rendered through ``alias_of`` when given."""

        def name(sentence_id: str) -> str:
            return (alias_of or {}).get(sentence_id, sentence_id)

        lines: list[str] = []
        if self.uncovered_sentence_ids:
            lines.append(
                "Sentences with no alignment to any returned target segment: "
                + ", ".join(name(item) for item in self.uncovered_sentence_ids)
                + ". Every current sentence must appear in exactly one alignment_suggestions entry."
            )
        if self.unknown_source_sentence_ids:
            lines.append(
                "Alignments reference sentence ids that are not in the current packet: "
                + ", ".join(self.unknown_source_sentence_ids)
                + ". Use only the sentence aliases listed in the packet."
            )
        if self.unknown_target_temp_ids:
            lines.append(
                "Alignments reference target_temp_ids with no matching target segment: "
                + ", ".join(self.unknown_target_temp_ids)
                + "."
            )
        if self.echoed_sentence_ids:
            lines.append(
                "These sentences were returned untranslated (the target repeats the source text): "
                + ", ".join(name(item) for item in self.echoed_sentence_ids)
                + ". Translate them into the target language."
            )
        if self.empty_target_temp_ids:
            lines.append(
                "Target segments with empty text_zh: "
                + ", ".join(self.empty_target_temp_ids)
                + ". Every segment must carry the translation."
            )
        for finding in self.length_ratio_findings:
            lines.append(
                "Implausible translation length for "
                + ", ".join(name(item) for item in finding.source_sentence_ids)
                + f": {finding.source_chars} source characters became {finding.target_chars} target characters. "
                + ("Check for truncation or dropped content." if finding.ratio < 1 else "Check for runaway or duplicated output.")
            )
        return lines


@dataclass(frozen=True, slots=True)
class OutputCorrection:
    """A rejected output and why, attached to the task for the repair call."""

    previous_output: TranslationWorkerOutput
    report: OutputValidationReport
    attempt: int


@dataclass(frozen=True, slots=True)
class OutputRejection:
    """Audit record of one rejected worker output within a call_worker turn."""

    attempt: int
    report: OutputValidationReport
    token_in: int = 0
    token_out: int = 0


@dataclass(slots=True)
class OutputValidator:
    # Ratios are target chars / source chars per aligned group. Chinese is
    # denser than English, so a faithful translation usually sits between 0.3
    # and 1.2; the bounds below only catch truncation and runaway output.
    min_length_ratio: float = 0.12
    max_length_ratio: float = 3.0
    # Ratio checks are meaningless on tiny groups (a heading, a number).
    min_source_chars_for_ratio: int = 60
    check_echo: bool = True
    check_length_ratio: bool = True
    _ratio_exempt: tuple[str, ...] = field(default=("code", "equation", "table", "url"), repr=False)

    def validate(
        self,
        current_sentence_ids: Iterable[str],
        output: TranslationWorkerOutput,
        *,
        source_texts: Mapping[str, str] | None = None,
    ) -> OutputValidationReport:
        """A sentence is covered when an alignment links it to a target segment the worker returned.

        ``source_texts`` (sentence id -> source text) enables the echo and
        length-ratio checks; without it only coverage is validated.
        """
        expected = list(dict.fromkeys(current_sentence_ids))
        expected_set = set(expected)
        segments_by_temp_id = {segment.temp_id: segment for segment in output.target_segments}
        covered: set[str] = set()
        unknown_sources: list[str] = []
        unknown_targets: list[str] = []
        echoed: list[str] = []
        ratio_findings: list[LengthRatioFinding] = []
        for suggestion in output.alignment_suggestions:
            valid_targets = [temp_id for temp_id in suggestion.target_temp_ids if temp_id in segments_by_temp_id]
            unknown_targets.extend(temp_id for temp_id in suggestion.target_temp_ids if temp_id not in segments_by_temp_id)
            known_sources: list[str] = []
            for sentence_id in suggestion.source_sentence_ids:
                if sentence_id not in expected_set:
                    unknown_sources.append(sentence_id)
                    continue
                known_sources.append(sentence_id)
                if valid_targets:
                    covered.add(sentence_id)
            if not source_texts or not known_sources or not valid_targets:
                continue
            target_text = " ".join(segments_by_temp_id[temp_id].text_zh or "" for temp_id in valid_targets)
            source_text = " ".join(source_texts.get(sentence_id) or "" for sentence_id in known_sources)
            if self.check_echo and self._is_echo(source_text, target_text):
                echoed.extend(known_sources)
            if self.check_length_ratio and not self._ratio_exempt_group(valid_targets, segments_by_temp_id):
                finding = self._length_ratio_finding(known_sources, valid_targets, source_text, target_text)
                if finding is not None:
                    ratio_findings.append(finding)
        empty_targets = tuple(
            segment.temp_id for segment in output.target_segments if not (segment.text_zh or "").strip()
        )
        return OutputValidationReport(
            uncovered_sentence_ids=tuple(sentence_id for sentence_id in expected if sentence_id not in covered),
            unknown_source_sentence_ids=tuple(dict.fromkeys(unknown_sources)),
            unknown_target_temp_ids=tuple(dict.fromkeys(unknown_targets)),
            echoed_sentence_ids=tuple(dict.fromkeys(echoed)),
            empty_target_temp_ids=empty_targets,
            length_ratio_findings=tuple(ratio_findings),
        )

    def validate_task(self, task: Any, output: TranslationWorkerOutput) -> OutputValidationReport:
        """Validate against a ``TranslationTask`` (current sentences with source text)."""
        sentences = list(task.current_sentences)
        return self.validate(
            [sentence.id for sentence in sentences],
            output,
            source_texts={sentence.id: (sentence.source_text or "") for sentence in sentences},
        )

    def _is_echo(self, source_text: str, target_text: str) -> bool:
        source = _normalize(source_text)
        target = _normalize(target_text)
        if not source or not target or source != target:
            return False
        # Numbers, symbols and identifiers are legitimately copied verbatim.
        return len(_LETTERS.findall(source)) >= 8

    def _ratio_exempt_group(self, temp_ids: list[str], segments: Mapping[str, Any]) -> bool:
        return any((segments[temp_id].segment_type or "").lower() in self._ratio_exempt for temp_id in temp_ids)

    def _length_ratio_finding(
        self,
        source_ids: list[str],
        target_ids: list[str],
        source_text: str,
        target_text: str,
    ) -> LengthRatioFinding | None:
        source_chars = len(_WHITESPACE.sub("", source_text))
        target_chars = len(_WHITESPACE.sub("", target_text))
        if source_chars < self.min_source_chars_for_ratio or target_chars == 0:
            return None
        ratio = target_chars / source_chars
        if self.min_length_ratio <= ratio <= self.max_length_ratio:
            return None
        return LengthRatioFinding(
            source_sentence_ids=tuple(source_ids),
            target_temp_ids=tuple(target_ids),
            source_chars=source_chars,
            target_chars=target_chars,
        )
