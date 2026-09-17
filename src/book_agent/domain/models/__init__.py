from book_agent.infra.db.base import Base, install_enum_check_constraints
from book_agent.domain.models.agent import AgentItem, AgentTurn, Approval, Decision
from book_agent.domain.models.document import (
    Block,
    BookProfile,
    Chapter,
    Document,
    DocumentImage,
    MemorySnapshot,
    Sentence,
    SentenceLineage,
)
from book_agent.domain.models.parse_revision import DocumentParseRevision, DocumentParseRevisionArtifact
from book_agent.domain.models.ops import (
    ArtifactInvalidation,
    AuditEvent,
    ChapterWorklistAssignment,
    DocumentRun,
    Event,
    JobRun,
    RunAuditEvent,
    RunBudget,
    WorkItem,
    WorkerLease,
)
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.domain.models.review import (
    ChapterQualitySummary,
    Export,
    ExportVersion,
    IssueAction,
    ReviewIssue,
    ReviewIssueEvent,
)
from book_agent.domain.models.translation import (
    AlignmentEdge,
    ChapterMemoryProposal,
    PacketSentenceMap,
    TargetSegment,
    TermEntry,
    TranslationPacket,
    TranslationRun,
)

__all__ = [
    "AgentItem",
    "AgentTurn",
    "Approval",
    "Decision",
    "AlignmentEdge",
    "ArtifactInvalidation",
    "AuditEvent",
    "Block",
    "BookProfile",
    "ChapterQualitySummary",
    "ChapterMemoryProposal",
    "ChapterWorklistAssignment",
    "Chapter",
    "Document",
    "DocumentImage",
    "DocumentParseRevision",
    "DocumentParseRevisionArtifact",
    "DocumentRun",
    "Event",
    "Export",
    "ExportVersion",
    "IssueAction",
    "JobRun",
    "MemorySnapshot",
    "PacketSentenceMap",
    "ProviderCredential",
    "RunAuditEvent",
    "RunBudget",
    "ReviewIssue",
    "ReviewIssueEvent",
    "Sentence",
    "SentenceLineage",
    "TargetSegment",
    "TermEntry",
    "TranslationPacket",
    "TranslationRun",
    "WorkItem",
    "WorkerLease",
]

install_enum_check_constraints(Base.metadata)
