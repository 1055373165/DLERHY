"""Eval suites: terminology, review, structure, export. See evals/README.md for datasets and thresholds."""

from __future__ import annotations

import json
import sys
import zipfile
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy import select

from book_agent.domain.enums import Detector, ExportType
from book_agent.domain.models import Chapter, Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.terminology.enforcement import LockedTerm, find_term_violations
from book_agent.domain.terminology.matching import SourceTermIndex, source_term_key
from book_agent.evals.runner import DATASETS, REPO_ROOT, EvalContext, SuiteFn, SuiteResult, Threshold
from book_agent.infra.repositories.review import active_target_texts
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.workflows import DocumentWorkflowService

# --- shared: build an EPUB from chapters of paragraphs ------------------------------------

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>
"""


def write_epub(path: Path, *, title: str, author: str, chapters: list[dict[str, Any]]) -> Path:
    """``chapters``: [{"title": str, "paragraphs": [str, ...]}]."""
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />']
    spine: list[str] = []
    nav: list[str] = []
    files: dict[str, str] = {}
    for index, chapter in enumerate(chapters, start=1):
        href = f"chapter{index}.xhtml"
        manifest.append(f'<item id="chap{index}" href="{href}" media-type="application/xhtml+xml" />')
        spine.append(f'<itemref idref="chap{index}" />')
        nav.append(f'<li><a href="{href}">{escape(chapter["title"])}</a></li>')
        body = "\n".join(f"<p>{escape(paragraph)}</p>" for paragraph in chapter["paragraphs"])
        files[f"OEBPS/{href}"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><body>\n'
            f'<h1 id="ch{index}">{escape(chapter["title"])}</h1>\n{body}\n</body></html>\n'
        )
    content_opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(author)}</dc:creator><dc:language>en</dc:language>"
        "</metadata>\n"
        f"<manifest>{''.join(manifest)}</manifest>\n<spine>{''.join(spine)}</spine>\n</package>\n"
    )
    nav_xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        f'<nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>\n'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", content_opf)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def _workflow(context: EvalContext, session, worker: Any = None) -> DocumentWorkflowService:
    return DocumentWorkflowService(
        session,
        export_root=context.workdir / "exports",
        translation_worker=worker if worker is not None else context.translation_worker,
        translation_max_output_repairs=context.settings.translation_max_output_repairs,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# --- terminology ------------------------------------------------------------------------


def terminology_suite(context: EvalContext) -> SuiteResult:
    dataset = json.loads((DATASETS / "terminology" / "momentum_book.json").read_text(encoding="utf-8"))
    epub_path = write_epub(
        context.workdir / "terminology.epub", title=dataset["title"], author=dataset["author"], chapters=dataset["chapters"]
    )
    terms = [
        LockedTerm(source_term=item["source_term"], renderings=(item["target_term"], *item.get("variants", [])))
        for item in dataset["locked_terms"]
    ]
    with context.session_factory() as session:
        workflow = _workflow(context, session)
        document_id = workflow.bootstrap_document(epub_path).document_id
        glossary = GlossaryService(session)
        for item in dataset["locked_terms"]:
            glossary.lock_term(document_id, item["source_term"], item["target_term"], target_variants=item.get("variants", []))
        session.commit()
        workflow.translate_document(document_id)
        session.commit()

        sentences = list(
            session.scalars(
                select(Sentence).where(Sentence.document_id == document_id, Sentence.translatable.is_(True))
            ).all()
        )
        targets = active_target_texts(session, [sentence.id for sentence in sentences])
        units = [(sentence.id, sentence.source_text, targets.get(sentence.id, "")) for sentence in sentences]
        index = SourceTermIndex(term.source_term for term in terms)
        keys = {source_term_key(term.source_term) for term in terms}
        occurrences = sum(
            len({occurrence.key for occurrence in index.find(source) if occurrence.key in keys}) for _, source, _ in units
        )
        violations = find_term_violations(units, terms, skip_empty_targets=True)
        covered = sum(1 for _, _, target in units if target.strip())
        cases = [
            {"sentence_id": v.unit_id, "source_term": v.source_term, "expected": v.expected_target_term, "target": v.target_text}
            for v in violations
        ]
    context.shared["terminology_document_id"] = document_id
    return SuiteResult(
        suite="terminology",
        metrics={
            "locked_term_consistency": _ratio(occurrences - len(violations), occurrences),
            "coverage": _ratio(covered, len(units)),
            "term_occurrences": float(occurrences),
            "violations": float(len(violations)),
        },
        thresholds=[Threshold("locked_term_consistency", 0.95), Threshold("coverage", 1.0)],
        cases=cases,
    )


# --- review ------------------------------------------------------------------------------


class _PairWorker:
    """Returns the annotated translation for each sentence, so the reviewer sees exactly the dataset pairs."""

    def __init__(self, targets_by_sentence_id: dict[str, str]) -> None:
        from book_agent.workers.translator import EchoTranslationWorker

        self._echo = EchoTranslationWorker(model_name="eval-annotated-pairs", prompt_version="eval.pairs.v1")
        self._targets = targets_by_sentence_id

    def metadata(self):
        return self._echo.metadata()

    def translate(self, task):
        result = self._echo.translate(task)
        for segment in result.output.target_segments:
            texts = [self._targets.get(sentence_id) for sentence_id in segment.source_sentence_ids]
            if all(texts):
                segment.text_zh = "".join(texts)
        return result


def load_review_pairs(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or DATASETS / "review" / "annotated_pairs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def review_suite(context: EvalContext) -> SuiteResult:
    from book_agent.harness.agents.reviewer import MODE_FULL, ReviewerAgent
    from book_agent.harness.kernel.turn import AgentTurnRunner

    pairs = load_review_pairs()
    # One chapter per pair: identical sources with different annotated targets stay apart.
    epub_path = write_epub(
        context.workdir / "review.epub",
        title="Review Eval Pairs",
        author="Eval Fixture",
        chapters=[{"title": f"Pair {pair['id']}", "paragraphs": [pair["source"]]} for pair in pairs],
    )
    notes: list[str] = []
    with context.session_factory() as session:
        document_id = _workflow(context, session).bootstrap_document(epub_path).document_id
        session.commit()
        chapters = list(session.scalars(select(Chapter).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)).all())
        pair_by_chapter = dict(zip([chapter.id for chapter in chapters], pairs, strict=False))
        sentences = list(session.scalars(select(Sentence).where(Sentence.document_id == document_id)).all())
        pair_by_sentence: dict[str, dict[str, Any]] = {}
        for sentence in sentences:
            pair = pair_by_chapter.get(sentence.chapter_id)
            if pair is not None and sentence.translatable and sentence.source_text.strip() in pair["source"]:
                pair_by_sentence.setdefault(sentence.id, pair)
        sentences_per_pair: dict[str, list[str]] = {}
        for sentence_id, pair in pair_by_sentence.items():
            sentences_per_pair.setdefault(pair["id"], []).append(sentence_id)
        targets: dict[str, str] = {}
        for pair in pairs:
            ids = sentences_per_pair.get(pair["id"], [])
            if len(ids) != 1:
                notes.append(f"pair {pair['id']} segmented into {len(ids)} sentences; the whole target is put on the first")
            for position, sentence_id in enumerate(sorted(ids, key=lambda sid: next(s.ordinal_in_block for s in sentences if s.id == sid))):
                targets[sentence_id] = pair["target"] if position == 0 else "。"
        # The dataset pairs are the translations under review: no output repairs.
        DocumentWorkflowService(
            session, export_root=context.workdir / "exports", translation_worker=_PairWorker(targets), translation_max_output_repairs=0
        ).translate_document(document_id)
        seed = ReviewerAgent(session).start_turn(
            document_id=document_id, model_name=context.translation_worker.metadata().model_name, mode=MODE_FULL
        )
        session.commit()

    outcome = AgentTurnRunner(
        session_factory=context.session_factory,
        model=context.agent_model,
        registry=ReviewerAgent.registry(),
        policy=ReviewerAgent.policy(),
    ).run(seed.turn_id)
    notes.append(f"reviewer turn {outcome.status.value}: {outcome.stop_reason}")

    with context.session_factory() as session:
        issues = list(
            session.scalars(
                select(ReviewIssue).where(ReviewIssue.document_id == document_id, ReviewIssue.detector == Detector.MODEL)
            ).all()
        )
    found: dict[str, set[str]] = {}
    for issue in issues:
        pair = pair_by_sentence.get(str(issue.sentence_id or "")) or pair_by_chapter.get(str(issue.chapter_id or ""))
        if pair is not None:
            found.setdefault(pair["id"], set()).add(issue.issue_type)
    true_positive = false_positive = false_negative = type_hits = 0
    cases = []
    for pair in pairs:
        expected = set(pair["expected"])
        reported = found.get(pair["id"], set())
        if expected and reported:
            true_positive += 1
            type_hits += int(bool(expected & reported))
        elif reported:
            false_positive += 1
        elif expected:
            false_negative += 1
        cases.append({"id": pair["id"], "expected": sorted(expected), "reported": sorted(reported)})
    return SuiteResult(
        suite="review",
        metrics={
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "type_accuracy": _ratio(type_hits, true_positive),
            "pairs": float(len(pairs)),
        },
        thresholds=[Threshold("precision", 0.8), Threshold("recall", 0.7)],
        cases=cases,
        notes=notes,
    )


# --- structure ---------------------------------------------------------------------------


def _block_types(snapshot: dict[str, Any]) -> dict[tuple[int, str], str]:
    return {
        (chapter_index, str(block.get("anchor") or block.get("ordinal"))): str(block.get("block_type"))
        for chapter_index, chapter in enumerate(snapshot.get("chapters") or [])
        for block in chapter.get("blocks") or []
    }


def structure_suite(context: EvalContext) -> SuiteResult:
    golden_dir = REPO_ROOT / "tests" / "golden" / "pdf_structure"
    thresholds = [Threshold("block_type_agreement", 0.99)]
    if not golden_dir.is_dir() or not (REPO_ROOT / "tests" / "pdf_structure_scenario.py").is_file():
        return SuiteResult("structure", {}, thresholds, skipped_reason="needs a repository checkout (tests/golden/pdf_structure)")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tests.pdf_structure_scenario import fixture_writers, normalize, parse_fixture

    agreed = total = exact = fixtures = 0
    cases = []
    for name, writer in sorted(fixture_writers().items()):
        golden_path = golden_dir / f"{name}.json"
        if not golden_path.is_file():
            continue
        fixtures += 1
        root = context.workdir / "structure" / name
        root.mkdir(parents=True, exist_ok=True)
        actual = normalize(parse_fixture(writer, root), root)
        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        expected_types, actual_types = _block_types(expected), _block_types(actual)
        matches = sum(1 for key, block_type in expected_types.items() if actual_types.get(key) == block_type)
        # Blocks the recovery invented count against agreement too.
        extra = len(set(actual_types) - set(expected_types))
        agreed += matches
        total += len(expected_types) + extra
        is_exact = json.dumps(actual, sort_keys=True, ensure_ascii=False) == json.dumps(expected, sort_keys=True, ensure_ascii=False)
        exact += int(is_exact)
        if not is_exact:
            cases.append({"fixture": name, "blocks": len(expected_types), "type_matches": matches, "extra_blocks": extra})
    return SuiteResult(
        suite="structure",
        metrics={
            "block_type_agreement": _ratio(agreed, total),
            "exact_snapshot_ratio": _ratio(exact, fixtures),
            "fixtures": float(fixtures),
        },
        thresholds=thresholds,
        cases=cases,
    )


# --- export ------------------------------------------------------------------------------


def export_suite(context: EvalContext) -> SuiteResult:
    from book_agent.services.export_qa import ExportQaService

    thresholds = [Threshold("qa_pass_ratio", 0.9)]
    document_id = context.shared.get("terminology_document_id")
    if not document_id:
        # Not a skip: without the book there is nothing to export, so the suite fails.
        return SuiteResult("export", {}, thresholds, notes=["the terminology suite did not produce a document"])
    with context.session_factory() as session:
        workflow = _workflow(context, session)
        workflow.review_document(document_id)
        session.commit()
        # The suite measures what the renderer produces, not whether review lets the book out:
        # the gate is the terminology and review suites' concern.
        exported = workflow.export_service.export_document_merged_html(document_id, enforce_gate=False)
        session.commit()
        html_path = Path(exported.file_path)
        audit = ExportQaService(session).audit(document_id, ExportType.MERGED_HTML, [(None, html_path)])
        session.commit()
    report = json.loads(Path(audit.report_paths[0]).read_text(encoding="utf-8")) if audit.report_paths else {"checks": []}
    checks = report.get("checks") or []
    passed = sum(1 for check in checks if check.get("ok"))
    return SuiteResult(
        suite="export",
        metrics={"qa_pass_ratio": _ratio(passed, len(checks)), "checks": float(len(checks))},
        thresholds=thresholds,
        cases=audit.failed_checks,
    )


SUITES: dict[str, SuiteFn] = {
    "terminology": terminology_suite,
    "review": review_suite,
    "structure": structure_suite,
    "export": export_suite,
}
