from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ActorType, RelationType, TargetSegmentStatus
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.translation import AlignmentEdge
from book_agent.infra.repositories.ops import OpsRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RealignArtifacts:
    packet_ids: list[str]
    recreated_edge_ids: list[str]


class RealignService:
    def __init__(self, ops_repository: OpsRepository):
        self.ops_repository = ops_repository

    def execute(self, packet_ids: list[str]) -> RealignArtifacts:
        recreated_edge_ids: list[str] = []
        realigned_packet_ids: list[str] = []

        for packet_id in packet_ids:
            bundle = self.ops_repository.get_packet_bundle(packet_id)
            latest_run = self._latest_run(bundle.translation_runs)
            if latest_run is None:
                continue

            target_segments = sorted(
                [
                    segment
                    for segment in bundle.target_segments
                    if segment.translation_run_id == latest_run.id
                    and segment.final_status != TargetSegmentStatus.SUPERSEDED
                ],
                key=lambda item: item.ordinal,
            )
            if not target_segments:
                continue

            alignment_edges = self._rebuild_alignment_edges(latest_run.output_json or {}, target_segments)
            if not alignment_edges:
                continue

            self.ops_repository.replace_alignment_edges(
                [segment.id for segment in target_segments],
                alignment_edges,
            )
            self.ops_repository.session.merge(
                AuditEvent(
                    id=stable_id("audit", "packet", packet_id, "packet.realigned", latest_run.id),
                    object_type="packet",
                    object_id=packet_id,
                    action="packet.realigned",
                    actor_type=ActorType.SYSTEM,
                    actor_id="realign-service",
                    payload_json={
                        "translation_run_id": latest_run.id,
                        "recreated_edge_count": len(alignment_edges),
                    },
                    created_at=_utcnow(),
                )
            )
            realigned_packet_ids.append(packet_id)
            recreated_edge_ids.extend(edge.id for edge in alignment_edges)

        return RealignArtifacts(
            packet_ids=realigned_packet_ids,
            recreated_edge_ids=recreated_edge_ids,
        )

    def _latest_run(self, translation_runs: list[object]) -> object | None:
        if not translation_runs:
            return None
        return max(translation_runs, key=lambda run: run.attempt)

    def _rebuild_alignment_edges(
        self,
        output_json: dict[str, object],
        target_segments: list[object],
    ) -> list[AlignmentEdge]:
        output_segments = output_json.get("target_segments", [])
        if not isinstance(output_segments, list) or len(output_segments) != len(target_segments):
            return []

        temp_to_target_id = {
            segment_payload.get("temp_id"): target_segment.id
            for segment_payload, target_segment in zip(output_segments, target_segments)
            if isinstance(segment_payload, dict) and segment_payload.get("temp_id")
        }
        if not temp_to_target_id:
            return []

        alignment_payload = output_json.get("alignment_suggestions", [])
        if isinstance(alignment_payload, list) and alignment_payload:
            return self._edges_from_alignment_suggestions(alignment_payload, temp_to_target_id)
        return self._edges_from_target_segments(output_segments, temp_to_target_id)

    def _edges_from_alignment_suggestions(
        self,
        alignment_payload: list[object],
        temp_to_target_id: dict[str, str],
    ) -> list[AlignmentEdge]:
        now = _utcnow()
        edges: list[AlignmentEdge] = []
        for suggestion in alignment_payload:
            if not isinstance(suggestion, dict):
                continue
            try:
                relation_type = RelationType(str(suggestion.get("relation_type", RelationType.ONE_TO_ONE.value)))
            except ValueError:
                relation_type = RelationType.ONE_TO_ONE
            target_ids = [
                temp_to_target_id[temp_id]
                for temp_id in suggestion.get("target_temp_ids", [])
                if temp_id in temp_to_target_id
            ]
            for sentence_id in suggestion.get("source_sentence_ids", []):
                for target_id in target_ids:
                    edges.append(
                        AlignmentEdge(
                            id=stable_id("alignment-edge", sentence_id, target_id),
                            sentence_id=sentence_id,
                            target_segment_id=target_id,
                            relation_type=relation_type,
                            confidence=suggestion.get("confidence"),
                            created_by=ActorType.SYSTEM,
                            created_at=now,
                        )
                    )
        return edges

    def _edges_from_target_segments(
        self,
        output_segments: list[object],
        temp_to_target_id: dict[str, str],
    ) -> list[AlignmentEdge]:
        now = _utcnow()
        edges: list[AlignmentEdge] = []
        for segment_payload in output_segments:
            if not isinstance(segment_payload, dict):
                continue
            temp_id = segment_payload.get("temp_id")
            if temp_id not in temp_to_target_id:
                continue
            sentence_ids = segment_payload.get("source_sentence_ids", [])
            if not sentence_ids:
                continue
            relation_type = RelationType.ONE_TO_ONE if len(sentence_ids) == 1 else RelationType.MANY_TO_ONE
            target_id = temp_to_target_id[temp_id]
            for sentence_id in sentence_ids:
                edges.append(
                    AlignmentEdge(
                        id=stable_id("alignment-edge", sentence_id, target_id),
                        sentence_id=sentence_id,
                        target_segment_id=target_id,
                        relation_type=relation_type,
                        confidence=segment_payload.get("confidence"),
                        created_by=ActorType.SYSTEM,
                        created_at=now,
                    )
                )
        return edges
