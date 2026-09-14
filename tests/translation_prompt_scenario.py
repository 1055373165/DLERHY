"""Record the translation context and prompts for every packet of fixture books.

Guards the P3.4 split of the translation core: a recording worker sees each
real ``TranslationTask`` (compiled context packet, memory, glossary, previous
translations) while an EPUB and an academic PDF are translated, snapshots the
context packet, and builds the prompt request for every prompt profile and
layout before delegating to the echo worker. Ids are normalized.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, get_args

import tests.test_api_workflow as api_fixtures
import tests.test_pdf_support as pdf_fixtures
from book_agent.infra.db.session import session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import (
    EchoTranslationWorker,
    PromptLayout,
    PromptProfile,
    TranslationTask,
    TranslationWorkerMetadata,
    build_translation_prompt_request,
)
from tests.workflow_golden_scenario import SECOND_CHAPTER_XHTML

PROMPT_PROFILES: tuple[str, ...] = get_args(PromptProfile)
PROMPT_LAYOUTS: tuple[str, ...] = get_args(PromptLayout)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


class RecordingPromptWorker:
    def __init__(self) -> None:
        self._echo = EchoTranslationWorker()
        self.records: list[dict[str, Any]] = []

    def metadata(self) -> TranslationWorkerMetadata:
        return self._echo.metadata()

    def translate(self, task: TranslationTask):
        prompts: dict[str, Any] = {}
        for layout in PROMPT_LAYOUTS:
            for profile in PROMPT_PROFILES:
                request = build_translation_prompt_request(
                    task,
                    model_name="golden-model",
                    prompt_version="golden.v1",
                    prompt_layout=layout,
                    prompt_profile=profile,
                )
                prompts[f"{layout}/{profile}"] = {
                    "system_prompt_static": request.system_prompt_static,
                    "system_prompt_dynamic": request.system_prompt_dynamic,
                    "system_prompt": request.system_prompt,
                    "user_prompt": request.user_prompt,
                    "response_schema": request.response_schema,
                    "sentence_alias_map": request.sentence_alias_map,
                }
        self.records.append(
            {
                "context_packet": task.context_packet.model_dump(mode="json"),
                "current_sentences": [sentence.source_text for sentence in task.current_sentences],
                "prompts": prompts,
            }
        )
        return self._echo.translate(task)


def _write_epub(root: Path) -> Path:
    chapters = [("Chapter One", "chapter1.xhtml"), ("Chapter Two", "chapter2.xhtml"), ("Chapter Three", "chapter3.xhtml")]
    epub_path = root / "prompt-golden.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", api_fixtures._content_opf_with_chapters(chapters))
        archive.writestr("OEBPS/nav.xhtml", api_fixtures._nav_xhtml_with_chapters(chapters))
        archive.writestr("OEBPS/chapter1.xhtml", api_fixtures.CHAPTER_XHTML)
        archive.writestr("OEBPS/chapter2.xhtml", SECOND_CHAPTER_XHTML)
        archive.writestr("OEBPS/chapter3.xhtml", api_fixtures.CODE_CHAPTER_XHTML)
    return epub_path


def _write_pdf(root: Path) -> Path:
    pdf_path = root / "prompt-golden.pdf"
    pdf_fixtures._write_academic_paper_pdf(pdf_path)
    return pdf_path


def record_prompts(session_factory, root: Path, fixture: str) -> list[dict[str, Any]]:
    source = _write_epub(root) if fixture == "epub" else _write_pdf(root)
    worker = RecordingPromptWorker()
    export_root = str(root / "exports")
    with session_scope(session_factory) as session:
        document_id = DocumentWorkflowService(session, export_root=export_root).bootstrap_document(source).document_id
    with session_scope(session_factory) as session:
        DocumentWorkflowService(
            session,
            export_root=export_root,
            translation_worker=worker,
            translation_auto_commit_memory=True,
        ).translate_document(document_id)
    return worker.records


def normalize(value: Any, root: Path) -> Any:
    ids: dict[str, str] = {}
    root_texts = sorted({str(root.resolve()), str(root)}, key=len, reverse=True)

    def text(item: str) -> str:
        for prefix in root_texts:
            item = item.replace(prefix, "<root>")
        item = _ISO_TIMESTAMP.sub("<ts>", item)
        return _UUID.sub(lambda match: ids.setdefault(match.group(0), f"<id{len(ids) + 1}>"), item)

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {text(str(key)): walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        if isinstance(item, str):
            return text(item)
        if isinstance(item, float):
            return round(item, 4)
        return item

    return walk(value)


def intern_long_strings(snapshot: Any, *, min_length: int = 200) -> dict[str, Any]:
    """Replace repeated long strings (prompts) with references into a shared table."""
    import hashlib

    texts: dict[str, str] = {}

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        if isinstance(item, str) and len(item) >= min_length:
            key = hashlib.sha256(item.encode("utf-8")).hexdigest()[:16]
            texts[key] = item
            return f"<text:{key}>"
        return item

    return {"records": walk(snapshot), "texts": texts}
