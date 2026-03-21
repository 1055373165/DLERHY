from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    BlockType,
    ChapterStatus,
    Detector,
    IssueStatus,
    JobScopeType,
    LockLevel,
    RootCauseLayer,
    TargetSegmentStatus,
    SentenceStatus,
    Severity,
)
from book_agent.domain.models.review import ChapterQualitySummary as ChapterQualitySummaryRecord, IssueAction, ReviewIssue
from book_agent.domain.structure.artifact_grouping import normalize_artifact_role, resolve_artifact_group_context_ids
from book_agent.infra.repositories.review import ChapterReviewBundle, ReviewRepository
from book_agent.orchestrator.rerun import RerunPlan, build_rerun_plan
from book_agent.orchestrator.rule_engine import IssueRoutingContext, resolve_action
from book_agent.services.style_drift import STYLE_DRIFT_RULES
from book_agent.services.term_normalization import normalize_term_rendering


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_review_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _severity_rank(value: Severity | None) -> int:
    order = {
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }
    return order.get(value, 0)

_FRAGMENTARY_PDF_OMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,24}[.?!:;]$")
_LOCKED_TERM_PREFIX_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
}
_REFERENCE_LIKE_CHAPTER_TITLES = {"reference", "references", "glossary", "bibliography", "index"}
_LOCKED_TERM_LEADING_MODIFIERS = {"大", "小", "超", "微", "强", "弱", "多", "单"}


@dataclass(slots=True)
class ChapterQualitySummary:
    coverage_ok: bool
    alignment_ok: bool
    term_ok: bool
    format_ok: bool
    blocking_issue_count: int
    low_confidence_count: int
    format_pollution_count: int


@dataclass(slots=True)
class ReviewArtifacts:
    issues: list[ReviewIssue]
    actions: list[IssueAction]
    rerun_plans: list[RerunPlan]
    summary: ChapterQualitySummary
    resolved_issue_ids: list[str]


@dataclass(slots=True)
class ReviewAlignmentState:
    active_target_map: dict[str, object]
    active_alignments_by_sentence: dict[str, list[str]]
    latest_segments_by_packet: dict[str, list[object]]
    recoverable_sentence_ids_by_packet: dict[str, set[str]]
    recoverable_target_sentence_ids_by_packet: dict[str, dict[str, list[str]]]


