"""PDF recovery skills: registry, pass skipping, per-document settings and the refresh API."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.domain.structure import pdf as pdf_structure
from book_agent.domain.structure import recovery_skills as registry
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_every_skill_names_real_passes_and_has_a_guide(self) -> None:
        pass_names = {block_pass.name for block_pass in pdf_structure._BLOCK_RECOVERY_PASSES}
        for skill in registry.RECOVERY_SKILLS:
            self.assertTrue(set(skill.passes) <= pass_names, skill.name)
            guide = ROOT / "skills" / "structure" / skill.name / "SKILL.md"
            self.assertTrue(guide.is_file(), guide)
            self.assertIn(f"name: {skill.name}", guide.read_text(encoding="utf-8"))

    def test_defaults_keep_every_pass_and_overrides_disable_theirs(self) -> None:
        self.assertEqual(registry.disabled_passes(None), frozenset())
        self.assertTrue(all(registry.resolve({}).values()))
        self.assertEqual(registry.disabled_passes({"manning-listings": False}), frozenset({"lock_listing_scope"}))
        self.assertEqual(registry.disabled_passes({"not-a-skill": False}), frozenset())
        with self.assertRaisesRegex(ValueError, "unknown recovery skills: nope"):
            registry.validate_overrides({"nope": False})

    def test_recover_skips_disabled_passes(self) -> None:
        calls: list[str] = []

        def spy(name):
            def run(service, blocks, context):
                calls.append(name)
                return blocks

            return run

        fake_passes = tuple(
            pdf_structure.BlockRecoveryPass(block_pass.name, spy(block_pass.name))
            for block_pass in pdf_structure._BLOCK_RECOVERY_PASSES
        )
        service = pdf_structure.PdfStructureRecoveryService.__new__(pdf_structure.PdfStructureRecoveryService)
        with patch.object(pdf_structure, "_BLOCK_RECOVERY_PASSES", fake_passes), patch.object(
            pdf_structure.PdfStructureRecoveryService, "_recover_blocks", return_value=[]
        ), patch.object(pdf_structure.PdfStructureRecoveryService, "_page_contexts", return_value={}), patch.object(
            pdf_structure.PdfStructureRecoveryService, "_page_layout_assessments", return_value={}
        ), patch.object(pdf_structure.PdfStructureRecoveryService, "_find_repeated_edge_text", return_value=set()), patch.object(
            pdf_structure.pdf_chapters, "build_chapters", side_effect=RuntimeError("stop after passes")
        ):
            extraction = type("Extraction", (), {"pages": [], "title": None, "outline_entries": []})()
            with self.assertRaisesRegex(RuntimeError, "stop after passes"):
                service.recover("x.pdf", extraction, None, recovery_skills={"manning-listings": False, "text-only-figures": False})
        self.assertNotIn("lock_listing_scope", calls)
        self.assertNotIn("recover_text_only_figures", calls)
        self.assertIn("link_footnotes", calls)
        self.assertEqual(len(calls), len(fake_passes) - 2)


class RecoverySkillsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'skills.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        app = create_app()
        app.state.session_factory = build_session_factory(engine=engine)
        app.state.export_root = str(root / "exports")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        epub_path = root / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        self.document_id = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)}).json()["document_id"]

    def test_list_update_and_refresh(self) -> None:
        listing = self.client.get(f"/v1/documents/{self.document_id}/recovery-skills")
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(all(skill["enabled"] for skill in listing.json()["skills"]))

        updated = self.client.put(
            f"/v1/documents/{self.document_id}/recovery-skills", json={"skills": {"manning-listings": False}}
        )
        self.assertEqual(updated.status_code, 200)
        states = {skill["name"]: skill["enabled"] for skill in updated.json()["skills"]}
        self.assertFalse(states["manning-listings"])
        self.assertTrue(states["academic-sections"])
        self.assertFalse(
            {s["name"]: s["enabled"] for s in self.client.get(f"/v1/documents/{self.document_id}/recovery-skills").json()["skills"]}["manning-listings"]
        )
        bad = self.client.put(f"/v1/documents/{self.document_id}/recovery-skills", json={"skills": {"nope": True}})
        self.assertEqual(bad.status_code, 422)

        refresh = self.client.post(f"/v1/documents/{self.document_id}/structure-refresh")
        self.assertEqual(refresh.status_code, 200, refresh.text)
        body = refresh.json()
        self.assertEqual(body["source_type"], "epub")
        self.assertGreaterEqual(body["refreshed_block_count"], 1)
        self.assertEqual(body["retired_sentence_count"], 0)
        self.assertEqual(self.client.get("/v1/documents/00000000-0000-0000-0000-000000000000/recovery-skills").status_code, 404)


if __name__ == "__main__":
    unittest.main()
