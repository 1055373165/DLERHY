from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REQUIRED_LANE_CONTRACT_TAGS: dict[str, list[str]] = {
    "lane-delivery-upgrade": ["delivery-artifact-contract", "export-manifest"],
    "lane-pdf-scan-scale": ["pdf-scan-runtime", "scan-corpus-acceptance"],
    "lane-review-naturalness": ["review-style-contract", "naturalness-gate"],
}

REQUIRED_LANE_DOCS: dict[str, str] = {
    "lane-delivery-upgrade": "docs/phase-3-delivery-upgrade-plan.md",
    "lane-pdf-scan-scale": "docs/phase-3-pdf-scan-scale-plan.md",
    "lane-review-naturalness": "docs/phase-3-review-naturalness-plan.md",
}

REQUIRED_ACCEPTANCE_ARTIFACTS: dict[str, list[str]] = {
    "lane-delivery-upgrade": [
        "tests/test_persistence_and_review.py",
        "tests/test_api_workflow.py",
    ],
    "lane-pdf-scan-scale": [
        "tests/test_pdf_scan_corpus_acceptance.py",
    ],
    "lane-review-naturalness": [
        "tests/test_review_naturalness_acceptance.py",
    ],
}

ACCEPTANCE_TEST_MATRIX: dict[str, list[str]] = {
    "lane-delivery-upgrade": [
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_exports_rebuilt_epub_with_manifest_and_assets",
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_export_service_rebuilt_epub_rejects_non_epub_source_document",
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_exports_rebuilt_pdf_from_merged_html_substrate",
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_rebuilt_pdf_fails_closed_when_renderer_unavailable",
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_exports_merged_markdown_with_assets",
        "tests.test_persistence_and_review.PersistenceAndReviewTests.test_workflow_exports_merged_markdown_from_legacy_db_without_document_images_table",
        "tests.test_api_workflow.ApiWorkflowTests.test_rebuilt_epub_export_produces_document_level_epub_artifact",
        "tests.test_api_workflow.ApiWorkflowTests.test_rebuilt_pdf_export_downloads_document_level_pdf_when_renderer_is_available",
        "tests.test_api_workflow.ApiWorkflowTests.test_rebuilt_pdf_export_fails_closed_when_renderer_is_unavailable",
        "tests.test_api_workflow.ApiWorkflowTests.test_merged_html_export_renders_structured_artifacts_with_special_modes",
    ],
    "lane-pdf-scan-scale": [
        "tests.test_pdf_scan_corpus_acceptance.PdfScanCorpusAcceptanceTests.test_locked_larger_corpus_acceptance_passes_phase3_thresholds",
        "tests.test_pdf_scan_corpus_acceptance.PdfScanCorpusAcceptanceTests.test_locked_larger_corpus_acceptance_snapshot_records_frozen_baseline_values",
    ],
    "lane-review-naturalness": [
        "tests.test_review_naturalness_acceptance.ReviewNaturalnessAcceptanceTests.test_locked_benchmark_families_emit_expected_naturalness_signals",
        "tests.test_review_naturalness_acceptance.ReviewNaturalnessAcceptanceTests.test_guided_followup_clears_literalism_benchmark_under_locked_contract",
        "tests.test_review_naturalness_acceptance.ReviewNaturalnessAcceptanceTests.test_mixed_benchmark_keeps_term_priority_before_style_followup",
    ],
}

PHASE3_PLAN_PATH = Path("docs/phase-3-parallel-autopilot-plan.md")
WORK_GRAPH_PATH = Path("WORK_GRAPH.json")
LANE_STATE_PATH = Path("LANE_STATE.json")


