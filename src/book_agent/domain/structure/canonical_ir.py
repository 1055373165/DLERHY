from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class CanonicalIRNode:
    node_id: str
    node_type: str
    parent_id: str | None = None
    source_ref: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CanonicalIRRelation:
    relation_id: str
    relation_type: str
    source_node_id: str
    target_node_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ParseProjectionHint:
    source_ref: str
    canonical_node_id: str
    target_kind: str
    target_id: str
    role: str = "primary"
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class PageExtractionPlan:
    page_number: int
    source_ref: str
    intent: str
    risk_reasons: list[str] = field(default_factory=list)
    layout_risk: str | None = None
    recovery_lane: str | None = None
    page_family: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CanonicalDocumentIR:
    schema_version: int
    document_id: str
    revision_id: str
    root_node_id: str
    source_type: str
    nodes: list[CanonicalIRNode]
    relations: list[CanonicalIRRelation]
    projection_hints: list[ParseProjectionHint]
    page_plans: list[PageExtractionPlan] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "root_node_id": self.root_node_id,
            "source_type": self.source_type,
            "nodes": [asdict(node) for node in self.nodes],
            "relations": [asdict(relation) for relation in self.relations],
            "projection_hints": [asdict(hint) for hint in self.projection_hints],
            "page_plans": [asdict(plan) for plan in self.page_plans],
            "metadata": dict(self.metadata),
        }
