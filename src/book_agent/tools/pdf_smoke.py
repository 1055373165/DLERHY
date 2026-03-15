from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from book_agent.domain.structure.pdf import PDFParser, PdfFileProfiler
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator

_SKIPPED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "pdf-smoke-case"


def _pdf_candidate_kind(profile: dict[str, Any]) -> str:
    pdf_kind = str(profile.get("pdf_kind") or "")
    layout_risk = str(profile.get("layout_risk") or "")
    ocr_required = bool(profile.get("ocr_required"))
    if pdf_kind == "text_pdf" and layout_risk == "low" and not ocr_required:
        return "pass_path"
    if pdf_kind == "text_pdf":
        return "reject_path"
    if ocr_required:
        return "ocr_path"
    return "unsupported_path"


def _candidate_score(profile: dict[str, Any]) -> int:
    kind = _pdf_candidate_kind(profile)
    score = {
        "pass_path": 100,
        "reject_path": 48,
        "ocr_path": 18,
        "unsupported_path": 10,
        "profile_error": 0,
    }.get(kind, 0)

    page_count = int(profile.get("page_count") or 0)
    if 2 <= page_count <= 40:
        score += 8
    elif 41 <= page_count <= 120:
        score += 4
    elif page_count == 1:
        score -= 6
    elif page_count >= 300:
        score -= 12

    if bool(profile.get("outline_present")):
        score += 6
    if not bool(profile.get("has_extractable_text")):
        score -= 20

    suspicious_count = len(profile.get("suspicious_page_numbers") or [])
    score -= min(16, suspicious_count * 4)
    return max(0, min(100, score))


def _candidate_reasons(profile: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    kind = _pdf_candidate_kind(profile)
    page_count = int(profile.get("page_count") or 0)
    suspicious_count = len(profile.get("suspicious_page_numbers") or [])

    if kind == "pass_path":
        reasons.append("Low-risk text PDF can validate the bootstrap pass path.")
    elif kind == "reject_path":
        reasons.append("Text PDF with elevated layout risk can exercise the guarded recovery path.")
    elif kind == "ocr_path":
        reasons.append("OCR-required PDF is useful for fail-safe rejection coverage.")
    else:
        reasons.append("Profile does not currently match a prioritized smoke-corpus gap.")

    if 2 <= page_count <= 40:
        reasons.append("Page count is short enough for fast smoke debugging.")
    elif page_count >= 300:
        reasons.append("Long document will be more expensive to use as a recurring smoke sample.")

    if bool(profile.get("outline_present")):
        reasons.append("Outline/bookmarks are present and can validate chapter recovery.")
    if suspicious_count:
        reasons.append(f"Profiler already flagged {suspicious_count} suspicious page(s).")
    if not bool(profile.get("has_extractable_text")):
        reasons.append("Extractable text is missing, so this stays outside the text-PDF pass path.")
    return reasons


def _is_manifest_recommendation(profile: dict[str, Any]) -> bool:
    return _pdf_candidate_kind(profile) == "pass_path"


def _suggest_manifest_case(path: Path, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _slugify(path.stem),
        "source_path": str(path),
        "optional": True,
        "include_parse_summary": True,
        "expectations": [
            {"path": "profile.pdf_kind", "equals": str(profile.get("pdf_kind") or "text_pdf")},
            {"path": "profile.layout_risk", "equals": str(profile.get("layout_risk") or "low")},
            {"path": "bootstrap.status", "equals": "succeeded"},
        ],
    }


def _should_skip_directory(name: str) -> bool:
    if not name:
        return True
    if name.startswith("."):
        return True
    return name.casefold() in _SKIPPED_DIRECTORY_NAMES


def _iter_pdf_paths(
    roots: list[str | Path],
    *,
    recursive: bool = True,
    max_files: int | None = None,
):
    seen: set[Path] = set()
    yielded = 0

    for root in [Path(root) for root in roots]:
        if root.is_file():
            if root.suffix.casefold() == ".pdf" and root not in seen:
                seen.add(root)
                yield root
            continue
        if not root.exists() or not root.is_dir():
            continue

        if recursive:
            for current_root, dirnames, filenames in os.walk(root, topdown=True):
                dirnames[:] = sorted(
                    dirname for dirname in dirnames if not _should_skip_directory(dirname)
                )
                for filename in sorted(filenames):
                    if filename.startswith(".") or not filename.casefold().endswith(".pdf"):
                        continue
                    path = Path(current_root) / filename
                    if path in seen:
                        continue
                    seen.add(path)
                    yield path
                    yielded += 1
                    if max_files is not None and yielded >= max_files:
                        return
        else:
            for child in sorted(root.iterdir()):
                if (
                    not child.is_file()
                    or child.name.startswith(".")
                    or child.suffix.casefold() != ".pdf"
                    or child in seen
                ):
                    continue
                seen.add(child)
                yield child
                yielded += 1
                if max_files is not None and yielded >= max_files:
                    return


def _chapter_summary(artifacts) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": chapter.ordinal,
            "title_src": chapter.title_src,
            "risk_level": chapter.risk_level.value if chapter.risk_level else None,
            "source_page_start": chapter.metadata_json.get("source_page_start"),
            "source_page_end": chapter.metadata_json.get("source_page_end"),
            "pdf_section_family": chapter.metadata_json.get("pdf_section_family"),
            "structure_flags": chapter.metadata_json.get("structure_flags"),
        }
        for chapter in artifacts.chapters
    ]


