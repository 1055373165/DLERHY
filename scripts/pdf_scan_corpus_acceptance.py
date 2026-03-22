from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from scripts.real_book_live_reporting_common import (
    build_telemetry_compatibility,
    classify_failure_taxonomy,
    summarize_report_failure_taxonomy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

LOCKED_SCAN_CORPUS: dict[str, dict[str, str]] = {
    "tier_a_full_book": {
        "report": "artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/report.json",
        "db": "artifacts/real-book-live/deepseek-agentic-design-book-v11-full-run-chunked-pagecount/book-agent.db",
    },
    "tier_b_retry_resume": {
        "report": "artifacts/real-book-live/deepseek-agentic-design-book-v12-retry-after-balance/report.json",
    },
    "tier_c_slice_repair": {
        "repair_report": "artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-2/repair-report.json",
        "db": "artifacts/real-book-live/deepseek-agentic-design-book-v21-slice-2/book-agent.db",
    },
    "tier_c_final_repair": {
        "repair_report": "artifacts/real-book-live/deepseek-agentic-design-book-v23-final-prose-repair/repair-report.json",
        "db": "artifacts/real-book-live/deepseek-agentic-design-book-v23-final-prose-repair/book-agent.db",
    },
    "tier_d_readable_rescue": {
        "report": "artifacts/real-book-live/deepseek-agentic-design-book-v28-readable-rescue-titlefix/report.json",
        "db": "artifacts/real-book-live/deepseek-agentic-design-book-v28-readable-rescue-titlefix/book-agent.db",
    },
}

LARGER_CORPUS_ACCEPTANCE_THRESHOLDS: dict[str, Any] = {
    "full_book_min_page_count": 400,
    "full_book_min_chapter_count": 90,
    "full_book_min_packet_count": 2000,
    "full_book_min_run_count": 2000,
    "slice_repair_min_candidate_total": 100,
    "slice_repair_min_selected_count": 30,
    "slice_repair_min_success_ratio": 0.90,
    "slice_repair_max_total_cost_usd": 0.05,
    "slice_repair_max_failed_candidates": 3,
    "final_repair_min_selected_count": 1,
    "final_repair_required_success_ratio": 1.0,
    "final_repair_max_total_cost_usd": 0.01,
    "required_rescue_exports": [
        "merged_markdown",
        "merged_markdown_manifest",
        "merged_html",
        "merged_html_manifest",
    ],
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sqlite_count(database_path: Path, table_name: str) -> int:
    with closing(sqlite3.connect(database_path)) as connection:
        return int(connection.execute(f"select count(*) from {table_name}").fetchone()[0])


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _resolve_path(repo_root: Path, relative_path: str) -> Path:
    return (repo_root / relative_path).resolve()


def _repair_failure_families(failed_candidates: list[dict[str, Any]]) -> list[str]:
    families: set[str] = set()
    for candidate in failed_candidates:
        taxonomy = classify_failure_taxonomy(
            stage="repair",
            error_message=str(candidate.get("error") or ""),
        )
        families.add(str((taxonomy or {}).get("family") or "unclassified"))
    return sorted(families)


def evaluate_larger_corpus_acceptance(*, repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root.resolve() if repo_root is not None else REPO_ROOT

    tier_a_report_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_a_full_book"]["report"])
    tier_a_db_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_a_full_book"]["db"])
    tier_b_report_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_b_retry_resume"]["report"])
    tier_c_slice_report_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_c_slice_repair"]["repair_report"])
    tier_c_slice_db_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_c_slice_repair"]["db"])
    tier_c_final_report_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_c_final_repair"]["repair_report"])
    tier_c_final_db_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_c_final_repair"]["db"])
    tier_d_report_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_d_readable_rescue"]["report"])
    tier_d_db_path = _resolve_path(root, LOCKED_SCAN_CORPUS["tier_d_readable_rescue"]["db"])

    tier_a_report = _load_json(tier_a_report_path)
    tier_b_report = _load_json(tier_b_report_path)
    tier_c_slice_report = _load_json(tier_c_slice_report_path)
    tier_c_final_report = _load_json(tier_c_final_report_path)
    tier_d_report = _load_json(tier_d_report_path)

    tier_a_counts = {
        "chapter_count": _sqlite_count(tier_a_db_path, "chapters"),
        "packet_count": _sqlite_count(tier_a_db_path, "translation_packets"),
        "run_count": _sqlite_count(tier_a_db_path, "translation_runs"),
    }
    tier_c_slice_counts = {
        "chapter_count": _sqlite_count(tier_c_slice_db_path, "chapters"),
        "packet_count": _sqlite_count(tier_c_slice_db_path, "translation_packets"),
        "run_count": _sqlite_count(tier_c_slice_db_path, "translation_runs"),
    }
    tier_c_final_counts = {
        "chapter_count": _sqlite_count(tier_c_final_db_path, "chapters"),
        "packet_count": _sqlite_count(tier_c_final_db_path, "translation_packets"),
        "run_count": _sqlite_count(tier_c_final_db_path, "translation_runs"),
    }
    tier_d_counts = {
        "chapter_count": _sqlite_count(tier_d_db_path, "chapters"),
        "packet_count": _sqlite_count(tier_d_db_path, "translation_packets"),
        "run_count": _sqlite_count(tier_d_db_path, "translation_runs"),
    }

    tier_a_failure_taxonomy = summarize_report_failure_taxonomy(tier_a_report)
    tier_a_pdf_profile = (
        ((tier_a_report.get("bootstrap") or {}).get("pdf_profile") or {})
        if isinstance(tier_a_report.get("bootstrap"), dict)
        else {}
    )
    tier_a_telemetry_compatibility = build_telemetry_compatibility(tier_a_report)

    tier_b_telemetry_compatibility = build_telemetry_compatibility(tier_b_report)

    slice_repair = dict(tier_c_slice_report.get("repair_result") or {})
    slice_selected = int(slice_repair.get("candidate_selected") or 0)
    slice_repaired = int(slice_repair.get("repaired_chain_count") or 0)
    slice_failed_candidates = list(slice_repair.get("failed_candidates") or [])
    slice_failure_families = _repair_failure_families(slice_failed_candidates)

    final_repair = dict(tier_c_final_report.get("repair_result") or {})
    final_selected = int(final_repair.get("candidate_selected") or 0)
    final_repaired = int(final_repair.get("repaired_chain_count") or 0)
    final_failed_candidates = list(final_repair.get("failed_candidates") or [])

    rescue_exports = dict(tier_d_report.get("exports") or {})
    rescue_export_existence = {
        export_name: _resolve_path(root, export_path).exists()
        for export_name, export_path in rescue_exports.items()
        if isinstance(export_path, str) and export_path.strip()
    }

    lineage_signatures = {
        "tier_a_full_book": tier_a_counts,
        "tier_c_slice_repair": tier_c_slice_counts,
        "tier_c_final_repair": tier_c_final_counts,
        "tier_d_readable_rescue": tier_d_counts,
    }
    unique_structure_signatures = {
        (counts["chapter_count"], counts["packet_count"], counts["run_count"])
        for counts in lineage_signatures.values()
    }

    checks = {
        "full_book_structure_floor": {
            "passed": (
                int(tier_a_pdf_profile.get("page_count") or 0)
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["full_book_min_page_count"])
                and tier_a_counts["chapter_count"]
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["full_book_min_chapter_count"])
                and tier_a_counts["packet_count"]
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["full_book_min_packet_count"])
                and tier_a_counts["run_count"]
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["full_book_min_run_count"])
            ),
            "observed": {
                "page_count": int(tier_a_pdf_profile.get("page_count") or 0),
                **tier_a_counts,
            },
        },
        "legacy_bootstrap_failure_classified": {
            "passed": (
                isinstance(tier_a_failure_taxonomy, dict)
                and tier_a_failure_taxonomy.get("family") == "provider_exhaustion"
                and tier_a_failure_taxonomy.get("recovery_action")
                == "top_up_provider_balance_and_resume"
                and tier_a_telemetry_compatibility.get("generation") == "legacy-report-generation"
            ),
            "observed": {
                "telemetry_generation": tier_a_telemetry_compatibility.get("generation"),
                "failure_taxonomy": tier_a_failure_taxonomy,
            },
        },
        "retry_resume_lineage_present": {
            "passed": (
                tier_b_report.get("resume_from_status") == "failed"
                and tier_b_report.get("retry_from_status") == "failed"
                and tier_b_telemetry_compatibility.get("generation") == "legacy-report-generation"
            ),
            "observed": {
                "resume_from_status": tier_b_report.get("resume_from_status"),
                "retry_from_status": tier_b_report.get("retry_from_status"),
                "telemetry_generation": tier_b_telemetry_compatibility.get("generation"),
            },
        },
        "slice_repair_acceptance": {
            "passed": (
                int(slice_repair.get("candidate_total") or 0)
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["slice_repair_min_candidate_total"])
                and slice_selected
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["slice_repair_min_selected_count"])
                and _ratio(slice_repaired, slice_selected)
                >= float(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["slice_repair_min_success_ratio"])
                and float(slice_repair.get("total_cost_usd") or 0.0)
                <= float(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["slice_repair_max_total_cost_usd"])
                and len(slice_failed_candidates)
                <= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["slice_repair_max_failed_candidates"])
                and slice_failure_families == ["repair_timeout"]
            ),
            "observed": {
                "candidate_total": int(slice_repair.get("candidate_total") or 0),
                "candidate_selected": slice_selected,
                "repaired_chain_count": slice_repaired,
                "success_ratio": _ratio(slice_repaired, slice_selected),
                "total_cost_usd": float(slice_repair.get("total_cost_usd") or 0.0),
                "failed_candidates": len(slice_failed_candidates),
                "failure_families": slice_failure_families,
            },
        },
        "final_repair_closure": {
            "passed": (
                final_selected
                >= int(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["final_repair_min_selected_count"])
                and _ratio(final_repaired, final_selected)
                >= float(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["final_repair_required_success_ratio"])
                and not final_failed_candidates
                and float(final_repair.get("total_cost_usd") or 0.0)
                <= float(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["final_repair_max_total_cost_usd"])
            ),
            "observed": {
                "candidate_selected": final_selected,
                "repaired_chain_count": final_repaired,
                "success_ratio": _ratio(final_repaired, final_selected),
                "failed_candidates": len(final_failed_candidates),
                "total_cost_usd": float(final_repair.get("total_cost_usd") or 0.0),
            },
        },
        "readable_rescue_exports": {
            "passed": all(
                rescue_export_existence.get(export_name, False)
                for export_name in LARGER_CORPUS_ACCEPTANCE_THRESHOLDS["required_rescue_exports"]
            ),
            "observed": rescue_export_existence,
        },
        "lineage_structure_stability": {
            "passed": len(unique_structure_signatures) == 1,
            "observed": lineage_signatures,
        },
    }

    return {
        "thresholds": dict(LARGER_CORPUS_ACCEPTANCE_THRESHOLDS),
        "tiers": {
            "tier_a_full_book": {
                "report_path": str(tier_a_report_path),
                "database_path": str(tier_a_db_path),
                "telemetry_compatibility": tier_a_telemetry_compatibility,
                "failure_taxonomy": tier_a_failure_taxonomy,
                "page_count": int(tier_a_pdf_profile.get("page_count") or 0),
                **tier_a_counts,
            },
            "tier_b_retry_resume": {
                "report_path": str(tier_b_report_path),
                "telemetry_compatibility": tier_b_telemetry_compatibility,
                "resume_from_status": tier_b_report.get("resume_from_status"),
                "retry_from_status": tier_b_report.get("retry_from_status"),
            },
            "tier_c_slice_repair": {
                "repair_report_path": str(tier_c_slice_report_path),
                "database_path": str(tier_c_slice_db_path),
                "candidate_total": int(slice_repair.get("candidate_total") or 0),
                "candidate_selected": slice_selected,
                "repaired_chain_count": slice_repaired,
                "success_ratio": _ratio(slice_repaired, slice_selected),
                "total_cost_usd": float(slice_repair.get("total_cost_usd") or 0.0),
                "failure_families": slice_failure_families,
                **tier_c_slice_counts,
            },
            "tier_c_final_repair": {
                "repair_report_path": str(tier_c_final_report_path),
                "database_path": str(tier_c_final_db_path),
                "candidate_selected": final_selected,
                "repaired_chain_count": final_repaired,
                "success_ratio": _ratio(final_repaired, final_selected),
                "total_cost_usd": float(final_repair.get("total_cost_usd") or 0.0),
                "failed_candidates": len(final_failed_candidates),
                **tier_c_final_counts,
            },
            "tier_d_readable_rescue": {
                "report_path": str(tier_d_report_path),
                "database_path": str(tier_d_db_path),
                "exports": rescue_export_existence,
                **tier_d_counts,
            },
        },
        "checks": checks,
        "overall_passed": all(bool(check.get("passed")) for check in checks.values()),
    }
