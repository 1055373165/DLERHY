# Book Agent

**LLM-powered translation agent for English books → Chinese.** EPUB/PDF in, bilingual or Chinese-only EPUB/PDF/HTML/Markdown out — with sentence-level alignment, packet-level re-translation, and a Kubernetes-style self-healing runtime.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![React](https://img.shields.io/badge/frontend-React%20%2B%20TS-61DAFB)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Quick Start

You only need to configure **one** variable: `OPENAI_API_KEY`. Pick one path:

### Path A — Docker (recommended)

```bash
git clone https://github.com/your-org/book-agent.git && cd book-agent
cp .env.example .env           # then edit .env: set OPENAI_API_KEY=sk-...
docker compose up -d
```

Open `http://localhost:58000`. Docker Compose brings up PostgreSQL 16 (with a persistent `postgres_data` volume) and the backend container wired together. Exports persist in the `app_exports` volume.

### Path B — Local (macOS)

```bash
# 1. Install & start PostgreSQL 16, create the database
brew install postgresql@16
brew services start postgresql@16
createdb book_agent

# 2. Clone, configure, launch
git clone https://github.com/your-org/book-agent.git && cd book-agent
cp .env.example .env           # then edit .env: set OPENAI_API_KEY=sk-...
./dev.sh
```

`dev.sh` auto-installs Python dependencies (via `uv` or `venv`), installs frontend `node_modules`, runs Alembic migrations, and boots backend + frontend. Open `http://localhost:4173`.

> Upload an `.epub` or `.pdf` in the **WORK** workspace → click **Bootstrap** → click **Translate**. Progress streams live.

---

## Why Book Agent

- **Packet-level re-translation** — the smallest translation unit is 3–8 sentences. A rate-limit or schema failure only re-runs the affected packet, never the whole chapter or book.
- **Kubernetes-style reconciliation** — a set of stateless controllers drive PostgreSQL-backed state toward the desired end state. Any crash resumes on the next tick.
- **Self-healing runtime** — classified exceptions trigger retries; stalled packets trigger blockage detection; runtime anomalies open incidents with auto-generated patch proposals.
- **Full-run observability** — every state transition is an auditable event. Budget, token spend, per-chapter timelines, and pending decisions are all live in the UI and REST API.
- **Deterministic fix routing** — review issues map to one of 12 action types (`RERUN_PACKET`, `UPDATE_TERMBASE_THEN_RERUN_TARGETED`, `REPARSE_CHAPTER`, ...) — no black-box "LLM fixes itself".
- **Multi-route everywhere** — any OpenAI-compatible LLM backend (DeepSeek, OpenAI, Moonshot, local vLLM); multi-format export (HTML / Markdown / EPUB / PDF / JSONL).
- **8-worker parallelism out of the box** — `default_max_parallel_workers = 8`, adjustable per run via the budget field.

---

## Architecture

Book Agent is built like a mini Kubernetes: **stateless controllers watch PostgreSQL and nudge the world toward the desired state.** Data plane workers (parsers, translators, reviewers, exporters) are dumb — they do one thing and report back. The control plane reconciles. Failures are not special cases — they're just another state that the next reconcile tick will handle.

```d2
direction: right

user: User (WORK Workspace) {
  shape: person
}

api: FastAPI REST {
  shape: rectangle
  documents.upload
  runs.create
  runs.events
  runs.pause_resume
}

state_store: PostgreSQL 16 {
  shape: cylinder
  style.fill: "#336791"
  style.font-color: white
  documents
  chapters
  packets
  work_items
  runtime_incidents
  audit_events
}

control_plane: Control Plane (Reconciliation Loops) {
  run_ctrl: RunController
  chapter_ctrl: ChapterController
  packet_ctrl: PacketController
  review_ctrl: ReviewController
  export_ctrl: ExportController
  incident_ctrl: IncidentController
  run_ctrl -> state_store: watch + reconcile
  chapter_ctrl -> state_store: watch + reconcile
  packet_ctrl -> state_store: watch + reconcile
  review_ctrl -> state_store: watch + reconcile
  export_ctrl -> state_store: watch + reconcile
  incident_ctrl -> state_store: watch + reconcile
}

data_plane: Data Plane (Stateless Workers) {
  parser: Parser\n(EPUB / PDF / OCR)
  segmenter: Segmenter\n(sentences + protected spans)
  context: Context Compiler\n(terms / entities / briefs)
  translators: Translator Pool\n(× 8 parallel workers)
  reviewer: Reviewer\n(coverage / alignment / term / format / style)
  rule_engine: Rule Engine\n(12 action types)
  exporter: Exporter\n(HTML / MD / EPUB / PDF / JSONL)

  parser -> segmenter
  segmenter -> context
  context -> translators
  translators -> reviewer
  reviewer -> rule_engine
  rule_engine -> exporter
}

llm_routes: LLM Backends {
  shape: cloud
  deepseek: DeepSeek
  openai: OpenAI
  vllm: local vLLM
}

self_heal: Self-Healing Loop {
  classifier: Exception Classifier\n(retry / pause / incident)
  blockage: Blockage Detector\n(stalled leases)
  repair: Runtime Repair Registry
  patch: Patch Proposals
  classifier -> repair
  blockage -> repair
  repair -> patch
  patch -> incident_ctrl: apply within budget
}

observability: Observability Stream {
  shape: queue
  events: Run Audit Events
  metrics: Budget / Token Usage
  timelines: Chapter Timelines
}

user -> api
api -> state_store: write desired state
control_plane -> data_plane: lease work items
data_plane -> state_store: report results
translators -> llm_routes: any OpenAI-compatible
state_store -> observability: event stream
observability -> user: live polling (2.5s)
data_plane -> self_heal: exceptions
```

**The key insight:** nothing in the data plane remembers anything. Kill any worker, any controller, any backend process — the next reconcile tick reads PostgreSQL, sees what's missing, and resumes. No orchestration bus, no task queue, no in-memory state to lose.

---

## End-to-End Workflow

```
Upload .epub/.pdf
      │
      ▼
┌─────────────────┐   BOOTSTRAP
│ Parse → Segment │   Document → Chapters → Blocks → Sentences
│ Profile → Packetize │ + BookProfile + initial memory snapshots
└─────────┬───────┘
          │
          ▼
┌─────────────────┐   TRANSLATE (parallel × 8)
│ For each packet:│   claim → lease → compile context → LLM call
│ Context Compile │   → Pydantic-validated output → alignment
│ → LLM → Validate│   → persist target segments + audit event
└─────────┬───────┘
          │
          ▼
┌─────────────────┐   REVIEW
│ Coverage,       │   per-chapter QA → ReviewIssue[]
│ Alignment, Term,│   each issue → Rule Engine → IssueAction
│ Format, Style   │
└─────────┬───────┘
          │
          ▼
┌─────────────────┐   FIX LOOP (packet-level invalidation)
│ Execute action  │   only affected packets: TRANSLATED → INVALIDATED → BUILT
│ → Rebuild       │   controllers re-queue them on the next tick
└─────────┬───────┘
          │
          ▼
┌─────────────────┐   EXPORT
│ Bilingual HTML, │   multi-format emission → artifacts/exports/{doc_id}/
│ Merged HTML/MD, │   document moves to PARTIALLY_EXPORTED → EXPORTED
│ Rebuilt EPUB/PDF│
└─────────────────┘
```

---

## Packet-Level Re-translation: Why It Matters

Traditional LLM translation tools fail at **book granularity** or **chapter granularity** — when the LLM call errors out, hits a rate limit, or returns malformed JSON, the whole task rolls back. This is like TCP without segmentation: lose one byte in the middle of a 1 MB transfer and you re-send the entire 1 MB.

Book Agent splits every chapter into dozens of `TranslationPacket`s — 3–8 consecutive sentences with surrounding context. The packet is the atomic unit of translation. Its state machine is `BUILT → RUNNING → TRANSLATED`, with a failure edge back to `BUILT`. When something breaks, **only the failed packet re-runs**. The other 99% stay exactly as they were.

| Scenario | Coarse-grained retry | Book Agent packet re-translation |
|---------|---------------------|--------------------------------|
| Transient HTTP 429 | Re-translate entire chapter | Rate-limited packet backs off and retries |
| LLM returns schema-invalid JSON | Entire chapter discarded | Single packet goes back to `BUILT`, re-runs next tick |
| Terminology fix (e.g. renaming a proper noun) | Re-translate the whole book | Rule Engine invalidates only packets containing the term |
| Mid-translation chapter-brief edit | Re-translate whole chapter | Only that chapter's packets are invalidated |
| API key swap / model upgrade on a running job | Start over | `resume_from_run_id` inherits every completed packet |

Packet-level invalidation is what makes the whole `Review → Action → Rebuild` loop **locally convergent**. An 800k-word book with a single flaky packet wastes tens of seconds, not hours.

---

## Checkpoint & Resume

Book Agent has three layers of checkpointing, all backed by PostgreSQL transactions — there is no separate checkpoint file to corrupt or forget.

1. **Implicit packet checkpoints.** Every packet that reaches `TRANSLATED` is committed. Process crash, Docker restart, server reboot — completed packets cannot revert. The translator pool simply picks up where it left off.
2. **Run-level checkpoints.** `DocumentRun.status_detail_json` tracks the current pipeline stage, completed work-item count, and cumulative token/cost usage. `POST /v1/runs/{run_id}/resume` reads this and restarts exactly where the run stopped.
3. **Cross-run inheritance.** The `resume_from_run_id` parameter lets a new run inherit all completed translations, review decisions, and export progress from a prior run. Use this when you change models, tweak terminology, or retry after fixing a review issue — only the affected work re-runs.

```bash
# Typical recovery flow:
# 1. Run pauses on HTTP 402 insufficient_balance
curl -X POST http://localhost:58000/v1/runs/$RUN_ID/resume   # after topping up

# 2. Something else failed and you want a clean retry
curl -X POST http://localhost:58000/v1/runs/$RUN_ID/retry

# 3. Switched provider mid-book — start a new run inheriting the old one
curl -X POST http://localhost:58000/v1/runs \
  -d '{"document_id": "...", "run_type": "translate_full", "resume_from_run_id": "'$RUN_ID'"}'
```

---

## Observability

Book Agent treats observability as a first-class concern, not a sidecar. Every state transition — packet claimed, lease expired, incident opened, patch applied — is an auditable event persisted in PostgreSQL.

**Where to look for what:**

| I want to know... | Look at... |
|------------------|-----------|
| How far has this translation progressed? | WORK workspace chapter timeline • `GET /v1/runs/{id}` |
| Why is this packet stuck? | `GET /v1/runs/{id}/events` — full audit stream |
| How many tokens have I spent? | Run summary `budget` field (live, per-provider) |
| Which chapters have pending human decisions? | WORK workspace worklist priority queue |
| What incidents are open? | Worklist panel — shows `RuntimeIncident` with root cause layer |
| Backend traceback / stack trace | `artifacts/server.log` |
| Frontend console output | `artifacts/frontend.log` |

**Run control endpoints** — the full lifecycle is REST-addressable:

```
GET  /v1/runs/{id}              # snapshot: status, budget, stage progress
GET  /v1/runs/{id}/events       # paginated audit event stream
POST /v1/runs/{id}/pause        # graceful pause at next safe point
POST /v1/runs/{id}/resume       # resume from checkpoint
POST /v1/runs/{id}/retry        # reset retryable_failed items
POST /v1/runs/{id}/drain        # finish in-flight, no new work
POST /v1/runs/{id}/cancel       # hard stop
```

The frontend polls `runs/{id}` and `chapters/worklist` every 2.5 seconds when a run is active — the UI is a live view of PostgreSQL state, not a cached snapshot.

---

## Self-Healing & Fault Tolerance

Every exception during translation is classified (`document_run_executor.py:80-115`) before the runtime decides what to do:

| Class | Examples | Action |
|-------|----------|--------|
| **Retryable** | HTTP 429 / 5xx, timeouts, connection reset, schema-validation failures | Packet returns to `BUILT`, re-queued with backoff |
| **Non-retryable** | HTTP 400 / 401 / 403, invalid API key | Marked `terminal_failed`, incident opened |
| **Budget** | HTTP 402 + "insufficient balance" | **Entire run auto-paused**, resumable via REST |

Beyond classification, two subsystems keep long-running jobs alive:

- **Blockage Detector** (`services/runtime_repair_blockage.py`) — monitors leases. Items stuck past expiration are categorized (`ready_to_continue` / `backoff_blocked` / `manual_escalation_waiting`). Backoff-blocked items resume automatically when their retry window elapses.
- **Incident Manager** (`controllers/incident_controller.py`) — detects anomalies (export misrouting, review deadlocks, runtime defects), auto-generates `RuntimePatchProposal`s, and applies them if within budget. Otherwise, surfaces them in the UI for human approval.

---

## Project Structure

```
book-agent/
├── src/book_agent/
│   ├── app/
│   │   ├── api/routes/          # REST endpoints (documents, runs, actions, health)
│   │   └── runtime/
│   │       ├── document_run_executor.py   # translator pool + exception classifier
│   │       └── controllers/               # reconciliation loops
│   ├── domain/
│   │   ├── structure/           # Parser (epub.py, pdf.py, ocr.py)
│   │   ├── segmentation/        # Segmenter
│   │   ├── context/             # Context Compiler builders
│   │   └── enums.py             # State machines
│   ├── services/
│   │   ├── bootstrap.py         # Ingest pipeline
│   │   ├── translation.py       # Packet dispatch
│   │   ├── review.py            # 5-dimension QA
│   │   ├── export.py            # Multi-format emission
│   │   ├── memory_service.py    # Terms / entities / briefs
│   │   ├── run_control.py       # Run lifecycle & checkpointing
│   │   └── runtime_repair_*.py  # Self-healing subsystem
│   ├── orchestrator/
│   │   ├── rule_engine.py       # Issue → Action mapping (12 types)
│   │   └── state_machine.py     # Packet / run substates
│   ├── workers/
│   │   ├── translator.py
│   │   └── providers/openai_compatible.py
│   └── infra/                   # DB, repositories
├── frontend/src/features/       # workspace / runs / library / deliverables
├── alembic/                     # PostgreSQL migrations
├── compose.yaml                 # Docker: PostgreSQL + app
├── dev.sh                       # Local dev launcher (macOS)
└── service.sh                   # Production process manager
```

---

## Configuration

Only `OPENAI_API_KEY` is required. See `.env.example` for the full list.

| Variable | Required | Default | Note |
|---------|---------|---------|------|
| `OPENAI_API_KEY` | **Yes** | — | Any OpenAI-compatible provider |
| `OPENAI_BASE_URL` | No | `https://api.deepseek.com/v1` | Change this to swap providers |
| `BOOK_AGENT_TRANSLATION_MODEL` | No | `deepseek-chat` | — |
| `BOOK_AGENT_DATABASE_URL` | No | `postgresql+psycopg://postgres:postgres@localhost:55432/book_agent` | `./dev.sh` local development and Docker Compose both use `55432` |
| `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS` | No | `120` | Per-request timeout |
| Per-run `max_parallel_workers` | No | `8` | Set via run budget field |

**Swapping providers** — change `OPENAI_BASE_URL` + `BOOK_AGENT_TRANSLATION_MODEL`. That's it. Same API surface for DeepSeek, OpenAI, Moonshot, local vLLM.

---

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic 2 · Alembic |
| Frontend | React 18 · TypeScript · Vite · React Query |
| Database | PostgreSQL 16 |
| PDF | PyMuPDF |
| LLM | httpx → any OpenAI-compatible endpoint, structured output |
| Deploy | Docker Compose · uvicorn · uv/pip |

---

## License

MIT