class ReviewService:
    def __init__(self, repository: ReviewRepository):
        self.repository = repository

    def review_chapter(self, chapter_id: str) -> ReviewArtifacts:
        bundle = self.repository.load_chapter_bundle(chapter_id)
        artifacts = self._build_review_artifacts(bundle)
        resolved = self.repository.resolve_missing_issues(
            chapter_id,
            {issue.id for issue in artifacts.issues},
            resolution_note="Resolved by latest QA pass.",
        )
        structure_severity = self._max_issue_severity(artifacts.issues, RootCauseLayer.STRUCTURE)
        if structure_severity is not None and _severity_rank(structure_severity) > _severity_rank(bundle.chapter.risk_level):
            bundle.chapter.risk_level = structure_severity
        if artifacts.summary.blocking_issue_count > 0:
            bundle.chapter.status = ChapterStatus.REVIEW_REQUIRED
        else:
            bundle.chapter.status = ChapterStatus.QA_CHECKED
        artifacts.resolved_issue_ids.extend(issue.id for issue in resolved)
        persisted_summary = self._build_persisted_summary_record(bundle, artifacts, len(resolved))
        self.repository.save_review_artifacts(
            artifacts.issues,
            artifacts.actions,
            bundle.chapter,
            persisted_summary,
        )
        self.repository.session.flush()
        return artifacts

    def _should_suppress_fragmentary_pdf_omission(self, sentence, block) -> bool:
        if block is None:
            return False
        source_path = str((sentence.source_span_json or {}).get("source_path") or block.source_anchor or "")
        if not source_path.startswith("pdf://"):
            return False
        text = _normalize_review_text(sentence.source_text)
        if not _FRAGMENTARY_PDF_OMISSION_PATTERN.fullmatch(text):
            return False
        if sentence.ordinal_in_block != 1:
            return False
        block_text = _normalize_review_text(block.source_text)
        if len(block_text) <= len(text) + 1 or not block_text.startswith(text):
            return False
        parse_confidence = float(block.parse_confidence or 0.0)
        return parse_confidence <= 0.8

    def _max_issue_severity(
        self,
        issues: list[ReviewIssue],
        root_cause_layer: RootCauseLayer,
    ) -> Severity | None:
        matching = [issue.severity for issue in issues if issue.root_cause_layer == root_cause_layer]
        if not matching:
            return None
        return max(matching, key=_severity_rank)

    def _build_review_artifacts(self, bundle: ChapterReviewBundle) -> ReviewArtifacts:
        now = _utcnow()
        alignment_state = self._build_alignment_state(bundle)
        sentence_to_packet = self._sentence_to_packet_map(bundle)
        block_map = {block.id: block for block in bundle.blocks}
        handled_missing_alignment_ids: set[str] = set()
        alignment_issues, handled_missing_alignment_ids = self._alignment_failure_issues(
            bundle,
            alignment_state,
            sentence_to_packet,
            now,
        )

        issues: list[ReviewIssue] = []
        issues.extend(alignment_issues)
        issues.extend(self._pdf_structure_issues(bundle, now))
        for sentence in bundle.sentences:
            if not sentence.translatable or sentence.sentence_status == SentenceStatus.BLOCKED:
                continue
            packet_id = sentence_to_packet.get(sentence.id)
            if sentence.id not in alignment_state.active_alignments_by_sentence:
                if sentence.id in handled_missing_alignment_ids:
                    continue
                if self._should_suppress_fragmentary_pdf_omission(sentence, block_map.get(sentence.block_id)):
                    continue
                issues.append(
                    self._make_issue(
                        now=now,
                        chapter_id=bundle.chapter.id,
                        document_id=bundle.chapter.document_id,
                        sentence_id=sentence.id,
                        packet_id=packet_id,
                        issue_type="OMISSION",
                        root_cause_layer=RootCauseLayer.ALIGNMENT,
                        severity=Severity.HIGH,
                        blocking=True,
                        evidence={"reason": "no_alignment_edge", "source_text": sentence.source_text},
                    )
                )
                continue
            if sentence.sentence_status == SentenceStatus.REVIEW_REQUIRED:
                issues.append(
                    self._make_issue(
                        now=now,
                        chapter_id=bundle.chapter.id,
                        document_id=bundle.chapter.document_id,
                        sentence_id=sentence.id,
                        packet_id=packet_id,
                        issue_type="LOW_CONFIDENCE",
                        root_cause_layer=RootCauseLayer.TRANSLATION,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        evidence={
                            "reason": "translation_marked_review_required",
                            "source_text": sentence.source_text,
                        },
                    )
                )

            aligned_text = self._aligned_text_for_sentence(
                sentence.id,
                alignment_state.active_alignments_by_sentence,
                alignment_state.active_target_map,
            )
            if self._has_format_pollution(sentence, aligned_text):
                issues.append(
                    self._make_issue(
                        now=now,
                        chapter_id=bundle.chapter.id,
                        document_id=bundle.chapter.document_id,
                        sentence_id=sentence.id,
                        packet_id=packet_id,
                        issue_type="FORMAT_POLLUTION",
                        root_cause_layer=RootCauseLayer.TRANSLATION,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        evidence={
                            "actual_target_text": aligned_text,
                            "block_type": str(sentence.source_span_json.get("block_type", "")),
                        },
                        )
                    )

        issues.extend(self._packet_context_failure_issues(bundle, now))
        chapter_context_issue = self._chapter_context_failure_issue(bundle, now)
        if chapter_context_issue is not None:
            issues.append(chapter_context_issue)
        issues.extend(self._unlocked_key_concept_issues(bundle, now))
        issues.extend(self._stale_chapter_brief_issues(bundle, now))
        issues.extend(self._style_drift_issues(bundle, alignment_state, sentence_to_packet, now))
        issues.extend(self._duplication_issues(bundle, alignment_state, now))

        active_locked_terms = [
            term for term in bundle.term_entries if term.lock_level == LockLevel.LOCKED and term.status.value == "active"
        ]
        for term in active_locked_terms:
            expected_target_term = normalize_term_rendering(term.source_term, term.target_term)
            for sentence in bundle.sentences:
                if not sentence.translatable:
                    continue
                if term.source_term.lower() not in (sentence.normalized_text or sentence.source_text).lower():
                    continue
                aligned_text = self._aligned_text_for_sentence(
                    sentence.id,
                    alignment_state.active_alignments_by_sentence,
                    alignment_state.active_target_map,
                )
                if self._should_skip_locked_term_conflict(
                    bundle=bundle,
                    sentence=sentence,
                    source_term=term.source_term,
                    expected_target_term=expected_target_term,
                    aligned_text=aligned_text,
                ):
                    continue
                if expected_target_term not in aligned_text:
                    issues.append(
                        self._make_issue(
                            now=now,
                            chapter_id=bundle.chapter.id,
                            document_id=bundle.chapter.document_id,
                            sentence_id=sentence.id,
                            packet_id=self._find_packet_for_sentence(bundle, sentence.id),
                            issue_type="TERM_CONFLICT",
                            root_cause_layer=RootCauseLayer.MEMORY,
                            severity=Severity.HIGH,
                            blocking=True,
                            evidence={
                                "source_term": term.source_term,
                                "source_terms": [term.source_term],
                                "expected_target_term": expected_target_term,
                                "actual_target_text": aligned_text,
                            },
                            unique_key=self._term_conflict_unique_key(expected_target_term, term.source_term),
                        )
                    )

        issues = self._dedupe_issues(issues)

        actions: list[IssueAction] = []
        rerun_plans: list[RerunPlan] = []
        for issue in issues:
            action = self._build_action(issue)
            actions.append(action)
            rerun_plans.append(build_rerun_plan(issue, action))

        summary = ChapterQualitySummary(
            coverage_ok=not any(issue.issue_type == "OMISSION" for issue in issues),
            alignment_ok=not any(issue.root_cause_layer == RootCauseLayer.ALIGNMENT for issue in issues),
            term_ok=not any(issue.issue_type == "TERM_CONFLICT" for issue in issues),
            format_ok=not any(issue.issue_type == "FORMAT_POLLUTION" for issue in issues),
            blocking_issue_count=sum(1 for issue in issues if issue.blocking),
            low_confidence_count=sum(1 for issue in issues if issue.issue_type == "LOW_CONFIDENCE"),
            format_pollution_count=sum(1 for issue in issues if issue.issue_type == "FORMAT_POLLUTION"),
        )
        return ReviewArtifacts(
            issues=issues,
            actions=actions,
            rerun_plans=rerun_plans,
            summary=summary,
            resolved_issue_ids=[],
        )

    def _term_conflict_unique_key(self, expected_target_term: str | None, source_term: str | None) -> str:
        normalized_expected = _normalize_review_text(expected_target_term)
        if normalized_expected:
            return normalized_expected.casefold()
        normalized_source = _normalize_review_text(source_term)
        return normalized_source.casefold() or "term-conflict"

    def _dedupe_issues(self, issues: list[ReviewIssue]) -> list[ReviewIssue]:
        deduped: dict[str, ReviewIssue] = {}
        for issue in issues:
            existing = deduped.get(issue.id)
            if existing is None:
                deduped[issue.id] = issue
                continue
            existing.severity = max(existing.severity, issue.severity, key=_severity_rank)
            existing.blocking = existing.blocking or issue.blocking
            existing.confidence = max(float(existing.confidence or 0.0), float(issue.confidence or 0.0))
            existing.updated_at = max(existing.updated_at, issue.updated_at)
            existing.evidence_json = self._merge_issue_evidence(existing, issue)
        return list(deduped.values())

    def _merge_issue_evidence(self, existing: ReviewIssue, incoming: ReviewIssue) -> dict[str, Any]:
        merged = dict(existing.evidence_json or {})
        incoming_evidence = incoming.evidence_json or {}
        for key, value in incoming_evidence.items():
            if key == "source_terms" and isinstance(value, list):
                existing_terms = merged.get("source_terms", [])
                source_terms = {
                    str(item).strip()
                    for item in [*existing_terms, *value]
                    if str(item).strip()
                }
                if source_terms:
                    merged["source_terms"] = sorted(source_terms)
                continue
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        if existing.issue_type == "TERM_CONFLICT":
            source_terms = {
                str(item).strip()
                for item in [
                    merged.get("source_term"),
                    incoming_evidence.get("source_term"),
                    *(merged.get("source_terms", []) or []),
                    *(incoming_evidence.get("source_terms", []) or []),
                ]
                if str(item).strip()
            }
            if source_terms:
                merged["source_terms"] = sorted(source_terms)
                merged["source_term"] = sorted(source_terms)[0]
        return merged

    def _unlocked_key_concept_issues(
        self,
        bundle: ChapterReviewBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        snapshot = bundle.chapter_translation_memory
        if snapshot is None:
            return []
        active_concepts = snapshot.content_json.get("active_concepts", [])
        if not isinstance(active_concepts, list):
            return []

        locked_term_sources = {
            term.source_term.casefold()
            for term in bundle.term_entries
            if term.status.value == "active"
            and term.lock_level == LockLevel.LOCKED
            and term.source_term
        }

        issues: list[ReviewIssue] = []
        for concept in active_concepts:
            if not isinstance(concept, dict):
                continue
            source_term = str(concept.get("source_term") or "").strip()
            if not source_term:
                continue
            if source_term.casefold() in locked_term_sources:
                continue
            if concept.get("canonical_zh"):
                continue
            times_seen = int(concept.get("times_seen") or 0)
            mention_count = int(concept.get("mention_count") or times_seen or 0)
            if mention_count < 2:
                continue
            packet_ids_seen = [
                str(packet_id).strip()
                for packet_id in list(concept.get("packet_ids_seen") or [])
                if str(packet_id).strip()
            ]
            packet_id = packet_ids_seen[0] if len(packet_ids_seen) == 1 else None
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=None,
                    packet_id=packet_id,
                    issue_type="UNLOCKED_KEY_CONCEPT",
                    root_cause_layer=RootCauseLayer.MEMORY,
                    severity=Severity.MEDIUM,
                    blocking=False,
                    evidence={
                        "source_term": source_term,
                        "times_seen": times_seen,
                        "mention_count": mention_count,
                        "packet_ids_seen": packet_ids_seen,
                        "chapter_memory_snapshot_version": snapshot.version,
                    },
                    unique_key=source_term.casefold(),
                )
            )
        return issues

    def _style_drift_issues(
        self,
        bundle: ChapterReviewBundle,
        alignment_state: ReviewAlignmentState,
        sentence_to_packet: dict[str, str],
        now: datetime,
    ) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        chapter_memory_snapshot_version = (
            bundle.chapter_translation_memory.version if bundle.chapter_translation_memory is not None else None
        )
        for sentence in bundle.sentences:
            if not sentence.translatable or sentence.sentence_status == SentenceStatus.BLOCKED:
                continue
            aligned_text = self._aligned_text_for_sentence(
                sentence.id,
                alignment_state.active_alignments_by_sentence,
                alignment_state.active_target_map,
            )
            if not aligned_text.strip():
                continue
            for rule in STYLE_DRIFT_RULES:
                if not rule.source_pattern.search(sentence.source_text or ""):
                    continue
                target_match = rule.target_pattern.search(aligned_text)
                if target_match is None:
                    continue
                issues.append(
                    self._make_issue(
                        now=now,
                        chapter_id=bundle.chapter.id,
                        document_id=bundle.chapter.document_id,
                        sentence_id=sentence.id,
                        packet_id=sentence_to_packet.get(sentence.id),
                        issue_type="STYLE_DRIFT",
                        root_cause_layer=RootCauseLayer.PACKET,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        evidence={
                            "style_subtype": "literalism",
                            "style_rule": rule.pattern_id,
                            "source_text": sentence.source_text,
                            "actual_target_text": aligned_text,
                            "matched_target_excerpt": target_match.group(0),
                            "preferred_hint": rule.preferred_hint,
                            "prompt_guidance": rule.prompt_guidance,
                            "chapter_memory_snapshot_version": chapter_memory_snapshot_version,
                            "message": rule.message,
                        },
                        unique_key=f"{sentence.id}:{rule.pattern_id}",
                    )
                )
        return issues

    def _stale_chapter_brief_issues(
        self,
        bundle: ChapterReviewBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        chapter_brief = bundle.chapter_brief
        snapshot = bundle.chapter_translation_memory
        if chapter_brief is None or snapshot is None:
            return []

        brief_summary = _normalize_review_text(str(chapter_brief.content_json.get("summary") or ""))
        if not brief_summary:
            return []

        recent_translations = snapshot.content_json.get("recent_accepted_translations", [])
        if not isinstance(recent_translations, list) or len(recent_translations) < 3:
            return []

        active_concepts = snapshot.content_json.get("active_concepts", [])
        if not isinstance(active_concepts, list):
            return []

        locked_term_sources = {
            term.source_term.casefold()
            for term in bundle.term_entries
            if term.status.value == "active"
            and term.lock_level == LockLevel.LOCKED
            and term.source_term
        }

        missing_concepts: list[str] = []
        missing_concept_packet_ids: set[str] = set()
        brief_lower = brief_summary.casefold()
        for concept in active_concepts:
            if not isinstance(concept, dict):
                continue
            source_term = str(concept.get("source_term") or "").strip()
            if not source_term:
                continue
            if int(concept.get("times_seen") or 0) < 2:
                continue
            if concept.get("canonical_zh") or source_term.casefold() in locked_term_sources:
                continue
            if source_term.casefold() in brief_lower:
                continue
            missing_concepts.append(source_term)
            for packet_id in list(concept.get("packet_ids_seen") or []):
                normalized_packet_id = str(packet_id).strip()
                if normalized_packet_id:
                    missing_concept_packet_ids.add(normalized_packet_id)

        if not missing_concepts:
            return []

        representative_sentence_id = next(
            (sentence.id for sentence in bundle.sentences if sentence.translatable),
            bundle.sentences[0].id if bundle.sentences else None,
        )
        return [
            self._make_issue(
                now=now,
                chapter_id=bundle.chapter.id,
                document_id=bundle.chapter.document_id,
                sentence_id=representative_sentence_id,
                packet_id=None,
                issue_type="STALE_CHAPTER_BRIEF",
                root_cause_layer=RootCauseLayer.MEMORY,
                severity=Severity.LOW,
                blocking=False,
                evidence={
                    "missing_concepts": missing_concepts,
                    "packet_ids_seen": sorted(missing_concept_packet_ids),
                    "chapter_brief_summary": brief_summary,
                    "chapter_brief_version": chapter_brief.version,
                    "chapter_memory_snapshot_version": snapshot.version,
                },
                unique_key=":".join(sorted(term.casefold() for term in missing_concepts)),
            )
        ]

    def _build_persisted_summary_record(
        self,
        bundle: ChapterReviewBundle,
        artifacts: ReviewArtifacts,
        resolved_issue_count: int,
    ) -> ChapterQualitySummaryRecord:
        return self.repository.upsert_chapter_quality_summary(
            document_id=bundle.chapter.document_id,
            chapter_id=bundle.chapter.id,
            issue_count=len(artifacts.issues),
            action_count=len(artifacts.actions),
            resolved_issue_count=resolved_issue_count,
            coverage_ok=artifacts.summary.coverage_ok,
            alignment_ok=artifacts.summary.alignment_ok,
            term_ok=artifacts.summary.term_ok,
            format_ok=artifacts.summary.format_ok,
            blocking_issue_count=artifacts.summary.blocking_issue_count,
            low_confidence_count=artifacts.summary.low_confidence_count,
            format_pollution_count=artifacts.summary.format_pollution_count,
        )

    def _aligned_text_for_sentence(
        self,
        sentence_id: str,
        alignments_by_sentence: dict[str, list[str]],
        target_map: dict[str, object],
    ) -> str:
        return " ".join(
            target_map[target_id].text_zh
            for target_id in alignments_by_sentence.get(sentence_id, [])
            if target_id in target_map
        )

    def _has_format_pollution(self, sentence, aligned_text: str) -> bool:
        if not aligned_text.strip():
            return False
        block_type = str(sentence.source_span_json.get("block_type", ""))
        if block_type in {"code", "table"}:
            return False
        if re.search(r"```", aligned_text) or re.search(r"<!(?:DOCTYPE|--)", aligned_text):
            return True
        target_tags = self._extract_markup_like_tokens(aligned_text)
        if not target_tags:
            return False
        source_tags = self._extract_markup_like_tokens(sentence.source_text)
        return any(tag not in source_tags for tag in target_tags)

    def _find_packet_for_sentence(self, bundle: ChapterReviewBundle, sentence_id: str) -> str | None:
        for packet in bundle.packets:
            sentence_ids = self._packet_current_sentence_ids(packet)
            if sentence_id in sentence_ids:
                return packet.id
        return None

    def _should_skip_locked_term_conflict(
        self,
        *,
        bundle: ChapterReviewBundle,
        sentence,
        source_term: str | None,
        expected_target_term: str | None,
        aligned_text: str,
    ) -> bool:
        if not aligned_text.strip():
            return False
        if not source_term or not expected_target_term:
            return False
        has_modifier_variant = self._source_term_has_modifier_variant(sentence.source_text or "", source_term)
        if not has_modifier_variant:
            return False
        if self._is_reference_like_chapter(bundle.chapter.title_src):
            return True
        if self._aligned_text_contains_locked_term_variant(aligned_text, expected_target_term):
            return True
        return False

    def _source_term_has_modifier_variant(self, source_text: str, source_term: str) -> bool:
        pattern = re.compile(rf"(?i)\b{re.escape(source_term)}\b")
        for match in pattern.finditer(source_text):
            prefix = source_text[: match.start()].rstrip()
            suffix = source_text[match.end() :].lstrip()
            previous_token = re.search(r"([A-Za-z][A-Za-z0-9.+_-]*)\s*$", prefix) if prefix else None
            next_token = re.search(r"^([A-Za-z][A-Za-z0-9.+_-]*)", suffix) if suffix else None
            if self._modifier_token_is_structural(previous_token.group(1) if previous_token else None):
                return True
            if self._modifier_token_is_structural(next_token.group(1) if next_token else None):
                return True
        return False

    def _modifier_token_is_structural(self, token: str | None) -> bool:
        if not token:
            return False
        normalized = token.strip()
        if not normalized:
            return False
        if normalized.lower() in _LOCKED_TERM_PREFIX_STOPWORDS:
            return False
        if any(char in normalized for char in ".-_+/"):
            return True
        return normalized[0].isupper()

    def _aligned_text_contains_locked_term_variant(self, aligned_text: str, expected_target_term: str) -> bool:
        normalized_aligned = _normalize_review_text(aligned_text)
        candidates = self._locked_term_variant_candidates(expected_target_term)
        if not normalized_aligned or not candidates:
            return False
        return any(candidate in normalized_aligned for candidate in candidates)

    def _locked_term_variant_candidates(self, expected_target_term: str | None) -> set[str]:
        normalized_expected = _normalize_review_text(expected_target_term)
        if not normalized_expected:
            return set()
        candidates = {normalized_expected}
        trailing_cjk = re.search(r"([\u4e00-\u9fff]{2,})$", normalized_expected)
        if trailing_cjk is not None:
            candidates.add(trailing_cjk.group(1))
        if normalized_expected[0] in _LOCKED_TERM_LEADING_MODIFIERS and len(normalized_expected) > 2:
            candidates.add(normalized_expected[1:])
        return {candidate for candidate in candidates if candidate}

    def _is_reference_like_chapter(self, title_src: str | None) -> bool:
        normalized_title = _normalize_review_text(title_src).lower()
        return normalized_title in _REFERENCE_LIKE_CHAPTER_TITLES

    def _packet_context_failure_issues(
        self,
        bundle: ChapterReviewBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        for packet in bundle.packets:
            open_questions = packet.packet_json.get("open_questions", [])
            if not open_questions:
                continue
            if self._should_skip_packet_context_failure(bundle, packet.id, open_questions):
                continue
            sentence_ids = self._packet_current_sentence_ids(packet)
            if not sentence_ids:
                continue
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=sentence_ids[0],
                    packet_id=packet.id,
                    issue_type="CONTEXT_FAILURE",
                    root_cause_layer=RootCauseLayer.PACKET,
                    severity=Severity.HIGH,
                    blocking=True,
                    evidence={
                        "open_questions": " | ".join(open_questions),
                        "packet_id": packet.id,
                    },
                    unique_key=packet.id,
                )
            )
        return issues

    def _should_skip_packet_context_failure(
        self,
        bundle: ChapterReviewBundle,
        packet_id: str,
        open_questions: list[str],
    ) -> bool:
        if set(open_questions) != {"missing_chapter_title"}:
            return False
        return self._is_image_only_untitled_chapter(bundle)

    def _pdf_structure_issues(
        self,
        bundle: ChapterReviewBundle,
        now: datetime,
    ) -> list[ReviewIssue]:
        pdf_blocks = [
            block
            for block in bundle.blocks
            if (block.source_span_json or {}).get("pdf_block_role")
        ]
        if not pdf_blocks:
            return []

        issues: list[ReviewIssue] = []
        sentences_by_block: dict[str, list[object]] = {}
        for sentence in bundle.sentences:
            sentences_by_block.setdefault(sentence.block_id, []).append(sentence)

        representative_sentence_id = next(
            (sentence.id for sentence in bundle.sentences if sentence.translatable),
            bundle.sentences[0].id if bundle.sentences else None,
        )
        chapter_metadata = bundle.chapter.metadata_json or {}
        layout_risk = str(chapter_metadata.get("pdf_layout_risk") or "low")
        parse_confidence = chapter_metadata.get("parse_confidence")
        suspicious_pages = list(chapter_metadata.get("suspicious_page_numbers") or [])
        structure_flags = list(chapter_metadata.get("structure_flags") or [])
        chapter_page_evidence = self._chapter_pdf_page_evidence(bundle)
        local_suspicious_pages = [
            int(page["page_number"])
            for page in chapter_page_evidence
            if isinstance(page, dict)
            and page.get("layout_suspect") is True
            and isinstance(page.get("page_number"), int)
        ]
        local_layout_risk_pages = [
            int(page["page_number"])
            for page in chapter_page_evidence
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and str(page.get("page_layout_risk") or "low") != "low"
        ]
        local_high_layout_risk_pages = [
            int(page["page_number"])
            for page in chapter_page_evidence
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and str(page.get("page_layout_risk") or "low") == "high"
        ]
        review_policy = self._pdf_layout_review_policy(
            bundle,
            layout_risk,
            parse_confidence,
            suspicious_pages,
            local_suspicious_pages,
            local_layout_risk_pages,
            local_high_layout_risk_pages,
            chapter_page_evidence,
        )

        if layout_risk in {"medium", "high"} and bool(review_policy["emit_issue"]):
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=None,
                    issue_type="MISORDERING",
                    root_cause_layer=RootCauseLayer.STRUCTURE,
                    severity=review_policy["severity"],
                    blocking=review_policy["blocking"],
                    evidence={
                        "layout_risk": layout_risk,
                        "parse_confidence": parse_confidence,
                        "suspicious_page_numbers": local_suspicious_pages or suspicious_pages,
                        "page_layout_risk_pages": local_layout_risk_pages,
                        "high_layout_risk_pages": local_high_layout_risk_pages,
                        "page_layout_reasons_by_page": {
                            str(page["page_number"]): [
                                str(reason)
                                for reason in list(page.get("page_layout_reasons") or [])
                                if isinstance(reason, str)
                            ]
                            for page in chapter_page_evidence
                            if isinstance(page, dict)
                            and isinstance(page.get("page_number"), int)
                            and str(page.get("page_layout_risk") or "low") != "low"
                        },
                        "structure_flags": structure_flags,
                        "recovery_lane": review_policy["recovery_lane"],
                        "review_policy": review_policy["reason"],
                    },
                    unique_key=f"layout-risk:{layout_risk}",
                )
            )

        leaked_blocks = []
        for block in pdf_blocks:
            role = (block.source_span_json or {}).get("pdf_block_role")
            if role not in {"header", "footer"}:
                continue
            if any(sentence.translatable for sentence in sentences_by_block.get(block.id, [])):
                leaked_blocks.append(block)
        if leaked_blocks:
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=None,
                    issue_type="STRUCTURE_POLLUTION",
                    root_cause_layer=RootCauseLayer.STRUCTURE,
                    severity=Severity.HIGH,
                    blocking=True,
                    evidence={
                        "leaked_block_count": len(leaked_blocks),
                        "leaked_roles": sorted(
                            {
                                str((block.source_span_json or {}).get("pdf_block_role"))
                                for block in leaked_blocks
                            }
                        ),
                    },
                    unique_key="header-footer-leak",
                )
            )

        orphaned_footnotes = [
            block
            for block in pdf_blocks
            if (block.source_span_json or {}).get("pdf_block_role") == "footnote"
            and (block.source_span_json or {}).get("footnote_anchor_matched") is False
        ]
        if orphaned_footnotes:
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=None,
                    issue_type="FOOTNOTE_RECOVERY_REQUIRED",
                    root_cause_layer=RootCauseLayer.STRUCTURE,
                    severity=Severity.MEDIUM,
                    blocking=False,
                    evidence={
                        "orphaned_footnote_count": len(orphaned_footnotes),
                        "orphaned_footnote_labels": sorted(
                            {
                                str((block.source_span_json or {}).get("footnote_anchor_label") or "unknown")
                                for block in orphaned_footnotes
                            }
                        ),
                    },
                    unique_key="footnote-orphaning",
                )
            )

        artifact_group_context_ids = resolve_artifact_group_context_ids(
            pdf_blocks,
            academic_paper=review_policy["recovery_lane"] == "academic_paper",
        )
        captioned_artifact_blocks = [
            block
            for block in pdf_blocks
            if normalize_artifact_role(
                (block.source_span_json or {}).get("pdf_block_role"),
                block.block_type,
            ) in {"image", "table", "equation"}
            and (
                isinstance((block.source_span_json or {}).get("linked_caption_block_id"), str)
                or isinstance((block.source_span_json or {}).get("linked_caption_text"), str)
            )
        ]
        missing_group_context_blocks = [
            block
            for block in captioned_artifact_blocks
            if int((block.source_span_json or {}).get("source_page_start", 0) or 0) in local_high_layout_risk_pages
            and not artifact_group_context_ids.get(block.id)
        ]
        artifact_group_review_policy = self._pdf_artifact_group_review_policy(
            review_policy["recovery_lane"],
            local_high_layout_risk_pages,
            missing_group_context_blocks,
        )
        if missing_group_context_blocks and bool(artifact_group_review_policy["emit_issue"]):
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=None,
                    issue_type="ARTIFACT_GROUP_RECOVERY_REQUIRED",
                    root_cause_layer=RootCauseLayer.STRUCTURE,
                    severity=artifact_group_review_policy["severity"],
                    blocking=bool(artifact_group_review_policy["blocking"]),
                    evidence={
                        "captioned_artifact_count": len(captioned_artifact_blocks),
                        "grouped_artifact_count": len(artifact_group_context_ids),
                        "missing_group_context_count": len(missing_group_context_blocks),
                        "missing_group_context_block_ids": [block.id for block in missing_group_context_blocks],
                        "missing_group_context_page_numbers": sorted(
                            {
                                int((block.source_span_json or {})["source_page_start"])
                                for block in missing_group_context_blocks
                                if isinstance((block.source_span_json or {}).get("source_page_start"), int)
                            }
                        ),
                        "high_layout_risk_pages": local_high_layout_risk_pages,
                        "recovery_lane": artifact_group_review_policy["recovery_lane"],
                        "review_policy": artifact_group_review_policy["reason"],
                    },
                    unique_key="artifact-group-recovery",
                )
            )

        image_blocks = [
            block
            for block in pdf_blocks
            if block.block_type in {BlockType.IMAGE, BlockType.FIGURE}
            or (block.source_span_json or {}).get("pdf_block_role") in {"image", "figure"}
        ]
        linked_image_blocks = []
        uncaptioned_image_blocks = []
        for block in image_blocks:
            metadata = block.source_span_json or {}
            linked_caption_block_id = metadata.get("linked_caption_block_id")
            linked_caption_text = metadata.get("linked_caption_text")
            if linked_caption_block_id or (
                isinstance(linked_caption_text, str) and linked_caption_text.strip()
            ):
                linked_image_blocks.append(block)
            else:
                uncaptioned_image_blocks.append(block)

        image_caption_review_policy = self._pdf_image_caption_review_policy(
            bundle,
            pdf_blocks,
            linked_image_blocks,
            uncaptioned_image_blocks,
        )
        if uncaptioned_image_blocks and bool(image_caption_review_policy["emit_issue"]):
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=None,
                    issue_type="IMAGE_CAPTION_RECOVERY_REQUIRED",
                    root_cause_layer=RootCauseLayer.STRUCTURE,
                    severity=image_caption_review_policy["severity"],
                    blocking=bool(image_caption_review_policy["blocking"]),
                    evidence={
                        "image_count": len(image_blocks),
                        "caption_linked_count": len(linked_image_blocks),
                        "uncaptioned_image_count": len(uncaptioned_image_blocks),
                        "uncaptioned_block_ids": [block.id for block in uncaptioned_image_blocks],
                        "uncaptioned_page_numbers": sorted(
                            {
                                int((block.source_span_json or {})["source_page_start"])
                                for block in uncaptioned_image_blocks
                                if isinstance((block.source_span_json or {}).get("source_page_start"), int)
                            }
                        ),
                        "recovery_lane": image_caption_review_policy["recovery_lane"],
                        "review_policy": image_caption_review_policy["reason"],
                    },
                    unique_key="image-caption-recovery",
                )
            )

        return issues

    def _pdf_layout_review_policy(
        self,
        bundle: ChapterReviewBundle,
        layout_risk: str,
        parse_confidence: float | None,
        suspicious_pages: list[int],
        local_suspicious_pages: list[int],
        local_layout_risk_pages: list[int],
        local_high_layout_risk_pages: list[int],
        chapter_page_evidence: list[dict[str, Any]],
    ) -> dict[str, object]:
        default = {
            "severity": Severity.HIGH if layout_risk == "medium" else Severity.CRITICAL,
            "blocking": True,
            "emit_issue": True,
            "recovery_lane": None,
            "reason": "default_blocking_layout_risk",
        }
        if layout_risk != "medium":
            return default

        if not local_suspicious_pages and not local_layout_risk_pages:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": None,
                "reason": "no_local_layout_signals",
            }

        pdf_profile = (bundle.document.metadata_json or {}).get("pdf_profile")
        recovery_lane = None
        if isinstance(pdf_profile, dict) and pdf_profile.get("recovery_lane"):
            recovery_lane = str(pdf_profile["recovery_lane"])

        if recovery_lane == "outlined_book":
            if self._outlined_book_single_multi_column_is_advisory(
                parse_confidence=parse_confidence,
                local_suspicious_pages=local_suspicious_pages,
                local_layout_risk_pages=local_layout_risk_pages,
                local_high_layout_risk_pages=local_high_layout_risk_pages,
                chapter_page_evidence=chapter_page_evidence,
            ):
                return {
                    "severity": Severity.LOW,
                    "blocking": False,
                    "emit_issue": True,
                    "recovery_lane": recovery_lane,
                    "reason": "outlined_book_single_multi_column_advisory",
                }
            return default | {"recovery_lane": recovery_lane}

        if recovery_lane != "academic_paper":
            return default | {"recovery_lane": recovery_lane}

        if parse_confidence is None or float(parse_confidence) < 0.8:
            return default | {
                "recovery_lane": recovery_lane,
                "reason": "academic_paper_medium_confidence_too_low",
            }

        if local_suspicious_pages and self._academic_paper_suspicious_pages_structurally_anchored(chapter_page_evidence):
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": recovery_lane,
                "reason": "academic_paper_medium_structurally_anchored",
            }

        if local_high_layout_risk_pages and not local_suspicious_pages:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": True,
                "recovery_lane": recovery_lane,
                "reason": "academic_paper_local_page_layout_advisory",
            }

        if len(suspicious_pages) > 2:
            return {
                "severity": Severity.MEDIUM,
                "blocking": False,
                "emit_issue": True,
                "recovery_lane": recovery_lane,
                "reason": "academic_paper_medium_wide_layout_advisory",
            }

        return {
            "severity": Severity.MEDIUM,
            "blocking": False,
            "emit_issue": True,
            "recovery_lane": recovery_lane,
            "reason": "academic_paper_medium_layout_advisory",
        }

    def _outlined_book_single_multi_column_is_advisory(
        self,
        *,
        parse_confidence: float | None,
        local_suspicious_pages: list[int],
        local_layout_risk_pages: list[int],
        local_high_layout_risk_pages: list[int],
        chapter_page_evidence: list[dict[str, Any]],
    ) -> bool:
        if parse_confidence is None or float(parse_confidence) < 0.82:
            return False
        if local_high_layout_risk_pages:
            return False

        suspect_pages = sorted(
            {
                int(page_number)
                for page_number in [*local_suspicious_pages, *local_layout_risk_pages]
                if isinstance(page_number, int)
            }
        )
        if len(suspect_pages) != 1 or len(chapter_page_evidence) < 4:
            return False

        page_by_number = {
            int(page["page_number"]): page
            for page in chapter_page_evidence
            if isinstance(page, dict) and isinstance(page.get("page_number"), int)
        }
        suspect_page = page_by_number.get(suspect_pages[0])
        if suspect_page is None:
            return False

        reasons = {
            str(reason)
            for reason in list(suspect_page.get("page_layout_reasons") or [])
            if isinstance(reason, str)
        }
        if not reasons or not reasons.issubset({"multi_column"}):
            return False

        anchored_page_count = 0
        for page in chapter_page_evidence:
            if not isinstance(page, dict):
                continue
            recovery_flags = {
                str(flag)
                for flag in list(page.get("recovery_flags") or [])
                if isinstance(flag, str)
            }
            role_counts = page.get("role_counts")
            heading_count = 0
            caption_count = 0
            if isinstance(role_counts, dict):
                heading_count = int(role_counts.get("heading", 0) or 0)
                caption_count = int(role_counts.get("caption", 0) or 0)
            if "cross_page_repaired" in recovery_flags or heading_count > 0 or caption_count > 0:
                anchored_page_count += 1

        return anchored_page_count >= 3

    def _pdf_image_caption_review_policy(
        self,
        bundle: ChapterReviewBundle,
        pdf_blocks: list[object],
        linked_image_blocks: list[object],
        uncaptioned_image_blocks: list[object],
    ) -> dict[str, object]:
        pdf_profile = (bundle.document.metadata_json or {}).get("pdf_profile")
        recovery_lane = None
        if isinstance(pdf_profile, dict) and pdf_profile.get("recovery_lane"):
            recovery_lane = str(pdf_profile["recovery_lane"])

        if not uncaptioned_image_blocks:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": recovery_lane,
                "reason": "no_uncaptioned_images",
            }

        if recovery_lane == "academic_paper":
            return {
                "severity": Severity.MEDIUM,
                "blocking": False,
                "emit_issue": True,
                "recovery_lane": recovery_lane,
                "reason": "academic_paper_uncaptioned_images",
            }

        if linked_image_blocks:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": True,
                "recovery_lane": recovery_lane,
                "reason": "partial_caption_link_coverage",
            }

        chapter_has_caption_blocks = any(
            (block.source_span_json or {}).get("pdf_block_role") == "caption"
            for block in pdf_blocks
        )
        if chapter_has_caption_blocks:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": True,
                "recovery_lane": recovery_lane,
                "reason": "caption_blocks_present_but_images_unlinked",
            }

        return {
            "severity": Severity.LOW,
            "blocking": False,
            "emit_issue": False,
            "recovery_lane": recovery_lane,
            "reason": "no_caption_context",
        }

    def _pdf_artifact_group_review_policy(
        self,
        recovery_lane: str | None,
        local_high_layout_risk_pages: list[int],
        missing_group_context_blocks: list[object],
    ) -> dict[str, object]:
        if recovery_lane != "academic_paper":
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": recovery_lane,
                "reason": "non_academic_lane",
            }
        if not local_high_layout_risk_pages:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": recovery_lane,
                "reason": "no_local_high_layout_risk_pages",
            }
        if not missing_group_context_blocks:
            return {
                "severity": Severity.LOW,
                "blocking": False,
                "emit_issue": False,
                "recovery_lane": recovery_lane,
                "reason": "artifact_groups_already_recovered",
            }
        return {
            "severity": Severity.LOW,
            "blocking": False,
            "emit_issue": True,
            "recovery_lane": recovery_lane,
            "reason": "academic_paper_captioned_artifact_missing_group_context",
        }

    def _chapter_pdf_page_evidence(self, bundle: ChapterReviewBundle) -> list[dict[str, Any]]:
        metadata = bundle.document.metadata_json or {}
        page_evidence = metadata.get("pdf_page_evidence")
        if not isinstance(page_evidence, dict):
            return []
        pages = page_evidence.get("pdf_pages")
        if not isinstance(pages, list):
            return []

        chapter_metadata = bundle.chapter.metadata_json or {}
        page_start = chapter_metadata.get("source_page_start")
        page_end = chapter_metadata.get("source_page_end")
        if not isinstance(page_start, int) or not isinstance(page_end, int):
            return [page for page in pages if isinstance(page, dict)]

        return [
            page
            for page in pages
            if isinstance(page, dict)
            and isinstance(page.get("page_number"), int)
            and page_start <= int(page["page_number"]) <= page_end
        ]

    def _academic_paper_suspicious_pages_structurally_anchored(
        self,
        chapter_page_evidence: list[dict[str, Any]],
    ) -> bool:
        suspicious_pages = [
            page
            for page in chapter_page_evidence
            if isinstance(page, dict) and page.get("layout_suspect") is True
        ]
        if not suspicious_pages:
            return False

        recovered_heading_page_count = 0
        for page in chapter_page_evidence:
            if not isinstance(page, dict):
                continue
            recovery_flags = {
                str(flag)
                for flag in list(page.get("recovery_flags") or [])
                if isinstance(flag, str)
            }
            role_counts = page.get("role_counts")
            if "academic_section_heading_recovered" in recovery_flags:
                recovered_heading_page_count += 1
                continue
            if isinstance(role_counts, dict) and int(role_counts.get("heading", 0) or 0) > 0:
                recovered_heading_page_count += 1

        if recovered_heading_page_count < 3:
            return False

        for page in suspicious_pages:
            recovery_flags = {
                str(flag)
                for flag in list(page.get("recovery_flags") or [])
                if isinstance(flag, str)
            }
            if "academic_section_heading_recovered" in recovery_flags:
                continue

            role_counts = page.get("role_counts")
            if not isinstance(role_counts, dict):
                return False
            nonzero_roles = {
                str(role)
                for role, count in role_counts.items()
                if isinstance(role, str) and int(count or 0) > 0
            }
            if nonzero_roles and nonzero_roles.issubset({"caption", "heading"}):
                continue
            return False
        return True

    def _build_alignment_state(self, bundle: ChapterReviewBundle) -> ReviewAlignmentState:
        active_target_map = {
            segment.id: segment
            for segment in bundle.target_segments
            if segment.final_status != TargetSegmentStatus.SUPERSEDED
        }
        active_alignments_by_sentence: dict[str, list[str]] = {}
        for edge in bundle.alignment_edges:
            if edge.target_segment_id not in active_target_map:
                continue
            active_alignments_by_sentence.setdefault(edge.sentence_id, []).append(edge.target_segment_id)

        latest_runs_by_packet: dict[str, object] = {}
        for run in bundle.translation_runs:
            current = latest_runs_by_packet.get(run.packet_id)
            if current is None or run.attempt > current.attempt:
                latest_runs_by_packet[run.packet_id] = run

        latest_segments_by_packet: dict[str, list[object]] = {}
        for packet in bundle.packets:
            latest_run = latest_runs_by_packet.get(packet.id)
            if latest_run is None:
                continue
            latest_segments_by_packet[packet.id] = sorted(
                [
                    segment
                    for segment in bundle.target_segments
                    if segment.translation_run_id == latest_run.id
                    and segment.final_status != TargetSegmentStatus.SUPERSEDED
                ],
                key=lambda item: item.ordinal,
            )

        recoverable_sentence_ids_by_packet: dict[str, set[str]] = {}
        recoverable_target_sentence_ids_by_packet: dict[str, dict[str, list[str]]] = {}
        for packet in bundle.packets:
            latest_run = latest_runs_by_packet.get(packet.id)
            latest_segments = latest_segments_by_packet.get(packet.id, [])
            if latest_run is None or not latest_segments:
                continue
            recoverable_sentence_ids, recoverable_target_sentence_ids = self._recoverable_alignment_metadata(
                latest_run,
                latest_segments,
            )
            if recoverable_sentence_ids:
                recoverable_sentence_ids_by_packet[packet.id] = recoverable_sentence_ids
            if recoverable_target_sentence_ids:
                recoverable_target_sentence_ids_by_packet[packet.id] = recoverable_target_sentence_ids

        return ReviewAlignmentState(
            active_target_map=active_target_map,
            active_alignments_by_sentence=active_alignments_by_sentence,
            latest_segments_by_packet=latest_segments_by_packet,
            recoverable_sentence_ids_by_packet=recoverable_sentence_ids_by_packet,
            recoverable_target_sentence_ids_by_packet=recoverable_target_sentence_ids_by_packet,
        )

    def _alignment_failure_issues(
        self,
        bundle: ChapterReviewBundle,
        alignment_state: ReviewAlignmentState,
        sentence_to_packet: dict[str, str],
        now: datetime,
    ) -> tuple[list[ReviewIssue], set[str]]:
        sentence_map = {sentence.id: sentence for sentence in bundle.sentences}
        issues: list[ReviewIssue] = []
        handled_sentence_ids: set[str] = set()
        active_target_ids = {
            target_id
            for target_ids in alignment_state.active_alignments_by_sentence.values()
            for target_id in target_ids
        }

        for packet in bundle.packets:
            recoverable_sentence_ids = alignment_state.recoverable_sentence_ids_by_packet.get(packet.id, set())
            recoverable_target_sentence_ids = alignment_state.recoverable_target_sentence_ids_by_packet.get(packet.id, {})
            latest_segments = alignment_state.latest_segments_by_packet.get(packet.id, [])
            if not recoverable_sentence_ids and not recoverable_target_sentence_ids:
                continue
            current_sentence_ids = [
                sentence_id
                for sentence_id in self._packet_current_sentence_ids(packet)
                if sentence_id in sentence_map
                and sentence_map[sentence_id].translatable
                and sentence_map[sentence_id].sentence_status != SentenceStatus.BLOCKED
            ]
            missing_sentence_ids = [
                sentence_id
                for sentence_id in current_sentence_ids
                if sentence_id not in alignment_state.active_alignments_by_sentence
            ]
            recoverable_missing = [sentence_id for sentence_id in missing_sentence_ids if sentence_id in recoverable_sentence_ids]
            orphan_target_ids = [
                segment.id
                for segment in latest_segments
                if segment.id in recoverable_target_sentence_ids
                and segment.id not in active_target_ids
            ]
            requires_packet_rerun = self._alignment_failure_requires_packet_rerun(
                orphan_target_ids=orphan_target_ids,
                latest_segments=latest_segments,
                recoverable_target_sentence_ids=recoverable_target_sentence_ids,
            )
            orphan_sentence_ids = sorted(
                {
                    sentence_id
                    for target_id in orphan_target_ids
                    for sentence_id in recoverable_target_sentence_ids.get(target_id, [])
                }
            )
            if not recoverable_missing and not orphan_target_ids:
                continue
            representative_sentence_id = (
                recoverable_missing[0]
                if recoverable_missing
                else (
                    orphan_sentence_ids[0]
                    if orphan_sentence_ids
                    else (current_sentence_ids[0] if current_sentence_ids else None)
                )
            )
            issues.append(
                self._make_issue(
                    now=now,
                    chapter_id=bundle.chapter.id,
                    document_id=bundle.chapter.document_id,
                    sentence_id=representative_sentence_id,
                    packet_id=sentence_to_packet.get(representative_sentence_id) if representative_sentence_id else packet.id,
                    issue_type="ALIGNMENT_FAILURE",
                    root_cause_layer=RootCauseLayer.ALIGNMENT,
                    severity=Severity.HIGH,
                    blocking=True,
                    evidence={
                        "reason": "recoverable_alignment_gap",
                        "packet_id": packet.id,
                        "missing_sentence_ids": ",".join(recoverable_missing),
                        "orphan_target_segment_ids": ",".join(orphan_target_ids),
                        "orphan_sentence_ids": ",".join(orphan_sentence_ids),
                        "requires_packet_rerun": requires_packet_rerun,
                    },
                    unique_key=packet.id,
                )
            )
            handled_sentence_ids.update(recoverable_missing)

        return issues, handled_sentence_ids

    def _chapter_context_failure_issue(
        self,
        bundle: ChapterReviewBundle,
        now: datetime,
    ) -> ReviewIssue | None:
        if bundle.chapter_brief is None:
            return None
        open_questions = bundle.chapter_brief.content_json.get("open_questions", [])
        if not open_questions:
            return None
        if self._should_skip_chapter_context_failure(bundle, open_questions):
            return None
        representative_sentence_id = bundle.sentences[0].id if bundle.sentences else None
        return self._make_issue(
            now=now,
            chapter_id=bundle.chapter.id,
            document_id=bundle.chapter.document_id,
            sentence_id=representative_sentence_id,
            packet_id=None,
            issue_type="CONTEXT_FAILURE",
            root_cause_layer=RootCauseLayer.MEMORY,
            severity=Severity.HIGH,
            blocking=True,
            evidence={
                "open_questions": " | ".join(open_questions),
                "chapter_brief_version": str(bundle.chapter_brief.version),
            },
            unique_key=f"chapter-brief:{bundle.chapter_brief.version}",
        )

    def _should_skip_chapter_context_failure(
        self,
        bundle: ChapterReviewBundle,
        open_questions: list[str],
    ) -> bool:
        if set(open_questions) != {"missing_chapter_title"}:
            return False
        has_translatable_sentences = any(sentence.translatable for sentence in bundle.sentences)
        if not has_translatable_sentences and not bundle.packets:
            return True
        return self._is_image_only_untitled_chapter(bundle)

    def _is_image_only_untitled_chapter(self, bundle: ChapterReviewBundle) -> bool:
        if bundle.chapter.title_src:
            return False
        image_only_block_count = 0
        for block in bundle.blocks:
            metadata = block.source_span_json or {}
            if not metadata.get("image_src"):
                return False
            if block.block_type not in {BlockType.CAPTION, BlockType.FIGURE, BlockType.IMAGE}:
                return False
            normalized_text = _normalize_review_text(block.source_text)
            normalized_alt = _normalize_review_text(str(metadata.get("image_alt") or ""))
            if normalized_text and normalized_text != "[Image]" and (not normalized_alt or normalized_text != normalized_alt):
                return False
            image_only_block_count += 1
        return image_only_block_count > 0

    def _duplication_issues(
        self,
        bundle: ChapterReviewBundle,
        alignment_state: ReviewAlignmentState,
        now: datetime,
    ) -> list[ReviewIssue]:
        source_sentence_map = {sentence.id: sentence for sentence in bundle.sentences}
        sentence_ids_by_target: dict[str, list[str]] = {}
        for sentence_id, target_ids in alignment_state.active_alignments_by_sentence.items():
            for target_id in target_ids:
                sentence_ids_by_target.setdefault(target_id, []).append(sentence_id)

        issues: list[ReviewIssue] = []
        for packet in bundle.packets:
            segments = alignment_state.latest_segments_by_packet.get(packet.id, [])
            for previous, current in zip(segments, segments[1:]):
                prev_text = self._normalize_target_text(previous.text_zh)
                curr_text = self._normalize_target_text(current.text_zh)
                if not prev_text or prev_text != curr_text or len(prev_text) < 6:
                    continue
                prev_sentence_ids = sorted(set(sentence_ids_by_target.get(previous.id, [])))
                curr_sentence_ids = sorted(set(sentence_ids_by_target.get(current.id, [])))
                if not prev_sentence_ids or not curr_sentence_ids or prev_sentence_ids == curr_sentence_ids:
                    continue
                if self._source_signature(prev_sentence_ids, source_sentence_map) == self._source_signature(
                    curr_sentence_ids,
                    source_sentence_map,
                ):
                    continue
                issues.append(
                    self._make_issue(
                        now=now,
                        chapter_id=bundle.chapter.id,
                        document_id=bundle.chapter.document_id,
                        sentence_id=prev_sentence_ids[0],
                        packet_id=packet.id,
                        issue_type="DUPLICATION",
                        root_cause_layer=RootCauseLayer.PACKET,
                        severity=Severity.MEDIUM,
                        blocking=False,
                        evidence={
                            "duplicated_text": previous.text_zh,
                            "first_target_segment_id": previous.id,
                            "second_target_segment_id": current.id,
                        },
                        unique_key=packet.id,
                    )
                )
                break
        return issues

    def _sentence_to_packet_map(self, bundle: ChapterReviewBundle) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for packet in bundle.packets:
            for sentence_id in self._packet_current_sentence_ids(packet):
                mapping[sentence_id] = packet.id
        return mapping

    def _packet_current_sentence_ids(self, packet) -> list[str]:
        sentence_ids: list[str] = []
        current_blocks = packet.packet_json.get("current_blocks", [])
        for block in current_blocks:
            sentence_ids.extend(block.get("sentence_ids", []))
        return sentence_ids

    def _normalize_target_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", "", text or "")
        normalized = re.sub(r"[。！？；，、,.!?;:：\"'“”‘’（）()\\-]", "", normalized)
        return normalized

    def _extract_markup_like_tokens(self, text: str) -> set[str]:
        return set(re.findall(r"</?[a-zA-Z][^>]*>", text or ""))

    def _source_signature(self, sentence_ids: list[str], source_sentence_map: dict[str, object]) -> tuple[str, ...]:
        return tuple(
            (source_sentence_map[sentence_id].normalized_text or source_sentence_map[sentence_id].source_text).strip()
            for sentence_id in sentence_ids
            if sentence_id in source_sentence_map
        )

    def _recoverable_alignment_metadata(
        self,
        translation_run,
        target_segments: list[object],
    ) -> tuple[set[str], dict[str, list[str]]]:
        output_json = translation_run.output_json or {}
        output_segments = output_json.get("target_segments", [])
        if not output_segments or len(output_segments) != len(target_segments):
            return set(), {}

        temp_to_segment_id = {
            segment_payload.get("temp_id"): segment.id
            for segment_payload, segment in zip(output_segments, target_segments)
            if segment_payload.get("temp_id")
        }
        recoverable_sentence_ids: set[str] = set()
        recoverable_target_sentence_ids: dict[str, list[str]] = {}
        alignment_suggestions = output_json.get("alignment_suggestions", [])
        if alignment_suggestions:
            for suggestion in alignment_suggestions:
                target_ids = [
                    temp_to_segment_id[temp_id]
                    for temp_id in suggestion.get("target_temp_ids", [])
                    if temp_id in temp_to_segment_id
                ]
                if not target_ids:
                    continue
                source_sentence_ids = list(suggestion.get("source_sentence_ids", []))
                recoverable_sentence_ids.update(source_sentence_ids)
                for target_id in target_ids:
                    recoverable_target_sentence_ids.setdefault(target_id, [])
                    recoverable_target_sentence_ids[target_id].extend(source_sentence_ids)
            return recoverable_sentence_ids, {
                target_id: sorted(set(sentence_ids))
                for target_id, sentence_ids in recoverable_target_sentence_ids.items()
            }

        for segment_payload in output_segments:
            temp_id = segment_payload.get("temp_id")
            if temp_id not in temp_to_segment_id:
                continue
            source_sentence_ids = list(segment_payload.get("source_sentence_ids", []))
            if not source_sentence_ids:
                continue
            target_id = temp_to_segment_id[temp_id]
            recoverable_sentence_ids.update(source_sentence_ids)
            recoverable_target_sentence_ids[target_id] = source_sentence_ids
        return recoverable_sentence_ids, recoverable_target_sentence_ids

    def _alignment_failure_requires_packet_rerun(
        self,
        *,
        orphan_target_ids: list[str],
        latest_segments: list[object],
        recoverable_target_sentence_ids: dict[str, list[str]],
    ) -> bool:
        if not orphan_target_ids:
            return False
        segment_map = {segment.id: segment for segment in latest_segments}
        for target_id in orphan_target_ids:
            if recoverable_target_sentence_ids.get(target_id):
                continue
            segment = segment_map.get(target_id)
            if segment is None:
                continue
            if not self._is_short_tail_label_text(segment.text_zh):
                return True
        return False

    def _is_short_tail_label_text(self, text: object) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if len(normalized) > 24:
            return False
        if normalized.endswith((":", "：", ";", "；")):
            return True
        return len(normalized) <= 12 and not any(mark in normalized for mark in "。！？.!?")

    def _make_issue(
        self,
        *,
        now: datetime,
        chapter_id: str,
        document_id: str,
        sentence_id: str | None,
        packet_id: str | None,
        issue_type: str,
        root_cause_layer: RootCauseLayer,
        severity: Severity,
        blocking: bool,
        evidence: dict[str, Any],
        block_id: str | None = None,
        unique_key: str | None = None,
    ) -> ReviewIssue:
        issue_id_parts = ["review-issue", document_id, chapter_id, sentence_id or "no-sentence", issue_type]
        if unique_key is not None:
            issue_id_parts.append(unique_key)
        return ReviewIssue(
            id=stable_id(*issue_id_parts),
            document_id=document_id,
            chapter_id=chapter_id,
            block_id=block_id,
            sentence_id=sentence_id,
            packet_id=packet_id,
            issue_type=issue_type,
            root_cause_layer=root_cause_layer,
            severity=severity,
            blocking=blocking,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json=evidence,
            status=IssueStatus.OPEN,
            created_at=now,
            updated_at=now,
        )

    def _build_action(self, issue: ReviewIssue) -> IssueAction:
        action_type = (
            ActionType.RERUN_PACKET
            if issue.issue_type == "ALIGNMENT_FAILURE"
            and bool((issue.evidence_json or {}).get("requires_packet_rerun"))
            else resolve_action(
                IssueRoutingContext(
                    issue_type=issue.issue_type,
                    root_cause_layer=issue.root_cause_layer,
                    involves_locked_term=issue.issue_type == "TERM_CONFLICT",
                    translation_content_ok=issue.issue_type != "OMISSION",
                )
            )
        )
        scope_type, scope_id = self._scope_for_action(issue, action_type)
        return IssueAction(
            id=stable_id("issue-action", issue.id, action_type.value),
            issue_id=issue.id,
            action_type=action_type,
            scope_type=scope_type,
            scope_id=scope_id,
            status=ActionStatus.PLANNED,
            reason_json={"issue_type": issue.issue_type, "packet_id": issue.packet_id},
            created_by=ActionActorType.SYSTEM,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
        )

    def _scope_for_action(self, issue: ReviewIssue, action_type: ActionType) -> tuple[JobScopeType, str | None]:
        if action_type in {ActionType.RERUN_PACKET, ActionType.REBUILD_PACKET_THEN_RERUN, ActionType.REALIGN_ONLY} and issue.packet_id:
            return JobScopeType.PACKET, issue.packet_id
        if (
            action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED
            and issue.issue_type == "TERM_CONFLICT"
            and issue.packet_id
        ):
            return JobScopeType.PACKET, issue.packet_id
        if (
            action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED
            and issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            and issue.packet_id
        ):
            packet_ids_seen = [
                str(packet_id).strip()
                for packet_id in list((issue.evidence_json or {}).get("packet_ids_seen") or [])
                if str(packet_id).strip()
            ]
            if len(packet_ids_seen) == 1:
                return JobScopeType.PACKET, issue.packet_id
        if action_type in {
            ActionType.RESEGMENT_CHAPTER,
            ActionType.REPARSE_CHAPTER,
            ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
            ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED,
            ActionType.REBUILD_CHAPTER_BRIEF,
        }:
            return JobScopeType.CHAPTER, issue.chapter_id
        if action_type == ActionType.REPARSE_DOCUMENT:
            return JobScopeType.DOCUMENT, issue.document_id
        return JobScopeType.SENTENCE, issue.sentence_id
