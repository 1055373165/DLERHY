# ruff: noqa: E402
"""Tests for ParseService modality pipeline wiring (TATR-c)."""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.models import Chapter, Document
from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.bootstrap import (
    ParseService,
    _build_modality_options_from_env,
    _flag_is_truthy,
)
from book_agent.services.modality_pipeline import ModalityPipelineOptions


# ---------------------------------------------------------------------------
# Env flag tests
# ---------------------------------------------------------------------------


class FlagParseTests(unittest.TestCase):
    def test_truthy_strings(self) -> None:
        for value in ("1", "true", "TRUE", "Yes", "on"):
            with mock.patch.dict(os.environ, {"X": value}):
                self.assertTrue(_flag_is_truthy("X"), value)

    def test_falsy_strings(self) -> None:
        for value in ("0", "false", "off", "no", " "):
            with mock.patch.dict(os.environ, {"X": value}):
                self.assertFalse(_flag_is_truthy("X"))

    def test_unset_returns_false(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X", None)
            self.assertFalse(_flag_is_truthy("X"))


class BuildModalityOptionsFromEnvTests(unittest.TestCase):
    _FLAGS = (
        "BOOK_AGENT_PDF_MODALITY_REFERENCES",
        "BOOK_AGENT_PDF_MODALITY_EQUATIONS",
        "BOOK_AGENT_PDF_MODALITY_TABLES",
        "BOOK_AGENT_PDF_MODALITY_IMAGES",
        "BOOK_AGENT_PDF_TATR_TABLE_RECOVERY",
    )

    def _clean(self) -> None:
        for f in self._FLAGS:
            os.environ.pop(f, None)

    def test_all_off_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            self._clean()
            self.assertIsNone(_build_modality_options_from_env())

    def test_single_modality_on(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            self._clean()
            os.environ["BOOK_AGENT_PDF_MODALITY_IMAGES"] = "1"
            opts = _build_modality_options_from_env()
            self.assertIsNotNone(opts)
            self.assertTrue(opts.enable_images)
            self.assertFalse(opts.enable_references)
            self.assertFalse(opts.enable_equations)
            self.assertFalse(opts.enable_tables)
            self.assertIsNone(opts.page_image_table_extractor)

    def test_tatr_only_attached_when_tables_also_enabled(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            self._clean()
            # TATR alone (without enable_tables) — flag returns no
            # opts because no other modality is on.
            os.environ["BOOK_AGENT_PDF_TATR_TABLE_RECOVERY"] = "1"
            self.assertIsNone(_build_modality_options_from_env())

            os.environ["BOOK_AGENT_PDF_MODALITY_TABLES"] = "1"
            opts = _build_modality_options_from_env()
            self.assertIsNotNone(opts)
            self.assertIsNotNone(opts.page_image_table_extractor)


# ---------------------------------------------------------------------------
# ParseService _build_block / modality pipeline integration
# ---------------------------------------------------------------------------


class ParseServiceModalityIntegrationTests(unittest.TestCase):
    """Drive `enhance_parsed_document` through ParseService using an
    explicit override (not env flags) to keep tests hermetic.

    We exercise the pipeline indirectly via _build_block + the override
    rather than spinning up a full bootstrap; the goal is to verify
    wiring, not the modality logic (which has its own suite).
    """

    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.document = Document(
            id=str(uuid4()),
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fp-1",
            source_path="test.pdf",
            title="t",
            author="a",
            src_lang="en",
            tgt_lang="zh",
            status=DocumentStatus.ACTIVE,
            parser_version=1,
            segmentation_version=1,
            metadata_json={},
        )

    def _service(
        self,
        *,
        modality_options: ModalityPipelineOptions | None = None,
    ) -> ParseService:
        return ParseService(modality_options=modality_options)

    def test_override_takes_precedence_over_env(self) -> None:
        # Even with all env flags on, an explicit override of None
        # should disable the pipeline. (Subclassing semantics covered
        # by the override field; here we verify it's stored.)
        with mock.patch.dict(
            os.environ,
            {
                "BOOK_AGENT_PDF_MODALITY_REFERENCES": "1",
                "BOOK_AGENT_PDF_MODALITY_TABLES": "1",
            },
        ):
            service = self._service(modality_options=ModalityPipelineOptions())
            self.assertIsNotNone(service._modality_options_override)
            self.assertFalse(service._modality_options_override.enable_references)


# ---------------------------------------------------------------------------
# Full pipeline doc transformation + telemetry stamp on Document.metadata_json
# ---------------------------------------------------------------------------


class ParseServiceApplyModalityPipelineTests(unittest.TestCase):
    """Direct unit tests for `ParseService._apply_modality_pipeline`.

    Drives the helper alone instead of full `parse()` to keep the test
    boundaries clean and avoid mocking unrelated internals.
    """

    def setUp(self) -> None:
        self.document = Document(
            id=str(uuid4()),
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fp-1",
            source_path="test.pdf",
            title="t",
            author="a",
            src_lang="en",
            tgt_lang="zh",
            status=DocumentStatus.ACTIVE,
            parser_version=1,
            segmentation_version=1,
            metadata_json={},
        )

    def _make_parsed_doc(self) -> ParsedDocument:
        return ParsedDocument(
            title="T",
            author="A",
            language="en",
            chapters=[
                ParsedChapter(
                    chapter_id="ch1",
                    href="h1",
                    title="Chapter 1",
                    blocks=[
                        ParsedBlock(
                            block_type="paragraph",
                            text="Body.",
                            source_path="x",
                            ordinal=1,
                            anchor="b1",
                        ),
                        ParsedBlock(
                            block_type="figure",
                            text="",
                            source_path="x",
                            ordinal=2,
                            anchor="b2",
                            metadata={"image_alt": "Diagram"},
                        ),
                    ],
                )
            ],
            metadata={},
        )

    def test_pipeline_summary_stamped_when_override_enables_images(self) -> None:
        service = ParseService(
            modality_options=ModalityPipelineOptions(enable_images=True)
        )
        rewritten = service._apply_modality_pipeline(
            self._make_parsed_doc(), self.document
        )
        self.assertIn("modality_pipeline", self.document.metadata_json)
        summary = self.document.metadata_json["modality_pipeline"]
        self.assertEqual(summary["image_blocks_protected"], 1)
        self.assertIn("references", summary["skipped_due_to_disabled"])
        # And the parsed doc was rewritten — figure block protected.
        figure_block = rewritten.chapters[0].blocks[1]
        self.assertEqual(figure_block.translatability, TRANSLATE_NONE)

    def test_no_override_no_env_skips_pipeline(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for f in (
                "BOOK_AGENT_PDF_MODALITY_REFERENCES",
                "BOOK_AGENT_PDF_MODALITY_EQUATIONS",
                "BOOK_AGENT_PDF_MODALITY_TABLES",
                "BOOK_AGENT_PDF_MODALITY_IMAGES",
            ):
                os.environ.pop(f, None)
            service = ParseService()
            original = self._make_parsed_doc()
            rewritten = service._apply_modality_pipeline(original, self.document)
        self.assertIs(rewritten, original)
        self.assertNotIn("modality_pipeline", self.document.metadata_json)

    def test_env_flag_drives_pipeline_when_no_override(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for f in (
                "BOOK_AGENT_PDF_MODALITY_REFERENCES",
                "BOOK_AGENT_PDF_MODALITY_EQUATIONS",
                "BOOK_AGENT_PDF_MODALITY_TABLES",
                "BOOK_AGENT_PDF_MODALITY_IMAGES",
            ):
                os.environ.pop(f, None)
            os.environ["BOOK_AGENT_PDF_MODALITY_IMAGES"] = "1"
            service = ParseService()
            rewritten = service._apply_modality_pipeline(
                self._make_parsed_doc(), self.document
            )
        self.assertIn("modality_pipeline", self.document.metadata_json)
        figure_block = rewritten.chapters[0].blocks[1]
        self.assertEqual(figure_block.translatability, TRANSLATE_NONE)

    def test_explicit_override_with_all_flags_off_disables_env(self) -> None:
        # An explicit override (even one with all flags False) must
        # win over a permissive env.
        with mock.patch.dict(
            os.environ,
            {"BOOK_AGENT_PDF_MODALITY_IMAGES": "1"},
        ):
            service = ParseService(modality_options=ModalityPipelineOptions())
            original = self._make_parsed_doc()
            rewritten = service._apply_modality_pipeline(original, self.document)
        # Pipeline ran with all flags off → all skipped → no behaviour change
        # but telemetry was stamped (since options object was non-None).
        self.assertIn("modality_pipeline", self.document.metadata_json)
        skipped = self.document.metadata_json["modality_pipeline"][
            "skipped_due_to_disabled"
        ]
        self.assertEqual(
            sorted(skipped),
            ["equations", "images", "references", "tables"],
        )
        # No actual modifications to the doc.
        self.assertEqual(
            rewritten.chapters[0].blocks[1].translatability, TRANSLATE_ALL
        )


if __name__ == "__main__":
    unittest.main()
