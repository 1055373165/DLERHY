"""Structural checks on a worker's translation output before it is persisted.

The validator does not reject output: review turns missing coverage into
OMISSION / ALIGNMENT issues. It reports what is wrong so the translation run
can carry an ``error_code`` and the event stream can say why.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from book_agent.translation.contracts import TranslationWorkerOutput

COVERAGE_INCOMPLETE = "output_sentence_coverage_incomplete"


@dataclass(frozen=True, slots=True)
class OutputValidationReport:
    uncovered_sentence_ids: tuple[str, ...] = ()
    unknown_source_sentence_ids: tuple[str, ...] = ()
    unknown_target_temp_ids: tuple[str, ...] = ()

    @property
    def error_code(self) -> str | None:
        return COVERAGE_INCOMPLETE if self.uncovered_sentence_ids else None

    @property
    def ok(self) -> bool:
        return not (self.uncovered_sentence_ids or self.unknown_source_sentence_ids or self.unknown_target_temp_ids)

    def to_json(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "uncovered_sentence_ids": list(self.uncovered_sentence_ids),
            "unknown_source_sentence_ids": list(self.unknown_source_sentence_ids),
            "unknown_target_temp_ids": list(self.unknown_target_temp_ids),
        }


class OutputValidator:
    def validate(self, current_sentence_ids: Iterable[str], output: TranslationWorkerOutput) -> OutputValidationReport:
        """A sentence is covered when an alignment links it to a target segment the worker returned."""
        expected = list(dict.fromkeys(current_sentence_ids))
        expected_set = set(expected)
        target_temp_ids = {segment.temp_id for segment in output.target_segments}
        covered: set[str] = set()
        unknown_sources: list[str] = []
        unknown_targets: list[str] = []
        for suggestion in output.alignment_suggestions:
            valid_targets = [temp_id for temp_id in suggestion.target_temp_ids if temp_id in target_temp_ids]
            unknown_targets.extend(temp_id for temp_id in suggestion.target_temp_ids if temp_id not in target_temp_ids)
            for sentence_id in suggestion.source_sentence_ids:
                if sentence_id not in expected_set:
                    unknown_sources.append(sentence_id)
                elif valid_targets:
                    covered.add(sentence_id)
        return OutputValidationReport(
            uncovered_sentence_ids=tuple(sentence_id for sentence_id in expected if sentence_id not in covered),
            unknown_source_sentence_ids=tuple(dict.fromkeys(unknown_sources)),
            unknown_target_temp_ids=tuple(dict.fromkeys(unknown_targets)),
        )
