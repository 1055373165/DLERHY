from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ArtifactStatus, ParseRevisionStatus
from book_agent.domain.models import Document
from book_agent.domain.models.parse_revision import DocumentParseRevision, DocumentParseRevisionArtifact
from book_agent.domain.structure.canonical_ir import (
    CanonicalDocumentIR,
    CanonicalIRNode,
    CanonicalIRRelation,
    PageExtractionPlan,
    ParseProjectionHint,
)
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument


@dataclass(slots=True, frozen=True)
class ParseIrBuildResult:
    parse_revision: DocumentParseRevision
    parse_revision_artifact: DocumentParseRevisionArtifact
    canonical_ir: CanonicalDocumentIR
    parsed_document: ParsedDocument


def _pdf_page_plans(parsed_document: ParsedDocument) -> list[PageExtractionPlan]:
    pdf_evidence = parsed_document.metadata.get("pdf_page_evidence")
    if not isinstance(pdf_evidence, dict):
        return []
    pages = pdf_evidence.get("pdf_pages")
    if not isinstance(pages, list):
        return []

    plans: list[PageExtractionPlan] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        if not isinstance(page_number, int):
            continue
        page_family = str(page.get("page_family") or "body")
        layout_risk = str(page.get("page_layout_risk") or "low")
        reasons = [
            str(reason)
            for reason in (page.get("extraction_intent_reasons") or page.get("page_layout_reasons") or [])
            if str(reason).strip()
        ]
        if not reasons and page_family != "body":
            reasons.append(f"page_family_{page_family}")

        intent = str(page.get("extraction_intent") or "").strip()
        if intent not in {"native_text", "ocr_overlay", "hybrid_merge"}:
            if page.get("layout_suspect") or layout_risk in {"medium", "high"}:
                if "ocr_scanned_page" in reasons or str(page.get("page_layout_risk") or "") == "high":
                    intent = "ocr_overlay"
                else:
                    intent = "hybrid_merge"
            else:
                intent = "native_text"

        plans.append(
            PageExtractionPlan(
                page_number=page_number,
                source_ref=f"pdf://page/{page_number}",
                intent=intent,
                risk_reasons=reasons,
                layout_risk=layout_risk,
                recovery_lane=str(pdf_evidence.get("recovery_lane") or parsed_document.metadata.get("pdf_profile", {}).get("recovery_lane") or "") or None,
                page_family=page_family,
                metadata={
                    "layout_suspect": bool(page.get("layout_suspect")),
                    "raw_block_count": page.get("raw_block_count"),
                    "recovered_block_count": page.get("recovered_block_count"),
                    "page_layout_reasons": page.get("page_layout_reasons"),
                    "layout_signals": page.get("layout_signals"),
                    "role_counts": page.get("role_counts"),
                    "recovery_flags": page.get("recovery_flags"),
                    "pdf_page_family_source": page.get("page_family_source"),
                },
            )
        )
    return plans