def _read_frontmatter_status(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^status:\s*([^\n]+)$", text)
    return match.group(1).strip() if match else None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_phase3_integration_gate(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    work_graph = _load_json(repo_root / WORK_GRAPH_PATH)
    lane_state = _load_json(repo_root / LANE_STATE_PATH)
    phase_plan_status = _read_frontmatter_status(repo_root / PHASE3_PLAN_PATH)

    node_map = {
        str(node["node_id"]): node
        for node in work_graph.get("nodes", [])
    }
    lane_map = {
        str(lane["lane_id"]): lane
        for lane in lane_state.get("lanes", [])
    }
    contract_map = {
        str(contract["tag"]): contract
        for contract in work_graph.get("contracts", [])
    }

    lane_doc_statuses = {
        lane_id: _read_frontmatter_status(repo_root / relative_path)
        for lane_id, relative_path in REQUIRED_LANE_DOCS.items()
    }
    acceptance_artifact_presence = {
        lane_id: [
            {
                "path": relative_path,
                "exists": (repo_root / relative_path).exists(),
            }
            for relative_path in relative_paths
        ]
        for lane_id, relative_paths in REQUIRED_ACCEPTANCE_ARTIFACTS.items()
    }

    lane_contract_coverage: dict[str, dict[str, Any]] = {}
    for lane_id, expected_tags in REQUIRED_LANE_CONTRACT_TAGS.items():
        lane = lane_map.get(lane_id, {})
        actual_tags = sorted(str(tag) for tag in lane.get("contract_tags", []))
        missing_tags = [tag for tag in expected_tags if tag not in contract_map]
        lane_contract_coverage[lane_id] = {
            "expected_tags": expected_tags,
            "actual_tags": actual_tags,
            "status": lane.get("status"),
            "missing_tags": missing_tags,
            "owner_node_ids": {
                tag: contract_map.get(tag, {}).get("owner_node_id")
                for tag in expected_tags
            },
        }

    gate_node = node_map.get("mdu-18.1.1", {})
    gate_dependencies = list(gate_node.get("depends_on", []))
    dependency_statuses = {
        dependency: node_map.get(dependency, {}).get("status")
        for dependency in gate_dependencies
    }

    checks = {
        "lane_docs_complete": {
            "passed": all(
                isinstance(status, str) and status.endswith("complete")
                for status in lane_doc_statuses.values()
            ),
            "lane_doc_statuses": lane_doc_statuses,
        },
        "lane_acceptance_artifacts_present": {
            "passed": all(
                entry["exists"]
                for entries in acceptance_artifact_presence.values()
                for entry in entries
            ),
            "artifacts": acceptance_artifact_presence,
        },
        "lane_statuses_done": {
            "passed": all(
                lane_map.get(lane_id, {}).get("status") == "done"
                for lane_id in REQUIRED_LANE_CONTRACT_TAGS
            ),
            "lane_statuses": {
                lane_id: lane_map.get(lane_id, {}).get("status")
                for lane_id in REQUIRED_LANE_CONTRACT_TAGS
            },
        },
        "contract_coverage": {
            "passed": all(
                lane_contract_coverage[lane_id]["actual_tags"] == sorted(expected_tags)
                and not lane_contract_coverage[lane_id]["missing_tags"]
                for lane_id, expected_tags in REQUIRED_LANE_CONTRACT_TAGS.items()
            ),
            "lanes": lane_contract_coverage,
        },
        "integration_preconditions_ready": {
            "passed": all(status == "done" for status in dependency_statuses.values()),
            "dependency_statuses": dependency_statuses,
            "gate_node_status": gate_node.get("status"),
        },
    }

    return {
        "phase_plan_status": phase_plan_status,
        "current_wave_id": lane_state.get("current_wave_id"),
        "gate_node": {
            "node_id": gate_node.get("node_id"),
            "status": gate_node.get("status"),
            "depends_on": gate_dependencies,
            "contract_tags": gate_node.get("contract_tags", []),
        },
        "lane_doc_statuses": lane_doc_statuses,
        "acceptance_matrix": ACCEPTANCE_TEST_MATRIX,
        "checks": checks,
    }
