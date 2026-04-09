# LLM-powered Translation Agent

**LLM-powered translation agent for English books → Chinese.** Ingests EPUB/PDF, translates via any OpenAI-compatible API, exports bilingual or Chinese-only EPUB/PDF/HTML with sentence-level alignment and multi-dimensional QA.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![React](https://img.shields.io/badge/frontend-React%20%2B%20TypeScript-61DAFB)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Full-book translation pipeline** — Parse → Segment → Context-compile → Translate → Review → Export, fully automated
- **Any OpenAI-compatible LLM** — DeepSeek, OpenAI, Moonshot, local vLLM — swap by changing one env var
- **Sentence-level alignment** — Structured LLM output with Pydantic validation, every translated segment traces back to source
- **Multi-dimensional QA** — Automated coverage, alignment, terminology, format, and style-drift checks with deterministic fix routing
- **Self-healing runtime** — Retryable error classification, blockage detection, incident patching, checkpoint resume — runs unattended
- **Rich context compilation** — Terminology locking, entity resolution, chapter briefs, discourse bridges, style constraints per packet
- **Multiple export formats** — Bilingual HTML/Markdown, merged Chinese HTML/Markdown, rebuilt EPUB/PDF, JSONL

---

## Quick Start

**Prerequisites:** Python 3.12+, Node.js 18+, an OpenAI-compatible API key.

```bash
# 1. Clone
git clone https://github.com/your-org/book-agent.git && cd book-agent

# 2. Configure — only OPENAI_API_KEY is required
cp .env.example .env
# Edit .env: set OPENAI_API_KEY=sk-your-key-here

# 3. Launch (auto-installs all dependencies, creates DB, starts backend + frontend)
./dev.sh
```

That's it. Open `http://localhost:4173` — you'll see the **WORK** workspace. Upload an `.epub` or `.pdf`, click **Bootstrap**, then **Translate**.

> **What `dev.sh` does automatically:**
> - Detects `uv` (fast) or falls back to `python3 -m venv` + pip
> - Installs all Python and Node.js dependencies
> - Creates SQLite database with all tables on first startup
> - Starts backend (uvicorn, port 8999) + frontend (Vite, port 4173)
> - `Ctrl+C` stops everything cleanly

### Docker Deployment

```bash
cp .env.example .env   # set OPENAI_API_KEY
docker compose up -d   # PostgreSQL + App on port 58000
```

### Production Mode

```bash
./service.sh start            # Background mode with PID management
./service.sh start postgres   # Use PostgreSQL via Docker
./service.sh status           # Check running services
./service.sh stop             # Stop all
```

---

## Architecture

Book Agent is a **pipeline-oriented translation system** with a Kubernetes-style reconciliation loop driving state transitions. Documents flow through a fixed sequence of stages, each backed by a specialized component. The runtime is self-healing: transient failures trigger automatic retries, persistent failures open incidents with auto-generated patch proposals, and the entire pipeline supports checkpoint-based resume.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         WORK Workspace (React)                      │
│  Upload .epub/.pdf → Bootstrap → Translate → Review → Export        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ REST API (FastAPI)
┌────────────────────────────────▼────────────────────────────────────┐
│                        Translation Pipeline                         │
│                                                                     │
│  ┌──────────┐  ┌───────────┐  ┌─────────┐  ┌──────────┐           │
│  │  Parser   │→│ Segmenter │→│ Context  │→│Translator│           │
│  │          │  │           │  │ Compiler │  │          │           │
│  │EPUB/PDF  │  │ Sentence  │  │TermBase │  │ LLM API  │           │
│  │→Chapters │  │ splitting │  │ Entities │  │ Pydantic │           │
│  │→Blocks   │  │ Protected │  │ Style    │  │ Aligned  │           │
│  │→Metadata │  │ spans     │  │ Bridges  │  │ output   │           │
│  └──────────┘  └───────────┘  └─────────┘  └────┬─────┘           │
│                                                   │                 │
│  ┌──────────┐  ┌───────────┐  ┌─────────┐       │                 │
│  │ Exporter │←│Rule Engine│←│Reviewer │←──────┘                 │
│  │          │  │           │  │          │                         │
│  │HTML/EPUB │  │ Issue →   │  │Coverage │                         │
│  │PDF/MD    │  │ Action    │  │Alignment│                         │
│  │JSONL     │  │ routing   │  │TermCheck│                         │
│  └──────────┘  └───────────┘  │Format   │                         │
│                                │StyleDrift│                         │
│                                └─────────┘                         │
├─────────────────────────────────────────────────────────────────────┤
│  Run Controller (reconciliation loop)    Memory Service             │
│  Incident Manager ← Runtime Repair       (terminology, entities)   │
└─────────────────────────────────────────────────────────────────────┘
```

### Pipeline Components

#### Parser — `domain/structure/{epub.py, pdf.py, ocr.py}`

**In:** Raw `.epub` or `.pdf` file  
**Out:** `Document` → `Chapter[]` → `Block[]` hierarchy with block types (heading, paragraph, code, table, figure, etc.)

Extracts document structure using PyMuPDF. EPUB: walks spine and resolves chapter boundaries from the table of contents. PDF: performs layout analysis to detect columns, headers, footers, and reading order. Scanned PDFs route to the OCR parser for text extraction. Each block is tagged with a `BlockType` and `ProtectedPolicy` (translate/protect/mixed) to control downstream behavior.

#### Segmenter — `domain/segmentation/sentences.py`

**In:** `Block[]` with raw source text  
**Out:** `Sentence[]` with ordinal positions and protected span annotations

Splits English text into sentences while identifying spans that must not be translated: inline code, URLs, citations, cross-references. These protected spans are preserved through the entire pipeline and restored in the final export.

#### Context Compiler — `services/context_compile.py` + `domain/context/builders.py`

**In:** `TranslationPacket` (a batch of consecutive sentences)  
**Out:** `ContextPacket` enriched with chapter brief, locked/preferred terminology, named entities with canonical Chinese renderings, discourse bridges to adjacent packets, and style constraints from the book profile

This is where translation quality is won or lost. The compiler pulls from chapter-level memory snapshots — terminology base, entity registry, style deltas, and translation memory — to give the LLM everything it needs for a contextually accurate translation. Terminology can be `locked` (must use exact rendering), `preferred` (should use), or `suggested` (available as reference).

#### Translator — `workers/translator.py` + `workers/providers/openai_compatible.py`

**In:** `ContextPacket` with full translation context  
**Out:** `TranslationWorkerOutput` — target segments with sentence-level `AlignmentEdge[]` (1:1, 1:n, n:1 mappings)

Calls any OpenAI-compatible API endpoint with a structured output schema enforced by Pydantic. The response must conform to the `TranslationWorkerOutput` schema — if the LLM returns malformed JSON, the error is classified as retryable and the packet is re-queued. Each output includes token usage metrics for budget tracking.

#### Reviewer — `services/review.py`

**In:** Translated chapter with all packets completed  
**Out:** `ReviewIssue[]` with severity, root cause layer, and suggested `IssueAction`

Runs five QA dimensions:
1. **Coverage** — Every source sentence has a corresponding target segment
2. **Alignment** — Segment boundaries match sentence boundaries, no orphans
3. **Terminology** — Locked terms rendered exactly, preferred terms applied consistently
4. **Format** — HTML tags, code spans, and structural markers preserved
5. **Style drift** — Detects unnatural phrasing, over-literal translation, register mismatch

Each issue gets a `Severity` (low → critical) and a `RootCauseLayer` (parse, segment, translation, alignment, etc.) that determines the fix strategy.

#### Rule Engine — `orchestrator/rule_engine.py`

**In:** `ReviewIssue` with context  
**Out:** Deterministic `IssueAction` — one of 12 action types from `EDIT_TARGET_ONLY` to `REPARSE_DOCUMENT`

Maps each issue to the minimum-disruption fix. A terminology violation routes to `UPDATE_TERMBASE_THEN_RERUN_TARGETED` (update the term base, then retranslate only affected packets). A structural parse error escalates to `REPARSE_CHAPTER`. Actions are executed, affected packets are invalidated (`TRANSLATED → INVALIDATED → BUILT`), and the pipeline re-runs only what changed.

#### Exporter — `services/export.py` + `services/export_routing.py`

**In:** Translated + QA-approved chapters  
**Out:** Files in `artifacts/exports/{document_id}/` — bilingual HTML, merged Chinese HTML/Markdown, rebuilt EPUB/PDF, JSONL

Export routing selects the output format based on source type and quality gate results. EPUB sources can produce rebuilt EPUB with translations injected. PDF sources produce HTML/Markdown or rebuilt PDF with layout preservation. Each export records its manifest (coverage metrics, export date, version) alongside the artifact.

#### Run Controller — `app/runtime/controllers/{run,chapter,packet,export,review,incident}_controller.py`

The orchestration brain. Implements a **Kubernetes-style reconciliation loop**: each tick, controllers observe current state (DB) and take the minimum action to move toward desired state. `RunController` ensures chapter runs exist. `ChapterController` ensures packet tasks and review sessions. `PacketController` binds work items and projects lane health. This design is idempotent — a crash at any point simply resumes on the next tick.

#### Memory Service — `services/memory_service.py`

Maintains chapter-level knowledge that improves across the translation: terminology base (with lock levels), entity registry (people/orgs with canonical Chinese names), chapter briefs, style deltas, and translation memory. Memory snapshots are versioned — proposals from translation runs are surfaced for human approval/rejection before committing.

---

## End-to-End Workflow

This section traces the complete journey from file upload to exported book.

### Phase 1: Bootstrap

| Step | Actor | Endpoint / Function | What Happens |
|------|-------|-------------------|--------------|
| 1. Upload | User | `POST /v1/documents/bootstrap-upload` | User clicks "Click to select .pdf / .epub file" in the WORK workspace. File is saved to `artifacts/uploads/{uuid}/` |
| 2. Parse | System | `BootstrapOrchestrator.bootstrap_document()` | Routes to EPUBParser or PDFParser based on file type. Extracts chapter boundaries, block structure (headings, paragraphs, code, tables), and metadata |
| 3. Segment | System | `EnglishSentenceSegmenter` | Splits blocks into sentences. Identifies protected spans (code, URLs, citations) that must pass through untranslated |
| 4. Profile | System | `BookProfile` builder | Analyzes document for domain (tech/business/fiction), style metrics, vocabulary complexity, and layout characteristics |
| 5. Packetize | System | Packet builder | Groups consecutive sentences into `TranslationPacket`s — the atomic unit of translation. Each packet includes prev/next context sentences for coherence |
| 6. Initialize memory | System | `MemoryService` | Creates initial chapter briefs, empty terminology base, entity registry, and style delta snapshots |
| 7. Persist | System | SQLAlchemy | Inserts Document, Chapters, Blocks, Sentences, TranslationPackets, PacketSentenceMaps, BookProfile, MemorySnapshots |
| 8. Respond | System | → Frontend | Returns `DocumentSummary` (chapter count, sentence count, packet count). Frontend enables the "Translate" button |

### Phase 2: Translation

| Step | Actor | What Happens |
|------|-------|--------------|
| 1. Create Run | User clicks "Translate" | `POST /v1/runs` creates a `DocumentRun` (type: `translate_full`, status: `queued`) |
| 2. Schedule work | System | `RunControlService` creates `WorkItem` rows — one per translation packet — with stage `TRANSLATE` and status `PENDING` |
| 3. Reconciliation loop | System | `ControllerRunner.reconcile_run()` ensures ChapterRuns, PacketTasks, and ReviewSessions exist. Binds PacketTasks to WorkItems |
| 4. Claim & lease | System | `RunExecutionService` claims a pending WorkItem, creates a `WorkerLease` with expiration. Lease prevents double-processing |
| 5. Compile context | System | `ChapterContextCompiler` builds a `ContextPacket`: chapter brief + terminology + entities + discourse bridges + style constraints |
| 6. Call LLM | System | `LLMTranslationWorker` sends prompt + context to OpenAI-compatible API. Response must match `TranslationWorkerOutput` Pydantic schema |
| 7. Validate & store | System | Verifies alignment edges (every source sentence covered), persists target segments + alignment edges + usage metrics |
| 8. Advance state | System | Packet: `BUILT → RUNNING → TRANSLATED`. Chapter: advances when all packets complete. Run: advances when all chapters complete |

**The frontend polls every 2.5 seconds**, showing real-time progress per chapter in the worklist panel.

### Phase 3: Review

| Step | What Happens |
|------|--------------|
| 1. QA scan | Reviewer runs 5 checks (coverage, alignment, terminology, format, style drift) per chapter |
| 2. Issue creation | Each finding becomes a `ReviewIssue` with severity and root cause layer |
| 3. Action routing | Rule Engine maps each issue to the minimum-disruption fix action |
| 4. Execute action | System executes the action (edit target, update termbase, rebuild packet, reparse chapter, etc.) |
| 5. Invalidation cascade | Affected packets transition `TRANSLATED → INVALIDATED → BUILT` |
| 6. Re-translate | Only invalidated packets are re-queued — unchanged work is preserved |
| 7. Re-review | Cycle repeats until all issues are resolved or flagged for manual review |

### Phase 4: Export

| Step | What Happens |
|------|--------------|
| 1. Export routing | Selects format based on source type and quality gates |
| 2. Build artifacts | Generates bilingual HTML, merged Chinese HTML/Markdown, rebuilt EPUB/PDF per chapter and per document |
| 3. Write manifests | JSON metadata with coverage metrics, timestamps, version info alongside each artifact |
| 4. Update status | Document transitions to `PARTIALLY_EXPORTED` → `EXPORTED`. Deliverables appear in the Library |

---

## Self-Healing & Fault Tolerance

Book Agent is designed to run unattended on long translation jobs (hours to days). The runtime handles failures at every level without manual intervention.

### Exception Classifier

Every exception during translation is classified before the runtime decides what to do (`document_run_executor.py:80-115`):

| Error Type | Examples | Action |
|-----------|----------|--------|
| **Retryable** | HTTP 429 (rate limit), 500/502/503/504 (server error), timeouts, connection reset, "database is locked" | Auto-retry with backoff. Packet returns to queue |
| **Non-retryable** | HTTP 400 (bad request), 401 (invalid API key), 403 (forbidden) | Mark as `terminal_failed`, open incident |
| **Budget** | HTTP 402 + "insufficient balance" | **Auto-pause entire run**. Resume when user adds balance and clicks Resume |
| **Schema** | LLM returns malformed JSON / doesn't match Pydantic schema | Classified as retryable — re-queued for another attempt |

### Blockage Detection

The runtime repair system (`services/runtime_repair_blockage.py`) monitors work items for stalls:

- Work items stuck in `retryable_failed` or `running` beyond their lease expiration are detected
- The blockage projector determines the state: `ready_to_continue`, `backoff_blocked`, or `manual_escalation_waiting`
- Backoff-blocked items resume automatically when their retry window elapses
- Items requiring manual escalation are surfaced in the WORK workspace with context

### Incident & Patch System

When the reconciliation controllers detect anomalies — export misrouting, review deadlocks, packet runtime defects — they create `RuntimeIncident` records:

1. **Detection** — Controllers identify the anomaly (e.g., export landed in wrong format, review loop won't converge)
2. **Diagnosis** — Incident is tagged with `RuntimeIncidentKind` (export_misrouting, runtime_defect, review_deadlock, packet_runtime_defect)
3. **Patch proposal** — System auto-generates a `RuntimePatchProposal` with corrective actions
4. **Validation** — Patch is validated against budget constraints and safety rules
5. **Application** — If within budget, patch is applied automatically. Otherwise, surfaced for human approval

Incident kinds: `EXPORT_MISROUTING` | `RUNTIME_DEFECT` | `REVIEW_DEADLOCK` | `PACKET_RUNTIME_DEFECT`

### Checkpoint Resume

Translation runs support full checkpoint-based resume (`services/run_control.py`):

- **Resume from pause:** `POST /v1/runs/{run_id}/resume` picks up exactly where the run stopped
- **Resume from failure:** `POST /v1/runs/{run_id}/retry` resets failed work items and continues
- **Create from prior run:** `resume_from_run_id` parameter creates a new run that inherits all completed translation artifacts, review decisions, and export progress from a previous run
- **Recovery lineage:** Every recovery operation is recorded in `recovered_lineage` for auditability

### PDF-Specific Recovery

- `domain/structure/pdf_recovery.py` — Handles PDF parsing failures (corrupted pages, missing fonts, malformed structure)
- `services/pdf_prose_artifact_repair.py` — Repairs layout/structure issues in extracted text blocks

---

## Configuration Reference

All configuration is via `.env` (copy from `.env.example`).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | **Yes** | — | API key for any OpenAI-compatible provider |
| `OPENAI_BASE_URL` | No | `https://api.deepseek.com/v1` | LLM API endpoint. Change for OpenAI, Moonshot, local vLLM, etc. |
| `BOOK_AGENT_TRANSLATION_MODEL` | No | `deepseek-chat` | Model name to use |
| `BOOK_AGENT_TRANSLATION_BACKEND` | No | `openai_compatible` | Translation backend type |
| `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS` | No | `120` | Per-request timeout |
| `BOOK_AGENT_TRANSLATION_MAX_RETRIES` | No | `2` | Max retries per packet |
| `BOOK_AGENT_TRANSLATION_RETRY_BACKOFF_SECONDS` | No | `2.0` | Backoff between retries |
| `BOOK_AGENT_DATABASE_URL` | No | SQLite (local file) | PostgreSQL URL for production. SQLite works out of the box |
| `BOOK_AGENT_HOST` | No | `127.0.0.1` | Backend bind address |
| `BOOK_AGENT_PORT` | No | `8999` | Backend port |
| `BOOK_AGENT_FRONTEND_PORT` | No | `4173` | Frontend dev server port |
| `BOOK_AGENT_CORS_ALLOW_ORIGINS` | No | `[]` | Comma-separated CORS origins |

### Using Different LLM Providers

```bash
# DeepSeek (default)
OPENAI_API_KEY=sk-your-deepseek-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
BOOK_AGENT_TRANSLATION_MODEL=deepseek-chat

# OpenAI
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1
BOOK_AGENT_TRANSLATION_MODEL=gpt-4o

# Local vLLM
OPENAI_API_KEY=dummy
OPENAI_BASE_URL=http://localhost:8000/v1
BOOK_AGENT_TRANSLATION_MODEL=your-local-model
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic 2.x, Alembic |
| **Frontend** | React 18, TypeScript, Vite, React Query |
| **Database** | SQLite (dev, zero-config) / PostgreSQL 16 (production) |
| **PDF Processing** | PyMuPDF (text extraction, layout analysis, OCR routing) |
| **LLM Integration** | httpx → any OpenAI-compatible API (structured output with Pydantic validation) |
| **Deployment** | Docker Compose, uvicorn, uv/pip |

---

## Project Structure

```
book-agent/
├── src/book_agent/
│   ├── app/                    # FastAPI app, API routes, runtime executor
│   │   ├── api/routes/         # REST endpoints (documents, runs, actions, health)
│   │   └── runtime/            # Run executor + reconciliation controllers
│   │       └── controllers/    # run, chapter, packet, export, review, incident
│   ├── domain/                 # Core domain models and logic
│   │   ├── structure/          # Parsers (EPUB, PDF, OCR)
│   │   ├── segmentation/       # Sentence splitting
│   │   ├── context/            # Context packet builders
│   │   ├── models/             # SQLAlchemy models
│   │   └── enums.py            # All state machine enums
│   ├── services/               # Business logic layer
│   │   ├── bootstrap.py        # Document ingestion pipeline
│   │   ├── translation.py      # Translation orchestration
│   │   ├── review.py           # Multi-dimensional QA
│   │   ├── export.py           # Artifact generation
│   │   ├── context_compile.py  # Rich context building
│   │   ├── memory_service.py   # Terminology & entity memory
│   │   ├── run_control.py      # Work item scheduling
│   │   ├── run_execution.py    # Lease management
│   │   ├── runtime_repair_*.py # Self-healing subsystem
│   │   └── workflows.py        # High-level orchestration
│   ├── orchestrator/           # Rule engine, state machine, rerun planning
│   ├── workers/                # LLM translation workers
│   │   └── providers/          # OpenAI-compatible client
│   ├── infra/                  # Database, repositories
│   └── core/                   # Config, logging, ID generation
├── frontend/src/
│   ├── features/               # Page components (workspace, runs, library, deliverables)
│   └── lib/                    # API client, workflow state machine
├── alembic/                    # Database migrations (16 versions)
├── artifacts/                  # Runtime data (DB, uploads, exports)
├── dev.sh                      # One-command development launcher
├── service.sh                  # Production service manager
├── compose.yaml                # Docker Compose (PostgreSQL + App)
└── Dockerfile                  # Container image
```

---

## License

MIT