def _parse_summary(parsed) -> dict[str, Any]:
    evidence = parsed.metadata.get("pdf_page_evidence") or {}
    pages = evidence.get("pdf_pages") or []
    return {
        "title": parsed.title,
        "chapter_count": len(parsed.chapters),
        "chapter_titles": [chapter.title for chapter in parsed.chapters],
        "chapter_section_families": [chapter.metadata.get("pdf_section_family") for chapter in parsed.chapters],
        "extractor_kind": evidence.get("extractor_kind"),
        "page_count": evidence.get("page_count"),
        "page_families": {
            str(page["page_number"]): page["page_family"]
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and isinstance(page.get("page_family"), str)
        },
        "layout_signals": {
            str(page["page_number"]): page.get("layout_signals") or []
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
        },
        "layout_suspect_pages": [
            int(page["page_number"])
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and bool(page.get("layout_suspect"))
        ],
        "pdf_pages": pages,
        "pdf_outline_entries": evidence.get("pdf_outline_entries"),
    }


def build_pdf_smoke_report(
    source_path: str | Path,
    *,
    include_parse_summary: bool = False,
) -> dict[str, Any]:
    path = Path(source_path)
    profiler = PdfFileProfiler()
    profile = profiler.profile(path)
    report: dict[str, Any] = {
        "source_path": str(path),
        "profile": profile.to_dict(),
    }

    try:
        artifacts = BootstrapOrchestrator().bootstrap_document(path)
        report["bootstrap"] = {
            "status": "succeeded",
            "document_id": artifacts.document.id,
            "source_type": artifacts.document.source_type.value,
            "chapter_count": len(artifacts.chapters),
            "block_count": len(artifacts.blocks),
            "sentence_count": len(artifacts.sentences),
            "chapters": _chapter_summary(artifacts),
        }
    except Exception as exc:
        report["bootstrap"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    if include_parse_summary:
        parsed = PDFParser().parse(path, profile=profile)
        report["parse_summary"] = _parse_summary(parsed)
    return report


def discover_pdf_smoke_candidates(
    roots: list[str | Path],
    *,
    recursive: bool = True,
    max_files: int | None = None,
) -> dict[str, Any]:
    profiler = PdfFileProfiler()
    candidates: list[dict[str, Any]] = []
    scanned_file_count = 0

    for pdf_path in _iter_pdf_paths(roots, recursive=recursive, max_files=max_files):
        scanned_file_count += 1
        try:
            profile = profiler.profile(pdf_path).to_dict()
            candidate_kind = _pdf_candidate_kind(profile)
            candidate_score = _candidate_score(profile)
            recommended_for_manifest = _is_manifest_recommendation(profile)
            candidate: dict[str, Any] = {
                "path": str(pdf_path),
                "candidate_kind": candidate_kind,
                "candidate_score": candidate_score,
                "recommendation_reasons": _candidate_reasons(profile),
                "recommended_for_manifest": recommended_for_manifest,
                "profile": profile,
            }
            if recommended_for_manifest:
                candidate["suggested_manifest_case"] = _suggest_manifest_case(pdf_path, profile)
            candidates.append(candidate)
        except Exception as exc:
            candidates.append(
                {
                    "path": str(pdf_path),
                    "candidate_kind": "profile_error",
                    "candidate_score": 0,
                    "recommendation_reasons": ["Profiler raised an exception for this file."],
                    "recommended_for_manifest": False,
                    "profile_error": {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                }
            )

    priority = {
        "pass_path": 0,
        "reject_path": 1,
        "ocr_path": 2,
        "unsupported_path": 3,
        "profile_error": 4,
    }
    candidates.sort(
        key=lambda item: (
            priority.get(str(item.get("candidate_kind")), 99),
            -int(item.get("candidate_score") or 0),
            str(item.get("path")),
        )
    )

    counts: dict[str, int] = {}
    for candidate in candidates:
        kind = str(candidate.get("candidate_kind"))
        counts[kind] = counts.get(kind, 0) + 1

    recommended_cases = [
        candidate["suggested_manifest_case"]
        for candidate in candidates
        if candidate.get("recommended_for_manifest") and isinstance(candidate.get("suggested_manifest_case"), dict)
    ]

    return {
        "root_count": len(roots),
        "scanned_file_count": scanned_file_count,
        "candidate_count": len(candidates),
        "candidate_kind_counts": counts,
        "recommended_case_count": len(recommended_cases),
        "recommended_cases": recommended_cases,
        "candidates": candidates,
    }


def _lookup_path(payload: Any, path: str) -> Any:
    current = payload
    for token in path.split("."):
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(path)
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise KeyError(path) from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise KeyError(path) from exc
            continue
        raise KeyError(path)
    return current


def evaluate_pdf_smoke_expectations(
    report: dict[str, Any],
    expectations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for rule in expectations:
        path = str(rule.get("path", ""))
        if not path:
            failures.append(
                {
                    "path": "<missing>",
                    "operator": "invalid",
                    "expected": rule,
                    "actual": None,
                    "message": "Expectation rule requires a non-empty path.",
                }
            )
            continue
        try:
            actual = _lookup_path(report, path)
        except KeyError:
            failures.append(
                {
                    "path": path,
                    "operator": "missing",
                    "expected": rule,
                    "actual": None,
                    "message": f"Path not found: {path}",
                }
            )
            continue

        if "equals" in rule and actual != rule["equals"]:
            failures.append(
                {
                    "path": path,
                    "operator": "equals",
                    "expected": rule["equals"],
                    "actual": actual,
                    "message": f"Expected {path} == {rule['equals']!r}",
                }
            )

        if "contains" in rule:
            expected = rule["contains"]
            if isinstance(actual, list):
                passed = expected in actual
            else:
                passed = str(expected) in str(actual)
            if not passed:
                failures.append(
                    {
                        "path": path,
                        "operator": "contains",
                        "expected": expected,
                        "actual": actual,
                        "message": f"Expected {path} to contain {expected!r}",
                    }
                )
    return failures


def load_pdf_smoke_manifest(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if isinstance(cases, list):
        return cases
    raise ValueError("Smoke manifest must be a JSON list or an object with a 'cases' list.")


def load_pdf_smoke_expectations(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    expectations = payload.get("expectations") if isinstance(payload, dict) else None
    if isinstance(expectations, list):
        return expectations
    raise ValueError("Expectation file must be a JSON list or an object with an 'expectations' list.")


def run_pdf_smoke_corpus(
    cases: list[dict[str, Any]],
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    output_root = Path(output_dir) if output_dir is not None else None
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    passed_case_count = 0
    skipped_case_count = 0
    failed_case_count = 0
    for index, case in enumerate(cases, start=1):
        name = str(case.get("name") or f"case-{index}")
        source_path = Path(str(case.get("source_path", "")))
        include_parse_summary = bool(case.get("include_parse_summary", False))
        expectations = case.get("expectations") or []
        optional = bool(case.get("optional", False))
        skip_reason = str(case.get("skip_reason") or "optional_case_missing")
        failures: list[dict[str, Any]]
        if optional and not source_path.exists():
            report = {
                "source_path": str(source_path),
                "skip_reason": skip_reason,
            }
            failures = []
            status = "skipped"
        else:
            try:
                report = build_pdf_smoke_report(source_path, include_parse_summary=include_parse_summary)
                failures = evaluate_pdf_smoke_expectations(report, expectations)
                status = "passed" if not failures else "failed"
            except Exception as exc:
                report = {
                    "source_path": str(source_path),
                    "runner_error": {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                }
                failures = [
                    {
                        "path": "<runner>",
                        "operator": "raises",
                        "expected": "no exception",
                        "actual": f"{type(exc).__name__}: {exc}",
                        "message": "Smoke runner raised an exception.",
                    }
                ]
                status = "failed"

        report_path: str | None = None
        if output_root is not None:
            file_path = output_root / f"{_slugify(name)}.json"
            file_path.write_text(
                json.dumps(
                    {
                        "case_name": name,
                        "case_status": status,
                        "expectation_failures": failures,
                        **report,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            report_path = str(file_path)

        if status == "passed":
            passed_case_count += 1
        elif status == "skipped":
            skipped_case_count += 1
        else:
            failed_case_count += 1
        results.append(
            {
                "name": name,
                "source_path": str(source_path),
                "status": status,
                "failure_count": len(failures),
                "failures": failures,
                "report_path": report_path,
            }
        )

    return {
        "case_count": len(results),
        "passed_case_count": passed_case_count,
        "skipped_case_count": skipped_case_count,
        "failed_case_count": failed_case_count,
        "cases": results,
    }
