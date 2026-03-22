# ruff: noqa: E402

import html
import json
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.ids import stable_id
from book_agent.core.config import Settings
from book_agent.domain.enums import ActionActorType, ActionStatus, ActionType, ActorType, ArtifactStatus, BlockType, BookType, ChapterStatus, Detector, DocumentStatus, ExportType, IssueStatus, JobScopeType, LockLevel, MemoryScopeType, MemoryStatus, ProtectedPolicy, RelationType, RootCauseLayer, RunStatus, SegmentType, Severity, SnapshotType, SentenceStatus, SourceType, TargetSegmentStatus, TermStatus, TermType
from book_agent.domain.enums import PacketStatus, PacketType
from book_agent.domain.models import ArtifactInvalidation, AuditEvent, Block, BookProfile, Chapter, ChapterQualitySummary, Document, Export, MemorySnapshot, Sentence, TermEntry
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ChapterExportBundle, DocumentExportBundle, ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ChapterReviewBundle, ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.rerun import RerunPlan
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TranslationPacket, TranslationRun
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.services.actions import ActionExecutionArtifacts, IssueActionExecutor
from book_agent.services.chapter_concept_autolock import (
    ChapterConceptAutoLockService,
    ConceptTranslationExample,
    ConceptResolutionPayload,
    FallbackConceptResolver,
    HeuristicConceptResolver,
    OpenAICompatibleConceptResolver,
    build_default_concept_resolver,
)
from book_agent.services.chapter_concept_lock import ChapterConceptLockService
from book_agent.services.export import ExportFollowupAction, ExportGateError, ExportService, MergedRenderBlock
from book_agent.services.pdf_prose_artifact_repair import PdfProseArtifactRepairService
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildService
from book_agent.services.rerun import RerunExecutionArtifacts, RerunService
from book_agent.services.review import ChapterQualitySummary as ReviewChapterQualitySummary, ReviewArtifacts, ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import ActionWorkflowResult, DocumentWorkflowService
from book_agent.workers.contracts import AlignmentSuggestion, TranslationTargetSegment, TranslationUsage, TranslationWorkerOutput
from book_agent.workers.translator import TranslationTask, TranslationWorkerMetadata


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Business Strategy Handbook</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Pricing power matters. Strategy compounds.</p>
    <blockquote>A quoted paragraph.</blockquote>
  </body>
</html>
"""

IMAGE_ONLY_FIGURE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="figure-container">
      <img src="images/cover.png" alt="cover art" />
    </div>
  </body>
</html>
"""

CODE_CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Use the example carefully.</p>
    <pre class="code-area">def run_agent():
    return "ok"

print(run_agent())</pre>
  </body>
</html>
"""

STRUCTURED_ARTIFACT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:m="http://www.w3.org/1998/Math/MathML">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <div id="fig-1" class="browsable-container figure-container">
      <img src="images/agent-loop.png" alt="Agent loop architecture" />
      <h5>Figure 1.1 Agent loop architecture</h5>
    </div>
    <pre class="code-area">def run_agent():
    return "ok"

print(run_agent())</pre>
    <table id="tbl-1">
      <tr><th>Tier</th><th>Latency</th></tr>
      <tr><td>Basic</td><td>Slow</td></tr>
    </table>
    <m:math id="eq-1"><m:mi>x</m:mi><m:mo>=</m:mo><m:mn>1</m:mn></m:math>
    <p>https://example.com/agent-docs</p>
  </body>
</html>
"""

CONTEXT_ENGINEERING_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Context engineering determines how context is created.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

CONTEXT_ENGINEERING_THREE_PACKET_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Context engineering determines how context is created.</p>
    <p>Strategy compounds over time.</p>
    <p>Decision records reduce surprises.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

CONTEXT_ENGINEERING_PACKET_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Context engineering determines how context is created. Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>This broader challenge is what some are beginning to call context engineering, which is the deliberate design of how context is created, maintained, and applied to shape reasoning.</p>
    <p>In essence, the weight of evidence shows relying on external content supplied at inference time from up-to-date, relevant sources tends to yield more reliable and contextually accurate outputs.</p>
  </body>
</html>
"""

KNOWLEDGE_TIMELINE_LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Memory is also the structured record of what was known, when it was known, and why it mattered for action.</p>
  </body>
</html>
"""

DURABLE_SUBSTRATE_LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Within this view, memory becomes the durable substrate of context, providing more than just raw recall of what has been said.</p>
  </body>
</html>
"""

RESPONSIBILITY_LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Deploying agentic systems in production requires a profound sense of responsibility.</p>
  </body>
</html>
"""

CONSISTENCY_CARE_LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>The chef’s value is greater than just preparing a single meal, because the chef draws on memory and experience to provide consistency and care over time.</p>
  </body>
</html>
"""

AGENCY_AUTONOMY_LITERALISM_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>In practice, agency exists on a spectrum.</p>
  </body>
</html>
"""

MIXED_AUTO_FOLLOWUP_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Agentic AI continuously improves by absorbing feedback.</p>
    <p>Agentic AI also adapts over time.</p>
    <p>This broader challenge is what some are beginning to call context engineering, which is the deliberate design of how context is created, maintained, and applied to shape reasoning.</p>
    <p>In essence, the weight of evidence shows relying on external content supplied at inference time from up-to-date, relevant sources tends to yield more reliable and contextually accurate outputs.</p>
  </body>
</html>
"""

STALE_BRIEF_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>A recipe book offers instructions for many meals.</p>
    <p>A chef adapts when your pantry is missing ingredients.</p>
    <p>Memory helps agents act consistently over time.</p>
    <p>Context engineering determines how context is created.</p>
    <p>Context engineering also determines how context is maintained.</p>
  </body>
</html>
"""

STALE_BRIEF_ADAPTIVE_AGENT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>A recipe book offers instructions for many meals.</p>
    <p>A chef adapts when your pantry is missing ingredients.</p>
    <p>An adaptive agent adjusts plans when feedback changes.</p>
    <p>Memory helps systems remain reliable over time.</p>
    <p>An adaptive agent also revises priorities over time.</p>
    <p>Decision records reduce operational surprises.</p>
  </body>
</html>
"""

AGENTIC_AI_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Agentic AI continuously improves by ingesting feedback.</p>
    <p>Agentic AI also adapts over time.</p>
  </body>
</html>
"""

LANGUAGE_MODELS_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Language models improve over time.</p>
  </body>
</html>
"""

SMALL_LANGUAGE_MODELS_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Small Language Models (SLMs) are rising.</p>
  </body>
</html>
"""

REFERENCE_LANGUAGE_MODELS_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">References</h1>
    <p>(2023) 3. "Program-Aided Language Models" by Gao et al.</p>
  </body>
</html>
"""

AI_AGENT_VARIANT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>A Crew.ai Agent named financial_analyst_agent is created with the role of a Senior Financial Analyst.</p>
  </body>
</html>
"""

REFERENCE_AI_AGENT_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">References</h1>
    <p>2. LangGraph Memory. 3. Vertex AI Agent Engine Memory Bank.</p>
  </body>
</html>
"""

AGENTIC_AI_PACKET_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Agentic AI continuously improves by ingesting feedback. Agentic AI also adapts over time.</p>
  </body>
</html>
"""


class DuplicateWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="DuplicateWorker",
            model_name="duplicate-test",
            prompt_version="test.duplication.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            if any(token in sentence.source_text for token in ["Pricing power", "Strategy compounds"]):
                text = "完全重复的译文片段。"
            else:
                text = f"译文::{sentence.source_text}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class SplitSegmentWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="SplitSegmentWorker",
            model_name="split-segment-test",
            prompt_version="test.split-segment.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "Pricing power" in sentence.source_text:
                temp_intro = f"temp-{sentence.id}-a"
                temp_tail = f"temp-{sentence.id}-b"
                target_segments.extend(
                    [
                        TranslationTargetSegment(
                            temp_id=temp_intro,
                            text_zh="定价权很重要。",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        ),
                        TranslationTargetSegment(
                            temp_id=temp_tail,
                            text_zh="它会持续复利。",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        ),
                    ]
                )
                alignments.append(
                    AlignmentSuggestion(
                        source_sentence_ids=[sentence.id],
                        target_temp_ids=[temp_intro, temp_tail],
                        relation_type="1:n",
                        confidence=0.95,
                    )
                )
                continue

            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译文::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )

        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class EmptySourceAlignmentWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="EmptySourceAlignmentWorker",
            model_name="empty-source-alignment-test",
            prompt_version="test.empty-source-alignment.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译文::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class EmptySourceTailSentenceWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="EmptySourceTailSentenceWorker",
            model_name="empty-source-tail-sentence-test",
            prompt_version="test.empty-source-tail-sentence.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for index, sentence in enumerate(task.current_sentences):
            temp_id = f"temp-{sentence.id}"
            source_sentence_ids = [sentence.id]
            if index == len(task.current_sentences) - 1:
                source_sentence_ids = []
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译文::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=source_sentence_ids,
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=source_sentence_ids,
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class TrailingLabelWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="TrailingLabelWorker",
            model_name="trailing-label-test",
            prompt_version="test.trailing-label.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        sentence_ids = [sentence.id for sentence in task.current_sentences]
        target_segments = [
            TranslationTargetSegment(
                temp_id="temp-main",
                text_zh="主要译文内容。",
                segment_type="sentence",
                source_sentence_ids=sentence_ids,
                confidence=0.95,
            ),
            TranslationTargetSegment(
                temp_id="temp-tail",
                text_zh="输入摘要：",
                segment_type="sentence",
                source_sentence_ids=[],
                confidence=0.9,
            ),
        ]
        alignments = [
            AlignmentSuggestion(
                source_sentence_ids=sentence_ids,
                target_temp_ids=["temp-main"],
                relation_type="n:1",
                confidence=0.95,
            )
        ]
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ParagraphFlowWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="ParagraphFlowWorker",
            model_name="paragraph-flow-test",
            prompt_version="test.paragraph-flow.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        mapping = {
            "Chapter One": "第一章",
            "Pricing power matters.": "定价能力很重要。",
            "Strategy compounds.": "战略会持续复利。",
            "A quoted paragraph.": "这是一段引用。",
        }
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=mapping.get(sentence.source_text, f"译文::{sentence.source_text}"),
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class AgenticLiteralWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="AgenticLiteralWorker",
            model_name="agentic-literal-test",
            prompt_version="test.agentic-literal.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            temp_id = f"temp-{sentence.id}"
            if "Agentic AI" in sentence.source_text:
                text = "智能体AI通过吸收反馈持续改进。"
            else:
                text = f"译文::{sentence.source_text}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LanguageModelLiteralWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LanguageModelLiteralWorker",
            model_name="language-model-literal-test",
            prompt_version="test.language-model-literal.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Language models" in source_text:
                text = "语言模型会随着时间不断改进。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class SmallLanguageModelVariantWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="SmallLanguageModelVariantWorker",
            model_name="small-language-model-variant-test",
            prompt_version="test.small-language-model-variant.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Small Language Models" in source_text:
                text = "小语言模型（SLM）正在兴起。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ReferenceLanguageModelWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="ReferenceLanguageModelWorker",
            model_name="reference-language-model-test",
            prompt_version="test.reference-language-model.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Program-Aided Language Models" in source_text:
                text = "（2023）3. “程序辅助语言模型” 作者：Gao等人。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class CrewAiAgentVariantWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="CrewAiAgentVariantWorker",
            model_name="crew-ai-agent-variant-test",
            prompt_version="test.crew-ai-agent-variant.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Crew.ai Agent" in source_text:
                text = "创建了一个名为financial_analyst_agent的Crew.ai智能体，其角色是高级金融分析师。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ReferenceAiAgentWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="ReferenceAiAgentWorker",
            model_name="reference-ai-agent-test",
            prompt_version="test.reference-ai-agent.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Vertex AI Agent Engine" in source_text:
                text = "2. LangGraph 内存。3. Vertex AI Agent Engine 内存库。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class LiteralismWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="LiteralismWorker",
            model_name="literalism-test",
            prompt_version="test.literalism.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            if "context engineering" in sentence.source_text:
                text = (
                    "这一更广泛的挑战正被一些人称之为情境工程的内容，即对情境如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                )
            elif "durable substrate" in sentence.source_text:
                text = "在这一视角下，记忆成为上下文的持久基底，其作用远不止于对已述内容的原始回忆。"
            elif "profound sense of responsibility" in sentence.source_text:
                text = "在生产环境中部署智能体系统需要一种深刻的责任感。"
            elif "provide consistency and care over time" in sentence.source_text:
                text = "厨师的价值远不止准备一顿饭，因为他能凭借记忆和经验，在长期服务中提供连贯性和关怀。"
            elif "agency exists on a spectrum" in sentence.source_text:
                text = "实际上，自主性是一个连续谱系。"
            elif "what was known, when it was known" in sentence.source_text:
                text = "记忆也是关于已知内容、获知时间及其对行动重要性的结构化记录。"
            elif "weight of evidence" in sentence.source_text:
                text = (
                    "本质上，证据权重显示，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且更具上下文准确性的输出结果。"
                )
            else:
                text = f"译文::{sentence.source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class GuidanceAwareLiteralismWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="GuidanceAwareLiteralismWorker",
            model_name="guidance-aware-literalism-test",
            prompt_version="test.literalism-guided.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        open_questions = " | ".join(task.context_packet.open_questions)
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "context engineering" in source_text:
                if "上下文工程" in open_questions:
                    text = (
                        "这一更广泛的挑战正被一些人称为上下文工程，即对上下文如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                    )
                else:
                    text = (
                        "这一更广泛的挑战正被一些人称为情境工程，即对情境如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                    )
            elif "weight of evidence" in source_text:
                if "大量证据表明" in open_questions and "更符合上下文的输出" in open_questions:
                    text = (
                        "本质上，大量证据表明，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且更符合上下文的输出。"
                    )
                else:
                    text = (
                        "本质上，证据权重表明，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且上下文更准确的输出。"
                    )
            elif "profound sense of responsibility" in source_text:
                if "强烈的责任感" in open_questions or "很强的责任意识" in open_questions:
                    text = "在生产环境中部署智能体系统需要很强的责任意识。"
                else:
                    text = "在生产环境中部署智能体系统需要一种深刻的责任感。"
            elif "provide consistency and care over time" in source_text:
                if "长期稳定、周到地照应" in open_questions or "始终如一地细心照应" in open_questions:
                    text = "厨师的价值不只在于做一顿饭，更在于他能凭借记忆和经验，长期稳定而周到地照应你。"
                else:
                    text = "厨师的价值远不止准备一顿饭，因为他能凭借记忆和经验，在长期服务中提供连贯性和关怀。"
            elif "agency exists on a spectrum" in source_text:
                if "智能体性" in open_questions or "决策与行动能力" in open_questions:
                    text = "实际上，智能体性是有连续谱系的。"
                else:
                    text = "实际上，自主性是一个连续谱系。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class GuidanceAwareAgenticWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="GuidanceAwareAgenticWorker",
            model_name="guidance-aware-agentic-test",
            prompt_version="test.agentic-guided.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        relevant_terms = " | ".join(
            f"{term.source_term}=>{term.target_term}"
            for term in task.context_packet.relevant_terms
        )
        chapter_concepts = " | ".join(
            f"{concept.source_term}=>{concept.canonical_zh}"
            for concept in task.context_packet.chapter_concepts
            if concept.canonical_zh
        )
        open_questions = " | ".join(task.context_packet.open_questions)
        guidance = " | ".join([relevant_terms, chapter_concepts, open_questions])
        locked_term = "代理式AI" if "代理式AI" in guidance else ("智能体AI" if "智能体AI" in guidance else None)

        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Agentic AI" in source_text:
                if "continuously improves" in source_text:
                    text = (
                        f"{locked_term}通过吸收反馈持续改进。"
                        if locked_term
                        else "智能体AI通过吸收反馈持续改进。"
                    )
                elif "adapts over time" in source_text:
                    text = (
                        f"{locked_term}也会随着时间推移不断适应。"
                        if locked_term
                        else "智能体AI也会随着时间推移不断适应。"
                    )
                else:
                    text = (
                        f"{locked_term}会持续改进。"
                        if locked_term
                        else "智能体AI会持续改进。"
                    )
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class CountingGuidanceAwareAdaptiveAgentWorker:
    def __init__(self) -> None:
        self.packet_ids_seen: list[str] = []

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="CountingGuidanceAwareAdaptiveAgentWorker",
            model_name="guidance-aware-adaptive-agent-test",
            prompt_version="test.adaptive-agent-guided.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        self.packet_ids_seen.append(task.context_packet.packet_id)
        target_segments = []
        alignments = []
        relevant_terms = " | ".join(
            f"{term.source_term}=>{term.target_term}"
            for term in task.context_packet.relevant_terms
        )
        chapter_concepts = " | ".join(
            f"{concept.source_term}=>{concept.canonical_zh}"
            for concept in task.context_packet.chapter_concepts
            if concept.canonical_zh
        )
        open_questions = " | ".join(task.context_packet.open_questions)
        guidance = " | ".join([relevant_terms, chapter_concepts, open_questions])
        use_locked_term = "自适应智能体" in guidance

        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "adaptive agent" in source_text.lower():
                if "adjusts plans" in source_text:
                    text = (
                        "自适应智能体会在反馈变化时调整计划。"
                        if use_locked_term
                        else "适应性智能体会在反馈变化时调整计划。"
                    )
                elif "revises priorities" in source_text:
                    text = (
                        "自适应智能体也会随着时间推移重新调整优先级。"
                        if use_locked_term
                        else "适应性智能体也会随着时间推移重新调整优先级。"
                    )
                else:
                    text = "自适应智能体会持续调整。"
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class ConsistentContextEngineeringWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="ConsistentContextEngineeringWorker",
            model_name="context-engineering-consistent-test",
            prompt_version="test.context-engineering-consistent.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Context engineering" in source_text:
                text = (
                    "上下文工程决定了上下文如何被创建。"
                    if "created" in source_text
                    else "上下文工程也决定了上下文如何被维护。"
                )
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class CountingConsistentContextEngineeringWorker(ConsistentContextEngineeringWorker):
    def __init__(self) -> None:
        self.packet_ids_seen: list[str] = []

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        self.packet_ids_seen.append(task.context_packet.packet_id)
        return super().translate(task)


class GuidanceAwareMixedWorker:
    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(
            worker_name="GuidanceAwareMixedWorker",
            model_name="guidance-aware-mixed-test",
            prompt_version="test.mixed-guided.v1",
        )

    def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
        target_segments = []
        alignments = []
        relevant_terms = " | ".join(
            f"{term.source_term}=>{term.target_term}"
            for term in task.context_packet.relevant_terms
        )
        chapter_concepts = " | ".join(
            f"{concept.source_term}=>{concept.canonical_zh}"
            for concept in task.context_packet.chapter_concepts
            if concept.canonical_zh
        )
        open_questions = " | ".join(task.context_packet.open_questions)
        guidance = " | ".join([relevant_terms, chapter_concepts, open_questions])
        locked_term = "代理式AI" if "代理式AI" in guidance else ("智能体AI" if "智能体AI" in guidance else None)
        has_style_hints = "大量证据表明" in open_questions and "更符合上下文的输出" in open_questions

        for sentence in task.current_sentences:
            source_text = sentence.source_text
            if "Agentic AI" in source_text:
                if "continuously improves" in source_text:
                    text = (
                        f"{locked_term}通过吸收反馈持续改进。"
                        if locked_term
                        else "智能体AI通过吸收反馈持续改进。"
                    )
                elif "adapts over time" in source_text:
                    text = (
                        f"{locked_term}也会随着时间推移不断适应。"
                        if locked_term
                        else "智能体AI也会随着时间推移不断适应。"
                    )
                else:
                    text = f"{locked_term}会持续改进。" if locked_term else "智能体AI会持续改进。"
            elif "context engineering" in source_text:
                if "上下文工程" in open_questions:
                    text = (
                        "这一更广泛的挑战正被一些人称为上下文工程，即对上下文如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                    )
                else:
                    text = (
                        "这一更广泛的挑战正被一些人称为情境工程，即对情境如何被创建、维护并应用于塑造推理过程进行有意识的设计。"
                    )
            elif "weight of evidence" in source_text:
                if has_style_hints:
                    text = (
                        "本质上，大量证据表明，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且更符合上下文的输出。"
                    )
                else:
                    text = (
                        "本质上，证据权重表明，在推理时依赖来自最新相关来源的外部内容，往往能产生更可靠且上下文更准确的输出。"
                    )
            else:
                text = f"译文::{source_text}"
            temp_id = f"temp-{sentence.id}"
            target_segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=text,
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(
                    source_sentence_ids=[sentence.id],
                    target_temp_ids=[temp_id],
                    relation_type="1:1",
                    confidence=0.95,
                )
            )
        return TranslationWorkerOutput(
            packet_id=task.context_packet.packet_id,
            target_segments=target_segments,
            alignment_suggestions=alignments,
        )


class PersistenceAndReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_to_db(self) -> tuple[str, str]:
        tmpdir = Path(tempfile.mkdtemp(prefix="book-agent-test-epub-"))
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        epub_path = tmpdir / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id, artifacts.translation_packets[0].id

    def _bootstrap_custom_epub_to_db(
        self,
        chapters: list[tuple[str, str, str]],
        *,
        extra_files: dict[str, bytes] | None = None,
    ) -> str:
        manifest_items = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />']
        spine_items: list[str] = []
        toc_items: list[str] = []
        for index, (title, href, _content) in enumerate(chapters, start=1):
            item_id = f"chap{index}"
            manifest_items.append(f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml" />')
            spine_items.append(f'    <itemref idref="{item_id}" />')
            toc_items.append(f'        <li><a href="{href}">{title}</a></li>')

        content_opf = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">',
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
                '    <dc:title>Business Strategy Handbook</dc:title>',
                '    <dc:creator>Test Author</dc:creator>',
                '    <dc:language>en</dc:language>',
                "  </metadata>",
                "  <manifest>",
                *manifest_items,
                "  </manifest>",
                "  <spine>",
                *spine_items,
                "  </spine>",
                "</package>",
            ]
        )
        nav_xhtml = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">',
                "  <body>",
                '    <nav epub:type="toc">',
                "      <ol>",
                *toc_items,
                "      </ol>",
                "    </nav>",
                "  </body>",
                "</html>",
            ]
        )

        tmpdir = Path(tempfile.mkdtemp(prefix="book-agent-test-epub-"))
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        epub_path = tmpdir / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", content_opf)
            archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
            for _title, href, content in chapters:
                archive.writestr(f"OEBPS/{href}", content)
            for relative_path, payload in (extra_files or {}).items():
                archive.writestr(relative_path, payload)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id

    def test_bootstrap_persists_to_sqlite(self) -> None:
        document_id, _ = self._bootstrap_to_db()
        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            self.assertEqual(bundle.document.id, document_id)
            self.assertEqual(len(bundle.chapters), 1)
            self.assertEqual(len(bundle.chapters[0].blocks), 3)
            self.assertEqual(len(bundle.chapters[0].sentences), 4)
            self.assertEqual(len(bundle.chapters[0].translation_packets), 3)

    def test_translation_and_review_generate_issue_and_action(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            translation_service = TranslationService(TranslationRepository(session))
            translation_service.execute_packet(packet_id)

            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = document_bundle.chapters[0].chapter.id
            sentence_id = next(
                sentence.id
                for sentence in document_bundle.chapters[0].sentences
                if "Pricing power" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000001",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertGreaterEqual(len(review_artifacts.issues), 1)
            self.assertTrue(any(issue.issue_type == "TERM_CONFLICT" for issue in review_artifacts.issues))
            self.assertTrue(any(action.action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED for action in review_artifacts.actions))
            self.assertTrue(any(action.scope_type == JobScopeType.PACKET for action in review_artifacts.actions))
            persisted_summary = session.query(ChapterQualitySummary).filter_by(chapter_id=chapter_id).one()
            self.assertGreaterEqual(persisted_summary.issue_count, 1)
            self.assertEqual(persisted_summary.action_count, len(review_artifacts.actions))
            self.assertFalse(persisted_summary.term_ok)

    def test_action_execution_and_export(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = document_bundle.chapters[0].chapter.id
            sentence_id = next(
                sentence.id
                for sentence in document_bundle.chapters[0].sentences
                if "Pricing power" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id="00000000-0000-0000-0000-000000000002",
                    document_id=document_id,
                    scope_type=MemoryScopeType.GLOBAL,
                    scope_id=None,
                    source_term="pricing power",
                    target_term="定价权",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
                review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
                action_id = review_artifacts.actions[0].id
                execution = IssueActionExecutor(OpsRepository(session)).execute(action_id)
                review_export = ExportService(ExportRepository(session), output_root=outdir).export_review_package(chapter_id)
                session.commit()

                self.assertGreaterEqual(len(execution.invalidations), 1)
                self.assertTrue(review_export.file_path.exists())
                with self.assertRaises(ExportGateError):
                    ExportService(ExportRepository(session), output_root=outdir).export_bilingual_html(chapter_id)
                self.assertGreaterEqual(session.query(ArtifactInvalidation).count(), 1)
                self.assertGreaterEqual(session.query(Export).count(), 1)

    def test_export_alignment_issues_ignore_packets_with_only_inactive_edge_history(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="11111111-1111-4111-8111-111111111111",
            source_type=SourceType.EPUB,
            file_fingerprint="export-inactive-edge-history",
            source_path="/tmp/export-history.epub",
            title="Export History",
            status=DocumentStatus.PARTIALLY_EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="22222222-2222-4222-8222-222222222222",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter One",
            title_tgt="第一章",
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.QA_CHECKED,
            summary_version=None,
            risk_level=None,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="33333333-3333-4333-8333-333333333333",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text="Pricing power matters.",
            normalized_text="Pricing power matters.",
            source_anchor=None,
            source_span_json={},
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="44444444-4444-4444-8444-444444444444",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.normalized_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor=None,
            source_span_json={},
            upstream_confidence=0.99,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        packet = TranslationPacket(
            id="55555555-5555-4555-8555-555555555555",
            chapter_id=chapter.id,
            block_start_id=block.id,
            block_end_id=block.id,
            packet_type=PacketType.TRANSLATE,
            book_profile_version=1,
            chapter_brief_version=None,
            termbase_version=None,
            entity_snapshot_version=None,
            style_snapshot_version=None,
            packet_json={"current_blocks": [{"block_id": block.id, "sentence_ids": [sentence.id]}]},
            risk_score=0.1,
            status=PacketStatus.TRANSLATED,
            created_at=now,
            updated_at=now,
        )
        prior_run = TranslationRun(
            id="66666666-6666-4666-8666-666666666666",
            packet_id=packet.id,
            model_name="echo-worker",
            model_config_json={},
            prompt_version="test",
            attempt=1,
            status=RunStatus.SUCCEEDED,
            output_json={},
            token_in=1,
            token_out=1,
            cost_usd=0,
            latency_ms=1,
            created_at=now,
            updated_at=now,
        )
        latest_run = TranslationRun(
            id="77777777-7777-4777-8777-777777777777",
            packet_id=packet.id,
            model_name="echo-worker",
            model_config_json={},
            prompt_version="test",
            attempt=2,
            status=RunStatus.SUCCEEDED,
            output_json={},
            token_in=1,
            token_out=1,
            cost_usd=0,
            latency_ms=1,
            created_at=now,
            updated_at=now,
        )
        superseded_target = TargetSegment(
            id="88888888-8888-4888-8888-888888888888",
            chapter_id=chapter.id,
            translation_run_id=prior_run.id,
            ordinal=1,
            text_zh="旧译文",
            segment_type=SegmentType.SENTENCE,
            confidence=0.9,
            final_status=TargetSegmentStatus.SUPERSEDED,
            created_at=now,
            updated_at=now,
        )
        active_target = TargetSegment(
            id="99999999-9999-4999-8999-999999999999",
            chapter_id=chapter.id,
            translation_run_id=latest_run.id,
            ordinal=1,
            text_zh="定价权很重要。",
            segment_type=SegmentType.SENTENCE,
            confidence=0.95,
            final_status=TargetSegmentStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[packet],
            translation_runs=[prior_run, latest_run],
            target_segments=[superseded_target, active_target],
            alignment_edges=[
                AlignmentEdge(
                    id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    sentence_id=sentence.id,
                    target_segment_id=superseded_target.id,
                    relation_type=RelationType.ONE_TO_ONE,
                    confidence=0.8,
                    created_by=ActorType.SYSTEM,
                    created_at=now,
                ),
                AlignmentEdge(
                    id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    sentence_id=sentence.id,
                    target_segment_id=active_target.id,
                    relation_type=RelationType.ONE_TO_ONE,
                    confidence=0.95,
                    created_by=ActorType.SYSTEM,
                    created_at=now,
                ),
            ],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            evidence = service._build_export_misalignment_evidence(bundle)
            issues = service._build_export_alignment_issues(bundle, now)

        self.assertFalse(evidence.has_anomalies)
        self.assertEqual(evidence.missing_target_sentence_ids, [])
        self.assertEqual(evidence.sentence_ids_with_only_inactive_targets, [])
        self.assertEqual(evidence.orphan_target_segment_ids, [])
        self.assertEqual(evidence.inactive_target_segment_ids_with_edges, [superseded_target.id])
        self.assertEqual(issues, [])

    def test_export_service_reflows_flattened_code_artifact_text(self) -> None:
        flattened = (
            'def booking handler(request: str) -> str:\n'
            '"""Simulates the Booking Agent handling a request."""\n'
            'print("\\n--- DELEGATING TO BOOKING HANDLER ---")\n'
            "return f\"Booking Handler processed request: '{request}'. Result:\n"
            'Simulated booking action."'
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = html.unescape(service._format_preformatted_text(flattened))

        self.assertIn(
            'def booking handler(request: str) -> str:\n'
            '    """Simulates the Booking Agent handling a request."""',
            formatted,
        )
        self.assertIn(
            '\n    print("\\n--- DELEGATING TO BOOKING HANDLER ---")',
            formatted,
        )
        self.assertIn(
            "Result: Simulated booking action.\"",
            formatted,
        )
        self.assertNotIn(
            "Result:\nSimulated booking action.",
            formatted,
        )

    def test_export_service_preserves_well_formatted_json_layout(self) -> None:
        json_text = (
            "{\n"
            '\t"trends": [{\n'
            '\t\t"trend_name": "AI-Powered Personalization",\n'
            '\t\t"supporting_data": "73% of consumers prefer personalized experiences."\n'
            "\t}]\n"
            "}"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted_html = html.unescape(service._format_preformatted_text(json_text))
            formatted_markdown = service._normalize_markdown_code_artifact_text(json_text)

        self.assertEqual(formatted_html, json_text)
        self.assertEqual(formatted_markdown, json_text)

    def test_export_service_preserves_restored_cross_page_code_layout_in_markdown(self) -> None:
        code_text = (
            "import os\n"
            "from langchain_openai import ChatOpenAI\n"
            "from langchain_core.prompts import ChatPromptTemplate\n"
            "from langchain_core.output_parsers import StrOutputParser\n"
            "# --- Build the Chain using LCEL ---\n"
            "# The StrOutputParser() converts the LLM's message output to a simple\n"
            "string.\n"
            "extraction_chain = prompt_extract | llm | StrOutputParser()\n"
            "# The full chain passes the output of the extraction chain into the\n"
            "'specifications'\n"
            "# variable for the transformation prompt.\n"
            "full_chain = (\n"
            '{"specifications": extraction_chain}\n'
            "| prompt_transform\n"
            "| llm\n"
            "| StrOutputParser()\n"
            ")\n"
            'print("\\n--- Final JSON Output ---")\n'
            "print(final_result)\n"
        )
        block = MergedRenderBlock(
            block_id="markdown-cross-page-code",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text=code_text,
            target_text=None,
            source_metadata={
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "recovery_flags": [
                    "cross_page_repaired",
                    "export_refresh_split_code_restored",
                    "export_code_blocks_merged",
                ],
            },
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted_markdown = service._render_block_markdown(block)

        self.assertIn(
            "# The StrOutputParser() converts the LLM's message output to a simple string.\n"
            "extraction_chain = prompt_extract | llm | StrOutputParser()",
            formatted_markdown,
        )
        self.assertIn("extraction_chain = prompt_extract | llm | StrOutputParser()", formatted_markdown)
        self.assertIn(
            "full_chain = (\n"
            "    {\"specifications\": extraction_chain}\n"
            "    | prompt_transform\n"
            "    | llm\n"
            "    | StrOutputParser()\n"
            ")",
            formatted_markdown,
        )
        self.assertIn('print("\\n--- Final JSON Output ---")\nprint(final_result)', formatted_markdown)

    def test_export_service_repairs_broken_cross_page_code_layout_consistently_for_html_and_markdown(self) -> None:
        code_text = (
            "import os\n"
            "from langchain_openai import ChatOpenAI\n"
            "from langchain_core.prompts import ChatPromptTemplate\n"
            "from langchain_core.output_parsers import StrOutputParser\n"
            "# For better security, load environment variables from a .env file\n"
            "# from dotenv import load_dotenv\n"
            "# load_dotenv()\n"
            "# Make sure your OPENAI_API_KEY is set in the .env file\n"
            "# Initialize the Language Model (using ChatOpenAI is recommended)\n"
            "llm = ChatOpenAI(temperature=0)\n"
            "# --- Prompt 1: Extract Information ---\n"
            "prompt_extract = ChatPromptTemplate.from_template(\n"
            '"Extract the technical specifications from the following\n'
            'text:\\n\\n{text_input}"\n'
            ")\n"
            "# --- Prompt 2: Transform to JSON ---\n"
            "prompt_transform = ChatPromptTemplate.from_template(\n"
            "\"Transform the following specifications into a JSON object with\n"
            "'cpu', 'memory', and 'storage' as keys:\\n\\n{specifications}\"\n"
            ")\n"
            "# --- Build the Chain using LCEL ---\n"
            "# The StrOutputParser() converts the LLM's message output to a simple\n"
            "string.\n"
            "extraction_chain = prompt_extract | llm | StrOutputParser()\n"
            "# The full chain passes the output of the extraction chain into the\n"
            "'specifications'\n"
            "# variable for the transformation prompt.\n"
            "full_chain = (\n"
            '{"specifications": extraction_chain}\n'
            "| prompt_transform\n"
            "| llm\n"
            "| StrOutputParser()\n"
            ")\n"
        )
        block = MergedRenderBlock(
            block_id="markdown-html-code-repair",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text=code_text,
            target_text=None,
            source_metadata={
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "recovery_flags": [
                    "cross_page_repaired",
                    "export_refresh_split_code_restored",
                    "export_code_blocks_merged",
                ],
            },
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted_html = html.unescape(service._format_preformatted_text(code_text, block=block))
            formatted_markdown = service._normalize_markdown_code_artifact_text(code_text, block=block)

        self.assertEqual(formatted_html, formatted_markdown)
        self.assertIn(
            "prompt_extract = ChatPromptTemplate.from_template(\n"
            "    \"Extract the technical specifications from the following text:\\n\\n{text_input}\"\n"
            ")",
            formatted_markdown,
        )
        self.assertIn(
            "# The StrOutputParser() converts the LLM's message output to a simple string.\n"
            "extraction_chain = prompt_extract | llm | StrOutputParser()",
            formatted_markdown,
        )
        self.assertIn(
            "# The full chain passes the output of the extraction chain into the 'specifications'\n"
            "# variable for the transformation prompt.",
            formatted_markdown,
        )
        self.assertIn(
            "full_chain = (\n"
            "    {\"specifications\": extraction_chain}\n"
            "    | prompt_transform\n"
            "    | llm\n"
            "    | StrOutputParser()\n"
            ")",
            formatted_markdown,
        )

    def test_export_service_reflow_dedents_after_terminal_statement(self) -> None:
        code_text = (
            "full_parallel_chain = map_chain | synthesis_prompt | llm |\n"
            "StrOutputParser()\n"
            "# --- Run the Chain ---\n"
            "async def run_parallel_example(topic: str) -> None:\n"
            "\"\"\"\n"
            "Asynchronously invokes the parallel processing chain with a\n"
            "specific topic.\n"
            "\"\"\"\n"
            "if not llm:\n"
            "print(\"LLM not initialized. Cannot run example.\")\n"
            "return\n"
            "print(f\"running {topic}\")\n"
            "try:\n"
            "response = await full_parallel_chain.ainvoke(topic)\n"
            "print(response)\n"
            "except Exception as e:\n"
            "print(f\"error: {e}\")\n"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = service._reflow_code_artifact_text(code_text)

        self.assertIn("async def run_parallel_example(topic: str) -> None:\n    \"\"\"", formatted)
        self.assertIn(
            "    if not llm:\n"
            "        print(\"LLM not initialized. Cannot run example.\")\n"
            "        return\n"
            "    print(f\"running {topic}\")",
            formatted,
        )
        self.assertIn(
            "    try:\n"
            "        response = await full_parallel_chain.ainvoke(topic)\n"
            "        print(response)\n"
            "    except Exception as e:\n"
            "        print(f\"error: {e}\")",
            formatted,
        )

    def test_export_service_reflow_keeps_apostrophe_lines_from_collapsing_into_code(self) -> None:
        code_text = (
            "def unclear_handler(request: str) -> str:\n"
            "\"\"\"Handles requests that couldn't be delegated.\"\"\"\n"
            "print(\"\\n--- HANDLING UNCLEAR REQUEST ---\")\n"
            "return f\"Coordinator could not delegate request: '{request}'.\n"
            "Please clarify.\"\n"
            "# --- Define Coordinator Router Chain (equivalent to ADK\n"
            "coordinator's instruction) ---\n"
            "coordinator_router_prompt = ChatPromptTemplate.from_messages([\n"
            "(\"system\", \"\"\"Analyze the user's request and determine which\n"
            "specialist handler should process it.\n"
            "- If the request is related to booking flights or hotels,\n"
            "output 'booker'.\n"
            "ONLY output one word: 'booker', 'info', or 'unclear'.\"\"\"),\n"
            "(\"user\", \"{request}\")\n"
            "])\n"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = service._reflow_code_artifact_text(code_text)

        self.assertIn(
            "def unclear_handler(request: str) -> str:\n"
            "    \"\"\"Handles requests that couldn't be delegated.\"\"\"\n"
            "    print(\"\\n--- HANDLING UNCLEAR REQUEST ---\")",
            formatted,
        )
        self.assertIn(
            "    return f\"Coordinator could not delegate request: '{request}'. Please clarify.\"",
            formatted,
        )
        self.assertIn(
            "# --- Define Coordinator Router Chain (equivalent to ADK coordinator's instruction) ---",
            formatted,
        )
        self.assertIn(
            "(\"system\", \"\"\"Analyze the user's request and determine which specialist handler should process it.",
            formatted,
        )
        self.assertIn("- If the request is related to booking flights or hotels, output 'booker'.", formatted)
        self.assertIn("ONLY output one word: 'booker', 'info', or 'unclear'.", formatted)

    def test_export_service_reflow_keeps_docstring_section_labels_inside_function_scope(self) -> None:
        code_text = (
            "def booking_handler(request: str) -> str:\n"
            "\"\"\"\n"
            "Handles booking requests for flights and hotels.\n"
            "Args:\n"
            "request: The user's request for a booking.\n"
            "Returns:\n"
            "A confirmation message that the booking was handled.\n"
            "\"\"\"\n"
            "print(\"handled\")\n"
            "return \"ok\"\n"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = service._reflow_code_artifact_text(code_text)

        self.assertIn(
            "def booking_handler(request: str) -> str:\n"
            "    \"\"\"\n"
            "    Handles booking requests for flights and hotels.\n"
            "    Args:\n"
            "    request: The user's request for a booking.\n"
            "    Returns:\n"
            "    A confirmation message that the booking was handled.\n"
            "    \"\"\"\n"
            "    print(\"handled\")\n"
            "    return \"ok\"",
            formatted,
        )

    def test_export_service_reflow_splits_inline_call_keyword_arguments_after_open_paren(self) -> None:
        code_text = (
            "# Researcher 3: Carbon Capture\n"
            'researcher_agent_3 = LlmAgent( name="CarbonCaptureResearcher",\n'
            "model=GEMINI_MODEL,\n"
            'instruction="""You are an AI Research Assistant specializing in\n'
            "climate solutions.\n"
            '""",\n'
            '# --- 3. Define the Merger Agent (Runs *after* the parallel agents)\n'
            "---\n"
            'merger_agent = LlmAgent( name="SynthesisAgent",\n'
            "model=GEMINI_MODEL,\n"
            'instruction="""You are an AI Assistant responsible for combining research findings.\n'
            '""",\n'
            ")\n"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = service._reflow_code_artifact_text(code_text)

        self.assertIn(
            "# Researcher 3: Carbon Capture\n"
            "researcher_agent_3 = LlmAgent(\n"
            '    name="CarbonCaptureResearcher",\n'
            "    model=GEMINI_MODEL,\n"
            '    instruction="""You are an AI Research Assistant specializing in climate solutions.\n'
            '    """,\n'
            '    description="Researches carbon capture methods.",',
            formatted,
        )
        self.assertIn(
            '# --- 3. Define the Merger Agent (Runs *after* the parallel agents) ---',
            formatted,
        )
        self.assertIn(
            "merger_agent = LlmAgent(\n"
            '    name="SynthesisAgent",\n'
            "    model=GEMINI_MODEL,\n"
            '    instruction="""You are an AI Assistant responsible for combining research findings.\n'
            '    """,',
            formatted,
        )

    def test_export_service_does_not_treat_mixed_python_block_as_stable_structured_layout(self) -> None:
        lines = [
            "from langchain_core.runnables import Runnable, RunnableParallel, RunnablePassthrough",
            "try:",
            'llm: Optional[ChatOpenAI] = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)',
            "except Exception as e:",
            'summarize_chain: Runnable = (',
            '"summary": summarize_chain,',
            '"topic": RunnablePassthrough(),',
        ]

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertFalse(service._looks_like_stable_structured_code_layout(lines))

    def test_export_misalignment_ignores_orphan_targets_from_non_preferred_translation_runs(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="misalignment-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-misalignment-doc",
            source_path="/tmp/misalignment.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="misalignment-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Foreword",
            title_tgt="前言",
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="misalignment-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text="Hello world.",
            normalized_text="Hello world.",
            source_anchor=None,
            source_span_json={},
            parse_confidence=0.99,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="misalignment-sentence",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text="Hello world.",
            normalized_text="Hello world.",
            source_lang="en",
            sentence_status=SentenceStatus.TRANSLATED,
            translatable=True,
            nontranslatable_reason=None,
            source_anchor=None,
            source_span_json={},
            upstream_confidence=0.99,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        packet_translate = TranslationPacket(
            id="packet-translate",
            chapter_id=chapter.id,
            block_start_id=block.id,
            block_end_id=block.id,
            packet_type=PacketType.TRANSLATE,
            book_profile_version=1,
            chapter_brief_version=None,
            termbase_version=None,
            entity_snapshot_version=None,
            style_snapshot_version=None,
            packet_json={"current_blocks": [{"block_id": block.id, "sentence_ids": [sentence.id]}]},
            risk_score=0.1,
            status=PacketStatus.TRANSLATED,
            created_at=now,
            updated_at=now,
        )
        packet_retranslate = TranslationPacket(
            id="packet-retranslate",
            chapter_id=chapter.id,
            block_start_id=block.id,
            block_end_id=block.id,
            packet_type=PacketType.RETRANSLATE,
            book_profile_version=1,
            chapter_brief_version=None,
            termbase_version=None,
            entity_snapshot_version=None,
            style_snapshot_version=None,
            packet_json={"current_blocks": [{"block_id": block.id, "sentence_ids": [sentence.id]}]},
            risk_score=0.1,
            status=PacketStatus.TRANSLATED,
            created_at=now,
            updated_at=now,
        )
        run_translate = TranslationRun(
            id="run-translate",
            packet_id=packet_translate.id,
            model_name="test-model",
            model_config_json={},
            prompt_version="v1",
            status=RunStatus.SUCCEEDED,
            attempt=1,
            output_json={},
            token_in=1,
            token_out=1,
            cost_usd=0,
            latency_ms=1,
            created_at=now,
            updated_at=now,
        )
        run_retranslate = TranslationRun(
            id="run-retranslate",
            packet_id=packet_retranslate.id,
            model_name="test-model",
            model_config_json={},
            prompt_version="v1",
            status=RunStatus.SUCCEEDED,
            attempt=1,
            output_json={},
            token_in=1,
            token_out=1,
            cost_usd=0,
            latency_ms=1,
            created_at=now,
            updated_at=now,
        )
        target_old = TargetSegment(
            id="target-old",
            chapter_id=chapter.id,
            translation_run_id=run_translate.id,
            ordinal=1,
            segment_type=SegmentType.SENTENCE,
            text_zh="旧译文",
            confidence=0.9,
            final_status=TargetSegmentStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        target_new = TargetSegment(
            id="target-new",
            chapter_id=chapter.id,
            translation_run_id=run_retranslate.id,
            ordinal=1,
            segment_type=SegmentType.SENTENCE,
            text_zh="新译文",
            confidence=0.95,
            final_status=TargetSegmentStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[packet_translate, packet_retranslate],
            translation_runs=[run_translate, run_retranslate],
            target_segments=[target_old, target_new],
            alignment_edges=[
                AlignmentEdge(
                    id="edge-old",
                    sentence_id=sentence.id,
                    target_segment_id=target_old.id,
                    relation_type=RelationType.ONE_TO_ONE,
                    confidence=0.8,
                    created_by=ActorType.SYSTEM,
                    created_at=now,
                ),
                AlignmentEdge(
                    id="edge-new",
                    sentence_id=sentence.id,
                    target_segment_id=target_new.id,
                    relation_type=RelationType.ONE_TO_ONE,
                    confidence=0.95,
                    created_by=ActorType.SYSTEM,
                    created_at=now,
                ),
            ],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            evidence = service._build_export_misalignment_evidence(bundle)

        self.assertEqual(evidence.rendered_targets_by_sentence, {sentence.id: [target_new.id]})
        self.assertEqual(evidence.orphan_target_segment_ids, [])

    def test_export_service_merge_adjacent_code_blocks_dedupes_overlapping_prefix(self) -> None:
        previous = MergedRenderBlock(
            block_id="code-prev",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text=(
                "# Copyright (c) 2025 Marco Fago\n"
                "# This code is licensed under the MIT License.\n"
                "import uuid\n"
                "from google.adk.agents import Agent\n"
                "def booking_handler(request: str) -> str:\n"
                "\"\"\"\n"
            ),
            target_text=None,
            source_metadata={"recovery_flags": ["mixed_code_prose_split"]},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )
        current = MergedRenderBlock(
            block_id="code-current",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text=(
                "# Copyright (c) 2025 Marco Fago\n"
                "# This code is licensed under the MIT License.\n"
                "import uuid\n"
                "from google.adk.agents import Agent\n"
                "def booking_handler(request: str) -> str:\n"
                "\"\"\"\n"
                "Handles booking requests.\n"
                "\"\"\"\n"
                "return 'ok'\n"
            ),
            target_text=None,
            source_metadata={"recovery_flags": ["cross_page_repaired"]},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            merged = service._merge_adjacent_code_render_blocks(previous, current)

        self.assertEqual(merged.source_text.count("# Copyright (c) 2025 Marco Fago"), 1)
        self.assertIn("Handles booking requests.", merged.source_text)
        self.assertIn("export_code_overlap_deduped", merged.source_metadata["recovery_flags"])

    def test_export_service_merges_adjacent_code_blocks_using_previous_page_end(self) -> None:
        previous = MergedRenderBlock(
            block_id="code-prev-multipage",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text='researcher_agent_3 = LlmAgent(\nname="CarbonCaptureResearcher",',
            target_text=None,
            source_metadata={"source_page_start": 56, "source_page_end": 57},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )
        current = MergedRenderBlock(
            block_id="code-current-multipage",
            chapter_id="chapter-1",
            block_type=BlockType.CODE.value,
            render_mode="source_artifact_full_width",
            artifact_kind="code",
            title=None,
            source_text='model=GEMINI_MODEL,\ninstruction="""You are an AI Research Assistant.',
            target_text=None,
            source_metadata={"source_page_start": 58, "source_page_end": 58},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="代码保持原样",
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertTrue(service._should_merge_adjacent_code_blocks(previous, current))

    def test_render_blocks_infer_shell_fragment_target_from_parent_target(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="shell-fragment-target-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-shell-fragment-target",
            source_path="/tmp/shell-fragment-target.pdf",
            title="Routing",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="shell-fragment-target-chapter",
            document_id=document.id,
            ordinal=8,
            title_src="Chapter 2: Routing",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 37, "source_page_end": 37},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="shell-fragment-target-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                "pip install langchain langgraph google-cloud-aiplatform\n"
                "langchain-google-genai google-adk deprecated pydantic"
            ),
            normalized_text="",
            source_anchor="pdf://page/37#b266",
            source_span_json={
                "source_page_start": 37,
                "source_page_end": 37,
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "refresh_split_render_fragments": [
                    {
                        "block_type": "paragraph",
                        "source_text": (
                            "You will also need to set up your environment with your API key for the language\n"
                            "model you choose (e.g., OpenAI, Google Gemini, Anthropic)."
                        ),
                        "target_text": None,
                        "source_metadata": {
                            "source_page_start": 37,
                            "source_page_end": 37,
                            "pdf_block_role": "body",
                            "pdf_page_family": "body",
                            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                        },
                    }
                ],
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="shell-fragment-target-sentence",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=(
                "pip install langchain langgraph google-cloud-aiplatform langchain-google-genai google-adk "
                "deprecated pydantic You will also need to set up your environment with your API key for "
                "the language model you choose (e.g., OpenAI, Google Gemini, Anthropic)."
            ),
            normalized_text="",
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/37#s266",
            source_span_json={},
            upstream_confidence=0.94,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="shell-fragment-target-segment",
            chapter_id=chapter.id,
            translation_run_id="shell-fragment-target-run",
            ordinal=1,
            text_zh=(
                "pip install langchain langgraph google-cloud-aiplatform "
                "langchain-google-genai google-adk deprecated pydantic "
                "您还需要为所选语言模型（例如，OpenAI、Google Gemini、Anthropic）设置环境并配置API密钥。"
            ),
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="shell-fragment-target-edge",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(
            render_blocks[1].target_text,
            "您还需要为所选语言模型（例如，OpenAI、Google Gemini、Anthropic）设置环境并配置API密钥。",
        )

    def test_render_blocks_restore_refresh_split_fragment_repair_target_text(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="fragment-repair-target-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-fragment-repair-target",
            source_path="/tmp/fragment-repair-target.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="fragment-repair-target-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 28, "source_page_end": 28},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="fragment-repair-target-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text='print(final_result)',
            normalized_text="",
            source_anchor="pdf://page/28#b198",
            source_span_json={
                "source_page_start": 28,
                "source_page_end": 28,
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "refresh_split_render_fragments": [
                    {
                        "block_type": "paragraph",
                        "source_text": "This Python code demonstrates how to use the LangChain library to process text.",
                        "target_text": None,
                        "repair_target_text": "这段 Python 代码演示了如何使用 LangChain 库处理文本。",
                        "source_metadata": {
                            "source_page_start": 28,
                            "source_page_end": 28,
                            "pdf_block_role": "body",
                            "pdf_page_family": "body",
                        },
                    }
                ],
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[1].target_text, "这段 Python 代码演示了如何使用 LangChain 库处理文本。")

    def test_export_service_leaves_non_code_preformatted_text_unchanged(self) -> None:
        prose = (
            "I admit, I began as a skeptic.\n"
            "Plausibility, I've found, is often inversely proportional to one's own\n"
            "knowledge of a subject."
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            formatted = html.unescape(service._format_preformatted_text(prose))

        self.assertEqual(formatted, prose)

    def test_export_service_detects_prose_artifact_text_with_phrase_class_of(self) -> None:
        prose = (
            "I admit, I began as a skeptic. Plausibility, I've found, is often inversely proportional "
            "to one's own knowledge of a subject. Early models, for all their fluency, felt like they "
            "were operating with a kind of impostor syndrome, optimized for credibility over "
            "correctness. But then came the inflection point, a step-change brought about by a new "
            'class of "reasoning" models. Suddenly, we were getting a peek into a nascent form of cognition.'
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertTrue(service._looks_like_prose_artifact_text(prose))
        self.assertFalse(service._looks_like_code_artifact_text(prose))

    def test_export_service_does_not_detect_codeish_ocr_block_as_prose_artifact(self) -> None:
        codeish = (
            "# 1. Define the block of tasks to run in parallel.\n"
            "map chain = RunnableParallel(\n"
            "{\n"
            '"summary": summarize chain,\n'
            '"questions": questions chain,\n'
            '"topic": lambda x: x,\n'
            "}\n"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertFalse(service._looks_like_prose_artifact_text(codeish))

    def test_export_service_does_not_detect_single_strong_code_line_as_prose_artifact(self) -> None:
        codeish = 'def aggregator(state: State):\n"""Combine the joke and story into a single output"""'

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertFalse(service._looks_like_prose_artifact_text(codeish))

    def test_workflow_exports_merged_markdown_with_assets(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML)],
            extra_files={"OEBPS/images/agent-loop.png": b"fake-png-binary"},
        )

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=outdir)
                workflow.translate_document(document_id)
                with patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None):
                    export = workflow.export_document(document_id, ExportType.MERGED_MARKDOWN)

            markdown_path = Path(export.file_path)
            manifest_path = Path(export.manifest_path)
            self.assertTrue(markdown_path.exists())
            self.assertTrue(manifest_path.exists())
            markdown_text = markdown_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue((markdown_path.parent / "assets" / "OEBPS" / "images" / "agent-loop.png").exists())

        self.assertEqual(manifest["export_type"], "merged_markdown")
        self.assertEqual(manifest["markdown_path"], str(markdown_path))
        self.assertIn("# Business Strategy Handbook", markdown_text)
        self.assertIn("## Reading Map", markdown_text)
        self.assertIn("## Chapter 1:", markdown_text)
        self.assertIn("_Source title: Chapter One_", markdown_text)
        self.assertIn("![Agent loop architecture](assets/OEBPS/images/agent-loop.png)", markdown_text)
        self.assertIn("```python", markdown_text)
        self.assertIn('return "ok"', markdown_text)
        self.assertIn("| Tier | Latency |", markdown_text)
        self.assertIn("| Basic | Slow |", markdown_text)
        self.assertIn("https://example.com/agent-docs", markdown_text)

    def test_workflow_exports_rebuilt_epub_with_manifest_and_assets(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML)],
            extra_files={"OEBPS/images/agent-loop.png": b"fake-png-binary"},
        )

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=outdir)
                workflow.translate_document(document_id)
                with patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None):
                    export = workflow.export_document(document_id, ExportType.REBUILT_EPUB)

            epub_path = Path(export.file_path)
            manifest_path = Path(export.manifest_path)
            self.assertTrue(epub_path.exists())
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            with zipfile.ZipFile(epub_path) as archive:
                names = archive.namelist()
                nav_xhtml = archive.read("OEBPS/nav.xhtml").decode("utf-8")
                chapter_xhtml = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")

        self.assertIn("mimetype", names)
        self.assertIn("META-INF/container.xml", names)
        self.assertIn("OEBPS/content.opf", names)
        self.assertIn("OEBPS/styles/book.css", names)
        self.assertIn("OEBPS/text/chapter-001.xhtml", names)
        self.assertIn("OEBPS/assets/OEBPS/images/agent-loop.png", names)
        self.assertIn("Chapter One", nav_xhtml)
        self.assertIn("Agent loop architecture", chapter_xhtml)
        self.assertEqual(manifest["export_type"], "rebuilt_epub")
        self.assertEqual(manifest["renderer_kind"], "epub_spine_rebuilder")
        self.assertEqual(manifest["derived_from_exports"], ["merged_html", "merged_markdown"])
        self.assertEqual(manifest["contract_version"], 1)
        self.assertEqual(manifest["source_type"], "epub")
        self.assertIn("single_document_level_output_only", manifest["expected_limitations"])

    def test_export_service_rebuilt_epub_rejects_non_epub_source_document(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="rebuilt-epub-guard-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-rebuilt-epub-guard",
            source_path="/tmp/guard.pdf",
            title="Guard PDF",
            status=DocumentStatus.ACTIVE,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        bundle = DocumentExportBundle(
            document=document,
            book_profile=None,
            chapters=[],
        )
        repository = SimpleNamespace(load_document_bundle=lambda _document_id: bundle)
        service = ExportService(repository, output_root="/tmp/book-agent-exports")

        with self.assertRaises(ExportGateError) as exc_info:
            service.export_document_rebuilt_epub(document.id)

        self.assertIn("only available for EPUB source documents", str(exc_info.exception))

    def test_workflow_exports_rebuilt_pdf_from_merged_html_substrate(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CHAPTER_XHTML)],
        )

        def _fake_render(_service, html_path: Path, pdf_path: Path) -> None:
            self.assertTrue(Path(html_path).name.endswith(".html"))
            pdf_path.write_bytes(b"%PDF-1.4\n% rebuilt\n")

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=outdir)
                workflow.translate_document(document_id)
                with (
                    patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None),
                    patch.object(
                        ExportService,
                        "_render_rebuilt_pdf_from_html",
                        autospec=True,
                        side_effect=_fake_render,
                    ) as render_mock,
                ):
                    export = workflow.export_document(document_id, ExportType.REBUILT_PDF)

            pdf_path = Path(export.file_path)
            manifest_path = Path(export.manifest_path)
            self.assertTrue(pdf_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF-1.4"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        render_mock.assert_called_once()
        self.assertEqual(manifest["export_type"], "rebuilt_pdf")
        self.assertEqual(manifest["renderer_kind"], "html_print_renderer")
        self.assertEqual(manifest["derived_from_exports"], ["merged_html", "merged_markdown"])
        self.assertIn("not_page_faithful_to_source_pdf", manifest["expected_limitations"])
        self.assertIn("merged_html", manifest["derived_export_artifacts"])
        self.assertIn("merged_markdown", manifest["derived_export_artifacts"])

    def test_workflow_rebuilt_pdf_fails_closed_when_renderer_unavailable(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CHAPTER_XHTML)],
        )

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=outdir)
                workflow.translate_document(document_id)
                with (
                    patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None),
                    patch.object(
                        ExportService,
                        "_render_rebuilt_pdf_from_html",
                        autospec=True,
                        side_effect=ExportGateError("Rebuilt PDF renderer unavailable."),
                    ),
                ):
                    with self.assertRaises(ExportGateError) as exc_info:
                        workflow.export_document(document_id, ExportType.REBUILT_PDF)

        self.assertIn("renderer unavailable", str(exc_info.exception))

    def test_workflow_exports_merged_markdown_from_legacy_db_without_document_images_table(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STRUCTURED_ARTIFACT_XHTML)],
            extra_files={"OEBPS/images/agent-loop.png": b"fake-png-binary"},
        )
        Base.metadata.tables["document_images"].drop(self.engine)

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=outdir)
                workflow.translate_document(document_id)
                with patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None):
                    export = workflow.export_document(document_id, ExportType.MERGED_MARKDOWN)

            markdown_path = Path(export.file_path)
            self.assertTrue(markdown_path.exists())
            markdown_text = markdown_path.read_text(encoding="utf-8")

        self.assertIn("## Chapter 1:", markdown_text)
        self.assertIn("_Source title: Chapter One_", markdown_text)
        self.assertIn("![Agent loop architecture](assets/OEBPS/images/agent-loop.png)", markdown_text)

    def test_visible_merged_chapters_group_pdf_auxiliary_sections_under_real_top_level_titles(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-visible-merged-pdf",
            source_path="/tmp/sample.pdf",
            title="Sample PDF",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )

        def make_block(chapter_id: str, ordinal: int, block_type: BlockType, text: str, page: int) -> Block:
            return Block(
                id=f"{chapter_id}-{ordinal}",
                chapter_id=chapter_id,
                ordinal=ordinal,
                block_type=block_type,
                source_text=text,
                normalized_text=text,
                source_anchor=f"pdf://page/{page}#b{ordinal}",
                source_span_json={
                    "source_page_start": page,
                    "source_page_end": page,
                    "source_bbox_json": {"regions": [{"page_number": page, "bbox": [96.0, 96.0, 520.0, 144.0]}]},
                    "pdf_block_role": "heading" if block_type == BlockType.HEADING else "body",
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        def make_bundle(ordinal: int, title: str, page: int, extra_text: str | None = None) -> ChapterExportBundle:
            chapter_id = f"chapter-{ordinal}"
            chapter = Chapter(
                id=chapter_id,
                document_id=document.id,
                ordinal=ordinal,
                title_src=title,
                title_tgt=None,
                anchor_start=None,
                anchor_end=None,
                status=ChapterStatus.TRANSLATED,
                summary_version=None,
                risk_level=None,
                metadata_json={"source_page_start": page, "source_page_end": page, "href": f"pdf://page/{page}"},
                created_at=now,
                updated_at=now,
            )
            blocks = [make_block(chapter_id, 1, BlockType.HEADING, title, page)]
            if extra_text:
                blocks.append(make_block(chapter_id, 2, BlockType.PARAGRAPH, extra_text, page))
            return ChapterExportBundle(
                chapter=chapter,
                document=document,
                book_profile=None,
                blocks=blocks,
                document_images=[],
                sentences=[],
                packets=[],
                translation_runs=[],
                target_segments=[],
                alignment_edges=[],
                review_issues=[],
                quality_summary=None,
                active_snapshots=[],
                audit_events=[],
            )

        bundle = DocumentExportBundle(
            document=document,
            book_profile=None,
            chapters=[
                make_bundle(1, "Acknowledgment", 1),
                make_bundle(2, "Foreword", 5),
                make_bundle(3, "Chapter 17.", 15, "stray early heading"),
                make_bundle(4, "Conclusion", 20, "aux section"),
                make_bundle(5, "References", 20, "aux refs"),
                make_bundle(6, "Chapter 1: Prompt Chaining", 21, "real chapter one"),
                make_bundle(7, "References", 33, "chapter one refs"),
                make_bundle(8, "Chapter 2: Routing", 34, "real chapter two"),
                make_bundle(9, "Appendix A: Advanced Prompting", 347, "appendix content"),
                make_bundle(10, "Techniques", 347, "appendix subsection"),
            ],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            visible = service._visible_merged_chapters(bundle)

        self.assertEqual(
            [title for _ordinal, _chapter_bundle, _render_blocks, title in visible],
            [
                "致谢",
                "前言",
                "Chapter 1: Prompt Chaining",
                "Chapter 2: Routing",
                "Appendix A: Advanced Prompting",
            ],
        )
        self.assertNotIn(
            "Chapter 17.",
            [block.source_text for block in visible[1][2]],
        )
        self.assertIn(
            "References",
            [block.source_text for block in visible[2][2]],
        )
        self.assertIn(
            "Techniques",
            [block.source_text for block in visible[4][2]],
        )

    def test_visible_merged_chapters_keep_academic_paper_sections_separate(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="dddddddd-dddd-4ddd-8ddd-aaaaaaaaaaaa",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-academic-paper-visible",
            source_path="/tmp/paper.pdf",
            title="Academic Paper",
            status=DocumentStatus.EXPORTED,
            metadata_json={"pdf_profile": {"recovery_lane": "academic_paper", "layout_risk": "medium"}},
            created_at=now,
            updated_at=now,
        )

        def make_block(chapter_id: str, ordinal: int, block_type: BlockType, text: str, page: int) -> Block:
            return Block(
                id=f"{chapter_id}-{ordinal}",
                chapter_id=chapter_id,
                ordinal=ordinal,
                block_type=block_type,
                source_text=text,
                normalized_text=text,
                source_anchor=f"pdf://page/{page}#b{ordinal}",
                source_span_json={
                    "source_page_start": page,
                    "source_page_end": page,
                    "pdf_page_family": "body",
                    "source_bbox_json": {"regions": [{"page_number": page, "bbox": [96.0, 96.0, 520.0, 144.0]}]},
                    "pdf_block_role": "heading" if block_type == BlockType.HEADING else "body",
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        def make_bundle(ordinal: int, title: str, page: int, body: str | None = None) -> ChapterExportBundle:
            chapter_id = f"paper-chapter-{ordinal}"
            chapter = Chapter(
                id=chapter_id,
                document_id=document.id,
                ordinal=ordinal,
                title_src=title,
                title_tgt=None,
                anchor_start=None,
                anchor_end=None,
                status=ChapterStatus.TRANSLATED,
                summary_version=None,
                risk_level=None,
                metadata_json={"source_page_start": page, "source_page_end": page},
                created_at=now,
                updated_at=now,
            )
            blocks = [make_block(chapter_id, 1, BlockType.HEADING, title, page)]
            if body:
                blocks.append(make_block(chapter_id, 2, BlockType.PARAGRAPH, body, page))
            return ChapterExportBundle(
                chapter=chapter,
                document=document,
                book_profile=None,
                blocks=blocks,
                document_images=[],
                sentences=[],
                packets=[],
                translation_runs=[],
                target_segments=[],
                alignment_edges=[],
                review_issues=[],
                quality_summary=None,
                active_snapshots=[],
                audit_events=[],
            )

        bundle = DocumentExportBundle(
            document=document,
            book_profile=None,
            chapters=[
                make_bundle(1, "Forming Effective Human-AI Teams", 1, "authors and abstract"),
                make_bundle(2, "1 Introduction", 1, "intro"),
                make_bundle(3, "2 Related Work", 2, "related"),
                make_bundle(4, "3 Problem Formulation", 3, "problem"),
                make_bundle(5, "4 Approach", 4, "approach"),
                make_bundle(6, "5 Experiments", 5, "experiments"),
                make_bundle(7, "6 Conclusion", 6, "conclusion"),
                make_bundle(8, "References", 7, "refs"),
            ],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            visible = service._visible_merged_chapters(bundle)

        self.assertEqual(
            [title for _ordinal, _chapter_bundle, _render_blocks, title in visible],
            [
                "Forming Effective Human-AI Teams",
                "1 Introduction",
                "2 Related Work",
                "3 Problem Formulation",
                "4 Approach",
                "5 Experiments",
                "6 Conclusion",
                "References",
            ],
        )

    def test_visible_merged_chapters_recognize_underscore_numbered_book_pdf_titles(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="underscore-book-visible-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-underscore-book-visible",
            source_path="/tmp/underscore-book-visible.pdf",
            title="Agentic Design Patterns",
            status=DocumentStatus.EXPORTED,
            metadata_json={"pdf_profile": {"recovery_lane": "book_pdf", "layout_risk": "medium"}},
            created_at=now,
            updated_at=now,
        )

        def make_block(chapter_id: str, ordinal: int, text: str, page: int) -> Block:
            return Block(
                id=f"{chapter_id}-{ordinal}",
                chapter_id=chapter_id,
                ordinal=ordinal,
                block_type=BlockType.HEADING,
                source_text=text,
                normalized_text=text,
                source_anchor=f"pdf://page/{page}#b{ordinal}",
                source_span_json={
                    "source_page_start": page,
                    "source_page_end": page,
                    "pdf_page_family": "body",
                    "source_bbox_json": {"regions": [{"page_number": page, "bbox": [96.0, 96.0, 520.0, 144.0]}]},
                    "pdf_block_role": "heading",
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        def make_bundle(ordinal: int, title: str, page: int, body: str | None = None) -> ChapterExportBundle:
            chapter_id = f"underscore-book-chapter-{ordinal}"
            chapter = Chapter(
                id=chapter_id,
                document_id=document.id,
                ordinal=ordinal,
                title_src=title,
                title_tgt=None,
                anchor_start=None,
                anchor_end=None,
                status=ChapterStatus.TRANSLATED,
                summary_version=None,
                risk_level=None,
                metadata_json={"source_page_start": page, "source_page_end": page},
                created_at=now,
                updated_at=now,
            )
            blocks = [make_block(chapter_id, 1, title, page)]
            if body:
                blocks.append(
                    Block(
                        id=f"{chapter_id}-2",
                        chapter_id=chapter_id,
                        ordinal=2,
                        block_type=BlockType.PARAGRAPH,
                        source_text=body,
                        normalized_text=body,
                        source_anchor=f"pdf://page/{page}#b2",
                        source_span_json={
                            "source_page_start": page,
                            "source_page_end": page,
                            "pdf_page_family": "body",
                            "source_bbox_json": {"regions": [{"page_number": page, "bbox": [96.0, 160.0, 520.0, 220.0]}]},
                            "pdf_block_role": "body",
                        },
                        parse_confidence=0.95,
                        protected_policy=ProtectedPolicy.TRANSLATE,
                        status=ArtifactStatus.ACTIVE,
                        created_at=now,
                        updated_at=now,
                    )
                )
            return ChapterExportBundle(
                chapter=chapter,
                document=document,
                book_profile=None,
                blocks=blocks,
                document_images=[],
                sentences=[],
                packets=[],
                translation_runs=[],
                target_segments=[],
                alignment_edges=[],
                review_issues=[],
                quality_summary=None,
                active_snapshots=[],
                audit_events=[],
            )

        bundle = DocumentExportBundle(
            document=document,
            book_profile=None,
            chapters=[
                make_bundle(1, "Dedication", 1, "thanks"),
                make_bundle(2, "Foreword", 5, "foreword"),
                make_bundle(3, "Introduction", 8, "introduction"),
                make_bundle(4, "Conclusion", 20, "intro conclusion"),
                make_bundle(5, "References", 20, "intro refs"),
                make_bundle(6, "Chapter 1_ Prompt Chaining", 21, "chapter one"),
                make_bundle(7, "References", 33, "chapter one refs"),
                make_bundle(8, "Chapter 2_ Routing", 34, "chapter two"),
                make_bundle(9, "Chapter 10_ Model Context Protocol (MCP)", 165, "chapter ten"),
                make_bundle(10, "Conclusion", 180, "chapter ten conclusion"),
                make_bundle(11, "References", 180, "chapter ten refs"),
                make_bundle(12, "Chapter 21_ Exploration and Discovery", 333, "chapter twenty-one"),
                make_bundle(13, "References", 346, "chapter twenty-one refs"),
                make_bundle(14, "Appendix A: Advanced Prompting Techniques", 347, "appendix a"),
                make_bundle(15, "Introduction to Prompting", 347, "appendix a intro"),
                make_bundle(16, "Appendix E - AI Agents on the CLI", 397, "appendix e"),
                make_bundle(17, "Introduction", 397, "appendix e intro"),
                make_bundle(18, "Appendix G - Coding Agents", 417, "appendix g"),
                make_bundle(19, "Glossary", 428, "glossary"),
            ],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            visible = service._visible_merged_chapters(bundle)

        self.assertEqual(
            [title for _ordinal, _chapter_bundle, _render_blocks, title in visible],
            [
                "致谢",
                "前言",
                "介绍",
                "Chapter 1_ Prompt Chaining",
                "Chapter 2_ Routing",
                "Chapter 10_ Model Context Protocol (MCP)",
                "Chapter 21_ Exploration and Discovery",
                "Appendix A: Advanced Prompting Techniques",
                "Appendix E - AI Agents on the CLI",
                "Appendix G - Coding Agents",
            ],
        )

    def test_merged_titles_fall_back_when_first_heading_target_looks_like_prose(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="abababab-abab-4bab-8bab-abababababab",
            source_type=SourceType.EPUB,
            file_fingerprint="fingerprint-merged-title-fallback",
            source_path="/tmp/merged-title-fallback.epub",
            title="Merged Title Fallback",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
            document_id=document.id,
            ordinal=1,
            title_src="Introduction",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"href": "intro.xhtml"},
            created_at=now,
            updated_at=now,
        )
        chapter_bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )
        suspicious_heading = MergedRenderBlock(
            block_id="heading-1",
            chapter_id=chapter.id,
            block_type=BlockType.HEADING.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title="Introduction",
            source_text="Preface",
            target_text=(
                "欢迎阅读《智能体设计模式：构建智能系统的实践指南》。纵观现代人工智能的发展图景，"
                "我们可以清晰地看到其演进轨迹：从简单的反应式程序，逐步发展为能够理解上下文、做出决策、"
                "并与其环境及其他系统动态交互的精密自主实体。这些正是智能体及其所构成的智能体系统。"
            ),
            source_metadata={},
            source_sentence_ids=["s1"],
            target_segment_ids=["t1"],
            is_expected_source_only=False,
            notice=None,
        )
        paragraph_block = MergedRenderBlock(
            block_id="paragraph-1",
            chapter_id=chapter.id,
            block_type=BlockType.PARAGRAPH.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title=None,
            source_text="Welcome to Agentic Design Patterns.",
            target_text="欢迎阅读《智能体设计模式》。",
            source_metadata={},
            source_sentence_ids=["s2"],
            target_segment_ids=["t2"],
            is_expected_source_only=False,
            notice=None,
        )
        bundle = DocumentExportBundle(
            document=document,
            book_profile=None,
            chapters=[chapter_bundle],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[suspicious_heading, paragraph_block],
            ):
                visible = service._visible_merged_chapters(bundle)
                merged_html = service._build_merged_document_html(bundle)

        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0][3], "Introduction")
        self.assertIn("Chapter 1", merged_html)
        self.assertIn(">Introduction</h2>", merged_html)
        self.assertNotIn("Chapter 1</span>欢迎阅读《智能体设计模式", merged_html)

    def test_book_pdf_prose_like_intro_heading_is_demoted_and_title_falls_back_to_localized_frontmatter(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="frontmatter-prose-heading-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-frontmatter-prose-heading",
            source_path="/tmp/frontmatter-prose-heading.pdf",
            title="Introduction",
            status=DocumentStatus.EXPORTED,
            metadata_json={"pdf_profile": {"recovery_lane": "book_pdf", "layout_risk": "medium"}},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="frontmatter-prose-heading-chapter",
            document_id=document.id,
            ordinal=3,
            title_src="Introduction",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 8, "source_page_end": 19},
            created_at=now,
            updated_at=now,
        )
        chapter_bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )
        suspicious_heading = MergedRenderBlock(
            block_id="preface-heading",
            chapter_id=chapter.id,
            block_type=BlockType.HEADING.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title="Introduction",
            source_text="Preface",
            target_text=(
                "欢迎阅读《智能体设计模式：构建智能系统的实践指南》。纵观现代人工智能的发展图景，"
                "我们可以清晰地看到其演进轨迹：从简单的反应式程序，逐步发展为能够理解上下文、做出决策、"
                "并与其环境及其他系统动态交互的精密自主实体。这些正是智能体及其所构成的智能体系统。"
            ),
            source_metadata={"pdf_page_family": "body", "pdf_block_role": "heading"},
            source_sentence_ids=["s1"],
            target_segment_ids=["t1"],
            is_expected_source_only=False,
            notice=None,
        )
        section_heading = MergedRenderBlock(
            block_id="intro-section-heading",
            chapter_id=chapter.id,
            block_type=BlockType.HEADING.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title=None,
            source_text="What are Agentic Systems?",
            target_text="什么是智能体系统？",
            source_metadata={"pdf_page_family": "body", "pdf_block_role": "heading"},
            source_sentence_ids=["s2"],
            target_segment_ids=["t2"],
            is_expected_source_only=False,
            notice=None,
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            repaired = service._repair_book_pdf_render_blocks(chapter_bundle, 0, suspicious_heading)
            self.assertEqual(len(repaired), 1)
            self.assertEqual(repaired[0].block_type, BlockType.PARAGRAPH.value)
            self.assertEqual(repaired[0].render_mode, "zh_primary_with_optional_source")

            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[repaired[0], section_heading],
            ):
                visible = service._visible_merged_chapters(
                    DocumentExportBundle(document=document, book_profile=None, chapters=[chapter_bundle])
                )
                merged_html = service._build_merged_document_html(
                    DocumentExportBundle(document=document, book_profile=None, chapters=[chapter_bundle])
                )

        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0][3], "介绍")
        self.assertIn("Chapter 1", merged_html)
        self.assertIn(">介绍</h2>", merged_html)
        self.assertNotIn("Chapter 1</div><h2>什么是智能体系统？</h2>", merged_html)

    def test_export_service_does_not_treat_academic_prose_as_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="ffffffff-eeee-4eee-8eee-eeeeeeeeeeee",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-academic-prose",
            source_path="/tmp/paper.pdf",
            title="Academic Paper",
            status=DocumentStatus.EXPORTED,
            metadata_json={"pdf_profile": {"recovery_lane": "academic_paper", "layout_risk": "medium"}},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-academic-prose",
            document_id=document.id,
            ordinal=2,
            title_src="1 Introduction",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 1, "source_page_end": 1},
            created_at=now,
            updated_at=now,
        )

        def make_block(ordinal: int, block_type: BlockType, text: str, role: str = "body") -> Block:
            return Block(
                id=f"academic-prose-{ordinal}",
                chapter_id=chapter.id,
                ordinal=ordinal,
                block_type=block_type,
                source_text=text,
                normalized_text=text,
                source_anchor=f"pdf://page/1#b{ordinal}",
                source_span_json={
                    "source_page_start": 1,
                    "source_page_end": 1,
                    "pdf_page_family": "body",
                    "source_bbox_json": {"regions": [{"page_number": 1, "bbox": [72.0, 96.0, 530.0, 260.0]}]},
                    "pdf_block_role": role,
                },
                parse_confidence=0.9,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[
                make_block(1, BlockType.HEADING, "1 Introduction", role="heading"),
                make_block(
                    2,
                    BlockType.PARAGRAPH,
                    "In recent years, there has been growing interest in optimiz-\n"
                    "ing the performance of human-AI teams—teams consisting of\n"
                    "both a human expert and a classiﬁer [Hemmer et al., 2021]—\n"
                    "by allocating a subset of the instances to a single human\n"
                    "expert [De et al., 2021; Keswani et al., 2021].",
                ),
            ],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(render_blocks[1].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[1].artifact_kind)

    def test_export_service_splits_academic_paper_frontmatter_and_abstract(self) -> None:
        block = MergedRenderBlock(
            block_id="paper-frontmatter",
            chapter_id="chapter-paper",
            block_type=BlockType.PARAGRAPH.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title=None,
            source_text=(
                "Patrick Hemmer1, Sebastian Schellhammer2\n"
                "Karlsruhe Institute of Technology\n"
                "patrick@example.com\n"
                "Abstract Machine learning models are increasingly used in application domains."
            ),
            target_text=(
                "Patrick Hemmer1，Sebastian Schellhammer2\n"
                "卡尔斯鲁厄理工学院\n"
                "patrick@example.com\n"
                "摘要 机器学习模型正越来越多地应用于各类场景。"
            ),
            source_metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            source_sentence_ids=["s1"],
            target_segment_ids=["t1"],
            is_expected_source_only=False,
            notice=None,
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            fragments = service._split_academic_paper_frontmatter_block(block)

        self.assertEqual([fragment.block_type for fragment in fragments], ["paragraph", "heading", "paragraph"])
        self.assertIn("Karlsruhe Institute of Technology", fragments[0].source_text)
        self.assertEqual(fragments[1].target_text, "摘要")
        self.assertIn("机器学习模型正越来越多地应用于各类场景。", fragments[2].target_text or "")

    def test_export_service_splits_abstract_only_paragraph_into_heading_and_body(self) -> None:
        block = MergedRenderBlock(
            block_id="paper-abstract-only",
            chapter_id="chapter-paper",
            block_type=BlockType.PARAGRAPH.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title=None,
            source_text="Abstract Machine learning models are increasingly used in application domains.",
            target_text="摘要 机器学习模型正越来越多地应用于各类场景。",
            source_metadata={"pdf_page_family": "body", "pdf_block_role": "body"},
            source_sentence_ids=["s2"],
            target_segment_ids=["t2"],
            is_expected_source_only=False,
            notice=None,
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            fragments = service._split_academic_paper_frontmatter_block(block)

        self.assertEqual([fragment.block_type for fragment in fragments], ["heading", "paragraph"])
        self.assertEqual(fragments[0].target_text, "摘要")
        self.assertIn("机器学习模型正越来越多地应用于各类场景。", fragments[1].target_text or "")

    def test_export_service_does_not_treat_wrapped_book_prose_as_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="book-prose-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-book-prose-guard",
            source_path="/tmp/book-prose.pdf",
            title="Acknowledgment Sample",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-book-prose",
            document_id=document.id,
            ordinal=1,
            title_src="Acknowledgment",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 3, "source_page_end": 3},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="block-book-prose",
            chapter_id=chapter.id,
            ordinal=2,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "I am deeply indebted to the many talented people who helped bring this book to life. My\n"
                "heartfelt thanks go to Marco Fago for his immense contributions, from code and\n"
                "diagrams to reviewing the entire text. I'm also grateful to Mahtab Syed for his coding work\n"
                "and to Ankita Guha for her incredibly detailed feedback on so many chapters."
            ),
            normalized_text="",
            source_anchor="pdf://page/3#b2",
            source_span_json={
                "source_page_start": 3,
                "source_page_end": 3,
                "source_bbox_json": {"regions": [{"page_number": 3, "bbox": [94.0, 696.0, 723.0, 860.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)

    def test_render_blocks_merge_multiline_heading_fragments_from_historical_pdf_export(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="heading-merge-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-heading-merge",
            source_path="/tmp/heading-merge.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={"pdf_profile": {"recovery_lane": "book_pdf"}},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-heading-merge",
            document_id=document.id,
            ordinal=1,
            title_src="Foreword",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 6, "source_page_end": 6},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="heading-block-1",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="A Thought Leader's Perspective: Power",
                normalized_text="",
                source_anchor="pdf://page/6#b1",
                source_span_json={
                    "source_page_start": 6,
                    "source_page_end": 6,
                    "source_bbox_json": {"regions": [{"page_number": 6, "bbox": [94.0, 104.0, 701.0, 132.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 31,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="heading-block-2",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.HEADING,
                source_text="and Responsibility",
                normalized_text="",
                source_anchor="pdf://page/6#b2",
                source_span_json={
                    "source_page_start": 6,
                    "source_page_end": 6,
                    "source_bbox_json": {"regions": [{"page_number": 6, "bbox": [95.0, 144.0, 382.0, 176.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 32,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        sentences = [
            Sentence(
                id="heading-sentence-1",
                block_id="heading-block-1",
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=1,
                source_text="A Thought Leader's Perspective: Power",
                normalized_text="A Thought Leader's Perspective: Power",
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor="pdf://page/6#s1",
                source_span_json={},
                upstream_confidence=0.95,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            ),
            Sentence(
                id="heading-sentence-2",
                block_id="heading-block-2",
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=1,
                source_text="and Responsibility",
                normalized_text="and Responsibility",
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor="pdf://page/6#s2",
                source_span_json={},
                upstream_confidence=0.95,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            ),
        ]
        target_segments = [
            TargetSegment(
                id="heading-target-1",
                chapter_id=chapter.id,
                translation_run_id="heading-run",
                ordinal=1,
                text_zh="思想领袖视角：力量",
                segment_type="sentence",
                confidence=0.95,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            ),
            TargetSegment(
                id="heading-target-2",
                chapter_id=chapter.id,
                translation_run_id="heading-run",
                ordinal=2,
                text_zh="与责任",
                segment_type="sentence",
                confidence=0.95,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            ),
        ]
        alignment_edges = [
            AlignmentEdge(
                id="heading-edge-1",
                sentence_id="heading-sentence-1",
                target_segment_id="heading-target-1",
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.95,
                created_by=ActorType.MODEL,
                created_at=now,
            ),
            AlignmentEdge(
                id="heading-edge-2",
                sentence_id="heading-sentence-2",
                target_segment_id="heading-target-2",
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.95,
                created_by=ActorType.MODEL,
                created_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=sentences,
            packets=[],
            translation_runs=[],
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.HEADING.value)
        self.assertEqual(render_blocks[0].source_text, "A Thought Leader's Perspective: Power and Responsibility")
        self.assertEqual(render_blocks[0].target_text, "思想领袖视角：力量与责任")
        self.assertIn("export_multiline_heading_merged", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_treat_prose_like_code_block_with_targets_as_translated_paragraph(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="prose-code-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-prose-code",
            source_path="/tmp/prose-code.pdf",
            title="Acknowledgment",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-prose-code",
            document_id=document.id,
            ordinal=1,
            title_src="Acknowledgment",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 2, "source_page_end": 2},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="prose-code-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                "I am deeply indebted to the many talented people who helped bring this book to life.\n"
                "My heartfelt thanks go to Marco Fago for his immense contributions."
            ),
            normalized_text="",
            source_anchor="pdf://page/2#b1",
            source_span_json={
                "source_page_start": 2,
                "source_page_end": 2,
                "source_bbox_json": {"regions": [{"page_number": 2, "bbox": [94.0, 720.0, 724.0, 812.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="prose-code-sentence",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/2#s1",
            source_span_json={},
            upstream_confidence=0.7,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="prose-code-target",
            chapter_id=chapter.id,
            translation_run_id="prose-code-run",
            ordinal=1,
            text_zh="我衷心感谢众多才华横溢的伙伴们，正是他们的帮助让这本书得以问世。我也特别感谢 Marco Fago 所做出的巨大贡献。",
            segment_type="sentence",
            confidence=0.92,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="prose-code-edge",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.92,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn("我衷心感谢众多才华横溢的伙伴们", render_blocks[0].target_text or "")

    def test_render_blocks_honor_prose_artifact_repair_metadata_and_skip_continuation(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="repair-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-repair-doc",
            source_path="/tmp/repair-doc.pdf",
            title="Acknowledgment",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-repair-doc",
            document_id=document.id,
            ordinal=1,
            title_src="Acknowledgment",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 2, "source_page_end": 3},
            created_at=now,
            updated_at=now,
        )
        lead_block = Block(
            id="repair-lead-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text="A special thanks to Yingchao Huang for being a brilliant AI engineer with a great career ahead of you,",
            normalized_text="",
            source_anchor="pdf://page/2#b1",
            source_span_json={
                "source_page_start": 2,
                "source_page_end": 2,
                "source_bbox_json": {"regions": [{"page_number": 2, "bbox": [94.0, 880.0, 718.0, 920.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "repair_source_text": (
                    "A special thanks to Yingchao Huang for being a brilliant AI engineer with a great career ahead of you, "
                    "Hann Wang for challenging me to return to my interest in Agents after an initial interest in 1994, "
                    "and to Lee Boonstra for your amazing work on prompt engineering."
                ),
                "repair_target_text": (
                    "特别感谢 Yingchao Huang，你是一位才华出众的 AI 工程师，前途无量；"
                    "感谢 Hann Wang，在我自 1994 年萌生最初兴趣之后，又激发我重新投入对智能体的关注；"
                    "也感谢 Lee Boonstra 在提示工程方面所做的出色工作。"
                ),
                "repair_block_type": "paragraph",
                "repair_skip_block_ids": ["repair-continuation-block"],
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        continuation_block = Block(
            id="repair-continuation-block",
            chapter_id=chapter.id,
            ordinal=2,
            block_type=BlockType.PARAGRAPH,
            source_text="and to Lee Boonstra for your amazing work on prompt engineering.",
            normalized_text="",
            source_anchor="pdf://page/3#b2",
            source_span_json={
                "source_page_start": 3,
                "source_page_end": 3,
                "source_bbox_json": {"regions": [{"page_number": 3, "bbox": [94.0, 104.0, 718.0, 140.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[lead_block, continuation_block],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn("Hann Wang", render_blocks[0].source_text)
        self.assertIn("Lee Boonstra 在提示工程方面所做的出色工作", render_blocks[0].target_text or "")

    def test_render_blocks_merge_translated_prose_artifact_continuation_after_paragraph(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="prose-cont-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-prose-cont-doc",
            source_path="/tmp/prose-cont-doc.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-prose-cont-doc",
            document_id=document.id,
            ordinal=2,
            title_src="Foreword",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 10, "source_page_end": 10},
            created_at=now,
            updated_at=now,
        )
        lead_block = Block(
            id="prose-cont-lead",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "While the chapters are ordered to build concepts progressively, feel free to use the book "
                "as a reference, jumping to chapters that address specific challenges you face in your own "
                "agent development projects. The appendices provide a comprehensive look at advanced "
                "prompting techniques, principles for applying AI agents in real-world"
            ),
            normalized_text="",
            source_anchor="pdf://page/10#b69",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "source_bbox_json": {"regions": [{"page_number": 10, "bbox": [94.0, 516.0, 724.0, 642.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 69,
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        continuation_block = Block(
            id="prose-cont-tail",
            chapter_id=chapter.id,
            ordinal=2,
            block_type=BlockType.CODE,
            source_text=(
                "environments, and an overview of essential agentic frameworks. To complement this,\n"
                "practical online-only tutorials are included, offering step-by-step guidance on building\n"
                "agents with specific platforms like AgentSpace and for the command-line interface. The\n"
                "emphasis throughout is on practical application; we strongly encourage you to run the\n"
                "code examples, experiment with them, and adapt them to build your own intelligent\n"
                "systems on your chosen canvas."
            ),
            normalized_text="",
            source_anchor="pdf://page/10#b70",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "source_bbox_json": {"regions": [{"page_number": 10, "bbox": [94.0, 648.0, 724.0, 790.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "reading_order_index": 70,
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        lead_sentence = Sentence(
            id="prose-cont-lead-sentence",
            block_id=lead_block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=lead_block.source_text,
            normalized_text=lead_block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/10#s69",
            source_span_json={},
            upstream_confidence=0.95,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        continuation_sentence = Sentence(
            id="prose-cont-tail-sentence",
            block_id=continuation_block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=continuation_block.source_text,
            normalized_text=continuation_block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/10#s70",
            source_span_json={},
            upstream_confidence=0.7,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        lead_target = TargetSegment(
            id="prose-cont-lead-target",
            chapter_id=chapter.id,
            translation_run_id="prose-cont-run",
            ordinal=1,
            text_zh="尽管本书各章节按概念递进顺序编排，但您完全可以将其作为参考工具使用，直接跳转到与您自身智能体开发项目中具体挑战相关的章节。附录部分则全面探讨了高级提示技术，以及在实际场景中应用 AI 智能体的核心原则，",
            segment_type="sentence",
            confidence=0.92,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        continuation_target = TargetSegment(
            id="prose-cont-tail-target",
            chapter_id=chapter.id,
            translation_run_id="prose-cont-run",
            ordinal=2,
            text_zh="并概述了关键的智能体框架。除此之外，书中还配有仅在线提供的实操教程，逐步讲解如何在 AgentSpace 等平台以及命令行界面上构建智能体。全书始终强调实践应用；我们强烈建议您亲自运行这些代码示例，动手实验，并将其改造成适合自己画布的智能系统。",
            segment_type="sentence",
            confidence=0.92,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        lead_edge = AlignmentEdge(
            id="prose-cont-lead-edge",
            sentence_id=lead_sentence.id,
            target_segment_id=lead_target.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.92,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        continuation_edge = AlignmentEdge(
            id="prose-cont-tail-edge",
            sentence_id=continuation_sentence.id,
            target_segment_id=continuation_target.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.92,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[lead_block, continuation_block],
            document_images=[],
            sentences=[lead_sentence, continuation_sentence],
            packets=[],
            translation_runs=[],
            target_segments=[lead_target, continuation_target],
            alignment_edges=[lead_edge, continuation_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn("essential agentic frameworks", render_blocks[0].source_text)
        self.assertIn("关键的智能体框架", render_blocks[0].target_text or "")
        self.assertIn(
            "export_prose_artifact_continuation_merged",
            list(render_blocks[0].source_metadata.get("recovery_flags") or []),
        )

    def test_pdf_prose_artifact_repair_service_translates_and_hides_continuation_blocks(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="11111111-1111-4111-8111-111111111111",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-repair-service-doc",
            source_path="/tmp/repair-service-doc.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="22222222-2222-4222-8222-222222222222",
            document_id=document.id,
            ordinal=2,
            title_src="Foreword",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 10, "source_page_end": 10},
            created_at=now,
            updated_at=now,
        )
        lead_block = Block(
            id="33333333-3333-4333-8333-333333333333",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "While the chapters are ordered to build concepts progressively, feel free to use the book "
                "as a reference, jumping to chapters that address specific challenges you face in your own "
                "agent development projects. The appendices provide a comprehensive look at advanced "
                "prompting techniques, principles for applying AI agents in real-world"
            ),
            normalized_text="",
            source_anchor="pdf://page/10#b69",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "source_bbox_json": {"regions": [{"page_number": 10, "bbox": [94.0, 516.0, 724.0, 642.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 69,
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        continuation_block = Block(
            id="44444444-4444-4444-8444-444444444444",
            chapter_id=chapter.id,
            ordinal=2,
            block_type=BlockType.CODE,
            source_text=(
                "environments, and an overview of essential agentic frameworks. To complement this,\n"
                "practical online-only tutorials are included, offering step-by-step guidance on building\n"
                "agents with specific platforms like AgentSpace and for the command-line interface."
            ),
            normalized_text="",
            source_anchor="pdf://page/10#b70",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "source_bbox_json": {"regions": [{"page_number": 10, "bbox": [94.0, 648.0, 724.0, 790.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "reading_order_index": 70,
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        lead_sentence = Sentence(
            id="55555555-5555-4555-8555-555555555555",
            block_id=lead_block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=lead_block.source_text,
            normalized_text=lead_block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/10#s69",
            source_span_json={},
            upstream_confidence=0.95,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        continuation_sentence = Sentence(
            id="66666666-6666-4666-8666-666666666666",
            block_id=continuation_block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=continuation_block.source_text,
            normalized_text=continuation_block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/10#s70",
            source_span_json={},
            upstream_confidence=0.7,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="77777777-7777-4777-8777-777777777777",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="88888888-8888-4888-8888-888888888888",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Foreword"], "summary": "Foreword summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="99999999-9999-4999-8999-999999999999",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add_all([lead_block, continuation_block])
            session.flush()
            session.add_all([lead_sentence, continuation_sentence])
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.apply(document.id)
            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(result.repaired_chain_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 1)
        self.assertIn("ZH::While the chapters are ordered", render_blocks[0].target_text or "")
        self.assertIn(
            "44444444-4444-4444-8444-444444444444",
            list(render_blocks[0].source_metadata.get("repair_skip_block_ids") or []),
        )
        self.assertIn(
            "persisted_prose_artifact_chain_repaired",
            list(render_blocks[0].source_metadata.get("recovery_flags") or []),
        )

    def test_pdf_prose_artifact_repair_service_translates_standalone_prose_artifact_blocks(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-standalone-prose-repair",
            source_path="/tmp/standalone-prose-repair.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            document_id=document.id,
            ordinal=2,
            title_src="Foreword",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 10, "source_page_end": 10},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                "Of course, it wasn't perfect. It made mistakes. It got stuck. It required my "
                "supervision and, crucially, my judgment to steer it back on course."
            ),
            normalized_text="",
            source_anchor="pdf://page/10#b1",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "source_bbox_json": {"regions": [{"page_number": 10, "bbox": [94.0, 516.0, 724.0, 642.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "reading_order_index": 10,
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/10#s1",
            source_span_json={},
            upstream_confidence=0.7,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="ffffffff-ffff-4fff-8fff-ffffffffffff",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="10101010-1010-4010-8010-101010101010",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Foreword"], "summary": "Foreword summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="20202020-2020-4020-8020-202020202020",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="30303030-3030-4030-8030-303030303030",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.apply(document.id)
            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(result.repaired_chain_count, 1)
            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn("ZH::Of course, it wasn't perfect.", render_blocks[0].target_text or "")
        self.assertIn(
            "persisted_prose_artifact_block_repaired",
            list(render_blocks[0].source_metadata.get("recovery_flags") or []),
        )

    def test_pdf_prose_artifact_repair_service_translates_refreshed_mixed_code_prose_fragments(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="12121212-1212-4212-8212-121212121212",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-mixed-code-prose-fragment-repair",
            source_path="/tmp/mixed-code-prose-fragment-repair.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="13131313-1313-4313-8313-131313131313",
            document_id=document.id,
            ordinal=2,
            title_src="Foreword",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 10, "source_page_end": 10},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="14141414-1414-4414-8414-141414141414",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text='{\n"trends": [{"trend_name": "AI-Powered Personalization"}]\n}',
            normalized_text="",
            source_anchor="pdf://page/10#b1",
            source_span_json={
                "source_page_start": 10,
                "source_page_end": 10,
                "pdf_block_role": "code_like",
                "refresh_split_render_fragments": [
                    {
                        "split_kind": "trailing_prose_suffix",
                        "block_type": BlockType.PARAGRAPH.value,
                        "source_anchor": "pdf://page/10#b1-trailing-prose",
                        "source_text": "This structured format ensures that the data is machine-readable.",
                        "source_metadata": {
                            "pdf_block_role": "body",
                            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                        },
                    }
                ],
            },
            parse_confidence=0.88,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="15151515-1515-4515-8515-151515151515",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text.replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/10#s1",
            source_span_json={},
            upstream_confidence=0.88,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="16161616-1616-4616-8616-161616161616",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="17171717-1717-4717-8717-171717171717",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Foreword"], "summary": "Foreword summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="18181818-1818-4818-8818-181818181818",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="19191919-1919-4919-8919-191919191919",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.repair_mixed_code_prose_fragments(document.id)
            self.assertEqual(result.fragment_count, 1)
            self.assertEqual(result.repaired_fragment_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertIn("This structured format ensures", render_blocks[1].source_text)
        self.assertIn("ZH::This structured format ensures", render_blocks[1].target_text or "")
        self.assertIn(
            "persisted_mixed_code_prose_fragment_repaired",
            list(render_blocks[0].source_metadata.get("recovery_flags") or []),
        )

    def test_pdf_prose_artifact_repair_service_translates_mixed_paragraph_refresh_fragment(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="29292929-2929-4292-8292-292929292929",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-mixed-paragraph-refresh",
            source_path="/tmp/mixed-paragraph-refresh.pdf",
            title="Tool Use",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="2a2a2a2a-2a2a-42a2-82a2-2a2a2a2a2a2a",
            document_id=document.id,
            ordinal=5,
            title_src="Chapter 5: Tool Use",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 83, "source_page_end": 83},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="2b2b2b2b-2b2b-42b2-82b2-2b2b2b2b2b2b",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text='"""Runs all agent queries concurrently."""\ntasks = [',
            normalized_text="",
            source_anchor="pdf://page/83#b1",
            source_span_json={
                "source_page_start": 83,
                "source_page_end": 83,
                "pdf_block_role": "code_like",
                "recovery_flags": ["mixed_code_prose_split", "leading_code_prefix"],
                "refresh_split_render_fragments": [
                    {
                        "split_kind": "trailing_prose_suffix",
                        "block_type": BlockType.PARAGRAPH.value,
                        "source_anchor": "pdf://page/83#b1-trailing-prose",
                        "source_text": (
                            'run_agent_with_tool("What is the capital of France?"),\n'
                            "run_agent_with_tool(\"What's the weather like in London?\"),\n"
                            'run_agent_with_tool("Tell me something about dogs.")\n'
                            "]\n"
                            "await asyncio.gather(*tasks)\n"
                            "asyncio.run(main())\n"
                            "The code sets up a tool-calling agent using the LangChain library."
                        ),
                        "source_metadata": {
                            "pdf_block_role": "body",
                            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                        },
                    }
                ],
            },
            parse_confidence=0.88,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="2c2c2c2c-2c2c-42c2-82c2-2c2c2c2c2c2c",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=(block.source_text or ""),
            normalized_text=(block.source_text or "").replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/83#s1",
            source_span_json={},
            upstream_confidence=0.88,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="21212121-2121-4212-8212-212121212121",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="22222222-2222-4222-8222-222222222222",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Tool Use"], "summary": "Tool Use summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="23232323-2323-4232-8232-232323232323",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="24242424-2424-4242-8242-242424242424",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.repair_mixed_code_prose_fragments(document.id)
            self.assertEqual(result.fragment_count, 1)
            self.assertEqual(result.repaired_fragment_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertIn('run_agent_with_tool("What is the capital of France?"),', render_blocks[0].source_text)
        self.assertIn("asyncio.run(main())", render_blocks[0].source_text)
        self.assertNotIn("The code sets up a tool-calling agent", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(
            render_blocks[1].source_text,
            "The code sets up a tool-calling agent using the LangChain library.",
        )
        self.assertIn("ZH::The code sets up a tool-calling agent", render_blocks[1].target_text or "")
        self.assertIn(
            "persisted_mixed_code_prose_fragment_repaired",
            list(render_blocks[0].source_metadata.get("recovery_flags") or []),
        )

    def test_pdf_prose_artifact_repair_service_rerepairs_stale_mixed_paragraph_refresh_fragment(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="31313131-3131-4313-8313-313131313131",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-stale-mixed-paragraph-refresh",
            source_path="/tmp/stale-mixed-paragraph-refresh.pdf",
            title="Tool Use",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="32323232-3232-4323-8323-323232323232",
            document_id=document.id,
            ordinal=5,
            title_src="Chapter 5: Tool Use",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 83, "source_page_end": 83},
            created_at=now,
            updated_at=now,
        )
        prose_tail = (
            "The code sets up a tool-calling agent using the LangChain library and the Google\n"
            "Gemini model. It defines a search_information tool that simulates providing factual\n"
            'answers to specific queries. The tool has predefined responses for "weather in\n'
            'london," "capital of france," and "population of earth," and a default response for\n'
            "other queries. A ChatGoogleGenerativeAI model is initialized, ensuring it has\n"
            "tool-calling capabilities. A ChatPromptTemplate is created to guide the agent's\n"
            "interaction. The create_tool_calling_agent function is used to combine the language\n"
            "model, tools, and prompt into an agent. An AgentExecutor is then set up to manage\n"
            "the agent's execution and tool invocation. The run_agent_with_tool asynchronous\n"
            "function is defined to invoke the agent with a given query and print the result. The\n"
            "main asynchronous function prepares multiple queries to be run concurrently. These\n"
            "queries are designed to test both the specific and default responses of the\n"
            "search_information tool. Finally, the asyncio.run(main()) call executes all the agent\n"
            "tasks. The code includes checks for successful LLM initialization before proceeding\n"
            "with agent setup and execution."
        )
        stale_tail = (
            "tasks. The code includes checks for successful LLM initialization before proceeding\n"
            "with agent setup and execution."
        )
        block = Block(
            id="33333333-3333-4333-8333-333333333333",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text='"""Runs all agent queries concurrently."""\ntasks = [',
            normalized_text="",
            source_anchor="pdf://page/83#b1",
            source_span_json={
                "source_page_start": 83,
                "source_page_end": 83,
                "pdf_block_role": "code_like",
                "recovery_flags": ["mixed_code_prose_split", "leading_code_prefix"],
                "refresh_split_render_fragments": [
                    {
                        "split_kind": "trailing_prose_suffix",
                        "block_type": BlockType.PARAGRAPH.value,
                        "source_anchor": "pdf://page/83#b1-trailing-prose",
                        "source_text": (
                            'run_agent_with_tool("What is the capital of France?"),\n'
                            "run_agent_with_tool(\"What's the weather like in London?\"),\n"
                            'run_agent_with_tool("Tell me something about dogs.") # Should\n'
                            "trigger the default tool response\n"
                            "]\n"
                            "await asyncio.gather(*tasks)\n"
                            "nest_asyncio.apply()\n"
                            "asyncio.run(main())\n"
                            f"{prose_tail}"
                        ),
                        "target_text": "ZH::" + stale_tail,
                        "repair_target_text": "ZH::" + stale_tail,
                        "repair_source_signature": stale_tail,
                        "source_metadata": {
                            "pdf_block_role": "body",
                            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                        },
                    }
                ],
            },
            parse_confidence=0.88,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="34343434-3434-4343-8343-343434343434",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=(block.source_text or ""),
            normalized_text=(block.source_text or "").replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/83#s1",
            source_span_json={},
            upstream_confidence=0.88,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="35353535-3535-4353-8353-353535353535",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="36363636-3636-4363-8363-363636363636",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Tool Use"], "summary": "Tool Use summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="37373737-3737-4373-8373-373737373737",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="38383838-3838-4383-8383-383838383838",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.repair_mixed_code_prose_fragments(document.id)
            self.assertEqual(result.fragment_count, 1)
            self.assertEqual(result.repaired_fragment_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertIn("nest_asyncio.apply()", render_blocks[0].source_text)
        self.assertIn("asyncio.run(main())", render_blocks[0].source_text)
        self.assertNotIn("The code sets up a tool-calling agent", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertTrue((render_blocks[1].source_text or "").startswith("The code sets up a tool-calling agent"))
        self.assertIn("ChatGoogleGenerativeAI model", render_blocks[1].source_text)
        self.assertIn("ZH::The code sets up a tool-calling agent", render_blocks[1].target_text or "")

    def test_pdf_prose_artifact_repair_service_persists_targets_for_direct_mixed_code_blocks(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="39393939-3939-4393-8393-393939393939",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-direct-mixed-code-block",
            source_path="/tmp/direct-mixed-code-block.pdf",
            title="Tool Use",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="40404040-4040-4404-8404-404040404040",
            document_id=document.id,
            ordinal=5,
            title_src="Chapter 4: Reflection",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 73, "source_page_end": 73},
            created_at=now,
            updated_at=now,
        )
        prose_prefix = (
            "2. Calculate its factorial (n!).\n"
            "3. Include a clear docstring explaining what the function does.\n"
            "4. Handle edge cases: The factorial of 0 is 1."
        )
        block = Block(
            id="41414141-4141-4414-8414-414141414141",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                f"{prose_prefix}\n"
                "\"\"\"\n"
                "# --- The Reflection Loop ---\n"
                "max_iterations = 3\n"
                'current_code = ""'
            ),
            normalized_text="",
            source_anchor="pdf://page/73#b1",
            source_span_json={
                "source_page_start": 73,
                "source_page_end": 73,
                "pdf_block_role": "code_like",
            },
            parse_confidence=0.88,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="42424242-4242-4424-8424-424242424242",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=(block.source_text or ""),
            normalized_text=(block.source_text or "").replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/73#s1",
            source_span_json={},
            upstream_confidence=0.88,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="43434343-4343-4434-8434-434343434343",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="44444444-4444-4444-8444-444444444444",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Tool Use"], "summary": "Tool Use summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="45454545-4545-4454-8454-454545454545",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="46464646-4646-4464-8464-464646464646",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.repair_mixed_code_prose_blocks(document.id)
            self.assertEqual(result.block_count, 1)
            self.assertEqual(result.repaired_block_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertIn(
            "persisted_mixed_code_prose_block_repaired",
            list(render_blocks[1].source_metadata.get("recovery_flags") or []),
        )
        self.assertEqual(render_blocks[0].source_text, prose_prefix)
        self.assertIn("ZH::2. Calculate its factorial", render_blocks[0].target_text or "")
        self.assertEqual(render_blocks[1].block_type, BlockType.CODE.value)
        self.assertIn("# --- The Reflection Loop ---", render_blocks[1].source_text)
        self.assertNotIn("2. Calculate its factorial", render_blocks[1].source_text)

    def test_pdf_prose_artifact_repair_service_translates_trailing_prose_inside_code_fragment(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="2d2d2d2d-2d2d-42d2-82d2-2d2d2d2d2d2d",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-mixed-code-fragment",
            source_path="/tmp/mixed-code-fragment.pdf",
            title="Multi-Agent Collaboration",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="2e2e2e2e-2e2e-42e2-82e2-2e2e2e2e2e2e",
            document_id=document.id,
            ordinal=7,
            title_src="Chapter 7: Multi-Agent Collaboration",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 118, "source_page_end": 119},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="2f2f2f2f-2f2f-42f2-82f2-2f2f2f2f2f2f",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text='if __name__ == "__main__":',
            normalized_text="",
            source_anchor="pdf://page/118#b1",
            source_span_json={
                "source_page_start": 118,
                "source_page_end": 119,
                "pdf_block_role": "code_like",
                "recovery_flags": ["cross_page_repaired", "mixed_code_prose_split", "leading_code_prefix"],
                "refresh_split_render_fragments": [
                    {
                        "split_kind": "trailing_prose_suffix",
                        "block_type": BlockType.CODE.value,
                        "source_anchor": "pdf://page/118#b1-trailing-prose",
                        "source_text": (
                            "main()\n"
                            'print("done")\n'
                            "We will now delve into further examples within the Google ADK framework."
                        ),
                        "source_metadata": {
                            "pdf_block_role": "code_like",
                            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
                        },
                    }
                ],
            },
            parse_confidence=0.88,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="30303030-3030-4303-8303-303030303030",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=(block.source_text or ""),
            normalized_text=(block.source_text or "").replace("\n", " "),
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/118#s1",
            source_span_json={},
            upstream_confidence=0.88,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="25252525-2525-4252-8252-252525252525",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="26262626-2626-4262-8262-262626262626",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["Multi-Agent Collaboration"], "summary": "Multi-agent summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="27272727-2727-4272-8272-272727272727",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="28282828-2828-4282-8282-282828282828",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.repair_mixed_code_prose_fragments(document.id)
            self.assertEqual(result.fragment_count, 1)
            self.assertEqual(result.repaired_fragment_count, 1)

            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertIn("main()", render_blocks[0].source_text)
        self.assertIn('print("done")', render_blocks[0].source_text)
        self.assertNotIn("We will now delve into further examples", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(
            render_blocks[1].source_text,
            "We will now delve into further examples within the Google ADK framework.",
        )
        self.assertIn("ZH::We will now delve into further examples", render_blocks[1].target_text or "")

    def test_pdf_prose_artifact_repair_service_translates_reference_family_prose_artifact_blocks(self) -> None:
        class _RepairWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="repair-worker",
                    model_name="repair-model",
                    prompt_version="repair.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                merged = " ".join(block.text for block in task.context_packet.current_blocks)
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=[
                        TranslationTargetSegment(
                            temp_id="repair-temp-1",
                            text_zh=f"ZH::{merged}",
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id for sentence in task.current_sentences],
                            confidence=0.9,
                        )
                    ],
                    alignment_suggestions=[],
                )

        now = datetime.now(timezone.utc)
        document = Document(
            id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-reference-prose-repair",
            source_path="/tmp/reference-prose-repair.pdf",
            title="References",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="cccccccc-cccc-4ccc-8ccc-cccccccccccd",
            document_id=document.id,
            ordinal=10,
            title_src="References",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.EXPORTED,
            summary_version=1,
            risk_level=None,
            metadata_json={"source_page_start": 62, "source_page_end": 62},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="dddddddd-dddd-4ddd-8ddd-ddddddddddde",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.TABLE,
            source_text=(
                "Frameworks provide distinct mechanisms for implementing this pattern. In LangChain, "
                "constructs like RunnableParallel are used to explicitly define and execute multiple "
                "processing chains simultaneously."
            ),
            normalized_text="",
            source_anchor="pdf://page/62#b1",
            source_span_json={
                "source_page_start": 62,
                "source_page_end": 62,
                "source_bbox_json": {"regions": [{"page_number": 62, "bbox": [94.0, 516.0, 724.0, 642.0]}]},
                "pdf_block_role": "table_like",
                "pdf_page_family": "references",
                "reading_order_index": 10,
            },
            parse_confidence=0.7,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        protected_sentence = Sentence(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeef",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=False,
            nontranslatable_reason="code_protected",
            source_anchor="pdf://page/62#s1",
            source_span_json={},
            upstream_confidence=0.7,
            sentence_status=SentenceStatus.PROTECTED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        book_profile = BookProfile(
            id="ffffffff-ffff-4fff-8fff-fffffffffffe",
            document_id=document.id,
            version=1,
            book_type=BookType.TECH,
            style_policy_json={"translation_material": "technical_book"},
            quote_policy_json={},
            special_content_policy_json={},
            created_by="test",
            created_at=now,
        )
        chapter_brief = MemorySnapshot(
            id="10101010-1010-4010-8010-101010101011",
            document_id=document.id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            version=1,
            content_json={"heading_path": ["References"], "summary": "References summary."},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        termbase = MemorySnapshot(
            id="20202020-2020-4020-8020-202020202021",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=1,
            content_json={"terms": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        entity_registry = MemorySnapshot(
            id="30303030-3030-4030-8030-303030303031",
            document_id=document.id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            version=1,
            content_json={"entities": []},
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.flush()
            session.add(protected_sentence)
            session.add_all([book_profile, chapter_brief, termbase, entity_registry])
            session.commit()

            service = PdfProseArtifactRepairService(session, worker=_RepairWorker())
            result = service.apply(document.id)
            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(result.repaired_chain_count, 1)
            bundle = BootstrapRepository(session).load_document_bundle(document.id).chapters[0]
            render_blocks = ExportService(ExportRepository(session))._render_blocks_for_chapter(
                ChapterExportBundle(
                    chapter=bundle.chapter,
                    document=document,
                    book_profile=book_profile,
                    blocks=bundle.blocks,
                    document_images=[],
                    sentences=bundle.sentences,
                    packets=bundle.translation_packets,
                    translation_runs=[],
                    target_segments=[],
                    alignment_edges=[],
                    review_issues=[],
                    quality_summary=None,
                    active_snapshots=[chapter_brief, termbase, entity_registry],
                    audit_events=[],
                )
            )

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertIn("ZH::Frameworks provide distinct mechanisms", render_blocks[0].target_text or "")

    def test_render_blocks_merge_code_like_paragraphs_and_tables_into_single_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-code-like-merge",
            source_path="/tmp/sample.pdf",
            title="Code Example",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="chapter-code",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 16: Resource-Aware",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 247, "source_page_end": 248},
            created_at=now,
            updated_at=now,
        )

        def codeish_block(
            ordinal: int,
            block_type: BlockType,
            text: str,
            page: int,
            role: str = "body",
        ) -> Block:
            return Block(
                id=f"code-block-{ordinal}",
                chapter_id=chapter.id,
                ordinal=ordinal,
                block_type=block_type,
                source_text=text,
                normalized_text=text,
                source_anchor=f"pdf://page/{page}#b{ordinal}",
                source_span_json={
                    "source_page_start": page,
                    "source_page_end": page,
                    "source_bbox_json": {"regions": [{"page_number": page, "bbox": [102.0, 144.0, 677.0, 337.0]}]},
                    "pdf_block_role": role,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[
                codeish_block(1, BlockType.HEADING, "Chapter 16: Resource-Aware", 247, role="heading"),
                codeish_block(
                    2,
                    BlockType.PARAGRAPH,
                    "# Conceptual Python-like structure\nfrom google.adk.agents import Agent, BaseAgent",
                    247,
                    role="code_like",
                ),
                codeish_block(
                    3,
                    BlockType.CODE,
                    'class QueryRouterAgent(BaseAgent):\n    name = "QueryRouter"',
                    247,
                    role="code_like",
                ),
                codeish_block(
                    4,
                    BlockType.TABLE,
                    "async def run_async_impl(self, context):\n    return None",
                    247,
                    role="code_like",
                ),
                codeish_block(
                    5,
                    BlockType.PARAGRAPH,
                    "The router chooses a cheaper model for easy questions.",
                    248,
                    role="body",
                ),
            ],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 3)
        self.assertEqual(render_blocks[1].artifact_kind, "code")
        self.assertEqual(render_blocks[1].render_mode, "source_artifact_full_width")
        self.assertIn("from google.adk.agents import Agent, BaseAgent", render_blocks[1].source_text)
        self.assertIn("class QueryRouterAgent(BaseAgent):", render_blocks[1].source_text)
        self.assertIn("async def run_async_impl(self, context):", render_blocks[1].source_text)
        self.assertEqual(render_blocks[2].artifact_kind, None)

    def test_render_blocks_drop_pure_chapter_label_heading_false_positive(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="drop-chapter-label-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-drop-chapter-label",
            source_path="/tmp/drop-chapter-label.pdf",
            title="Foreword",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="drop-chapter-label-chapter",
            document_id=document.id,
            ordinal=3,
            title_src="Chapter 17.",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 15, "source_page_end": 19},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="drop-chapter-label-body-1",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.PARAGRAPH,
                source_text="performs context engineering by selecting the most relevant information.",
                normalized_text="",
                source_anchor="pdf://page/15#b1",
                source_span_json={
                    "source_page_start": 15,
                    "source_page_end": 15,
                    "source_bbox_json": {"regions": [{"page_number": 15, "bbox": [94.0, 132.0, 704.0, 186.0]}]},
                    "pdf_block_role": "body",
                    "pdf_page_family": "body",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="drop-chapter-label-heading",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.HEADING,
                source_text="Chapter 17.",
                normalized_text="",
                source_anchor="pdf://page/15#b2",
                source_span_json={
                    "source_page_start": 15,
                    "source_page_end": 15,
                    "source_bbox_json": {"regions": [{"page_number": 15, "bbox": [286.0, 204.0, 482.0, 232.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 2,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="drop-chapter-label-body-2",
                chapter_id=chapter.id,
                ordinal=3,
                block_type=BlockType.PARAGRAPH,
                source_text="Level 3: The Rise of Collaborative Multi-Agent Systems",
                normalized_text="",
                source_anchor="pdf://page/15#b3",
                source_span_json={
                    "source_page_start": 15,
                    "source_page_end": 15,
                    "source_bbox_json": {"regions": [{"page_number": 15, "bbox": [94.0, 248.0, 704.0, 282.0]}]},
                    "pdf_block_role": "body",
                    "pdf_page_family": "body",
                    "reading_order_index": 3,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual([block.source_text for block in render_blocks], [blocks[0].source_text, blocks[2].source_text])

    def test_render_blocks_demote_sentence_like_heading_false_positive_and_merge_fragments(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="demote-heading-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-demote-heading",
            source_path="/tmp/demote-heading.pdf",
            title="Planning",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="demote-heading-chapter",
            document_id=document.id,
            ordinal=6,
            title_src="Chapter 6: Planning",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 101, "source_page_end": 101},
            created_at=now,
            updated_at=now,
        )

        def make_block(block_id: str, ordinal: int, block_type: BlockType, text: str, reading_order_index: int) -> Block:
            role = "heading" if block_type == BlockType.HEADING else "body"
            return Block(
                id=block_id,
                chapter_id=chapter.id,
                ordinal=ordinal,
                block_type=block_type,
                source_text=text,
                normalized_text="",
                source_anchor=f"pdf://page/101#{block_id}",
                source_span_json={
                    "source_page_start": 101,
                    "source_page_end": 101,
                    "source_bbox_json": {"regions": [{"page_number": 101, "bbox": [94.0, 120.0 + ordinal * 24.0, 704.0, 144.0 + ordinal * 24.0]}]},
                    "pdf_block_role": role,
                    "pdf_page_family": "body",
                    "reading_order_index": reading_order_index,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )

        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[
                make_block("chapter-title", 1, BlockType.HEADING, "Chapter 6: Planning", 1),
                make_block(
                    "bad-heading-1",
                    2,
                    BlockType.HEADING,
                    "user-provided documents, combining information from private sources with its",
                    2,
                ),
                make_block(
                    "bad-heading-2",
                    3,
                    BlockType.HEADING,
                    "web-based research. The final output is not merely a concatenated list of findings.",
                    3,
                ),
            ],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertIn("user-provided documents", render_blocks[1].source_text)
        self.assertIn("web-based research", render_blocks[1].source_text)
        self.assertIn("export_book_heading_demoted", render_blocks[1].source_metadata["recovery_flags"])
        self.assertIn("export_book_paragraph_fragments_merged", render_blocks[1].source_metadata["recovery_flags"])

    def test_render_blocks_promote_single_line_codeish_heading_to_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="promote-code-heading-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-promote-code-heading",
            source_path="/tmp/promote-code-heading.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="promote-code-heading-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 21, "source_page_end": 21},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="promote-code-heading-title",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="Chapter 1: Prompt Chaining",
                normalized_text="",
                source_anchor="pdf://page/21#b1",
                source_span_json={
                    "source_page_start": 21,
                    "source_page_end": 21,
                    "source_bbox_json": {"regions": [{"page_number": 21, "bbox": [94.0, 108.0, 704.0, 142.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="promote-code-heading-bad",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.HEADING,
                source_text='final_result = full_chain.invoke({"text_input": input_text})',
                normalized_text="",
                source_anchor="pdf://page/21#b2",
                source_span_json={
                    "source_page_start": 21,
                    "source_page_end": 21,
                    "source_bbox_json": {"regions": [{"page_number": 21, "bbox": [94.0, 168.0, 704.0, 198.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 2,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(render_blocks[1].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[1].render_mode, "source_artifact_full_width")
        self.assertEqual(render_blocks[1].artifact_kind, "code")
        self.assertIn("export_book_code_promoted", render_blocks[1].source_metadata["recovery_flags"])

    def test_render_blocks_keep_cjk_chapter_heading_out_of_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="keep-cjk-heading-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-keep-cjk-heading",
            source_path="/tmp/keep-cjk-heading.pdf",
            title="Tool Use",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="keep-cjk-heading-chapter",
            document_id=document.id,
            ordinal=17,
            title_src="Chapter 5: Tool Use (Function Calling)",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 155, "source_page_end": 155},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="keep-cjk-heading-title",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="第五章：工具使用（函数调用）",
                normalized_text="",
                source_anchor="pdf://page/155#b1",
                source_span_json={
                    "source_page_start": 155,
                    "source_page_end": 155,
                    "source_bbox_json": {"regions": [{"page_number": 155, "bbox": [94.0, 108.0, 704.0, 142.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="keep-cjk-heading-overview",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.HEADING,
                source_text="工具使用模式概述",
                normalized_text="",
                source_anchor="pdf://page/155#b2",
                source_span_json={
                    "source_page_start": 155,
                    "source_page_end": 155,
                    "source_bbox_json": {"regions": [{"page_number": 155, "bbox": [94.0, 168.0, 704.0, 198.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 2,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(render_blocks[0].block_type, BlockType.HEADING.value)
        self.assertEqual(render_blocks[0].artifact_kind, None)
        self.assertEqual(render_blocks[0].source_text, "第五章：工具使用（函数调用）")

    def test_render_blocks_demote_short_prose_code_false_positive(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="demote-short-prose-code-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-demote-short-prose-code",
            source_path="/tmp/demote-short-prose-code.pdf",
            title="Acknowledgment",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="demote-short-prose-code-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Acknowledgment",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 3, "source_page_end": 3},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="demote-short-prose-code-title",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="Acknowledgment",
                normalized_text="",
                source_anchor="pdf://page/3#b1",
                source_span_json={
                    "source_page_start": 3,
                    "source_page_end": 3,
                    "source_bbox_json": {"regions": [{"page_number": 3, "bbox": [94.0, 108.0, 704.0, 142.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "frontmatter",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="demote-short-prose-code-bad",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.CODE,
                source_text="With all my love.",
                normalized_text="",
                source_anchor="pdf://page/3#b2",
                source_span_json={
                    "source_page_start": 3,
                    "source_page_end": 3,
                    "source_bbox_json": {"regions": [{"page_number": 3, "bbox": [120.0, 180.0, 400.0, 208.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "frontmatter",
                    "reading_order_index": 2,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[1].artifact_kind, None)
        self.assertEqual(render_blocks[1].render_mode, "zh_primary_with_optional_source")
        self.assertIn("export_book_code_demoted", render_blocks[1].source_metadata["recovery_flags"])

    def test_export_service_keeps_structured_output_intro_sentence_out_of_code_artifact(self) -> None:
        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        sentence = "For example, the output from the trend identification step could be formatted as a JSON object:"

        self.assertFalse(service._looks_like_single_line_codeish_text(sentence))

    def test_render_blocks_keep_glossary_definition_paragraph_out_of_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="glossary-definition-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-glossary-definition",
            source_path="/tmp/glossary-definition.pdf",
            title="Glossary",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="glossary-definition-chapter",
            document_id=document.id,
            ordinal=92,
            title_src="Glossary",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 428, "source_page_end": 428},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="glossary-definition-title",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="Glossary",
                normalized_text="",
                source_anchor="pdf://page/428#b1",
                source_span_json={
                    "source_page_start": 428,
                    "source_page_end": 428,
                    "source_bbox_json": {"regions": [{"page_number": 428, "bbox": [94.0, 108.0, 704.0, 142.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "backmatter",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="glossary-definition-body",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.PARAGRAPH,
                source_text=(
                    "ReAct (Reason and Act): This pattern combines reasoning and acting so an agent can "
                    "interleave thought with tool use.\n"
                    "Planning: This is an agent's ability to\n"
                    "break down a high-level goal into a\n"
                    "sequence of smaller, coordinated steps."
                ),
                normalized_text="",
                source_anchor="pdf://page/428#b2",
                source_span_json={
                    "source_page_start": 428,
                    "source_page_end": 428,
                    "source_bbox_json": {"regions": [{"page_number": 428, "bbox": [94.0, 168.0, 704.0, 250.0]}]},
                    "pdf_block_role": "body",
                    "pdf_page_family": "backmatter",
                    "reading_order_index": 2,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[1].artifact_kind, None)
        self.assertEqual(render_blocks[1].render_mode, "zh_primary_with_optional_source")
        self.assertIn("Planning: This is an agent's ability to", render_blocks[1].source_text)
        self.assertNotIn("pdf_mixed_code_prose_split", render_blocks[1].source_metadata)

    def test_join_block_target_text_preserves_bullet_segment_breaks(self) -> None:
        source_text = (
            "● Processing: Check if all required fields were extracted and if they meet format requirements.\n"
            "● Prompt 2 (Conditional): If fields are missing or malformed, craft a new prompt asking the model to specifically find the missing/malformed information.\n"
            "● Processing: Validate the results again. Repeat if necessary.\n"
            "● Output: Provide the extracted, validated structured data."
        )
        target_texts = [
            "● 处理：检查是否已提取所有必填字段，以及这些字段是否符合格式要求。",
            "● 提示2（条件性）：如果字段缺失或格式错误，则创建新的提示，要求模型专门查找缺失或格式错误的信息。",
            "● 处理：再次验证结果。必要时重复。",
            "● 输出：提供提取并验证后的结构化数据。",
        ]

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            joined = service._join_block_target_text(
                target_texts,
                block_type=BlockType.PARAGRAPH,
                render_mode="zh_primary_with_optional_source",
                source_text=source_text,
            )
            rendered_html = service._render_block_html(
                MergedRenderBlock(
                    block_id="bullet-join",
                    chapter_id="chapter-1",
                    block_type=BlockType.PARAGRAPH.value,
                    render_mode="zh_primary_with_optional_source",
                    artifact_kind=None,
                    title=None,
                    source_text=source_text,
                    target_text=joined,
                    source_metadata={},
                    source_sentence_ids=[],
                    target_segment_ids=[],
                    is_expected_source_only=False,
                    notice=None,
                )
            )

        self.assertIn("● 处理：检查是否已提取所有必填字段，以及这些字段是否符合格式要求。\n● 提示2", joined)
        self.assertIn("<br/>● 提示2（条件性）", rendered_html)

    def test_render_blocks_demote_reference_listing_code_block_and_preserve_entry_breaks(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="reference-listing-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-reference-listing",
            source_path="/tmp/reference-listing.pdf",
            title="References",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="reference-listing-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="References",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 42, "source_page_end": 42},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="reference-listing-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                "1. LangChain Documentation on LCEL:\n"
                "https://python.langchain.com/v0.2/docs/core_modules/expression_language/\n"
                "2. LangGraph Documentation: https://langchain-ai.github.io/langgraph/\n"
                "3. Prompt Engineering Guide - Chaining Prompts:\n"
                "https://www.promptingguide.ai/techniques/chaining"
            ),
            normalized_text="",
            source_anchor="pdf://page/42#b1",
            source_span_json={
                "source_page_start": 42,
                "source_page_end": 42,
                "source_bbox_json": {"regions": [{"page_number": 42, "bbox": [72.0, 144.0, 540.0, 360.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "references",
                "reading_order_index": 1,
            },
            parse_confidence=0.92,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentences = [
            Sentence(
                id=f"reference-listing-sentence-{ordinal}",
                block_id=block.id,
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=ordinal,
                source_text=source_text,
                normalized_text=source_text,
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor=f"pdf://page/42#s{ordinal}",
                source_span_json={},
                upstream_confidence=0.92,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            )
            for ordinal, source_text in enumerate(
                [
                    "1. LangChain Documentation on LCEL:",
                    "https://python.langchain.com/v0.2/docs/core_modules/expression_language/",
                    "2. LangGraph Documentation: https://langchain-ai.github.io/langgraph/",
                    "3. Prompt Engineering Guide - Chaining Prompts:",
                    "https://www.promptingguide.ai/techniques/chaining",
                ],
                start=1,
            )
        ]
        target_segments = [
            TargetSegment(
                id=f"reference-listing-target-{ordinal}",
                chapter_id=chapter.id,
                translation_run_id="reference-listing-run",
                ordinal=ordinal,
                text_zh=text_zh,
                segment_type="sentence",
                confidence=0.94,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            )
            for ordinal, text_zh in enumerate(
                [
                    "1. LangChain 文档中的 LCEL：",
                    "https://python.langchain.com/v0.2/docs/core_modules/expression_language/",
                    "2. LangGraph 文档： https://langchain-ai.github.io/langgraph/",
                    "3. 提示工程指南 - 链式提示：",
                    "https://www.promptingguide.ai/techniques/chaining",
                ],
                start=1,
            )
        ]
        alignment_edges = [
            AlignmentEdge(
                id=f"reference-listing-edge-{ordinal}",
                sentence_id=sentences[ordinal - 1].id,
                target_segment_id=target_segments[ordinal - 1].id,
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.94,
                created_by=ActorType.MODEL,
                created_at=now,
            )
            for ordinal in range(1, 6)
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=sentences,
            packets=[],
            translation_runs=[],
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)
            rendered_html = service._render_block_html(render_blocks[0])

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn(
            "1. LangChain 文档中的 LCEL：\nhttps://python.langchain.com/v0.2/docs/core_modules/expression_language/",
            render_blocks[0].target_text or "",
        )
        self.assertIn("\n\n2. LangGraph 文档： https://langchain-ai.github.io/langgraph/", render_blocks[0].target_text or "")
        self.assertIn("<br/><br/>2. LangGraph 文档： https://langchain-ai.github.io/langgraph/", rendered_html)
        self.assertNotIn("代码保持原样", rendered_html)

    def test_render_blocks_split_inline_reference_entries_and_wrapped_urls(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="reference-inline-split-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-reference-inline-split",
            source_path="/tmp/reference-inline-split.pdf",
            title="References",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="reference-inline-split-chapter",
            document_id=document.id,
            ordinal=5,
            title_src="References",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 20, "source_page_end": 20},
            created_at=now,
            updated_at=now,
        )
        heading = Block(
            id="reference-inline-split-heading",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.HEADING,
            source_text="References",
            normalized_text="",
            source_anchor="pdf://page/20#b1",
            source_span_json={
                "source_page_start": 20,
                "source_page_end": 20,
                "source_bbox_json": {"regions": [{"page_number": 20, "bbox": [72.0, 88.0, 540.0, 130.0]}]},
                "pdf_block_role": "heading",
                "pdf_page_family": "references",
                "reading_order_index": 1,
            },
            parse_confidence=0.93,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        reference_block_1 = Block(
            id="reference-inline-split-block-1",
            chapter_id=chapter.id,
            ordinal=2,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "1.\u200b Cloudera, Inc. (April 2025), 96% of enterprises are increasing their use of AI agents."
                "https://www.cloudera.com/about/news-and-blogs/press-releases/2025-04-\n"
                "16-96-percent-of-enterprises-are-expanding-use-of-ai-agents-according-to-latest-\n"
                "data-from-cloudera.html\n"
                "2.\u200b Autonomous generative AI agents:"
            ),
            normalized_text="",
            source_anchor="pdf://page/20#b2",
            source_span_json={
                "source_page_start": 20,
                "source_page_end": 20,
                "source_bbox_json": {"regions": [{"page_number": 20, "bbox": [72.0, 148.0, 540.0, 298.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "references",
                "reading_order_index": 2,
            },
            parse_confidence=0.93,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        reference_block_2 = Block(
            id="reference-inline-split-block-2",
            chapter_id=chapter.id,
            ordinal=3,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "https://www.deloitte.com/us/en/insights/industry/technology/technology-media-an\n"
                "d-telecom-predictions/2025/autonomous-generative-ai-agents-still-under-develop\n"
                "ment.html\n"
                "3.\u200b Market.us. Global Agentic AI Market Size, Trends and Forecast 2025–2034."
            ),
            normalized_text="",
            source_anchor="pdf://page/20#b3",
            source_span_json={
                "source_page_start": 20,
                "source_page_end": 20,
                "source_bbox_json": {"regions": [{"page_number": 20, "bbox": [72.0, 302.0, 540.0, 420.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "references",
                "reading_order_index": 3,
            },
            parse_confidence=0.93,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentences = [
            Sentence(
                id="reference-inline-split-sentence-0",
                block_id=heading.id,
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=1,
                source_text=heading.source_text,
                normalized_text=heading.source_text,
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor="pdf://page/20#s0",
                source_span_json={},
                upstream_confidence=0.93,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            ),
            Sentence(
                id="reference-inline-split-sentence-1",
                block_id=reference_block_1.id,
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=1,
                source_text=reference_block_1.source_text,
                normalized_text=reference_block_1.source_text,
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor="pdf://page/20#s1",
                source_span_json={},
                upstream_confidence=0.93,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            ),
            Sentence(
                id="reference-inline-split-sentence-2",
                block_id=reference_block_2.id,
                chapter_id=chapter.id,
                document_id=document.id,
                ordinal_in_block=1,
                source_text=reference_block_2.source_text,
                normalized_text=reference_block_2.source_text,
                source_lang="en",
                translatable=True,
                nontranslatable_reason=None,
                source_anchor="pdf://page/20#s2",
                source_span_json={},
                upstream_confidence=0.93,
                sentence_status=SentenceStatus.TRANSLATED,
                active_version=1,
                created_at=now,
                updated_at=now,
            ),
        ]
        target_segments = [
            TargetSegment(
                id="reference-inline-split-target-0",
                chapter_id=chapter.id,
                translation_run_id="reference-inline-split-run",
                ordinal=1,
                text_zh="参考文献",
                segment_type="sentence",
                confidence=0.94,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            ),
            TargetSegment(
                id="reference-inline-split-target-1",
                chapter_id=chapter.id,
                translation_run_id="reference-inline-split-run",
                ordinal=2,
                text_zh=(
                    "1. Cloudera公司（2025年4月），96%的企业正在增加对AI智能体的使用。"
                    "https://www.cloudera.com/about/news-and-blogs/press-releases/2025-04-"
                    "16-96-percent-of-enterprises-are-expanding-use-of-ai-agents-according-to-latest-"
                    "data-from-cloudera.html 2. 自主生成式AI智能体："
                ),
                segment_type="sentence",
                confidence=0.94,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            ),
            TargetSegment(
                id="reference-inline-split-target-2",
                chapter_id=chapter.id,
                translation_run_id="reference-inline-split-run",
                ordinal=3,
                text_zh=(
                    "https://www.deloitte.com/us/en/insights/industry/technology/technology-media-and-telecom-"
                    "predictions/2025/autonomous-generative-ai-agents-still-under-development.html "
                    "3. Market.us。全球代理式AI市场规模、趋势与预测 2025-2034。"
                ),
                segment_type="sentence",
                confidence=0.94,
                final_status=TargetSegmentStatus.FINALIZED,
                created_at=now,
                updated_at=now,
            ),
        ]
        alignment_edges = [
            AlignmentEdge(
                id="reference-inline-split-edge-0",
                sentence_id=sentences[0].id,
                target_segment_id=target_segments[0].id,
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.94,
                created_by=ActorType.MODEL,
                created_at=now,
            ),
            AlignmentEdge(
                id="reference-inline-split-edge-1",
                sentence_id=sentences[1].id,
                target_segment_id=target_segments[1].id,
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.94,
                created_by=ActorType.MODEL,
                created_at=now,
            ),
            AlignmentEdge(
                id="reference-inline-split-edge-2",
                sentence_id=sentences[2].id,
                target_segment_id=target_segments[2].id,
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.94,
                created_by=ActorType.MODEL,
                created_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[heading, reference_block_1, reference_block_2],
            document_images=[],
            sentences=sentences,
            packets=[],
            translation_runs=[],
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 6)
        self.assertEqual(render_blocks[1].target_text, "1. Cloudera公司（2025年4月），96%的企业正在增加对AI智能体的使用。")
        self.assertEqual(
            render_blocks[2].target_text,
            "https://www.cloudera.com/about/news-and-blogs/press-releases/2025-04-16-96-percent-of-enterprises-are-expanding-use-of-ai-agents-according-to-latest-data-from-cloudera.html",
        )
        self.assertEqual(render_blocks[3].target_text, "2. 自主生成式AI智能体：")
        self.assertEqual(
            render_blocks[4].target_text,
            "https://www.deloitte.com/us/en/insights/industry/technology/technology-media-and-telecom-predictions/2025/autonomous-generative-ai-agents-still-under-development.html",
        )
        self.assertEqual(render_blocks[5].target_text, "3. Market.us。全球代理式AI市场规模、趋势与预测 2025-2034。")
        self.assertNotIn("2. 自主生成式AI智能体：", render_blocks[1].target_text or "")
        self.assertIn("export_reference_listing_normalized", render_blocks[1].source_metadata["recovery_flags"])

    def test_render_blocks_reflow_collapsed_inline_bullet_list_target(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="list-reflow-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-list-reflow",
            source_path="/tmp/list-reflow.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="list-reflow-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 23, "source_page_end": 23},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="list-reflow-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "●\u200b Prompt 1: Extract text content from a given URL or document.\n"
                "●\u200b Prompt 2: Summarize the cleaned text.\n"
                "●\u200b Prompt 3: Extract specific entities (e.g., names, dates, locations) from the summary or\n"
                "original text.\n"
                "●\u200b Prompt 4: Use the entities to search an internal knowledge base.\n"
                "●\u200b Prompt 5: Generate a final report incorporating the summary, entities, and search\n"
                "results."
            ),
            normalized_text="",
            source_anchor="pdf://page/23#b18",
            source_span_json={
                "source_page_start": 23,
                "source_page_end": 23,
                "source_bbox_json": {"regions": [{"page_number": 23, "bbox": [94.0, 148.0, 704.0, 322.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 18,
            },
            parse_confidence=0.94,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="list-reflow-sentence-1",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/23#s18",
            source_span_json={},
            upstream_confidence=0.94,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="list-reflow-target-1",
            chapter_id=chapter.id,
            translation_run_id="list-reflow-run",
            ordinal=1,
            text_zh=(
                "● 提示1：从给定的URL或文档中提取文本内容。"
                "● 提示2：总结清理后的文本。"
                "● 提示3：从摘要或原始文本中提取特定实体（例如，姓名、日期、地点）。"
                "● 提示4：使用实体搜索内部知识库。"
                "● 提示5：生成一份最终报告，整合摘要、实体和搜索结果。"
            ),
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="list-reflow-edge-1",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(
            render_blocks[0].target_text,
            "\n".join(
                [
                    "● 提示1：从给定的URL或文档中提取文本内容。",
                    "● 提示2：总结清理后的文本。",
                    "● 提示3：从摘要或原始文本中提取特定实体（例如，姓名、日期、地点）。",
                    "● 提示4：使用实体搜索内部知识库。",
                    "● 提示5：生成一份最终报告，整合摘要、实体和搜索结果。",
                ]
            ),
        )
        self.assertIn("export_book_list_target_layout_restored", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_reflow_collapsed_inline_bullet_list_target_when_source_is_also_single_line(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="list-inline-source-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-list-inline-source",
            source_path="/tmp/list-inline-source.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="list-inline-source-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 23, "source_page_end": 23},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="list-inline-source-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "● Prompt 1: Extract text content from a given URL or document. "
                "● Prompt 2: Summarize the cleaned text. "
                "● Prompt 3: Extract specific entities from the summary or original text. "
                "● Prompt 4: Use the entities to search an internal knowledge base. "
                "● Prompt 5: Generate a final report incorporating the summary, entities, and search results."
            ),
            normalized_text="",
            source_anchor="pdf://page/23#b19",
            source_span_json={
                "source_page_start": 23,
                "source_page_end": 23,
                "source_bbox_json": {"regions": [{"page_number": 23, "bbox": [94.0, 148.0, 704.0, 322.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 19,
            },
            parse_confidence=0.94,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="list-inline-source-sentence-1",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/23#s19",
            source_span_json={},
            upstream_confidence=0.94,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="list-inline-source-target-1",
            chapter_id=chapter.id,
            translation_run_id="list-inline-source-run",
            ordinal=1,
            text_zh=(
                "● 提示1：从给定的URL或文档中提取文本内容。"
                "● 提示2：总结清理后的文本。"
                "● 提示3：从摘要或原始文本中提取特定实体。"
                "● 提示4：使用实体搜索内部知识库。"
                "● 提示5：生成一份最终报告，整合摘要、实体和搜索结果。"
            ),
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="list-inline-source-edge-1",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(
            render_blocks[0].target_text,
            "\n".join(
                [
                    "● 提示1：从给定的URL或文档中提取文本内容。",
                    "● 提示2：总结清理后的文本。",
                    "● 提示3：从摘要或原始文本中提取特定实体。",
                    "● 提示4：使用实体搜索内部知识库。",
                    "● 提示5：生成一份最终报告，整合摘要、实体和搜索结果。",
                ]
            ),
        )
        self.assertIn("export_book_list_target_layout_restored", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_reflow_nested_bullet_list_target_and_preserve_html_indent(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="list-nested-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-list-nested",
            source_path="/tmp/list-nested.pdf",
            title="Parallelization",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="list-nested-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 3: Parallelization",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 48, "source_page_end": 48},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="list-nested-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "● Use case: Research company agents.\n"
                "○ Parallel task: Search news articles, pull stock data, and check social mentions.\n"
                "○ Benefit: Gather a fuller picture faster than sequential work."
            ),
            normalized_text="",
            source_anchor="pdf://page/48#b12",
            source_span_json={
                "source_page_start": 48,
                "source_page_end": 48,
                "source_bbox_json": {"regions": [{"page_number": 48, "bbox": [94.0, 148.0, 704.0, 322.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 12,
            },
            parse_confidence=0.94,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="list-nested-sentence-1",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/48#s12",
            source_span_json={},
            upstream_confidence=0.94,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="list-nested-target-1",
            chapter_id=chapter.id,
            translation_run_id="list-nested-run",
            ordinal=1,
            text_zh=(
                "● 用例：研究公司的代理。\n"
                "○ 并行任务：同时搜索新闻文章、拉取股票数据并检查社交媒体提及。\n"
                "○ 优势：比顺序工作更快地收集完整视图。"
            ),
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="list-nested-edge-1",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)
            rendered_html = service._render_block_html(render_blocks[0])

        self.assertEqual(
            render_blocks[0].target_text,
            "\n".join(
                [
                    "● 用例：研究公司的代理。",
                    "   ○ 并行任务：同时搜索新闻文章、拉取股票数据并检查社交媒体提及。",
                    "   ○ 优势：比顺序工作更快地收集完整视图。",
                ]
            ),
        )
        self.assertIn("&nbsp;&nbsp;&nbsp;○", rendered_html)
        self.assertIn("export_book_list_target_layout_restored", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_keep_single_bulleted_prompt_item_as_paragraph(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="bulleted-prompt-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-bulleted-prompt",
            source_path="/tmp/bulleted-prompt.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="bulleted-prompt-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 24, "source_page_end": 24},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="bulleted-prompt-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text="●\u200b Prompt 1: Attempt to extract specific fields (e.g., name, address, amount) from an\ninvoice document.",
            normalized_text="",
            source_anchor="pdf://page/24#b28",
            source_span_json={
                "source_page_start": 24,
                "source_page_end": 24,
                "source_bbox_json": {"regions": [{"page_number": 24, "bbox": [94.0, 148.0, 704.0, 228.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 151,
            },
            parse_confidence=0.94,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="bulleted-prompt-sentence-1",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=block.source_text,
            normalized_text=block.source_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/24#s28",
            source_span_json={},
            upstream_confidence=0.94,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="bulleted-prompt-target-1",
            chapter_id=chapter.id,
            translation_run_id="bulleted-prompt-run",
            ordinal=1,
            text_zh="● 提示1：尝试从发票文档中提取特定字段（例如，名称、地址、金额）。",
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="bulleted-prompt-edge-1",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertNotIn("export_book_code_promoted", render_blocks[0].source_metadata.get("recovery_flags", []))

    def test_export_service_detects_multiline_json_artifact_text(self) -> None:
        json_text = (
            "{\n"
            '"trends": [\n'
            "{\n"
            '"trend_name": "AI-Powered Personalization",\n'
            '"supporting_data": "73% of consumers prefer to do business with brands that use personal information "\n'
            "}\n"
            "]\n"
            "}"
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertTrue(service._looks_like_code_artifact_text(json_text))

    def test_render_blocks_split_json_code_artifact_with_trailing_prose_suffix(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="split-json-code-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-split-json-code",
            source_path="/tmp/split-json-code.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="split-json-code-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 21, "source_page_end": 23},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="split-json-code-title",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.HEADING,
                source_text="Chapter 1: Prompt Chaining",
                normalized_text="",
                source_anchor="pdf://page/21#b1",
                source_span_json={
                    "source_page_start": 21,
                    "source_page_end": 21,
                    "source_bbox_json": {"regions": [{"page_number": 21, "bbox": [94.0, 108.0, 704.0, 142.0]}]},
                    "pdf_block_role": "heading",
                    "pdf_page_family": "body",
                    "reading_order_index": 1,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="split-json-code-lead",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.PARAGRAPH,
                source_text="For example, the output from the trend identification step could be formatted as a JSON object:",
                normalized_text="",
                source_anchor="pdf://page/22#b136",
                source_span_json={
                    "source_page_start": 22,
                    "source_page_end": 22,
                    "source_bbox_json": {"regions": [{"page_number": 22, "bbox": [94.0, 516.0, 704.0, 548.0]}]},
                    "pdf_block_role": "body",
                    "pdf_page_family": "body",
                    "reading_order_index": 136,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="split-json-code-body",
                chapter_id=chapter.id,
                ordinal=3,
                block_type=BlockType.CODE,
                source_text=(
                    "{\n"
                    '"trends": [\n'
                    "{\n"
                    '"trend_name": "AI-Powered Personalization",\n'
                    '"supporting_data": "73% of consumers prefer to do business with brands that use personal information "\n'
                    "}\n"
                    "]\n"
                    "} This structured format ensures that the data is machine-readable and can be precisely parsed."
                ),
                normalized_text="",
                source_anchor="pdf://page/23#b5",
                source_span_json={
                    "source_page_start": 23,
                    "source_page_end": 23,
                    "source_bbox_json": {"regions": [{"page_number": 23, "bbox": [94.0, 120.0, 704.0, 342.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 5,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 4)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[1].artifact_kind, None)
        self.assertEqual(render_blocks[1].source_text, blocks[1].source_text)
        self.assertEqual(render_blocks[2].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[2].artifact_kind, "code")
        self.assertIn('"trend_name": "AI-Powered Personalization"', render_blocks[2].source_text)
        self.assertNotIn("This structured format ensures", render_blocks[2].source_text)
        self.assertEqual(render_blocks[3].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[3].artifact_kind, None)
        self.assertIn("This structured format ensures that the data is machine-readable", render_blocks[3].source_text)
        self.assertIn("export_mixed_code_prose_split", render_blocks[2].source_metadata["recovery_flags"])
        self.assertIn("export_mixed_code_prose_split", render_blocks[3].source_metadata["recovery_flags"])

    def test_render_blocks_split_code_artifact_with_zero_width_bare_function_call_before_trailing_prose(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="split-zero-width-main-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-split-zero-width-main",
            source_path="/tmp/split-zero-width-main.pdf",
            title="Routing",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="split-zero-width-main-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Hands-On Code Example",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 33, "source_page_end": 33},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="split-zero-width-main-code",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CODE,
            source_text=(
                'blog_creation_crew = Crew(\n'
                '    agents=[researcher, writer],\n'
                ")\n"
                'if __name__ == "__main__":\n'
                "main()\u200b\n"
                "We will now delve into further examples within the Google ADK framework."
            ),
            normalized_text="",
            source_anchor="pdf://page/33#b7",
            source_span_json={
                "source_page_start": 33,
                "source_page_end": 33,
                "source_bbox_json": {"regions": [{"page_number": 33, "bbox": [94.0, 120.0, 704.0, 342.0]}]},
                "pdf_block_role": "code_like",
                "pdf_page_family": "body",
                "reading_order_index": 7,
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.PROTECT,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn('if __name__ == "__main__":', render_blocks[0].source_text)
        self.assertIn("main()", render_blocks[0].source_text)
        self.assertNotIn("We will now delve into further examples", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[1].artifact_kind, None)
        self.assertNotIn("main()", render_blocks[1].source_text)
        self.assertTrue(render_blocks[1].source_text.startswith("We will now delve into further examples"))
        self.assertIn("export_mixed_code_prose_split", render_blocks[0].source_metadata["recovery_flags"])
        self.assertIn("export_mixed_code_prose_split", render_blocks[1].source_metadata["recovery_flags"])

    def test_render_blocks_split_single_line_json_code_artifact_with_trailing_prose_suffix(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="split-single-line-json-code-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-split-single-line-json-code",
            source_path="/tmp/split-single-line-json-code.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="split-single-line-json-code-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 23, "source_page_end": 23},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="split-single-line-json-code-body",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.CODE,
                source_text=(
                    '{"trends": [{"trend_name": "AI-Powered Personalization", '
                    '"supporting_data": "73% of consumers prefer personalized experiences."}]} '
                    "This structured format ensures that the data is machine-readable and can be precisely parsed."
                ),
                normalized_text="",
                source_anchor="pdf://page/23#b5",
                source_span_json={
                    "source_page_start": 23,
                    "source_page_end": 23,
                    "source_bbox_json": {"regions": [{"page_number": 23, "bbox": [94.0, 120.0, 704.0, 342.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 5,
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn('"trend_name": "AI-Powered Personalization"', render_blocks[0].source_text)
        self.assertNotIn("This structured format ensures", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[1].artifact_kind, None)
        self.assertIn("This structured format ensures that the data is machine-readable", render_blocks[1].source_text)

    def test_render_blocks_restore_labeled_prose_refresh_split_from_code_artifact(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="restore-labeled-prose-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-restore-labeled-prose",
            source_path="/tmp/restore-labeled-prose.pdf",
            title="Routing",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="restore-labeled-prose-chapter",
            document_id=document.id,
            ordinal=8,
            title_src="Chapter 2: Routing",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 45, "source_page_end": 45},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="restore-labeled-prose-what",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.CODE,
                source_text="What: Agentic systems must often respond to a wide variety of inputs and situations",
                normalized_text="",
                source_anchor="pdf://page/45#b256",
                source_span_json={
                    "source_page_start": 45,
                    "source_page_end": 45,
                    "source_bbox_json": {"regions": [{"page_number": 45, "bbox": [94.0, 188.0, 704.0, 242.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 256,
                    "recovery_flags": ["mixed_code_prose_split", "leading_code_prefix"],
                    "refresh_split_render_fragments": [
                        {
                            "block_type": "paragraph",
                            "source_text": (
                                "that cannot be handled by a single, linear process. A simple sequential workflow lacks\n"
                                "the ability to make decisions based on context."
                            ),
                            "target_text": None,
                            "source_metadata": {
                                "source_page_start": 45,
                                "source_page_end": 45,
                                "pdf_block_role": "body",
                                "pdf_page_family": "body",
                                "reading_order_index": 257,
                                "recovery_flags": ["mixed_code_prose_split", "trailing_prose_suffix"],
                            },
                        }
                    ],
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="restore-labeled-prose-why",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.CODE,
                source_text="Why: The Routing pattern provides a standardized solution by introducing conditional",
                normalized_text="",
                source_anchor="pdf://page/45#b257",
                source_span_json={
                    "source_page_start": 45,
                    "source_page_end": 45,
                    "source_bbox_json": {"regions": [{"page_number": 45, "bbox": [94.0, 246.0, 704.0, 310.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 257,
                    "recovery_flags": ["mixed_code_prose_split", "leading_code_prefix"],
                    "refresh_split_render_fragments": [
                        {
                            "block_type": "paragraph",
                            "source_text": (
                                "logic into an agent's operational framework. It enables the system to first analyze an\n"
                                "incoming query to determine its intent or nature."
                            ),
                            "target_text": None,
                            "source_metadata": {
                                "source_page_start": 45,
                                "source_page_end": 45,
                                "pdf_block_role": "body",
                                "pdf_page_family": "body",
                                "reading_order_index": 258,
                                "recovery_flags": ["mixed_code_prose_split", "trailing_prose_suffix"],
                            },
                        }
                    ],
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertIn("that cannot be handled by a single, linear process", render_blocks[0].source_text)
        self.assertIn(
            "export_refresh_split_labeled_prose_restored",
            render_blocks[0].source_metadata["recovery_flags"],
        )
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertIsNone(render_blocks[1].artifact_kind)
        self.assertTrue(render_blocks[1].source_text.startswith("Why: The Routing pattern"))
        self.assertNotIn("What:", render_blocks[1].source_text)

    def test_render_blocks_restore_code_refresh_split_continuation(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="restore-code-refresh-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-restore-code-refresh",
            source_path="/tmp/restore-code-refresh.pdf",
            title="Routing",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="restore-code-refresh-chapter",
            document_id=document.id,
            ordinal=8,
            title_src="Chapter 2: Routing",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 37, "source_page_end": 40},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="restore-code-refresh-body",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.CODE,
                source_text=(
                    "from langchain_core.runnables import RunnablePassthrough,\n"
                    "RunnableBranch"
                ),
                normalized_text="",
                source_anchor="pdf://page/37#b235",
                source_span_json={
                    "source_page_start": 37,
                    "source_page_end": 40,
                    "source_bbox_json": {"regions": [{"page_number": 37, "bbox": [94.0, 120.0, 704.0, 640.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 235,
                    "recovery_flags": ["cross_page_repaired", "mixed_code_prose_split", "leading_code_prefix"],
                    "refresh_split_render_fragments": [
                        {
                            "block_type": "paragraph",
                            "source_text": (
                                "# --- Configuration ---\n"
                                "# Ensure your API key environment variable is set.\n"
                                "try:\n"
                                'llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)\n'
                                "coordinator_router_prompt = ChatPromptTemplate.from_messages([\n"
                                '("user", "{request}")\n'
                                "])"
                            ),
                            "target_text": None,
                            "source_metadata": {
                                "source_page_start": 37,
                                "source_page_end": 40,
                                "pdf_block_role": "body",
                                "pdf_page_family": "body",
                                "reading_order_index": 236,
                                "recovery_flags": ["mixed_code_prose_split", "trailing_prose_suffix"],
                            },
                        }
                    ],
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn("RunnableBranch", render_blocks[0].source_text)
        self.assertIn("coordinator_router_prompt = ChatPromptTemplate.from_messages([", render_blocks[0].source_text)
        self.assertIn("export_refresh_split_code_restored", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_restore_quoted_code_refresh_split_and_strip_trailing_prose(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="restore-quoted-code-refresh-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-restore-quoted-code-refresh",
            source_path="/tmp/restore-quoted-code-refresh.pdf",
            title="Prompt Chaining",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="restore-quoted-code-refresh-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter 1: Prompt Chaining",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 27, "source_page_end": 29},
            created_at=now,
            updated_at=now,
        )
        blocks = [
            Block(
                id="restore-quoted-code-imports",
                chapter_id=chapter.id,
                ordinal=1,
                block_type=BlockType.CODE,
                source_text=(
                    "import os\n"
                    "from langchain_openai import ChatOpenAI\n"
                    "from langchain_core.prompts import ChatPromptTemplate\n"
                    "from langchain_core.output_parsers import StrOutputParser\n"
                    "# For better security, load environment variables from a .env file\n"
                    "# from dotenv import load_dotenv"
                ),
                normalized_text="",
                source_anchor="pdf://page/27#b173",
                source_span_json={
                    "source_page_start": 27,
                    "source_page_end": 27,
                    "source_bbox_json": {"regions": [{"page_number": 27, "bbox": [94.0, 120.0, 704.0, 342.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 173,
                    "recovery_flags": ["late_code_like_promoted"],
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            Block(
                id="restore-quoted-code-body",
                chapter_id=chapter.id,
                ordinal=2,
                block_type=BlockType.CODE,
                source_text=(
                    "# load_dotenv()\n"
                    "# Make sure your OPENAI_API_KEY is set in the .env file\n"
                    "# Initialize the Language Model (using ChatOpenAI is recommended)\n"
                    "llm = ChatOpenAI(temperature=0)\n"
                    "# --- Prompt 1: Extract Information ---\n"
                    "prompt_extract = ChatPromptTemplate.from_template("
                ),
                normalized_text="",
                source_anchor="pdf://page/28#b175",
                source_span_json={
                    "source_page_start": 28,
                    "source_page_end": 29,
                    "source_bbox_json": {"regions": [{"page_number": 28, "bbox": [94.0, 120.0, 704.0, 640.0]}]},
                    "pdf_block_role": "code_like",
                    "pdf_page_family": "body",
                    "reading_order_index": 175,
                    "recovery_flags": ["cross_page_repaired", "mixed_code_prose_split", "leading_code_prefix"],
                    "refresh_split_render_fragments": [
                        {
                            "block_type": "code",
                            "source_text": (
                                '"Extract the technical specifications from the following\n'
                                'text:\\n\\n{text_input}"\n'
                                ")\n"
                                "# --- Prompt 2: Transform to JSON ---\n"
                                "prompt_transform = ChatPromptTemplate.from_template(\n"
                                "\"Transform the following specifications into a JSON object with\\n\\n{specifications}\"\n"
                                ")\n"
                                "final_result = full_chain.invoke({\"text_input\": input_text})\n"
                                "print(\"\\n--- Final JSON Output ---\")\n"
                                "print(final_result)\n"
                                "This Python code demonstrates how to use the LangChain library to process text. "
                                "It utilizes two separate prompts and then prints the final result."
                            ),
                            "target_text": None,
                            "source_metadata": {
                                "source_page_start": 28,
                                "source_page_end": 29,
                                "pdf_block_role": "code_like",
                                "pdf_page_family": "body",
                                "reading_order_index": 176,
                                "recovery_flags": ["mixed_code_prose_split", "trailing_prose_suffix"],
                            },
                        }
                    ],
                },
                parse_confidence=0.95,
                protected_policy=ProtectedPolicy.PROTECT,
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ]
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=blocks,
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn("import os", render_blocks[0].source_text)
        self.assertIn('"Extract the technical specifications from the following', render_blocks[0].source_text)
        self.assertIn('print(final_result)', render_blocks[0].source_text)
        self.assertNotIn("This Python code demonstrates", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertIsNone(render_blocks[1].artifact_kind)
        self.assertTrue(render_blocks[1].source_text.startswith("This Python code demonstrates"))
        self.assertIn("export_refresh_split_code_restored", render_blocks[0].source_metadata["recovery_flags"])

    def test_render_blocks_do_not_promote_wrapped_runnableparallel_prose_to_code(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="runnableparallel-prose-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-runnableparallel-prose",
            source_path="/tmp/runnableparallel-prose.pdf",
            title="Parallelization",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="runnableparallel-prose-chapter",
            document_id=document.id,
            ordinal=3,
            title_src="Chapter 3: Parallelization",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 55, "source_page_end": 55},
            created_at=now,
            updated_at=now,
        )
        source_text = (
            "A RunnableParallel block is then constructed to bundle these three chains, allowing\n"
            "them to execute simultaneously. This parallel runnable also includes a\n"
            "RunnablePassthrough to ensure the original input topic is available for subsequent\n"
            "steps. A separate ChatPromptTemplate is defined for the final synthesis step, taking\n"
            "the summary, questions, key terms, and the original topic as input to generate a\n"
            "comprehensive answer. The full end-to-end processing chain, named\n"
            "full_parallel_chain, is created by sequencing the map_chain (the parallel block) into\n"
            "the synthesis prompt, followed by the language model and the output parser. An\n"
            "asynchronous function run_parallel_example is provided to demonstrate how to\n"
            'invoke this full_parallel_chain. This function takes the topic as input and uses invoke to\n'
            'run the asynchronous chain. Finally, the standard Python if __name__ == "__main__":\n'
            'block shows how to execute the run_parallel_example with a sample topic.'
        )
        block = Block(
            id="runnableparallel-prose-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=source_text,
            normalized_text="",
            source_anchor="pdf://page/55#b368",
            source_span_json={
                "source_page_start": 55,
                "source_page_end": 55,
                "source_bbox_json": {"regions": [{"page_number": 55, "bbox": [94.0, 186.0, 704.0, 522.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 368,
            },
            parse_confidence=0.95,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        sentence = Sentence(
            id="runnableparallel-prose-sentence-1",
            block_id=block.id,
            chapter_id=chapter.id,
            document_id=document.id,
            ordinal_in_block=1,
            source_text=source_text,
            normalized_text=source_text,
            source_lang="en",
            translatable=True,
            nontranslatable_reason=None,
            source_anchor="pdf://page/55#s368",
            source_span_json={},
            upstream_confidence=0.95,
            sentence_status=SentenceStatus.TRANSLATED,
            active_version=1,
            created_at=now,
            updated_at=now,
        )
        target_segment = TargetSegment(
            id="runnableparallel-prose-target-1",
            chapter_id=chapter.id,
            translation_run_id="runnableparallel-prose-run",
            ordinal=1,
            text_zh="随后会构造一个 RunnableParallel 块来捆绑这三条链，使它们能够同时执行。",
            segment_type="sentence",
            confidence=0.95,
            final_status=TargetSegmentStatus.FINALIZED,
            created_at=now,
            updated_at=now,
        )
        alignment_edge = AlignmentEdge(
            id="runnableparallel-prose-edge-1",
            sentence_id=sentence.id,
            target_segment_id=target_segment.id,
            relation_type=RelationType.ONE_TO_ONE,
            confidence=0.95,
            created_by=ActorType.MODEL,
            created_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[sentence],
            packets=[],
            translation_runs=[],
            target_segments=[target_segment],
            alignment_edges=[alignment_edge],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 1)
        self.assertEqual(render_blocks[0].block_type, BlockType.PARAGRAPH.value)
        self.assertEqual(render_blocks[0].render_mode, "zh_primary_with_optional_source")
        self.assertIsNone(render_blocks[0].artifact_kind)
        self.assertNotIn("export_book_code_promoted", render_blocks[0].source_metadata.get("recovery_flags", []))

    def test_render_blocks_promote_wrapped_shell_command_to_code_and_split_trailing_prose(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="shell-command-code-doc",
            source_type=SourceType.PDF_SCAN,
            file_fingerprint="fingerprint-shell-command-code",
            source_path="/tmp/shell-command-code.pdf",
            title="Routing",
            status=DocumentStatus.EXPORTED,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="shell-command-code-chapter",
            document_id=document.id,
            ordinal=2,
            title_src="Chapter 2: Routing",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={"source_page_start": 37, "source_page_end": 37},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="shell-command-code-block",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.PARAGRAPH,
            source_text=(
                "pip install langchain langgraph google-cloud-aiplatform\n"
                "langchain-google-genai google-adk deprecated pydantic\n"
                "You will also need to set up your environment with your API key for the language model you choose."
            ),
            normalized_text="",
            source_anchor="pdf://page/37#b266",
            source_span_json={
                "source_page_start": 37,
                "source_page_end": 37,
                "source_bbox_json": {"regions": [{"page_number": 37, "bbox": [94.0, 148.0, 704.0, 322.0]}]},
                "pdf_block_role": "body",
                "pdf_page_family": "body",
                "reading_order_index": 266,
            },
            parse_confidence=0.94,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[block],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            render_blocks = service._render_blocks_for_chapter(bundle)

        self.assertEqual(len(render_blocks), 2)
        self.assertEqual(render_blocks[0].block_type, BlockType.CODE.value)
        self.assertEqual(render_blocks[0].artifact_kind, "code")
        self.assertIn("pip install langchain", render_blocks[0].source_text)
        self.assertIn("langchain-google-genai google-adk deprecated pydantic", render_blocks[0].source_text)
        self.assertNotIn("You will also need to set up your environment", render_blocks[0].source_text)
        self.assertEqual(render_blocks[1].block_type, BlockType.PARAGRAPH.value)
        self.assertTrue(render_blocks[1].source_text.startswith("You will also need to set up your environment"))

    def test_short_source_paragraph_drops_suspicious_target_text(self) -> None:
        block = MergedRenderBlock(
            block_id="demote-short-prose-code-target",
            chapter_id="chapter-1",
            block_type=BlockType.PARAGRAPH.value,
            render_mode="zh_primary_with_optional_source",
            artifact_kind=None,
            title=None,
            source_text="With all my love.",
            target_text=(
                "致我的儿子布鲁诺，他在两岁时为我的人生带来了崭新而璀璨的光芒。"
                "感谢Lee Boonstra在提示工程方面的出色工作。"
                "特别感谢Patti Maes，她在90年代率先提出软件代理的概念。"
            ),
            source_metadata={"pdf_block_role": "code_like", "pdf_page_family": "frontmatter"},
            source_sentence_ids=[],
            target_segment_ids=["segment-1"],
            is_expected_source_only=False,
            notice="代码保持原样",
        )

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            self.assertTrue(service._should_clear_suspicious_short_source_target_text(block))
            repaired = service._drop_render_block_target_text(
                block,
                flag="export_book_short_source_target_cleared",
            )

        self.assertIsNone(repaired.target_text)
        self.assertEqual(repaired.block_type, BlockType.PARAGRAPH.value)
        self.assertIn("export_book_short_source_target_cleared", repaired.source_metadata["recovery_flags"])

    def test_layout_guided_pdf_crop_bbox_prefers_seed_aligned_image_block(self) -> None:
        class _FakeRect:
            x0 = 0.0
            y0 = 0.0
            x1 = 768.0
            y1 = 1024.0

        class _FakePage:
            rect = _FakeRect()

            def get_text(self, mode: str):
                assert mode == "blocks"
                return [
                    (92.0, 108.0, 706.0, 174.0, "Text above the image.", 0, 0),
                    (182.0, 206.0, 598.0, 472.0, "", 1, 1),
                    (96.0, 486.0, 708.0, 526.0, "Caption or text below.", 2, 0),
                ]

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            bbox = service._layout_guided_pdf_crop_bbox(_FakePage(), [128.0, 144.0, 644.0, 528.0])

        self.assertEqual(bbox, [172.0, 196.0, 608.0, 482.0])

    def test_caption_anchored_pdf_crop_bbox_stays_above_caption_region(self) -> None:
        block = MergedRenderBlock(
            block_id="caption-1",
            chapter_id="chapter-1",
            block_type=BlockType.CAPTION.value,
            render_mode="image_anchor_with_translated_caption",
            artifact_kind="image",
            title=None,
            source_text="Fig. 3: Various instances demonstrating the spectrum of agent complexity.",
            target_text="图 3：展示智能体复杂度谱系的不同实例。",
            source_metadata={
                "source_bbox_json": {
                    "regions": [
                        {"page_number": 16, "bbox": [146.0, 482.0, 672.0, 497.0]}
                    ]
                }
            },
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="图片锚点保留",
        )

        class _FakeRect:
            x0 = 0.0
            y0 = 0.0
            x1 = 768.0
            y1 = 1024.0

        class _FakePage:
            rect = _FakeRect()

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            crop_spec = service._pdf_asset_crop_spec(block)
            bbox = service._caption_anchored_pdf_crop_bbox(_FakePage(), crop_spec[2] if crop_spec else [])

        self.assertEqual(crop_spec, ("caption_anchor", 16, [146.0, 482.0, 672.0, 497.0]))
        assert bbox is not None
        self.assertLess(bbox[1], 482.0)
        self.assertLess(bbox[0], 146.0)
        self.assertGreater(bbox[2], 672.0)

    def test_caption_anchored_pdf_crop_bbox_prefers_layout_image_block(self) -> None:
        class _FakeRect:
            x0 = 0.0
            y0 = 0.0
            x1 = 768.0
            y1 = 1024.0

        class _FakePage:
            rect = _FakeRect()

            def get_text(self, mode: str):
                assert mode == "blocks"
                return [
                    (72.0, 96.0, 708.0, 164.0, "Introductory paragraph above the figure.", 0, 0),
                    (184.0, 214.0, 612.0, 454.0, "", 1, 1),
                    (620.0, 220.0, 732.0, 436.0, "Right column text.", 2, 0),
                ]

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            bbox = service._caption_anchored_pdf_crop_bbox(_FakePage(), [208.0, 482.0, 590.0, 497.0])

        self.assertEqual(bbox, [172.0, 202.0, 624.0, 466.0])

    def test_caption_anchored_pdf_crop_bbox_trims_above_overlapping_text_blocks(self) -> None:
        class _FakeRect:
            x0 = 0.0
            y0 = 0.0
            x1 = 768.0
            y1 = 1024.0

        class _FakePage:
            rect = _FakeRect()

            def get_text(self, mode: str):
                assert mode == "blocks"
                return [
                    (138.0, 126.0, 682.0, 238.0, "Paragraph text immediately above the figure.", 0, 0),
                    (54.0, 90.0, 120.0, 196.0, "Left margin note", 1, 0),
                ]

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            bbox = service._caption_anchored_pdf_crop_bbox(_FakePage(), [146.0, 482.0, 672.0, 497.0])

        assert bbox is not None
        self.assertGreaterEqual(bbox[1], 250.0)
        self.assertLess(bbox[3], 482.0)

    def test_export_epub_archive_assets_recovers_legacy_figure_image_path_from_caption(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="Para">Intro text.
      <figure class="Figure" id="Fig1">
        <div class="MediaObject" id="MO1">
          <img alt="" src="../images/ch1/fig1.png" />
        </div>
        <figcaption class="Caption" lang="en">
          <div class="CaptionContent">
            <span class="CaptionNumber">Fig. 1.1</span>
            <p class="SimplePara">The historical trajectory of AI Agents</p>
          </div>
        </figcaption>
      </figure>
    </div>
  </body>
</html>
"""

        block = MergedRenderBlock(
            block_id="legacy-epub-figure-caption",
            chapter_id="chapter-1",
            block_type=BlockType.CAPTION.value,
            render_mode="image_anchor_with_translated_caption",
            artifact_kind="figure",
            title=None,
            source_text="Fig. 1.1The historical trajectory of AI Agents",
            target_text="图 1.1 AI 智能体的历史发展轨迹",
            source_metadata={"source_path": "OEBPS/html/chapter1.xhtml", "tag": "figcaption"},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="图片锚点保留",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "legacy-figure.epub"
            output_dir = Path(tmpdir) / "export"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("OEBPS/html/chapter1.xhtml", chapter_xhtml)
                archive.writestr("OEBPS/images/ch1/fig1.png", b"fake-png-bytes")

            with self.session_factory() as session:
                service = ExportService(ExportRepository(session))
                asset_map = service._export_epub_archive_assets(str(epub_path), [block], output_dir)

            self.assertEqual(
                asset_map,
                {"legacy-epub-figure-caption": "assets/OEBPS/images/ch1/fig1.png"},
            )
            self.assertTrue((output_dir / "assets/OEBPS/images/ch1/fig1.png").is_file())

    def test_export_epub_archive_assets_recovers_legacy_figure_image_path_from_malformed_xhtml(self) -> None:
        chapter_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <div class="Para">AT&T legacy prose before the figure.
      <figure class="Figure" id="Fig1">
        <div class="MediaObject" id="MO1">
          <img alt="" src="../images/ch1/fig1.png" />
        </div>
        <figcaption class="Caption" lang="en">
          <div class="CaptionContent">
            <span class="CaptionNumber">Fig. 1.1</span>
            <p class="SimplePara">The historical trajectory of AI Agents</p>
          </div>
        </figcaption>
      </figure>
    </div>
  </body>
</html>
"""

        block = MergedRenderBlock(
            block_id="legacy-epub-figure-caption-malformed",
            chapter_id="chapter-1",
            block_type=BlockType.CAPTION.value,
            render_mode="image_anchor_with_translated_caption",
            artifact_kind="image",
            title=None,
            source_text="Fig. 1.1The historical trajectory of AI Agents",
            target_text="图 1.1 AI 智能体的历史发展轨迹",
            source_metadata={"source_path": "OEBPS/html/chapter1.xhtml", "tag": "figcaption"},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="图片锚点保留",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            epub_path = Path(tmpdir) / "legacy-figure-malformed.epub"
            output_dir = Path(tmpdir) / "export"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("OEBPS/html/chapter1.xhtml", chapter_xhtml)
                archive.writestr("OEBPS/images/ch1/fig1.png", b"fake-png-bytes")

            with self.session_factory() as session:
                service = ExportService(ExportRepository(session))
                asset_map = service._export_epub_archive_assets(str(epub_path), [block], output_dir)

            self.assertEqual(
                asset_map,
                {"legacy-epub-figure-caption-malformed": "assets/OEBPS/images/ch1/fig1.png"},
            )
            self.assertTrue((output_dir / "assets/OEBPS/images/ch1/fig1.png").is_file())

    def test_extract_main_chapter_number_rejects_lowercase_fragment_after_number(self) -> None:
        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))

        self.assertEqual(service._extract_main_chapter_number("Chapter 8: Memory Management"), 8)
        self.assertIsNone(
            service._extract_main_chapter_number(
                "Chapter 8) is initialized to manage sessions for the agent."
            )
        )

    def test_review_detects_packet_context_failure_and_routes_to_packet_rebuild(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            packet = session.get(TranslationPacket, packet_id)
            assert packet is not None
            packet_json = dict(packet.packet_json)
            packet_json["open_questions"] = ["speaker_reference_ambiguous"]
            packet.packet_json = packet_json
            session.merge(packet)
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            context_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "CONTEXT_FAILURE")
            context_action = next(action for action in review_artifacts.actions if action.issue_id == context_issue.id)

            self.assertEqual(context_issue.root_cause_layer.value, "packet")
            self.assertEqual(context_action.action_type, ActionType.REBUILD_PACKET_THEN_RERUN)
            self.assertEqual(context_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(context_action.scope_id, packet_id)

    def test_review_reports_unlocked_key_concept_from_chapter_memory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            concept_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            )
            concept_action = next(
                action for action in review_artifacts.actions if action.issue_id == concept_issue.id
            )

            self.assertEqual(concept_issue.root_cause_layer.value, "memory")
            self.assertFalse(concept_issue.blocking)
            self.assertEqual(concept_action.action_type, ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED)
            self.assertEqual(concept_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(concept_action.scope_id, concept_issue.packet_id)
            self.assertIn("context engineering", concept_issue.evidence_json["source_term"].lower())
            self.assertEqual(len(concept_issue.evidence_json.get("packet_ids_seen") or []), 1)
            self.assertEqual(concept_issue.evidence_json.get("mention_count"), 2)

    def test_review_scopes_single_packet_unlocked_key_concept_to_packet(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=ConsistentContextEngineeringWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            concept_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            )
            concept_action = next(
                action for action in review_artifacts.actions if action.issue_id == concept_issue.id
            )

            self.assertEqual(concept_issue.evidence_json.get("packet_ids_seen"), [concept_issue.packet_id])
            self.assertEqual(concept_issue.evidence_json.get("mention_count"), 2)
            self.assertEqual(concept_action.action_type, ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED)
            self.assertEqual(concept_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(concept_action.scope_id, concept_issue.packet_id)

    def test_unlocked_key_concept_action_targets_only_packets_seen(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_THREE_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            self.assertGreaterEqual(len(packet_ids), 3)
            service = TranslationService(TranslationRepository(session), worker=ConsistentContextEngineeringWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            concept_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            )
            concept_action = next(
                action for action in review_artifacts.actions if action.issue_id == concept_issue.id
            )
            affected_packet_ids = set(concept_issue.evidence_json.get("packet_ids_seen") or [])
            unaffected_packet_ids = set(packet_ids) - affected_packet_ids
            self.assertEqual(concept_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(len(affected_packet_ids), 2)

            action_execution = IssueActionExecutor(OpsRepository(session)).execute(concept_action.id)
            invalidated_packet_ids = {
                invalidation.object_id
                for invalidation in action_execution.invalidations
                if invalidation.object_type.value == "packet"
            }

            self.assertEqual(action_execution.rerun_plan.scope_type, JobScopeType.PACKET)
            self.assertEqual(set(action_execution.rerun_plan.scope_ids), affected_packet_ids)
            self.assertEqual(invalidated_packet_ids, affected_packet_ids)
            self.assertTrue(unaffected_packet_ids.isdisjoint(invalidated_packet_ids))

    def test_review_skips_unlocked_key_concept_when_locked_term_entry_exists(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter = bundle.chapters[0].chapter
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.flush()
            sentence_id = next(
                sentence.id
                for sentence in bundle.chapters[0].sentences
                if "Context engineering" in sentence.source_text
            )
            session.add(
                TermEntry(
                    id=stable_id("term-entry", document_id, chapter.id, "context engineering", 1),
                    document_id=document_id,
                    scope_type=MemoryScopeType.CHAPTER,
                    scope_id=chapter.id,
                    source_term="context engineering",
                    target_term="上下文工程",
                    term_type=TermType.CONCEPT,
                    lock_level=LockLevel.LOCKED,
                    status=TermStatus.ACTIVE,
                    evidence_sentence_id=sentence_id,
                    version=1,
                )
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertEqual(unlocked_issues, [])

    def test_review_reports_style_drift_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertGreaterEqual(len(style_issues), 3)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            style_actions = [
                action
                for action in review_artifacts.actions
                if action.issue_id in {issue.id for issue in style_issues}
            ]
            self.assertTrue(style_actions)
            self.assertTrue(all(action.action_type == ActionType.RERUN_PACKET for action in style_actions))
            self.assertTrue(all(action.scope_type == JobScopeType.PACKET for action in style_actions))
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "上下文工程" for issue in style_issues)
            )
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "有人开始将其称为……，它指的是……" for issue in style_issues)
            )
            self.assertTrue(
                any("证据权重显示" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "更符合上下文的输出" for issue in style_issues)
            )
            self.assertTrue(
                any("更具上下文准确性" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )
            self.assertTrue(
                any("称之为情境工程的内容" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )
            self.assertTrue(any(issue.evidence_json.get("prompt_guidance") for issue in style_issues))
            naturalness_summary = review_artifacts.summary.naturalness_summary
            self.assertIsNotNone(naturalness_summary)
            assert naturalness_summary is not None
            self.assertTrue(naturalness_summary.advisory_only)
            self.assertEqual(naturalness_summary.style_drift_issue_count, len(style_issues))
            self.assertGreaterEqual(naturalness_summary.affected_packet_count, 1)
            self.assertIn("context_engineering_literal", naturalness_summary.dominant_style_rules)
            self.assertIn("上下文工程", naturalness_summary.preferred_hints)
            self.assertIn("更符合上下文的输出", naturalness_summary.preferred_hints)

    def test_review_reports_knowledge_timeline_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", KNOWLEDGE_TIMELINE_LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertTrue(style_issues)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "已知内容、知晓这些内容的时间点，以及其对行动的重要性" for issue in style_issues)
            )
            self.assertTrue(
                any("获知时间" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )

    def test_review_reports_durable_substrate_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", DURABLE_SUBSTRATE_LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertTrue(style_issues)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            self.assertTrue(
                any(issue.evidence_json.get("preferred_hint") == "使上下文得以持久存在的基础" for issue in style_issues)
            )
            self.assertTrue(
                any("持久基底" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )

    def test_review_reports_profound_responsibility_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", RESPONSIBILITY_LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertTrue(style_issues)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            self.assertTrue(
                any(
                    issue.evidence_json.get("preferred_hint") == "强烈的责任感 / 很强的责任意识"
                    for issue in style_issues
                )
            )
            self.assertTrue(
                any("深刻的责任感" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )
            self.assertTrue(
                any(
                    "深刻的责任感" in str(issue.evidence_json.get("prompt_guidance") or "")
                    for issue in style_issues
                )
            )

    def test_review_reports_consistency_care_service_literalism_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONSISTENCY_CARE_LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertTrue(style_issues)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            self.assertTrue(
                any(
                    issue.evidence_json.get("preferred_hint") == "长期稳定、周到地照应 / 始终如一地细心照应"
                    for issue in style_issues
                )
            )
            self.assertTrue(
                any("长期服务中提供连贯性" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )
            self.assertTrue(
                any(
                    "avoid abstract service-language" in str(issue.evidence_json.get("prompt_guidance") or "")
                    for issue in style_issues
                )
            )

    def test_review_reports_agency_autonomy_collapse_as_non_blocking_advisory(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AGENCY_AUTONOMY_LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            style_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "STYLE_DRIFT"]
            self.assertTrue(style_issues)
            self.assertTrue(all(issue.root_cause_layer.value == "packet" for issue in style_issues))
            self.assertTrue(all(not issue.blocking for issue in style_issues))
            self.assertTrue(
                any(
                    "不要把 agency 译成“自主性”" in str(issue.evidence_json.get("preferred_hint") or "")
                    for issue in style_issues
                )
            )
            self.assertTrue(
                any("自主性" in str(issue.evidence_json.get("matched_target_excerpt") or "") for issue in style_issues)
            )

    def test_packet_style_drift_action_rerun_uses_aggregated_hints_and_resolves_packet_issues(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LiteralismWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_service = ReviewService(ReviewRepository(session))
            review_artifacts = review_service.review_chapter(chapter_id)
            target_issue = next(
                issue
                for issue in review_artifacts.issues
                if issue.issue_type == "STYLE_DRIFT"
                and issue.evidence_json.get("preferred_hint") == "更符合上下文的输出"
            )
            target_action = next(action for action in review_artifacts.actions if action.issue_id == target_issue.id)
            action_execution = IssueActionExecutor(OpsRepository(session)).execute(target_action.id)

            rerun_service = RerunService(
                OpsRepository(session),
                TranslationService(TranslationRepository(session), worker=GuidanceAwareLiteralismWorker()),
                review_service,
                TargetedRebuildService(session, BootstrapRepository(session)),
                RealignService(OpsRepository(session)),
            )
            rerun_execution = rerun_service.execute(action_execution.rerun_plan)
            session.commit()

            self.assertTrue(any("大量证据表明" in hint for hint in rerun_execution.rerun_plan.style_hints))
            self.assertTrue(any("更符合上下文的输出" in hint for hint in rerun_execution.rerun_plan.style_hints))
            self.assertTrue(any("上下文更准确的输出" in hint for hint in rerun_execution.rerun_plan.style_hints))
            self.assertTrue(
                any("Prefer natural Chinese evidential phrasing" in hint for hint in rerun_execution.rerun_plan.style_hints)
            )
            self.assertTrue(rerun_execution.issue_resolved)

            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            remaining_packet_style_issues = [
                issue
                for issue in final_review.issues
                if issue.issue_type == "STYLE_DRIFT"
                and issue.packet_id == target_issue.packet_id
            ]
            self.assertEqual(remaining_packet_style_issues, [])

    def test_review_reports_term_conflict_for_locked_chapter_concept_entry(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AGENTIC_AI_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=AgenticLiteralWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="代理式AI",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertTrue(term_issues)
            self.assertTrue(
                any(issue.evidence_json.get("expected_target_term") == "代理式AI" for issue in term_issues)
            )
            self.assertTrue(
                any("智能体AI" in str(issue.evidence_json.get("actual_target_text") or "") for issue in term_issues)
            )
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertEqual(unlocked_issues, [])

    def test_review_collapses_term_conflict_variants_with_shared_expected_target(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LANGUAGE_MODELS_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=LanguageModelLiteralWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="language model",
                canonical_zh="大语言模型",
            )
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="language models",
                canonical_zh="大语言模型",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(len(term_issues), 1)
            self.assertEqual(term_issues[0].evidence_json.get("expected_target_term"), "大语言模型")
            self.assertEqual(
                term_issues[0].evidence_json.get("source_terms"),
                ["language model", "language models"],
            )
            persisted_issues = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.chapter_id == chapter_id,
                    ReviewIssue.issue_type == "TERM_CONFLICT",
                )
            ).all()
            self.assertEqual(len(persisted_issues), 1)

    def test_review_skips_term_conflict_for_modified_language_model_variants(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", SMALL_LANGUAGE_MODELS_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=SmallLanguageModelVariantWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="language models",
                canonical_zh="大语言模型",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(term_issues, [])

    def test_review_skips_term_conflict_for_reference_titles_with_variant_translation(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("References", "references.xhtml", REFERENCE_LANGUAGE_MODELS_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=ReferenceLanguageModelWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="language models",
                canonical_zh="大语言模型",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(term_issues, [])

    def test_review_skips_term_conflict_for_ai_agent_product_name_variants(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AI_AGENT_VARIANT_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=CrewAiAgentVariantWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="AI agent",
                canonical_zh="AI智能体",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(term_issues, [])

    def test_review_skips_term_conflict_for_reference_titles_with_ai_agent_product_name(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("References", "references.xhtml", REFERENCE_AI_AGENT_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=ReferenceAiAgentWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="AI agent",
                canonical_zh="AI智能体",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            term_issues = [issue for issue in review_artifacts.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(term_issues, [])

    def test_review_limits_chapter_scoped_locked_terms_to_their_own_chapter(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [
                ("Chapter One", "chapter1.xhtml", LANGUAGE_MODELS_XHTML),
                ("Chapter Two", "chapter2.xhtml", LANGUAGE_MODELS_XHTML),
            ]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_ids = [chapter_bundle.chapter.id for chapter_bundle in bundle.chapters]
            service = TranslationService(TranslationRepository(session), worker=LanguageModelLiteralWorker())
            for chapter_bundle in bundle.chapters:
                for packet in chapter_bundle.translation_packets:
                    service.execute_packet(packet.id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_ids[0],
                source_term="language model",
                canonical_zh="大语言模型",
            )
            session.commit()

        with self.session_factory() as session:
            first_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_ids[0])
            first_term_issues = [issue for issue in first_review.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertTrue(first_term_issues)

            second_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_ids[1])
            second_term_issues = [issue for issue in second_review.issues if issue.issue_type == "TERM_CONFLICT"]
            self.assertEqual(second_term_issues, [])

    def test_pdf_layout_review_demotes_single_outlined_book_multi_column_page_to_advisory(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="outlined-book-medium-layout-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-outlined-book-medium-layout",
            source_path="/tmp/outlined-book.pdf",
            title="Outlined Book",
            status=DocumentStatus.ACTIVE,
            metadata_json={
                "pdf_profile": {"recovery_lane": "outlined_book", "layout_risk": "medium"},
                "pdf_page_evidence": {
                    "pdf_pages": [
                        {
                            "page_number": 10,
                            "page_layout_risk": "low",
                            "page_layout_reasons": [],
                            "layout_suspect": False,
                            "role_counts": {"heading": 1, "body": 3},
                            "recovery_flags": [],
                        },
                        {
                            "page_number": 11,
                            "page_layout_risk": "low",
                            "page_layout_reasons": [],
                            "layout_suspect": False,
                            "role_counts": {"body": 5},
                            "recovery_flags": ["cross_page_repaired"],
                        },
                        {
                            "page_number": 12,
                            "page_layout_risk": "medium",
                            "page_layout_reasons": ["multi_column"],
                            "layout_suspect": True,
                            "role_counts": {"body": 13, "footer": 1},
                            "recovery_flags": [],
                        },
                        {
                            "page_number": 13,
                            "page_layout_risk": "low",
                            "page_layout_reasons": [],
                            "layout_suspect": False,
                            "role_counts": {"caption": 1, "body": 4},
                            "recovery_flags": [],
                        },
                    ]
                },
            },
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="outlined-book-medium-layout-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter One",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={
                "source_page_start": 10,
                "source_page_end": 13,
                "parse_confidence": 0.824,
                "pdf_layout_risk": "medium",
                "suspicious_page_numbers": [12],
            },
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterReviewBundle(
            document=document,
            chapter=chapter,
            blocks=[],
            sentences=[],
            packets=[],
            chapter_brief=None,
            chapter_translation_memory=None,
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            term_entries=[],
            existing_issues=[],
        )

        with self.session_factory() as session:
            service = ReviewService(ReviewRepository(session))
            policy = service._pdf_layout_review_policy(
                bundle,
                "medium",
                0.824,
                [12],
                [12],
                [12],
                [],
                document.metadata_json["pdf_page_evidence"]["pdf_pages"],
            )

        self.assertEqual(policy["reason"], "outlined_book_single_multi_column_advisory")
        self.assertFalse(bool(policy["blocking"]))
        self.assertTrue(bool(policy["emit_issue"]))

    def test_pdf_layout_review_keeps_multiple_outlined_book_multi_column_pages_blocking(self) -> None:
        now = datetime.now(timezone.utc)
        document = Document(
            id="outlined-book-multi-layout-doc",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-outlined-book-multi-layout",
            source_path="/tmp/outlined-book.pdf",
            title="Outlined Book",
            status=DocumentStatus.ACTIVE,
            metadata_json={"pdf_profile": {"recovery_lane": "outlined_book", "layout_risk": "medium"}},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="outlined-book-multi-layout-chapter",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter One",
            title_tgt=None,
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.TRANSLATED,
            summary_version=None,
            risk_level=None,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        bundle = ChapterReviewBundle(
            document=document,
            chapter=chapter,
            blocks=[],
            sentences=[],
            packets=[],
            chapter_brief=None,
            chapter_translation_memory=None,
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            term_entries=[],
            existing_issues=[],
        )
        page_evidence = [
            {
                "page_number": 10,
                "page_layout_risk": "low",
                "page_layout_reasons": [],
                "layout_suspect": False,
                "role_counts": {"heading": 1, "body": 3},
                "recovery_flags": [],
            },
            {
                "page_number": 11,
                "page_layout_risk": "medium",
                "page_layout_reasons": ["multi_column"],
                "layout_suspect": True,
                "role_counts": {"body": 9},
                "recovery_flags": [],
            },
            {
                "page_number": 12,
                "page_layout_risk": "medium",
                "page_layout_reasons": ["multi_column"],
                "layout_suspect": True,
                "role_counts": {"body": 10},
                "recovery_flags": [],
            },
            {
                "page_number": 13,
                "page_layout_risk": "low",
                "page_layout_reasons": [],
                "layout_suspect": False,
                "role_counts": {"caption": 1, "body": 4},
                "recovery_flags": [],
            },
        ]

        with self.session_factory() as session:
            service = ReviewService(ReviewRepository(session))
            policy = service._pdf_layout_review_policy(
                bundle,
                "medium",
                0.9,
                [11, 12],
                [11, 12],
                [11, 12],
                [],
                page_evidence,
            )

        self.assertEqual(policy["reason"], "default_blocking_layout_risk")
        self.assertTrue(bool(policy["blocking"]))

    def test_packet_term_conflict_action_rerun_uses_locked_concept_and_resolves_packet_issues(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AGENTIC_AI_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=AgenticLiteralWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="代理式AI",
            )
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_service = ReviewService(ReviewRepository(session))
            review_artifacts = review_service.review_chapter(chapter_id)
            target_issue = next(
                issue
                for issue in review_artifacts.issues
                if issue.issue_type == "TERM_CONFLICT"
                and issue.evidence_json.get("expected_target_term") == "代理式AI"
            )
            target_action = next(action for action in review_artifacts.actions if action.issue_id == target_issue.id)
            self.assertEqual(target_action.scope_type, JobScopeType.PACKET)
            action_execution = IssueActionExecutor(OpsRepository(session)).execute(target_action.id)

            rerun_service = RerunService(
                OpsRepository(session),
                TranslationService(TranslationRepository(session), worker=GuidanceAwareAgenticWorker()),
                review_service,
                TargetedRebuildService(session, BootstrapRepository(session)),
                RealignService(OpsRepository(session)),
            )
            rerun_execution = rerun_service.execute(action_execution.rerun_plan)
            session.commit()

            self.assertEqual(
                [concept.source_term for concept in rerun_execution.rerun_plan.concept_overrides],
                ["agentic AI"],
            )
            self.assertEqual(rerun_execution.rerun_plan.scope_type, JobScopeType.PACKET)
            self.assertTrue(rerun_execution.issue_resolved)

            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            remaining_packet_term_issues = [
                issue
                for issue in final_review.issues
                if issue.issue_type == "TERM_CONFLICT"
                and issue.packet_id == target_issue.packet_id
            ]
            self.assertEqual(remaining_packet_term_issues, [])

    def test_review_reports_stale_chapter_brief_when_late_concept_is_missing(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_ADAPTIVE_AGENT_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issue = next(
                issue for issue in review_artifacts.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            )
            stale_action = next(
                action for action in review_artifacts.actions if action.issue_id == stale_issue.id
            )

            self.assertEqual(stale_issue.root_cause_layer.value, "memory")
            self.assertFalse(stale_issue.blocking)
            self.assertEqual(stale_action.action_type, ActionType.REBUILD_CHAPTER_BRIEF)
            self.assertEqual(stale_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(stale_action.scope_id, chapter_id)
            self.assertIn("adaptive agent", ",".join(stale_issue.evidence_json["missing_concepts"]).lower())
            self.assertEqual(len(stale_issue.evidence_json.get("packet_ids_seen") or []), 2)

    def test_review_skips_stale_chapter_brief_when_missing_concept_is_locked(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_ADAPTIVE_AGENT_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="adaptive agent",
                canonical_zh="自适应智能体",
            )
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            ]
            self.assertEqual(stale_issues, [])

    def test_review_skips_stale_chapter_brief_when_late_high_signal_concept_is_already_in_summary(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_XHTML)]
        )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session))
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            chapter_brief = bundle.chapters[0].chapter_brief
            assert chapter_brief is not None
            summary = str(chapter_brief.content_json.get("summary") or "")
            self.assertIn("Context engineering determines how context is created.", summary)
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            ]
            self.assertEqual(stale_issues, [])

    def test_chapter_concept_auto_lock_service_locks_unresolved_concepts_and_clears_memory_issues(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_XHTML)]
        )

        class _ContextEngineeringWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="ContextEngineeringWorker",
                    model_name="context-engineering-test",
                    prompt_version="test.context-engineering.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                target_segments = []
                alignments = []
                for sentence in task.current_sentences:
                    source_text = sentence.source_text
                    if "Context engineering" in source_text:
                        text = f"上下文工程::{source_text}"
                    else:
                        text = f"译文::{source_text}"
                    temp_id = f"temp-{sentence.id}"
                    target_segments.append(
                        TranslationTargetSegment(
                            temp_id=temp_id,
                            text_zh=text,
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        )
                    )
                    alignments.append(
                        AlignmentSuggestion(
                            source_sentence_ids=[sentence.id],
                            target_temp_ids=[temp_id],
                            relation_type="1:1",
                            confidence=0.95,
                        )
                    )
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=target_segments,
                    alignment_suggestions=alignments,
                )

        class _FakeResolver:
            def resolve(self, *, source_term, chapter_title, chapter_brief, examples):
                if source_term.casefold() != "context engineering":
                    return None, None
                self.last_request = {
                    "source_term": source_term,
                    "chapter_title": chapter_title,
                    "chapter_brief": chapter_brief,
                    "examples": examples,
                }
                return (
                    ConceptResolutionPayload(
                        source_term=source_term,
                        canonical_zh="上下文工程",
                        confidence=0.97,
                        rationale="Examples already use 上下文工程.",
                    ),
                    None,
                )

        resolver = _FakeResolver()

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=_ContextEngineeringWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertTrue(unlocked_issues)

            auto_lock = ChapterConceptAutoLockService(session, resolver=resolver)
            lock_artifacts = auto_lock.auto_lock_chapter_concepts(chapter_id)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            session.commit()

            self.assertEqual(lock_artifacts.requested_source_terms, ["Context engineering"])
            self.assertEqual([record.canonical_zh for record in lock_artifacts.locked_records], ["上下文工程"])
            self.assertEqual(lock_artifacts.skipped_source_terms, [])
            self.assertTrue(resolver.last_request["examples"])
            self.assertEqual(final_review.issues, [])

    def test_chapter_concept_auto_lock_service_heuristically_locks_consistent_examples_without_token_usage(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_XHTML)]
        )

        class _ContextEngineeringWorker:
            def metadata(self) -> TranslationWorkerMetadata:
                return TranslationWorkerMetadata(
                    worker_name="ContextEngineeringWorker",
                    model_name="context-engineering-test",
                    prompt_version="test.context-engineering.v1",
                )

            def translate(self, task: TranslationTask) -> TranslationWorkerOutput:
                target_segments = []
                alignments = []
                for sentence in task.current_sentences:
                    source_text = sentence.source_text
                    if "Context engineering" in source_text:
                        text = (
                            "这被称为上下文工程，它决定了上下文如何被创建。"
                            if "created" in source_text
                            else "这也被称为上下文工程，它决定了上下文如何被维护。"
                        )
                    else:
                        text = f"译文::{source_text}"
                    temp_id = f"temp-{sentence.id}"
                    target_segments.append(
                        TranslationTargetSegment(
                            temp_id=temp_id,
                            text_zh=text,
                            segment_type="sentence",
                            source_sentence_ids=[sentence.id],
                            confidence=0.95,
                        )
                    )
                    alignments.append(
                        AlignmentSuggestion(
                            source_sentence_ids=[sentence.id],
                            target_temp_ids=[temp_id],
                            relation_type="1:1",
                            confidence=0.95,
                        )
                    )
                return TranslationWorkerOutput(
                    packet_id=task.context_packet.packet_id,
                    target_segments=target_segments,
                    alignment_suggestions=alignments,
                )

        with self.session_factory() as session:
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            service = TranslationService(TranslationRepository(session), worker=_ContextEngineeringWorker())
            for packet_id in packet_ids:
                service.execute_packet(packet_id)
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            unlocked_issues = [
                issue for issue in review_artifacts.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            ]
            self.assertTrue(unlocked_issues)

            auto_lock = ChapterConceptAutoLockService(session, resolver=HeuristicConceptResolver())
            lock_artifacts = auto_lock.auto_lock_chapter_concepts(chapter_id)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            session.commit()

            self.assertEqual(lock_artifacts.requested_source_terms, ["Context engineering"])
            self.assertEqual([record.canonical_zh for record in lock_artifacts.locked_records], ["上下文工程"])
            self.assertEqual([record.token_in for record in lock_artifacts.locked_records], [0])
            self.assertEqual([record.token_out for record in lock_artifacts.locked_records], [0])
            self.assertEqual(final_review.issues, [])

    def test_openai_compatible_concept_resolver_backfills_missing_source_term_from_request(self) -> None:
        class _FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def generate_structured_object(
                self,
                *,
                model_name,
                system_prompt,
                user_prompt,
                response_schema,
                schema_name,
            ):
                self.calls.append(
                    {
                        "model_name": model_name,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "response_schema": response_schema,
                        "schema_name": schema_name,
                    }
                )
                return (
                    {
                        "canonical_zh": "上下文工程",
                        "confidence": 0.96,
                        "rationale": "Examples consistently use 上下文工程.",
                    },
                    TranslationUsage(token_in=123, token_out=45, total_tokens=168, latency_ms=3210),
                )

        resolver = OpenAICompatibleConceptResolver(client=_FakeClient(), model_name="mock-concept-model")
        resolution, usage = resolver.resolve(
            source_term="Context Engineering",
            chapter_title="Chapter One",
            chapter_brief="A chapter about context engineering.",
            examples=[
                ConceptTranslationExample(
                    source_text="Context Engineering determines how context is created.",
                    target_text="上下文工程决定了上下文如何被创建。",
                )
            ],
        )

        self.assertIsNotNone(resolution)
        assert resolution is not None
        self.assertEqual(resolution.source_term, "Context Engineering")
        self.assertEqual(resolution.canonical_zh, "上下文工程")
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.token_in, 123)

    def test_chapter_concept_auto_lock_service_reuses_singular_variant_key_for_plural_terms(self) -> None:
        with self.session_factory() as session:
            service = ChapterConceptAutoLockService(session, resolver=HeuristicConceptResolver())
            observed_terms = {"language model", "language models", "prompt engineering"}
            self.assertEqual(
                service._variant_key_for_source_term("language models", observed_terms),
                "language model",
            )
            self.assertEqual(
                service._variant_key_for_source_term("prompt engineering", observed_terms),
                "prompt engineering",
            )

    def test_heuristic_concept_resolver_rejects_overly_generic_candidate_for_multiword_term(self) -> None:
        resolver = HeuristicConceptResolver()
        resolution, usage = resolver.resolve(
            source_term="Context Engineering",
            chapter_title="Chapter One",
            chapter_brief="A chapter about context engineering.",
            examples=[
                ConceptTranslationExample(
                    source_text="Context Engineering improves system behavior.",
                    target_text="语境工程改善系统行为。",
                ),
                ConceptTranslationExample(
                    source_text="Prompt Engineering complements Context Engineering.",
                    target_text="提示工程补充工程实践。",
                ),
            ],
        )
        self.assertIsNone(resolution)
        self.assertIsNone(usage)

    def test_build_default_concept_resolver_prefers_openai_compatible_before_heuristic_fallback(self) -> None:
        resolver = build_default_concept_resolver(
            Settings(
                translation_backend="openai_compatible",
                translation_model="mock-model",
                translation_openai_api_key="test-key",
                translation_openai_base_url="https://example.com/v1/responses",
            )
        )
        self.assertIsInstance(resolver, FallbackConceptResolver)
        assert isinstance(resolver, FallbackConceptResolver)
        self.assertIsInstance(resolver.resolvers[0], OpenAICompatibleConceptResolver)
        self.assertIsInstance(resolver.resolvers[1], HeuristicConceptResolver)

    def test_review_suppresses_fragmentary_low_confidence_pdf_omission(self) -> None:
        with self.session_factory() as session:
            review_service = ReviewService(ReviewRepository(session))
            block = Block(
                chapter_id="chapter-1",
                ordinal=1,
                block_type=BlockType.PARAGRAPH,
                source_text="format. The final result is then printed.",
                normalized_text="format. The final result is then printed.",
                source_anchor="pdf://page/9#p9-b79",
                source_span_json={"source_path": "pdf://page/9"},
                parse_confidence=0.65,
                protected_policy=ProtectedPolicy.TRANSLATE,
                status=ArtifactStatus.ACTIVE,
            )
            sentence = Sentence(
                block_id="block-1",
                chapter_id="chapter-1",
                document_id="document-1",
                ordinal_in_block=1,
                source_text="format.",
                normalized_text="format.",
                translatable=True,
                source_anchor="pdf://page/9#p9-b79",
                source_span_json={"source_path": "pdf://page/9"},
                sentence_status=SentenceStatus.PENDING,
            )
            self.assertTrue(review_service._should_suppress_fragmentary_pdf_omission(sentence, block))

    def test_review_skips_image_only_cover_packet_missing_title_context_failure(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [
                ("Cover", "cover.xhtml", IMAGE_ONLY_FIGURE_XHTML),
                ("Chapter One", "chapter1.xhtml", CHAPTER_XHTML),
            ],
            extra_files={"OEBPS/images/cover.png": b"fake-cover"},
        )

        with self.session_factory() as session:
            cover_chapter = session.scalars(
                select(Chapter)
                .where(Chapter.document_id == document_id)
                .order_by(Chapter.ordinal)
            ).first()
            self.assertIsNotNone(cover_chapter)
            assert cover_chapter is not None
            cover_chapter.title_src = None

            cover_sentence = session.scalars(
                select(Sentence).where(Sentence.chapter_id == cover_chapter.id)
            ).one()

            chapter_brief = session.scalars(
                select(MemorySnapshot).where(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == cover_chapter.id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                )
            ).one()
            brief_json = dict(chapter_brief.content_json)
            brief_json["open_questions"] = ["missing_chapter_title"]
            chapter_brief.content_json = brief_json
            session.commit()

            cover_block = session.scalars(
                select(Block).where(Block.chapter_id == cover_chapter.id).order_by(Block.ordinal)
            ).first()
            self.assertIsNotNone(cover_block)
            assert cover_block is not None

            cover_packet = TranslationPacket(
                id=stable_id("packet", cover_chapter.id, "legacy-cover"),
                chapter_id=cover_chapter.id,
                block_start_id=cover_block.id,
                block_end_id=cover_block.id,
                packet_type=PacketType.TRANSLATE,
                book_profile_version=1,
                chapter_brief_version=1,
                termbase_version=1,
                entity_snapshot_version=1,
                style_snapshot_version=1,
                packet_json={
                    "current_blocks": [
                        {
                            "sentence_ids": [cover_sentence.id],
                        }
                    ],
                    "open_questions": ["missing_chapter_title"],
                },
                status=PacketStatus.BUILT,
            )
            session.add(cover_packet)
            session.commit()

            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(cover_chapter.id)
            self.assertEqual(review_artifacts.issues, [])
            with tempfile.TemporaryDirectory() as outdir:
                ExportService(ExportRepository(session), output_root=outdir).export_bilingual_html(cover_chapter.id)

    def test_final_export_auto_followup_can_rebuild_packet_context_failure(self) -> None:
        with tempfile.TemporaryDirectory() as outdir:
            with tempfile.TemporaryDirectory() as tmpdir:
                epub_path = Path(tmpdir) / "sample.epub"
                with zipfile.ZipFile(epub_path, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip")
                    archive.writestr("META-INF/container.xml", CONTAINER_XML)
                    archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                    archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                    archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

                with self.session_factory() as session:
                    workflow = DocumentWorkflowService(session, export_root=outdir)
                    summary = workflow.bootstrap_epub(epub_path)
                    document_id = summary.document_id
                    workflow.translate_document(document_id)
                    review = workflow.review_document(document_id)
                    self.assertEqual(review.total_issue_count, 0)

                    packet = session.scalars(
                        select(TranslationPacket).where(TranslationPacket.chapter_id.is_not(None))
                    ).first()
                    self.assertIsNotNone(packet)
                    assert packet is not None
                    packet_json = dict(packet.packet_json)
                    packet_json["open_questions"] = ["speaker_reference_ambiguous"]
                    packet.packet_json = packet_json
                    session.merge(packet)
                    session.flush()

                    review = workflow.review_document(document_id)
                    self.assertGreaterEqual(review.total_issue_count, 1)

                    export = workflow.export_document(
                        document_id,
                        ExportType.BILINGUAL_HTML,
                        auto_execute_followup_on_gate=True,
                    )
                    self.assertTrue(export.auto_followup_requested)
                    self.assertTrue(export.auto_followup_applied)
                    self.assertEqual(export.auto_followup_executions[0].action_type, "REBUILD_PACKET_THEN_RERUN")
                    self.assertTrue(export.auto_followup_executions[0].issue_resolved)
                    self.assertTrue(Path(export.chapter_results[0].file_path).exists())

    def test_bilingual_html_keeps_multi_sentence_paragraph_in_single_flow(self) -> None:
        with tempfile.TemporaryDirectory() as outdir:
            with tempfile.TemporaryDirectory() as tmpdir:
                epub_path = Path(tmpdir) / "sample.epub"
                with zipfile.ZipFile(epub_path, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip")
                    archive.writestr("META-INF/container.xml", CONTAINER_XML)
                    archive.writestr("OEBPS/content.opf", CONTENT_OPF)
                    archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
                    archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)

                with self.session_factory() as session:
                    workflow = DocumentWorkflowService(
                        session,
                        export_root=outdir,
                        translation_worker=ParagraphFlowWorker(),
                    )
                    summary = workflow.bootstrap_epub(epub_path)
                    document_id = summary.document_id
                    workflow.translate_document(document_id)
                    workflow.review_document(document_id)
                    export = workflow.export_document(document_id, ExportType.BILINGUAL_HTML)
                    chapter_html = Path(export.chapter_results[0].file_path).read_text(encoding="utf-8")
                    self.assertIn("定价能力很重要。战略会持续复利。", chapter_html)
                    self.assertNotIn("定价能力很重要。<br/>战略会持续复利。", chapter_html)

    def test_review_auto_followups_recompute_candidates_after_each_rerun(self) -> None:
        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            now = datetime.now(timezone.utc)
            issue = ReviewIssue(
                id="issue-style-1",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-1",
                packet_id="packet-1",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "context_engineering_literal",
                    "preferred_hint": "上下文工程",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            action = IssueAction(
                id="action-style-1",
                issue_id=issue.id,
                action_type=ActionType.RERUN_PACKET,
                scope_type=JobScopeType.PACKET,
                scope_id="packet-1",
                status=ActionStatus.PLANNED,
                reason_json={},
                created_by=ActionActorType.SYSTEM,
                created_at=now,
                updated_at=now,
            )
            initial_artifacts = ReviewArtifacts(
                issues=[issue],
                actions=[action],
                rerun_plans=[],
                summary=ReviewChapterQualitySummary(
                    coverage_ok=True,
                    alignment_ok=True,
                    term_ok=True,
                    format_ok=True,
                    blocking_issue_count=0,
                    low_confidence_count=0,
                    format_pollution_count=0,
                ),
                resolved_issue_ids=[],
            )
            updated_artifacts = ReviewArtifacts(
                issues=[],
                actions=[],
                rerun_plans=[],
                summary=ReviewChapterQualitySummary(
                    coverage_ok=True,
                    alignment_ok=True,
                    term_ok=True,
                    format_ok=True,
                    blocking_issue_count=0,
                    low_confidence_count=0,
                    format_pollution_count=0,
                ),
                resolved_issue_ids=[issue.id],
            )
            rerun_plan = RerunPlan(
                issue_id=issue.id,
                action_type=ActionType.RERUN_PACKET,
                scope_type=JobScopeType.PACKET,
                scope_ids=["packet-1"],
                style_hints=(
                    "Rerun focus [context_engineering_literal]: prefer '上下文工程' over literal phrasing in this packet.",
                ),
            )
            executions: list = []

            with patch.object(
                workflow,
                "_review_auto_followup_candidate_actions",
                side_effect=[[action], []],
            ) as candidate_mock, patch.object(
                workflow,
                "execute_action",
                return_value=ActionWorkflowResult(
                    action_execution=ActionExecutionArtifacts(
                        rerun_plan=rerun_plan,
                        invalidations=[],
                        audits=[],
                    ),
                    rerun_execution=RerunExecutionArtifacts(
                        rerun_plan=rerun_plan,
                        translated_packet_ids=["packet-1"],
                        translation_run_ids=["run-1"],
                        review_artifacts=updated_artifacts,
                        issue_resolved=True,
                    ),
                ),
            ) as execute_mock:
                result = workflow._apply_review_auto_followups(
                    chapter_id="chapter-1",
                    artifacts=initial_artifacts,
                    attempted_action_ids=set(),
                    executions=executions,
                    attempt_limit=2,
                )

            self.assertIs(result, updated_artifacts)
            self.assertEqual(candidate_mock.call_count, 2)
            self.assertEqual(execute_mock.call_count, 1)
            self.assertEqual(len(executions), 1)

    def test_review_auto_followups_stop_on_manual_hold_after_repeated_failed_attempts(self) -> None:
        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            now = datetime.now(timezone.utc)
            document_id = "11111111-1111-4111-8111-111111111111"
            chapter_id = "22222222-2222-4222-8222-222222222222"
            issue = ReviewIssue(
                id="issue-style-manual-hold",
                document_id=document_id,
                chapter_id=chapter_id,
                block_id=None,
                sentence_id="33333333-3333-4333-8333-333333333333",
                packet_id="44444444-4444-4444-8444-444444444444",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={"preferred_hint": "上下文工程"},
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            action = IssueAction(
                id="action-style-manual-hold",
                issue_id=issue.id,
                action_type=ActionType.RERUN_PACKET,
                scope_type=JobScopeType.PACKET,
                scope_id=issue.packet_id,
                status=ActionStatus.PLANNED,
                reason_json={},
                created_by=ActionActorType.SYSTEM,
                created_at=now,
                updated_at=now,
            )
            artifacts = ReviewArtifacts(
                issues=[issue],
                actions=[action],
                rerun_plans=[],
                summary=ReviewChapterQualitySummary(
                    coverage_ok=True,
                    alignment_ok=True,
                    term_ok=True,
                    format_ok=True,
                    blocking_issue_count=0,
                    low_confidence_count=0,
                    format_pollution_count=0,
                ),
                resolved_issue_ids=[],
            )
            session.add_all(
                [
                    AuditEvent(
                        id=stable_id("audit", "chapter", chapter_id, "review.auto_followup.executed", action.id, "1"),
                        object_type="chapter",
                        object_id=chapter_id,
                        action="review.auto_followup.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-review-workflow",
                        payload_json={"action_id": action.id, "issue_id": issue.id, "issue_resolved": False},
                        created_at=now,
                    ),
                    AuditEvent(
                        id=stable_id("audit", "chapter", chapter_id, "review.auto_followup.executed", action.id, "2"),
                        object_type="chapter",
                        object_id=chapter_id,
                        action="review.auto_followup.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-review-workflow",
                        payload_json={"action_id": action.id, "issue_id": issue.id, "issue_resolved": False},
                        created_at=now,
                    ),
                ]
            )
            session.commit()

            with patch.object(
                workflow,
                "_review_auto_followup_candidate_actions",
                return_value=[action],
            ) as candidate_mock, patch.object(workflow, "execute_action") as execute_mock:
                result = workflow._apply_review_auto_followups(
                    chapter_id=chapter_id,
                    artifacts=artifacts,
                    attempted_action_ids=set(),
                    executions=[],
                    attempt_limit=2,
                )

            self.assertIs(result, artifacts)
            self.assertEqual(candidate_mock.call_count, 1)
            execute_mock.assert_not_called()
            stop_events = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "review.auto_followup.stopped")
            ).all()
            self.assertEqual(len(stop_events), 1)
            self.assertEqual(stop_events[0].payload_json["stop_reason"], "manual_hold_required")
            self.assertEqual(stop_events[0].payload_json["followup_action_ids"], [action.id])

    def test_document_blocker_repair_stops_on_manual_hold_after_repeated_failed_attempts(self) -> None:
        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            now = datetime.now(timezone.utc)
            document_id = "55555555-5555-4555-8555-555555555555"
            chapter_id = "66666666-6666-4666-8666-666666666666"
            issue = ReviewIssue(
                id="issue-blocker-manual-hold",
                document_id=document_id,
                chapter_id=chapter_id,
                block_id=None,
                sentence_id="77777777-7777-4777-8777-777777777777",
                packet_id="88888888-8888-4888-8888-888888888888",
                issue_type="ALIGNMENT_FAILURE",
                root_cause_layer=RootCauseLayer.ALIGNMENT,
                severity=Severity.HIGH,
                blocking=True,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={"requires_packet_rerun": True},
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            action = IssueAction(
                id="action-blocker-manual-hold",
                issue_id=issue.id,
                action_type=ActionType.RERUN_PACKET,
                scope_type=JobScopeType.PACKET,
                scope_id=issue.packet_id,
                status=ActionStatus.PLANNED,
                reason_json={},
                created_by=ActionActorType.SYSTEM,
                created_at=now,
                updated_at=now,
            )
            session.add_all(
                [
                    AuditEvent(
                        id=stable_id("audit", "document", document_id, "document.blocker_repair.executed", action.id, "1"),
                        object_type="document",
                        object_id=document_id,
                        action="document.blocker_repair.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-review-workflow",
                        payload_json={"action_id": action.id, "issue_id": issue.id, "issue_resolved": False},
                        created_at=now,
                    ),
                    AuditEvent(
                        id=stable_id("audit", "document", document_id, "document.blocker_repair.executed", action.id, "2"),
                        object_type="document",
                        object_id=document_id,
                        action="document.blocker_repair.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-review-workflow",
                        payload_json={"action_id": action.id, "issue_id": issue.id, "issue_resolved": False},
                        created_at=now,
                    ),
                ]
            )
            session.commit()

            with patch.object(
                workflow,
                "_list_document_active_blocking_issues",
                side_effect=[[issue], [issue], [issue]],
            ), patch.object(
                workflow,
                "_document_blocker_candidate_actions",
                return_value=[action],
            ), patch.object(workflow, "execute_action") as execute_mock:
                result = workflow.repair_document_blockers_until_exportable(
                    document_id,
                    max_rounds=2,
                    max_actions_per_round=1,
                )

            execute_mock.assert_not_called()
            self.assertEqual(result.blocking_issue_count_before, 1)
            self.assertEqual(result.blocking_issue_count_after, 1)
            self.assertFalse(result.applied)
            self.assertEqual(result.round_count, 0)
            self.assertEqual(result.stop_reason, "manual_hold_required")
            stop_events = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "document.blocker_repair.stopped")
            ).all()
            self.assertEqual(len(stop_events), 1)
            self.assertEqual(stop_events[0].payload_json["stop_reason"], "manual_hold_required")
            self.assertEqual(stop_events[0].payload_json["followup_action_ids"], [action.id])

    def test_export_auto_followup_stops_on_manual_hold_after_repeated_failed_attempts(self) -> None:
        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            now = datetime.now(timezone.utc)
            document_id = "99999999-9999-4999-8999-999999999999"
            chapter_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            packet_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            document = Document(
                id=document_id,
                source_type=SourceType.EPUB,
                file_fingerprint="fingerprint-export-manual-hold",
                source_path="/tmp/export-manual-hold.epub",
                title="Export Manual Hold",
                status=DocumentStatus.ACTIVE,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
            chapter = Chapter(
                id=chapter_id,
                document_id=document_id,
                ordinal=1,
                title_src="Chapter One",
                title_tgt=None,
                anchor_start=None,
                anchor_end=None,
                status=ChapterStatus.REVIEW_REQUIRED,
                summary_version=None,
                risk_level=None,
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
            issue = ReviewIssue(
                id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                document_id=document_id,
                chapter_id=chapter_id,
                block_id=None,
                sentence_id=None,
                packet_id=None,
                issue_type="CONTEXT_FAILURE",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.HIGH,
                blocking=True,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={"open_questions": "speaker_reference_ambiguous"},
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(issue)
            session.add_all(
                [
                    AuditEvent(
                        id=stable_id("audit", "chapter", chapter_id, "export.auto_followup.executed", "action-export-manual-hold", "1"),
                        object_type="chapter",
                        object_id=chapter_id,
                        action="export.auto_followup.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-export-workflow",
                        payload_json={
                            "action_id": "action-export-manual-hold",
                            "issue_id": issue.id,
                            "issue_resolved": False,
                        },
                        created_at=now,
                    ),
                    AuditEvent(
                        id=stable_id("audit", "chapter", chapter_id, "export.auto_followup.executed", "action-export-manual-hold", "2"),
                        object_type="chapter",
                        object_id=chapter_id,
                        action="export.auto_followup.executed",
                        actor_type=ActorType.SYSTEM,
                        actor_id="document-export-workflow",
                        payload_json={
                            "action_id": "action-export-manual-hold",
                            "issue_id": issue.id,
                            "issue_resolved": False,
                        },
                        created_at=now,
                    ),
                ]
            )
            session.commit()

            followup_action = ExportFollowupAction(
                action_id="action-export-manual-hold",
                issue_id=issue.id,
                action_type=ActionType.REBUILD_PACKET_THEN_RERUN.value,
                scope_type=JobScopeType.PACKET.value,
                scope_id=packet_id,
                suggested_run_followup=True,
            )

            with patch.object(
                workflow.bootstrap_repository,
                "load_document_bundle",
                return_value=SimpleNamespace(
                    chapters=[SimpleNamespace(chapter=SimpleNamespace(id=chapter_id))],
                    document=SimpleNamespace(id=document_id, status=DocumentStatus.ACTIVE),
                ),
            ), patch.object(
                workflow.export_service,
                "assert_chapter_exportable",
                side_effect=ExportGateError(
                    "chapter blocked",
                    chapter_id=chapter_id,
                    issue_ids=[issue.id],
                    followup_actions=[followup_action],
                ),
            ), patch.object(workflow, "execute_action") as execute_mock:
                with self.assertRaises(ExportGateError) as exc_info:
                    workflow.export_document(
                        document_id,
                        ExportType.BILINGUAL_HTML,
                        auto_execute_followup_on_gate=True,
                        max_auto_followup_attempts=3,
                    )

            execute_mock.assert_not_called()
            self.assertEqual(exc_info.exception.auto_followup_stop_reason, "manual_hold_required")
            self.assertEqual(exc_info.exception.auto_followup_attempt_count, 0)
            stop_events = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "export.auto_followup.stopped")
            ).all()
            self.assertEqual(len(stop_events), 1)
            self.assertEqual(stop_events[0].payload_json["stop_reason"], "manual_hold_required")
            self.assertEqual(stop_events[0].payload_json["followup_action_ids"], [followup_action.action_id])

    def test_workflow_review_auto_executes_packet_style_followups(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareLiteralismWorker(),
            )
            workflow.translate_document(document_id)
            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=2,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.chapter_results), 1)
            self.assertEqual(review.chapter_results[0].status, ChapterStatus.QA_CHECKED.value)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "STYLE_DRIFT")
            self.assertEqual(execution.action_type, ActionType.RERUN_PACKET.value)
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertTrue(execution.followup_executed)
            self.assertTrue(execution.issue_resolved)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(
                workflow.bootstrap_repository.load_document_bundle(document_id).chapters[0].chapter.id
            )
            self.assertEqual(final_review.issues, [])

    def test_workflow_review_auto_executes_packet_term_followups(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", AGENTIC_AI_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareAgenticWorker(),
            )
            workflow.translate_document(document_id)
            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="代理式AI",
            )
            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=1,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.chapter_results), 1)
            self.assertEqual(review.chapter_results[0].status, ChapterStatus.QA_CHECKED.value)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "TERM_CONFLICT")
            self.assertEqual(
                execution.action_type,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED.value,
            )
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertTrue(execution.followup_executed)
            self.assertTrue(execution.issue_resolved)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(
                workflow.bootstrap_repository.load_document_bundle(document_id).chapters[0].chapter.id
            )
            self.assertEqual(final_review.issues, [])

    def test_workflow_review_auto_followups_prioritize_packet_term_conflicts_before_style_drift(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", MIXED_AUTO_FOLLOWUP_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareMixedWorker(),
            )
            workflow.translate_document(document_id)
            session.commit()
            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="代理式AI",
            )
            initial_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=1,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "TERM_CONFLICT")
            self.assertEqual(
                execution.action_type,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED.value,
            )
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertTrue(execution.issue_resolved)
            initial_term_issues = [
                issue for issue in initial_review.issues if issue.issue_type == "TERM_CONFLICT"
            ]
            initial_style_issues = [
                issue for issue in initial_review.issues if issue.issue_type == "STYLE_DRIFT"
            ]
            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            remaining_term_issues = [
                issue for issue in final_review.issues if issue.issue_type == "TERM_CONFLICT"
            ]
            remaining_style_issues = [
                issue for issue in final_review.issues if issue.issue_type == "STYLE_DRIFT"
            ]
            self.assertTrue(initial_term_issues)
            self.assertTrue(initial_style_issues)
            self.assertLess(len(remaining_term_issues), len(initial_term_issues))
            self.assertTrue(remaining_style_issues)

    def test_bootstrap_repository_document_image_probe_preserves_uncommitted_concept_lock_state(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", MIXED_AUTO_FOLLOWUP_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=GuidanceAwareMixedWorker(),
            )
            workflow.translate_document(document_id)
            session.commit()
            chapter_id = workflow.bootstrap_repository.load_document_bundle(document_id).chapters[0].chapter.id

            lock_result = ChapterConceptLockService(session).lock_concept(
                chapter_id=chapter_id,
                source_term="agentic AI",
                canonical_zh="智能体式AI",
            )

            self.assertTrue(workflow.bootstrap_repository._document_images_table_available())

            review_bundle = ReviewRepository(session).load_chapter_bundle(chapter_id)
            self.assertIsNotNone(review_bundle.chapter_translation_memory)
            assert review_bundle.chapter_translation_memory is not None
            self.assertEqual(review_bundle.chapter_translation_memory.version, lock_result.snapshot_version)
            self.assertEqual(review_bundle.chapter_translation_memory.status.value, "active")
            self.assertIn(
                ("agentic AI", "智能体AI"),
                [
                    (entry.source_term, entry.target_term)
                    for entry in review_bundle.term_entries
                    if entry.scope_id == chapter_id and entry.status == TermStatus.ACTIVE
                ],
            )

    def test_export_repository_document_image_probe_preserves_uncommitted_concept_lock_state(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", MIXED_AUTO_FOLLOWUP_XHTML)]
        )

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(
                    session,
                    export_root=outdir,
                    translation_worker=GuidanceAwareMixedWorker(),
                )
                workflow.translate_document(document_id)
                session.commit()
                chapter_id = workflow.bootstrap_repository.load_document_bundle(document_id).chapters[0].chapter.id

                lock_result = ChapterConceptLockService(session).lock_concept(
                    chapter_id=chapter_id,
                    source_term="agentic AI",
                    canonical_zh="智能体式AI",
                )

                export_repository = ExportRepository(session)
                self.assertTrue(export_repository._document_images_table_available())
                export_repository.load_chapter_bundle(chapter_id)

                review_bundle = ReviewRepository(session).load_chapter_bundle(chapter_id)
                self.assertIsNotNone(review_bundle.chapter_translation_memory)
                assert review_bundle.chapter_translation_memory is not None
                self.assertEqual(review_bundle.chapter_translation_memory.version, lock_result.snapshot_version)
                self.assertEqual(review_bundle.chapter_translation_memory.status.value, "active")
                self.assertIn(
                    ("agentic AI", "智能体AI"),
                    [
                        (entry.source_term, entry.target_term)
                        for entry in review_bundle.term_entries
                        if entry.scope_id == chapter_id and entry.status == TermStatus.ACTIVE
                    ],
                )

    def test_workflow_review_auto_followup_candidates_prioritize_mixed_packets_over_style_only_volume(self) -> None:
        with self.session_factory() as session:
            workflow = DocumentWorkflowService(session)
            now = datetime.now(timezone.utc)
            style_issue_packet_a_1 = ReviewIssue(
                id="issue-style-a-1",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-a-1",
                packet_id="packet-a",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "context_engineering_literal",
                    "preferred_hint": "上下文工程",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            style_issue_packet_a_2 = ReviewIssue(
                id="issue-style-a-2",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-a-2",
                packet_id="packet-a",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "context_engineering_literal",
                    "preferred_hint": "上下文工程",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            style_issue_packet_a_3 = ReviewIssue(
                id="issue-style-a-3",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-a-3",
                packet_id="packet-a",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "context_engineering_literal",
                    "preferred_hint": "上下文工程",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            term_issue_packet_b = ReviewIssue(
                id="issue-term-b",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-b-1",
                packet_id="packet-b",
                issue_type="TERM_CONFLICT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.HIGH,
                blocking=True,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "source_term": "agentic AI",
                    "actual_target_term": "代理型AI",
                    "expected_target_term": "代理式AI",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            style_issue_packet_b = ReviewIssue(
                id="issue-style-b",
                document_id="doc-1",
                chapter_id="chapter-1",
                block_id=None,
                sentence_id="sent-b-2",
                packet_id="packet-b",
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
                severity=Severity.MEDIUM,
                blocking=False,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "style_rule": "context_engineering_literal",
                    "preferred_hint": "上下文工程",
                },
                status=IssueStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            artifacts = ReviewArtifacts(
                issues=[
                    style_issue_packet_a_1,
                    style_issue_packet_a_2,
                    style_issue_packet_a_3,
                    term_issue_packet_b,
                    style_issue_packet_b,
                ],
                actions=[
                    IssueAction(
                        id="action-style-a-1",
                        issue_id="issue-style-a-1",
                        action_type=ActionType.RERUN_PACKET,
                        scope_type=JobScopeType.PACKET,
                        scope_id="packet-a",
                        status=ActionStatus.PLANNED,
                        reason_json={},
                        created_by=ActionActorType.SYSTEM,
                        created_at=now,
                        updated_at=now,
                    ),
                    IssueAction(
                        id="action-style-a-2",
                        issue_id="issue-style-a-2",
                        action_type=ActionType.RERUN_PACKET,
                        scope_type=JobScopeType.PACKET,
                        scope_id="packet-a",
                        status=ActionStatus.PLANNED,
                        reason_json={},
                        created_by=ActionActorType.SYSTEM,
                        created_at=now,
                        updated_at=now,
                    ),
                    IssueAction(
                        id="action-style-a-3",
                        issue_id="issue-style-a-3",
                        action_type=ActionType.RERUN_PACKET,
                        scope_type=JobScopeType.PACKET,
                        scope_id="packet-a",
                        status=ActionStatus.PLANNED,
                        reason_json={},
                        created_by=ActionActorType.SYSTEM,
                        created_at=now,
                        updated_at=now,
                    ),
                    IssueAction(
                        id="action-term-b",
                        issue_id="issue-term-b",
                        action_type=ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
                        scope_type=JobScopeType.PACKET,
                        scope_id="packet-b",
                        status=ActionStatus.PLANNED,
                        reason_json={},
                        created_by=ActionActorType.SYSTEM,
                        created_at=now,
                        updated_at=now,
                    ),
                    IssueAction(
                        id="action-style-b",
                        issue_id="issue-style-b",
                        action_type=ActionType.RERUN_PACKET,
                        scope_type=JobScopeType.PACKET,
                        scope_id="packet-b",
                        status=ActionStatus.PLANNED,
                        reason_json={},
                        created_by=ActionActorType.SYSTEM,
                        created_at=now,
                        updated_at=now,
                    ),
                ],
                rerun_plans=[],
                summary=ReviewChapterQualitySummary(
                    coverage_ok=True,
                    alignment_ok=True,
                    term_ok=False,
                    format_ok=True,
                    blocking_issue_count=1,
                    low_confidence_count=0,
                    format_pollution_count=0,
                ),
                resolved_issue_ids=[],
            )

            candidates = workflow._review_auto_followup_candidate_actions(
                artifacts,
                issue_by_id={issue.id: issue for issue in artifacts.issues},
                attempted_action_ids={"action-term-b"},
            )

            self.assertEqual([action.id for action in candidates], ["action-style-b", "action-style-a-1"])

    def test_workflow_review_auto_executes_single_packet_unlocked_concept_followups(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=ConsistentContextEngineeringWorker(),
            )
            workflow.translate_document(document_id)
            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=1,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "UNLOCKED_KEY_CONCEPT")
            self.assertEqual(
                execution.action_type,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED.value,
            )
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertTrue(execution.followup_executed)
            self.assertTrue(execution.issue_resolved)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(
                workflow.bootstrap_repository.load_document_bundle(document_id).chapters[0].chapter.id
            )
            self.assertEqual(final_review.issues, [])

    def test_workflow_review_unlocked_concept_followup_uses_default_concept_resolver(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_PACKET_XHTML)]
        )

        class _PatchedResolver:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def resolve(self, *, source_term, chapter_title, chapter_brief, examples):
                self.calls.append(
                    {
                        "source_term": source_term,
                        "chapter_title": chapter_title,
                        "chapter_brief": chapter_brief,
                        "example_count": len(examples),
                    }
                )
                if source_term.casefold() != "context engineering":
                    return None, None
                return (
                    ConceptResolutionPayload(
                        source_term=source_term,
                        canonical_zh="上下文工程",
                        confidence=0.98,
                        rationale="Patched resolver uses the preferred canonical term.",
                    ),
                    None,
                )

        resolver = _PatchedResolver()

        with self.session_factory() as session:
            workflow = DocumentWorkflowService(
                session,
                translation_worker=ConsistentContextEngineeringWorker(),
            )
            workflow.translate_document(document_id)
            with patch("book_agent.services.workflows.build_default_concept_resolver", return_value=resolver):
                review = workflow.review_document(
                    document_id,
                    auto_execute_packet_followups=True,
                    max_auto_followup_attempts=1,
                )
            session.commit()

            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(resolver.calls), 1)
            self.assertEqual(str(resolver.calls[0]["source_term"]).casefold(), "context engineering")

    def test_workflow_review_auto_executes_multi_packet_unlocked_concept_followups_without_chapter_rerun(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", CONTEXT_ENGINEERING_THREE_PACKET_XHTML)]
        )

        with self.session_factory() as session:
            worker = CountingConsistentContextEngineeringWorker()
            workflow = DocumentWorkflowService(
                session,
                translation_worker=worker,
            )
            workflow.translate_document(document_id)
            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            self.assertGreaterEqual(len(packet_ids), 3)

            initial_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            concept_issue = next(
                issue for issue in initial_review.issues if issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            )
            affected_packet_ids = set(concept_issue.evidence_json.get("packet_ids_seen") or [])
            unaffected_packet_ids = set(packet_ids) - affected_packet_ids
            self.assertEqual(len(affected_packet_ids), 2)

            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=1,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "UNLOCKED_KEY_CONCEPT")
            self.assertEqual(
                execution.action_type,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED.value,
            )
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertEqual(set(execution.rerun_scope_ids), affected_packet_ids)
            self.assertEqual(set(execution.rerun_packet_ids), affected_packet_ids)
            packet_counts = {packet_id: worker.packet_ids_seen.count(packet_id) for packet_id in packet_ids}
            for packet_id in affected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 2)
            for packet_id in unaffected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 1)
            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertEqual(final_review.issues, [])

    def test_workflow_review_auto_executes_packet_scoped_stale_brief_followups_when_concept_autolock_fails(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_ADAPTIVE_AGENT_XHTML)]
        )

        class _NullResolver:
            def resolve(self, *, source_term, chapter_title, chapter_brief, examples):
                return None, None

        with self.session_factory() as session:
            worker = CountingConsistentContextEngineeringWorker()
            workflow = DocumentWorkflowService(
                session,
                translation_worker=worker,
            )
            workflow.translate_document(document_id)
            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            initial_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issue = next(
                issue for issue in initial_review.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            )
            affected_packet_ids = set(stale_issue.evidence_json.get("packet_ids_seen") or [])
            unaffected_packet_ids = set(packet_ids) - affected_packet_ids
            self.assertEqual(len(affected_packet_ids), 2)

            with patch("book_agent.services.workflows.build_default_concept_resolver", return_value=_NullResolver()):
                review = workflow.review_document(
                    document_id,
                    auto_execute_packet_followups=True,
                    max_auto_followup_attempts=1,
                )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 1)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            stale_execution = review.auto_followup_executions[0]
            self.assertEqual(stale_execution.issue_type, "STALE_CHAPTER_BRIEF")
            self.assertEqual(stale_execution.action_type, ActionType.REBUILD_CHAPTER_BRIEF.value)
            self.assertEqual(stale_execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertEqual(set(stale_execution.rerun_scope_ids), affected_packet_ids)
            self.assertEqual(set(stale_execution.rerun_packet_ids), affected_packet_ids)
            self.assertTrue(stale_execution.followup_executed)
            self.assertTrue(stale_execution.issue_resolved)

            packet_counts = {packet_id: worker.packet_ids_seen.count(packet_id) for packet_id in packet_ids}
            for packet_id in affected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 2)
            for packet_id in unaffected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 1)

            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            remaining_issue_types = {issue.issue_type for issue in final_review.issues}
            self.assertEqual(remaining_issue_types, {"UNLOCKED_KEY_CONCEPT"})

    def test_workflow_review_does_not_run_stale_brief_followup_when_concept_autolock_succeeds(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", STALE_BRIEF_ADAPTIVE_AGENT_XHTML)]
        )

        with self.session_factory() as session:
            worker = CountingGuidanceAwareAdaptiveAgentWorker()
            workflow = DocumentWorkflowService(
                session,
                translation_worker=worker,
            )
            workflow.translate_document(document_id)
            bundle = workflow.bootstrap_repository.load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            packet_ids = [packet.id for packet in bundle.chapters[0].translation_packets]
            initial_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            stale_issue = next(
                issue for issue in initial_review.issues if issue.issue_type == "STALE_CHAPTER_BRIEF"
            )
            affected_packet_ids = set(stale_issue.evidence_json.get("packet_ids_seen") or [])
            unaffected_packet_ids = set(packet_ids) - affected_packet_ids

            review = workflow.review_document(
                document_id,
                auto_execute_packet_followups=True,
                max_auto_followup_attempts=2,
            )
            session.commit()

            self.assertTrue(review.auto_followup_requested)
            self.assertTrue(review.auto_followup_applied)
            self.assertEqual(review.auto_followup_attempt_count, 1)
            self.assertEqual(review.total_issue_count, 0)
            self.assertEqual(len(review.auto_followup_executions or []), 1)
            execution = review.auto_followup_executions[0]
            self.assertEqual(execution.issue_type, "UNLOCKED_KEY_CONCEPT")
            self.assertEqual(
                execution.action_type,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED.value,
            )
            self.assertEqual(execution.rerun_scope_type, JobScopeType.PACKET.value)
            self.assertEqual(set(execution.rerun_scope_ids), affected_packet_ids)
            self.assertEqual(set(execution.rerun_packet_ids), affected_packet_ids)
            self.assertTrue(execution.followup_executed)
            self.assertTrue(execution.issue_resolved)

            packet_counts = {packet_id: worker.packet_ids_seen.count(packet_id) for packet_id in packet_ids}
            for packet_id in affected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 2)
            for packet_id in unaffected_packet_ids:
                self.assertEqual(packet_counts[packet_id], 1)

            final_review = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            self.assertEqual(final_review.issues, [])

    def test_review_detects_chapter_brief_context_failure_and_routes_to_brief_rebuild(self) -> None:
        document_id, packet_id = self._bootstrap_to_db()

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            bundle = BootstrapRepository(session).load_document_bundle(document_id)
            chapter_id = bundle.chapters[0].chapter.id
            chapter_brief = session.scalars(
                session.query(MemorySnapshot).filter(
                    MemorySnapshot.document_id == document_id,
                    MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                    MemorySnapshot.scope_id == chapter_id,
                    MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                ).statement
            ).first()
            assert chapter_brief is not None
            content_json = dict(chapter_brief.content_json)
            content_json["open_questions"] = ["entity_state_missing"]
            chapter_brief.content_json = content_json
            session.merge(chapter_brief)
            session.commit()

        with self.session_factory() as session:
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            context_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "CONTEXT_FAILURE")
            context_action = next(action for action in review_artifacts.actions if action.issue_id == context_issue.id)

            self.assertEqual(context_issue.root_cause_layer.value, "memory")
            self.assertEqual(context_action.action_type, ActionType.REBUILD_CHAPTER_BRIEF)
            self.assertEqual(context_action.scope_type, JobScopeType.CHAPTER)
            self.assertEqual(context_action.scope_id, chapter_id)

    def test_review_detects_duplication_and_routes_to_packet_rebuild(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=DuplicateWorker()).execute_packet(packet_id)
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            duplication_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "DUPLICATION")
            duplication_action = next(action for action in review_artifacts.actions if action.issue_id == duplication_issue.id)

            self.assertEqual(duplication_issue.root_cause_layer.value, "packet")
            self.assertEqual(duplication_action.action_type, ActionType.REBUILD_PACKET_THEN_RERUN)
            self.assertEqual(duplication_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(duplication_action.scope_id, packet_id)

    def test_review_detects_recoverable_alignment_failure_and_routes_to_realign(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session)).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            target_segment_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal)
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == target_segment_id))
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertEqual(alignment_issue.root_cause_layer.value, "alignment")
            self.assertEqual(alignment_action.action_type, ActionType.REALIGN_ONLY)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)

    def test_review_detects_orphan_target_segment_and_routes_to_realign(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=SplitSegmentWorker()).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            orphan_target_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal.desc())
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == orphan_target_id))
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertIn(orphan_target_id, alignment_issue.evidence_json["orphan_target_segment_ids"])
            self.assertEqual(alignment_action.action_type, ActionType.REALIGN_ONLY)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)

    def test_review_routes_orphan_target_without_sentence_mapping_to_packet_rerun(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 1 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=EmptySourceAlignmentWorker()).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            orphan_target_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal.desc())
            ).first()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.target_segment_id == orphan_target_id))
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertIn(orphan_target_id, alignment_issue.evidence_json["orphan_target_segment_ids"])
            self.assertTrue(bool(alignment_issue.evidence_json.get("requires_packet_rerun")))
            self.assertEqual(alignment_action.action_type, ActionType.RERUN_PACKET)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)

    def test_review_routes_full_sentence_orphan_target_without_source_ids_to_packet_rerun(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet_id = next(
                packet.id
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=EmptySourceTailSentenceWorker()).execute_packet(packet_id)
            session.commit()

        with self.session_factory() as session:
            chapter_id = BootstrapRepository(session).load_document_bundle(document_id).chapters[0].chapter.id
            review_artifacts = ReviewService(ReviewRepository(session)).review_chapter(chapter_id)
            alignment_issue = next(issue for issue in review_artifacts.issues if issue.issue_type == "ALIGNMENT_FAILURE")
            alignment_action = next(action for action in review_artifacts.actions if action.issue_id == alignment_issue.id)

            self.assertTrue(bool(alignment_issue.evidence_json.get("requires_packet_rerun")))
            self.assertEqual(alignment_action.action_type, ActionType.RERUN_PACKET)
            self.assertEqual(alignment_action.scope_type, JobScopeType.PACKET)
            self.assertEqual(alignment_action.scope_id, packet_id)

    def test_realign_attaches_short_orphan_tail_segment_to_last_sentence(self) -> None:
        document_id, _ = self._bootstrap_to_db()

        with self.session_factory() as session:
            document_bundle = BootstrapRepository(session).load_document_bundle(document_id)
            packet = next(
                packet
                for packet in document_bundle.chapters[0].translation_packets
                if any(len(block.get("sentence_ids", [])) >= 2 for block in packet.packet_json.get("current_blocks", []))
            )
            packet_id = packet.id
            current_sentence_ids: list[str] = []
            for block in packet.packet_json.get("current_blocks", []):
                current_sentence_ids.extend(block.get("sentence_ids", []))

        with self.session_factory() as session:
            TranslationService(TranslationRepository(session), worker=TrailingLabelWorker()).execute_packet(packet_id)
            latest_run_id = session.scalars(
                select(TranslationRun.id)
                .where(TranslationRun.packet_id == packet_id)
                .order_by(TranslationRun.attempt.desc())
            ).first()
            orphan_target_id = session.scalars(
                select(TargetSegment.id)
                .where(TargetSegment.translation_run_id == latest_run_id)
                .order_by(TargetSegment.ordinal.desc())
            ).first()
            self.assertEqual(
                session.scalars(
                    select(AlignmentEdge).where(AlignmentEdge.target_segment_id == orphan_target_id)
                ).all(),
                [],
            )
            RealignService(OpsRepository(session)).execute([packet_id])
            session.commit()

        with self.session_factory() as session:
            repaired_edges = session.scalars(
                select(AlignmentEdge).where(AlignmentEdge.target_segment_id == orphan_target_id)
            ).all()
            self.assertEqual([edge.sentence_id for edge in repaired_edges], [current_sentence_ids[-1]])
            self.assertEqual(repaired_edges[0].relation_type, RelationType.ONE_TO_MANY)


if __name__ == "__main__":
    unittest.main()
