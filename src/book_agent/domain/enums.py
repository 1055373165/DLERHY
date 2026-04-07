from enum import StrEnum


class SourceType(StrEnum):
    EPUB = "epub"
    PDF_TEXT = "pdf_text"
    PDF_SCAN = "pdf_scan"
    PDF_MIXED = "pdf_mixed"


class DocumentStatus(StrEnum):
    INGESTED = "ingested"
    PARSED = "parsed"
    ACTIVE = "active"
    PARTIALLY_EXPORTED = "partially_exported"
    EXPORTED = "exported"
    FAILED = "failed"


class ParseRevisionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class ChapterStatus(StrEnum):
    READY = "ready"
    SEGMENTED = "segmented"
    PACKET_BUILT = "packet_built"
    TRANSLATED = "translated"
    QA_CHECKED = "qa_checked"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    EXPORTED = "exported"
    FAILED = "failed"


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    QUOTE = "quote"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    CODE = "code"
    TABLE = "table"
    LIST_ITEM = "list_item"
    FIGURE = "figure"
    EQUATION = "equation"
    IMAGE = "image"


class ProtectedPolicy(StrEnum):
    TRANSLATE = "translate"
    PROTECT = "protect"
    MIXED = "mixed"


class ArtifactStatus(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"


class SentenceStatus(StrEnum):
    PENDING = "pending"
    PROTECTED = "protected"
    TRANSLATED = "translated"
    REVIEW_REQUIRED = "review_required"
    FINALIZED = "finalized"
    BLOCKED = "blocked"


class BookType(StrEnum):
    TECH = "tech"
    BUSINESS = "business"
    NONFICTION = "nonfiction"
    HISTORY = "history"
    FICTION = "fiction"
    OTHER = "other"


class MemoryScopeType(StrEnum):
    GLOBAL = "global"
    CHAPTER = "chapter"


class SnapshotType(StrEnum):
    CHAPTER_BRIEF = "chapter_brief"
    CHAPTER_TRANSLATION_MEMORY = "chapter_translation_memory"
    TERMBASE = "termbase"
    ENTITY_REGISTRY = "entity_registry"
    STYLE_DELTA = "style_delta"
    ISSUE_MEMORY = "issue_memory"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class MemoryProposalStatus(StrEnum):
    PROPOSED = "proposed"
    COMMITTED = "committed"
    REJECTED = "rejected"


class PacketType(StrEnum):
    TRANSLATE = "translate"
    RETRANSLATE = "retranslate"
    REVIEW = "review"


class PacketStatus(StrEnum):
    BUILT = "built"
    RUNNING = "running"
    TRANSLATED = "translated"
    INVALIDATED = "invalidated"
    FAILED = "failed"


class PacketSentenceRole(StrEnum):
    CURRENT = "current"
    PREV_CONTEXT = "prev_context"
    NEXT_CONTEXT = "next_context"
    LOOKBACK = "lookback"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SegmentType(StrEnum):
    SENTENCE = "sentence"
    MERGED_SENTENCE = "merged_sentence"
    HEADING = "heading"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    PROTECTED = "protected"


class TargetSegmentStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    FINALIZED = "finalized"
    SUPERSEDED = "superseded"


class RelationType(StrEnum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:n"
    MANY_TO_ONE = "n:1"
    PROTECTED = "protected"


class TermType(StrEnum):
    PERSON = "person"
    ORG = "org"
    PLACE = "place"
    CONCEPT = "concept"
    TITLE = "title"
    ABBR = "abbr"
    OTHER = "other"


class LockLevel(StrEnum):
    SUGGESTED = "suggested"
    PREFERRED = "preferred"
    LOCKED = "locked"


class TermStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class RootCauseLayer(StrEnum):
    INGEST = "ingest"
    PARSE = "parse"
    STRUCTURE = "structure"
    SEGMENT = "segment"
    MEMORY = "memory"
    PACKET = "packet"
    TRANSLATION = "translation"
    ALIGNMENT = "alignment"
    REVIEW = "review"
    EXPORT = "export"
    OPS = "ops"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Detector(StrEnum):
    RULE = "rule"
    MODEL = "model"
    HUMAN = "human"


class IssueStatus(StrEnum):
    OPEN = "open"
    TRIAGED = "triaged"
    RESOLVED = "resolved"
    WONTFIX = "wontfix"


class ActionType(StrEnum):
    EDIT_TARGET_ONLY = "EDIT_TARGET_ONLY"
    REALIGN_ONLY = "REALIGN_ONLY"
    RERUN_PACKET = "RERUN_PACKET"
    REBUILD_PACKET_THEN_RERUN = "REBUILD_PACKET_THEN_RERUN"
    REBUILD_CHAPTER_BRIEF = "REBUILD_CHAPTER_BRIEF"
    UPDATE_TERMBASE_THEN_RERUN_TARGETED = "UPDATE_TERMBASE_THEN_RERUN_TARGETED"
    UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED = "UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED"
    RESEGMENT_CHAPTER = "RESEGMENT_CHAPTER"
    REPARSE_CHAPTER = "REPARSE_CHAPTER"
    REPARSE_DOCUMENT = "REPARSE_DOCUMENT"
    REEXPORT_ONLY = "REEXPORT_ONLY"
    MANUAL_FINALIZE = "MANUAL_FINALIZE"


class ActionStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportType(StrEnum):
    BILINGUAL_HTML = "bilingual_html"
    BILINGUAL_MARKDOWN = "bilingual_markdown"
    MERGED_HTML = "merged_html"
    MERGED_MARKDOWN = "merged_markdown"
    REBUILT_EPUB = "rebuilt_epub"
    REBUILT_PDF = "rebuilt_pdf"
    ZH_EPUB = "zh_epub"
    ZH_PDF = "zh_pdf"
    REVIEW_PACKAGE = "review_package"
    JSONL = "jsonl"


class ExportStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobType(StrEnum):
    INGEST = "ingest"
    PARSE = "parse"
    SEGMENT = "segment"
    PROFILE = "profile"
    BRIEF = "brief"
    PACKET = "packet"
    TRANSLATE = "translate"
    QA = "qa"
    RERUN = "rerun"
    EXPORT = "export"


class JobScopeType(StrEnum):
    DOCUMENT = "document"
    CHAPTER = "chapter"
    PACKET = "packet"
    SENTENCE = "sentence"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocumentRunType(StrEnum):
    BOOTSTRAP = "bootstrap"
    TRANSLATE_FULL = "translate_full"
    TRANSLATE_TARGETED = "translate_targeted"
    REVIEW_FULL = "review_full"
    EXPORT_FULL = "export_full"
    REPAIR_TARGETED = "repair_targeted"


class DocumentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DRAINING = "draining"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItemStage(StrEnum):
    BOOTSTRAP = "bootstrap"
    TRANSLATE = "translate"
    REVIEW = "review"
    REPAIR = "repair"
    EXPORT = "export"


class WorkItemScopeType(StrEnum):
    DOCUMENT = "document"
    CHAPTER = "chapter"
    PACKET = "packet"
    ISSUE_ACTION = "issue_action"
    EXPORT = "export"


class WorkItemStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    CANCELLED = "cancelled"


class WorkerLeaseStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class ActorType(StrEnum):
    SYSTEM = "system"
    MODEL = "model"
    HUMAN = "human"


class ActionActorType(StrEnum):
    SYSTEM = "system"
    HUMAN = "human"


class InvalidatedByType(StrEnum):
    ISSUE = "issue"
    VERSION_CHANGE = "version_change"
    HUMAN = "human"
    SYSTEM = "system"


class InvalidatedObjectType(StrEnum):
    CHAPTER = "chapter"
    BLOCK = "block"
    SENTENCE = "sentence"
    PACKET = "packet"
    TRANSLATION_RUN = "translation_run"
    TARGET_SEGMENT = "target_segment"
    ALIGNMENT_EDGE = "alignment_edge"
    MEMORY_SNAPSHOT = "memory_snapshot"
    EXPORT = "export"


class ChapterRunPhase(StrEnum):
    PACKETIZE = "packetize"
    TRANSLATE = "translate"
    REVIEW = "review"
    EXPORT = "export"
    COMPLETE = "complete"


class ChapterRunStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PacketTaskAction(StrEnum):
    TRANSLATE = "translate"
    RETRANSLATE = "retranslate"


class PacketTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewSessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewTerminalityState(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    BLOCKED = "blocked"


class RuntimeIncidentKind(StrEnum):
    EXPORT_MISROUTING = "export_misrouting"
    RUNTIME_DEFECT = "runtime_defect"
    REVIEW_DEADLOCK = "review_deadlock"
    PACKET_RUNTIME_DEFECT = "packet_runtime_defect"


class RuntimeIncidentStatus(StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    PATCH_PROPOSED = "patch_proposed"
    VALIDATING = "validating"
    PUBLISHED = "published"
    RESOLVED = "resolved"
    FAILED = "failed"
    FROZEN = "frozen"


class RuntimePatchProposalStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    VALIDATED = "validated"
    PUBLISHED = "published"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class RuntimeBundleRevisionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"
