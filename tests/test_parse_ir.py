# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.ids import stable_id
from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.parse_ir import ParseIrRepository
from book_agent.services.bootstrap import BootstrapArtifacts, ParseService, SegmentationService
from book_agent.services.parse_ir import ParseIrService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _FakeParsedDocumentParser:
    def parse(self, _file_path: str | Path) -> ParsedDocument:
        return ParsedDocument(
            title="Context Engineering Notes",
            author="Test Author",
            language="en",
            chapters=[
                ParsedChapter(
                    chapter_id="chapter-1",
                    href="chapter1.xhtml",
                    title="Chapter One",
                    blocks=[
                        ParsedBlock(
                            block_type="heading",
                            text="Chapter One",
                            source_path="chapter1.xhtml",
                            ordinal=1,
                            anchor="h1",
                            metadata={"heading_level": 1},
                            parse_confidence=0.98,
                        ),
                        ParsedBlock(
                            block_type="paragraph",
                            text="Context engineering determines how context is created.",
                            source_path="chapter1.xhtml",
                            ordinal=2,
                            anchor="p1",
                            metadata={},
                            parse_confidence=0.97,
                        ),
                    ],
                    metadata={},
                )
            ],
            metadata={},
        )


class ParseIrServiceTests(unittest.TestCase):
    def test_builds_sidecar_projection_hints_and_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            service = ParseIrService(output_root=Path(tempdir) / "parse-ir")
            now = _utcnow()
            document = Document(
                id=stable_id("document", "parse-ir-service"),
                source_type=SourceType.EPUB,
                file_fingerprint="fingerprint-parse-ir-service",
                source_path="/tmp/sample.epub",
                title="Context Engineering Notes",
                author="Test Author",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.PARSED,
                parser_version=3,
                segmentation_version=1,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
            parsed_document = ParsedDocument(
                title="Context Engineering Notes",
                author="Test Author",
                language="en",
                chapters=[
                    ParsedChapter(
                        chapter_id="chapter-1",
                        href="chapter1.xhtml",
                        title="Chapter One",
                        blocks=[
                            ParsedBlock(
                                block_type="heading",
                                text="Chapter One",
                                source_path="chapter1.xhtml",
                                ordinal=1,
                                anchor="h1",
                                metadata={"heading_level": 1},
                                parse_confidence=0.98,
                            ),
                            ParsedBlock(
                                block_type="paragraph",
                                text="Context engineering determines how context is created.",
                                source_path="chapter1.xhtml",
                                ordinal=2,
                                anchor="p1",
                                metadata={},
                                parse_confidence=0.97,
                            ),
                        ],
                        metadata={},
                    )
                ],
                metadata={},
            )

            result = service.build(document, parsed_document)

            sidecar_path = Path(result.parse_revision.canonical_ir_path or "")
            self.assertTrue(sidecar_path.is_file())
            self.assertEqual(result.parse_revision_artifact.storage_path, str(sidecar_path))
            self.assertEqual(result.parse_revision.status.value, "active")
            self.assertEqual(result.canonical_ir.root_node_id, result.parse_revision.metadata_json["root_node_id"])
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["canonical_ir"]["root_node_id"], result.canonical_ir.root_node_id)
            self.assertEqual(payload["projection_hints"][0]["target_kind"], "document")
            self.assertEqual(result.parsed_document.metadata["parse_revision_id"], result.parse_revision.id)
            self.assertEqual(result.parsed_document.chapters[0].metadata["canonical_node_id"], payload["canonical_ir"]["nodes"][1]["node_id"])
            self.assertEqual(
                result.parsed_document.chapters[0].blocks[0].metadata["canonical_node_id"],
                payload["canonical_ir"]["nodes"][2]["node_id"],
            )


class ParseIrRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _build_parse_artifacts(self) -> tuple[Document, BootstrapArtifacts]:
        with tempfile.TemporaryDirectory() as tempdir:
            parser = _FakeParsedDocumentParser()
            parse_ir_service = ParseIrService(output_root=Path(tempdir) / "parse-ir")
            parse_service = ParseService(epub_parser=parser, parse_ir_service=parse_ir_service)
            now = _utcnow()
            document = Document(
                id=stable_id("document", "parse-ir-repository"),
                source_type=SourceType.EPUB,
                file_fingerprint="fingerprint-parse-ir-repository",
                source_path=str(Path(tempdir) / "sample.epub"),
                title=None,
                author=None,
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.INGESTED,
                parser_version=2,
                segmentation_version=1,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
            parse_artifacts = parse_service.parse(document, document.source_path or "sample.epub")
            segmentation_artifacts = SegmentationService().segment(
                parse_artifacts.document,
                parse_artifacts.chapters,
                parse_artifacts.blocks,
            )
            bootstrap_artifacts = BootstrapArtifacts(
                document=parse_artifacts.document,
                chapters=parse_artifacts.chapters,
                blocks=parse_artifacts.blocks,
                sentences=segmentation_artifacts.sentences,
                parse_revision=parse_artifacts.parse_revision,
                parse_revision_artifact=parse_artifacts.parse_revision_artifact,
                job_runs=[parse_artifacts.job_run, segmentation_artifacts.job_run],
            )
        return document, bootstrap_artifacts

    def test_save_and_load_latest_parse_revision(self) -> None:
        document, artifacts = self._build_parse_artifacts()
        with self.session_factory() as session:
            session.merge(document)
            session.flush()
            repository = ParseIrRepository(session)
            repository.save(artifacts.parse_revision, artifacts.parse_revision_artifact)
            session.commit()

        with self.session_factory() as session:
            loaded = ParseIrRepository(session).load_latest(document.id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.revision.id, artifacts.parse_revision.id)
        self.assertEqual(len(loaded.artifacts), 1)
        self.assertEqual(loaded.artifacts[0].storage_path, artifacts.parse_revision_artifact.storage_path)

    def test_bootstrap_repository_persists_parse_revision_and_provenance(self) -> None:
        document, artifacts = self._build_parse_artifacts()
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document.id)

        self.assertIsNotNone(bundle.parse_revision)
        self.assertIsNotNone(bundle.parse_revision_artifact)
        self.assertEqual(bundle.parse_revision.id, artifacts.parse_revision.id)
        self.assertEqual(bundle.parse_revision_artifact.storage_path, artifacts.parse_revision_artifact.storage_path)
        self.assertEqual(bundle.chapters[0].blocks[0].parse_revision_id, artifacts.parse_revision.id)
        self.assertIsNotNone(bundle.chapters[0].blocks[0].canonical_node_id)
        self.assertEqual(bundle.chapters[0].sentences[0].parse_revision_id, artifacts.parse_revision.id)
        self.assertIsNotNone(bundle.chapters[0].sentences[0].canonical_node_id)