class ParseIrService:
    def __init__(self, output_root: str | Path = "artifacts/parse-ir"):
        self.output_root = Path(output_root)

    def build(self, document: Document, parsed_document: ParsedDocument) -> ParseIrBuildResult:
        revision_version = int(document.parser_version or 1)
        revision_id = stable_id("parse-revision", document.id, revision_version, 1)
        root_node_id = stable_id("canonical-document", document.id, revision_version, 1)
        page_plans = _pdf_page_plans(parsed_document) if document.source_type.value.startswith("pdf") else []

        nodes: list[CanonicalIRNode] = [
            CanonicalIRNode(
                node_id=root_node_id,
                node_type="document",
                source_ref=document.source_path,
                metadata={
                    "title": document.title or parsed_document.title,
                    "author": document.author or parsed_document.author,
                    "source_type": document.source_type.value,
                },
            )
        ]
        relations: list[CanonicalIRRelation] = []
        projection_hints: list[ParseProjectionHint] = [
            ParseProjectionHint(
                source_ref=document.source_path or document.file_fingerprint,
                canonical_node_id=root_node_id,
                target_kind="document",
                target_id=document.id,
                metadata={"role": "root"},
            )
        ]

        for page_plan in page_plans:
            page_node_id = stable_id("canonical-page-plan", document.id, page_plan.page_number, page_plan.intent)
            nodes.append(
                CanonicalIRNode(
                    node_id=page_node_id,
                    node_type="page_extraction_plan",
                    parent_id=root_node_id,
                    source_ref=page_plan.source_ref,
                    text=None,
                    metadata={
                        "page_number": page_plan.page_number,
                        "intent": page_plan.intent,
                        "risk_reasons": list(page_plan.risk_reasons),
                        "layout_risk": page_plan.layout_risk,
                        "recovery_lane": page_plan.recovery_lane,
                        "page_family": page_plan.page_family,
                        **(page_plan.metadata or {}),
                    },
                )
            )
            relations.append(
                CanonicalIRRelation(
                    relation_id=stable_id("canonical-relation", root_node_id, page_node_id, "contains"),
                    relation_type="contains",
                    source_node_id=root_node_id,
                    target_node_id=page_node_id,
                )
            )
            projection_hints.append(
                ParseProjectionHint(
                    source_ref=page_plan.source_ref,
                    canonical_node_id=page_node_id,
                    target_kind="page_extraction_plan",
                    target_id=f"{document.id}:page:{page_plan.page_number}",
                    role="planning",
                    metadata={
                        "page_number": page_plan.page_number,
                        "intent": page_plan.intent,
                        "risk_reasons": list(page_plan.risk_reasons),
                    },
                )
            )

        annotated_chapters: list[ParsedChapter] = []
        for chapter_index, chapter in enumerate(parsed_document.chapters, start=1):
            chapter_node_id = stable_id("canonical-chapter", document.id, chapter.chapter_id, chapter_index)
            chapter_source_ref = chapter.href
            nodes.append(
                CanonicalIRNode(
                    node_id=chapter_node_id,
                    node_type="chapter",
                    parent_id=root_node_id,
                    source_ref=chapter_source_ref,
                    text=chapter.title,
                    metadata={
                        "chapter_id": chapter.chapter_id,
                        "ordinal": chapter_index,
                        **(chapter.metadata or {}),
                    },
                )
            )
            relations.append(
                CanonicalIRRelation(
                    relation_id=stable_id("canonical-relation", root_node_id, chapter_node_id, "contains"),
                    relation_type="contains",
                    source_node_id=root_node_id,
                    target_node_id=chapter_node_id,
                )
            )
            projection_hints.append(
                ParseProjectionHint(
                    source_ref=chapter.href,
                    canonical_node_id=chapter_node_id,
                    target_kind="chapter",
                    target_id=chapter.chapter_id,
                    metadata={"ordinal": chapter_index},
                )
            )

            annotated_blocks: list[ParsedBlock] = []
            for block_index, block in enumerate(chapter.blocks, start=1):
                block_node_id = stable_id(
                    "canonical-block",
                    document.id,
                    chapter.chapter_id,
                    block.ordinal,
                    block.source_path,
                    block.anchor or "no-anchor",
                )
                block_source_ref = f"{block.source_path}#{block.anchor}" if block.anchor else block.source_path
                nodes.append(
                    CanonicalIRNode(
                        node_id=block_node_id,
                        node_type="block",
                        parent_id=chapter_node_id,
                        source_ref=block_source_ref,
                        text=block.text,
                        metadata={
                            "block_type": block.block_type,
                            "ordinal": block.ordinal,
                            **(block.metadata or {}),
                        },
                    )
                )
                relations.append(
                    CanonicalIRRelation(
                        relation_id=stable_id("canonical-relation", chapter_node_id, block_node_id, "contains"),
                        relation_type="contains",
                        source_node_id=chapter_node_id,
                        target_node_id=block_node_id,
                    )
                )
                projection_hints.append(
                    ParseProjectionHint(
                        source_ref=block_source_ref,
                        canonical_node_id=block_node_id,
                        target_kind="block",
                        target_id=f"{chapter.chapter_id}:{block.ordinal}",
                        confidence=block.parse_confidence,
                        metadata={
                            "block_type": block.block_type,
                            "ordinal": block.ordinal,
                        },
                    )
                )

                block_metadata = dict(block.metadata or {})
                block_metadata.update(
                    {
                        "parse_revision_id": revision_id,
                        "canonical_node_id": block_node_id,
                        "canonical_parent_node_id": chapter_node_id,
                        "canonical_root_node_id": root_node_id,
                    }
                )
                annotated_blocks.append(replace(block, metadata=block_metadata))

            chapter_metadata = dict(chapter.metadata or {})
            chapter_metadata.update(
                {
                    "parse_revision_id": revision_id,
                    "canonical_node_id": chapter_node_id,
                    "canonical_parent_node_id": root_node_id,
                }
            )
            annotated_chapters.append(replace(chapter, metadata=chapter_metadata, blocks=annotated_blocks))

        annotated_metadata = dict(parsed_document.metadata or {})
        annotated_metadata.update(
            {
                "parse_revision_id": revision_id,
                "canonical_ir_root_node_id": root_node_id,
                "canonical_ir_revision_version": revision_version,
            }
        )
        annotated_document = replace(parsed_document, chapters=annotated_chapters, metadata=annotated_metadata)

        canonical_ir = CanonicalDocumentIR(
            schema_version=1,
            document_id=document.id,
            revision_id=revision_id,
            root_node_id=root_node_id,
            source_type=document.source_type.value,
            nodes=nodes,
            relations=relations,
            projection_hints=projection_hints,
            page_plans=page_plans,
            metadata={
                "document_title": document.title or parsed_document.title,
                "chapter_count": len(parsed_document.chapters),
                "block_count": sum(len(chapter.blocks) for chapter in parsed_document.chapters),
                "planning_scope": "page",
                "page_plan_count": len(page_plans),
                "page_plan_intents": {
                    str(plan.page_number): plan.intent
                    for plan in page_plans
                },
                "page_plan_reasons": {
                    str(plan.page_number): list(plan.risk_reasons)
                    for plan in page_plans
                },
            },
        )

        sidecar_path = self._sidecar_path(document.id, revision_version)
        sidecar_payload = {
            "schema_version": 1,
            "document_id": document.id,
            "revision_id": revision_id,
            "source_type": document.source_type.value,
            "canonical_ir": canonical_ir.to_dict(),
            "projection_hints": [self._projection_hint_dict(hint) for hint in projection_hints],
        }
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_text = json.dumps(sidecar_payload, ensure_ascii=False, indent=2, sort_keys=True)
        sidecar_path.write_text(sidecar_text, encoding="utf-8")
        checksum = sha256(sidecar_text.encode("utf-8")).hexdigest()

        revision = DocumentParseRevision(
            id=revision_id,
            document_id=document.id,
            version=revision_version,
            parser_version=int(document.parser_version or 1),
            parse_ir_version=1,
            source_type=document.source_type,
            source_path=document.source_path,
            source_fingerprint=document.file_fingerprint,
            status=ParseRevisionStatus.ACTIVE,
            canonical_ir_path=str(sidecar_path),
            canonical_ir_checksum=checksum,
            projection_hints_json=[self._projection_hint_dict(hint) for hint in projection_hints],
            metadata_json={
                **dict(canonical_ir.metadata),
                "root_node_id": root_node_id,
                "canonical_ir_node_count": len(nodes),
                "canonical_ir_relation_count": len(relations),
                "projection_hint_count": len(projection_hints),
            },
        )
        artifact = DocumentParseRevisionArtifact(
            id=stable_id("parse-revision-artifact", revision.id, "canonical_ir_sidecar"),
            document_parse_revision_id=revision.id,
            artifact_type="canonical_ir_sidecar",
            storage_path=str(sidecar_path),
            content_type="application/json",
            checksum=checksum,
            status=ArtifactStatus.ACTIVE,
            metadata_json={
                "schema_version": 1,
                "revision_id": revision.id,
                "root_node_id": root_node_id,
            },
        )
        return ParseIrBuildResult(
            parse_revision=revision,
            parse_revision_artifact=artifact,
            canonical_ir=canonical_ir,
            parsed_document=annotated_document,
        )

    def _sidecar_path(self, document_id: str, revision_version: int) -> Path:
        return self.output_root / document_id / f"v{revision_version}" / "canonical-ir.json"

    def _projection_hint_dict(self, hint: ParseProjectionHint) -> dict[str, Any]:
        return {
            "source_ref": hint.source_ref,
            "canonical_node_id": hint.canonical_node_id,
            "target_kind": hint.target_kind,
            "target_id": hint.target_id,
            "role": hint.role,
            "confidence": hint.confidence,
            "metadata": dict(hint.metadata),
        }
