from dataclasses import dataclass

from book_agent.domain.enums import ChapterStatus, DocumentStatus, PacketStatus, SentenceStatus


@dataclass(frozen=True)
class TransitionRule:
    source: str
    target: str


DOCUMENT_TRANSITIONS = {
    TransitionRule(DocumentStatus.INGESTED, DocumentStatus.PARSED),
    TransitionRule(DocumentStatus.PARSED, DocumentStatus.ACTIVE),
    TransitionRule(DocumentStatus.ACTIVE, DocumentStatus.PARTIALLY_EXPORTED),
    TransitionRule(DocumentStatus.PARTIALLY_EXPORTED, DocumentStatus.EXPORTED),
}

CHAPTER_TRANSITIONS = {
    TransitionRule(ChapterStatus.READY, ChapterStatus.SEGMENTED),
    TransitionRule(ChapterStatus.SEGMENTED, ChapterStatus.PACKET_BUILT),
    TransitionRule(ChapterStatus.PACKET_BUILT, ChapterStatus.TRANSLATED),
    TransitionRule(ChapterStatus.TRANSLATED, ChapterStatus.QA_CHECKED),
    TransitionRule(ChapterStatus.QA_CHECKED, ChapterStatus.REVIEW_REQUIRED),
    TransitionRule(ChapterStatus.QA_CHECKED, ChapterStatus.APPROVED),
    TransitionRule(ChapterStatus.REVIEW_REQUIRED, ChapterStatus.PACKET_BUILT),
    TransitionRule(ChapterStatus.REVIEW_REQUIRED, ChapterStatus.SEGMENTED),
    TransitionRule(ChapterStatus.REVIEW_REQUIRED, ChapterStatus.READY),
    TransitionRule(ChapterStatus.APPROVED, ChapterStatus.EXPORTED),
}

PACKET_TRANSITIONS = {
    TransitionRule(PacketStatus.BUILT, PacketStatus.RUNNING),
    TransitionRule(PacketStatus.RUNNING, PacketStatus.TRANSLATED),
    TransitionRule(PacketStatus.TRANSLATED, PacketStatus.INVALIDATED),
    TransitionRule(PacketStatus.INVALIDATED, PacketStatus.BUILT),
}

SENTENCE_TRANSITIONS = {
    TransitionRule(SentenceStatus.PENDING, SentenceStatus.PROTECTED),
    TransitionRule(SentenceStatus.PENDING, SentenceStatus.TRANSLATED),
    TransitionRule(SentenceStatus.TRANSLATED, SentenceStatus.REVIEW_REQUIRED),
    TransitionRule(SentenceStatus.TRANSLATED, SentenceStatus.FINALIZED),
    TransitionRule(SentenceStatus.REVIEW_REQUIRED, SentenceStatus.FINALIZED),
    TransitionRule(SentenceStatus.REVIEW_REQUIRED, SentenceStatus.BLOCKED),
    TransitionRule(SentenceStatus.BLOCKED, SentenceStatus.PENDING),
}


def can_transition(current: str, target: str, rules: set[TransitionRule]) -> bool:
    return TransitionRule(current, target) in rules

