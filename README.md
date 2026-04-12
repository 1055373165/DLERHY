<div align="center">

# 📚 Book Agent

### Whole-book English → Chinese translation agent
**EPUB / PDF in, bilingual HTML · Markdown · EPUB · PDF out — with packet-level retries, Kubernetes-style reconciliation, and a self-healing runtime.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/frontend-React%20%2B%20TS-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![Docker](https://img.shields.io/badge/deploy-Docker%20Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#-license)

[Quick Start](#-quick-start) · [Why Book Agent](#-why-book-agent) · [How It Works](#-how-it-works) · [Architecture](#-architecture) · [API](#-api-reference) · [FAQ](#-faq)

</div>

---

## ✨ TL;DR

> Drop a 600-page technical book into the UI. Come back later to a polished bilingual Markdown / EPUB / PDF.
> Kill the process mid-run — restart it — translation resumes from the exact packet it left off.
> Change the LLM provider by editing **one** environment variable.

One required variable: `OPENAI_API_KEY`. Everything else has sane defaults.

---

## 🚀 Quick Start

### Option 1 · Docker (recommended, zero setup)

```bash
git clone https://github.com/1055373165/DLERHY.git
cd DLERHY
cp .env.example .env              # edit: OPENAI_API_KEY=sk-...
docker compose up -d              # → http://localhost:58000
```

Docker Compose brings up PostgreSQL 16 and the app container in one shot.
Uploads persist in the `app_exports` volume; database state in `postgres_data`.

### Option 2 · Local dev (macOS / Linux)

```bash
git clone https://github.com/1055373165/DLERHY.git
cd DLERHY
cp .env.example .env              # edit: OPENAI_API_KEY=sk-...
./dev.sh                          # → http://localhost:4173
```

`dev.sh` installs Python deps via [`uv`](https://docs.astral.sh/uv/), starts PostgreSQL in Docker, runs Alembic migrations, and boots backend + frontend with hot reload.

### Your first translation — 3 clicks

1. Open `http://localhost:4173` → **Library** → drag in an `.epub` or `.pdf`.
2. **Bootstrap** parses & segments the book into chapters → blocks → sentences → packets.
3. **Translate** streams progress live; **Export** delivers bilingual MD / HTML / EPUB / PDF.

> 💡 Prefer the terminal? Every step has a CLI equivalent — see [CLI reference](#cli).

---

## 🎯 Why Book Agent

Long-document translation isn't just "call an LLM in a loop." The hard problems are **failure recovery**, **context coherence**, and **cost control**. Book Agent tackles each head-on.

| Problem with naive pipelines | How Book Agent solves it |
|---|---|
| One HTTP 429 kills a 10-hour run | **Packet-level atomicity** — the unit is 3–8 sentences. A failure re-runs one packet; the other 99 % is untouched. |
| In-memory state lost on crash / deploy | **Kubernetes-style reconciliation** — stateless controllers diff DB state against the desired state every tick. Kill anything; it resumes. |
| "LLM fixes itself" ends in loops | **Deterministic fix routing** — review issues map to 12 named actions (`RERUN_PACKET`, `UPDATE_TERMBASE_THEN_RERUN_TARGETED`, `REPARSE_CHAPTER`, …). No black-box self-correction. |
| Terminology drifts between chapters | **Translation memory per chapter** — termbases, entity registries, style deltas, and discourse bridges are compiled into every packet's context. |
| Runs block on one slow chapter | **8-way parallelism with lane isolation** — PostgreSQL guarantees concurrency safety; per-chapter lanes keep context consistent. |
| Vendor lock-in to one LLM | **Any OpenAI-compatible backend** — DeepSeek, OpenAI, Moonshot, vLLM, Ollama. Swap `OPENAI_BASE_URL` + `BOOK_AGENT_TRANSLATION_MODEL`. |
| Silent failures you only notice at export | **Self-healing runtime** — exceptions classified into `retry` / `pause` / `incident`. Stalled leases auto-reclaim. Anomalies emit `PatchProposal`s that auto-apply within a budget. |
| Opaque pipelines you can't inspect | **Full audit trail** — every state transition, every LLM call, every fix action is a row in `audit_events`. The UI is a live view of Postgres, not a cached snapshot. |

### At a glance

- ✅ **One required env var** — `OPENAI_API_KEY`.
- ✅ **Sole data store is PostgreSQL** — no Redis, no Kafka, no task queue.
- ✅ **Pause · Resume · Retry · Cancel** are first-class REST verbs.
- ✅ **Nine export formats** — bilingual MD/HTML, interleaved, Chinese-only EPUB/PDF, review JSON, sentence-aligned JSONL.
- ✅ **Runs on a laptop** — 8 GB RAM is enough for the full stack.

---

## 🔄 How It Works

```
  ┌───────────┐   ┌───────┐   ┌─────────┐   ┌──────────┐   ┌───────────┐   ┌────────┐   ┌────────┐
  │  Ingest   │──▶│ Parse │──▶│ Segment │──▶│ Packetize│──▶│ Translate │──▶│ Review │──▶│ Export │
  └───────────┘   └───────┘   └─────────┘   └──────────┘   └───────────┘   └────┬───┘   └────────┘
                                                                  ▲              │
                                                                  └── Repair ◀───┘
```

| Stage | What happens | Output |
|---|---|---|
| **Ingest** | Upload `.epub` / `.pdf`, content-address the source | `documents` row, bytes on disk |
| **Parse** | PyMuPDF / EPUB structural analysis — headings, code, tables, figures | Chapter · Block IR |
| **Segment** | Sentence-level segmentation with CJK-aware rules | `sentences` table |
| **Packetize** | Group 3–8 sentences + compiled context (termbase, memory, bridge) | `translation_packets` |
| **Translate** | 8 workers pull leases, call the LLM with structured output | Translated packets, audit events |
| **Review** | Severity-scored QA issues → mapped to one of 12 action types | `review_issues`, `issue_actions` |
| **Repair** | Rerun the smallest unit that can fix the issue (packet > chapter > document) | New packet / chapter versions |
| **Export** | Assemble bilingual or Chinese-only deliverables from final sentences | Files under `artifacts/exports/` |

The loop is **driven by reconciliation**, not a pipeline orchestrator. Controllers wake on a tick, read Postgres, and do whatever moves the system closer to the desired terminal state. That's what makes it resumable.

---

## 🏛 Architecture

The system is organised in four horizontal tiers — **Client**, **API / Control Plane**, **Reconcilers & Services**, and **State** — plus one external dependency (**LLM provider**). Every reconciler is stateless; the only source of truth is PostgreSQL.

### Mermaid (`flowchart TB`)

```mermaid
%%{init: {'flowchart': {'htmlLabels': true, 'curve': 'linear'}, 'theme': 'base'}}%%
flowchart TB
    %% ---------- Client ----------
    subgraph Client["🖥️ Client"]
        UI["React + Vite Dashboard<br/>polls /v1/runs/* every 2.5s"]
        CLI["CLI<br/>book-agent …"]
    end

    %% ---------- API / Control Plane ----------
    subgraph API["🧭 API · Control Plane (FastAPI)"]
        direction LR
        R_DOC["/v1/documents<br/>bootstrap · summary"]
        R_RUN["/v1/runs<br/>create · pause · resume · retry · cancel"]
        R_ACT["/v1/actions<br/>12 deterministic fix types"]
        R_HEALTH["/v1/health"]
    end

    %% ---------- Reconcilers (stateless controllers) ----------
    subgraph Reconcile["🔁 Reconcilers · k8s-style control loop (stateless)"]
        direction LR
        C_RUN["RunController<br/>lifecycle & budget gate"]
        C_CHAP["ChapterController<br/>lane isolation"]
        C_PKT["PacketController<br/>lease · retry · classify"]
        C_REV["ReviewController<br/>issue → action routing"]
        C_EXP["ExportController"]
        C_INC["IncidentController<br/>patch proposals"]
    end

    %% ---------- Domain Services ----------
    subgraph Services["⚙️ Domain Services"]
        direction LR
        S_PARSE["Parse / Segment<br/>PDF · EPUB · IR"]
        S_CTX["Context Compiler<br/>termbase · memory · bridge"]
        S_TRANS["Translation Worker<br/>× 8 parallel"]
        S_QA["QA Review<br/>severity · issues"]
        S_REPAIR["Repair / Rerun<br/>RERUN_PACKET · REPARSE_CHAPTER · …"]
        S_EXP["Export<br/>bilingual MD/HTML/EPUB/PDF"]
    end

    %% ---------- State ----------
    subgraph State["🗄️ State — PostgreSQL 16 (sole store)"]
        direction LR
        T_DOC[("documents / chapters<br/>sentences / packets")]
        T_RUN[("document_runs<br/>audit_events · leases")]
        T_MEM[("memory_snapshots<br/>termbases · registries")]
        T_ART[("exports / artifacts")]
    end

    %% ---------- External ----------
    LLM[["🤖 OpenAI-compatible LLM<br/>DeepSeek · OpenAI · vLLM"]]

    %% Client → API
    UI -- "HTTPS / JSON" --> API
    CLI -- "HTTPS / JSON" --> API

    %% API writes desired state
    R_DOC -- "write desired state" --> State
    R_RUN -- "write desired state" --> State
    R_ACT -- "enqueue action" --> State
    R_HEALTH -. "read" .-> State

    %% Controllers observe state
    Reconcile == "watch · lease · advance" ==> State

    %% Controllers dispatch services
    C_RUN --> S_PARSE
    C_CHAP --> S_CTX
    C_PKT --> S_TRANS
    C_REV --> S_QA
    C_REV --> S_REPAIR
    C_EXP --> S_EXP
    C_INC --> S_REPAIR

    %% Services persist results
    Services == "commit result · emit audit event" ==> State

    %% External calls
    S_TRANS -- "chat.completions<br/>structured output" --> LLM
    S_QA -. "optional LLM judge" .-> LLM

    %% Styling
    classDef ext fill:#fff7e6,stroke:#d48806,stroke-width:1px;
    classDef store fill:#f0f5ff,stroke:#2f54eb,stroke-width:1px;
    classDef ctrl fill:#f6ffed,stroke:#389e0d,stroke-width:1px;
    classDef api fill:#fff0f6,stroke:#c41d7f,stroke-width:1px;
    class LLM ext;
    class T_DOC,T_RUN,T_MEM,T_ART store;
    class C_RUN,C_CHAP,C_PKT,C_REV,C_EXP,C_INC ctrl;
    class R_DOC,R_RUN,R_ACT,R_HEALTH api;
```

### D2 (`d2 architecture.d2 architecture.svg`)

```d2
direction: down

# ----------------------------------------------------------------------
# Layer 1 — Client
# ----------------------------------------------------------------------
client: Client {
  shape: package
  ui: React + Vite Dashboard\n(polls /v1/runs/* every 2.5s)
  cli: CLI\nbook-agent …
}

# ----------------------------------------------------------------------
# Layer 2 — API / Control Plane
# ----------------------------------------------------------------------
api: API · Control Plane (FastAPI) {
  shape: rectangle
  style.fill: "#fff0f6"
  style.stroke: "#c41d7f"

  docs:     /v1/documents\nbootstrap · summary
  runs:     /v1/runs\ncreate · pause · resume · retry · cancel
  actions:  /v1/actions\n12 deterministic fix types
  health:   /v1/health
}

# ----------------------------------------------------------------------
# Layer 3 — Reconcilers (k8s-style, stateless)
# ----------------------------------------------------------------------
reconcilers: Reconcilers · stateless control loop {
  shape: rectangle
  style.fill: "#f6ffed"
  style.stroke: "#389e0d"

  run:      RunController\nlifecycle & budget
  chapter:  ChapterController\nlane isolation
  packet:   PacketController\nlease · retry · classify
  review:   ReviewController\nissue → action
  export:   ExportController
  incident: IncidentController\npatch proposals
}

# ----------------------------------------------------------------------
# Layer 4 — Domain services (stateless workers)
# ----------------------------------------------------------------------
services: Domain Services {
  shape: rectangle

  parse:   Parse / Segment\nPDF · EPUB · IR
  ctx:     Context Compiler\ntermbase · memory · bridge
  trans:   Translation Worker ×8\nparallel, per-chapter lane
  qa:      QA Review\nseverity · issues
  repair:  Repair / Rerun\nRERUN_PACKET · REPARSE_CHAPTER …
  export:  Export\nbilingual MD/HTML/EPUB/PDF
}

# ----------------------------------------------------------------------
# Layer 5 — State (sole source of truth)
# ----------------------------------------------------------------------
state: State — PostgreSQL 16 (sole store) {
  shape: cylinder
  style.fill: "#f0f5ff"
  style.stroke: "#2f54eb"

  docs:    documents · chapters\nsentences · packets
  runs:    document_runs\naudit_events · leases
  memory:  memory_snapshots\ntermbases · registries
  exports: exports · artifacts
}

# ----------------------------------------------------------------------
# External dependency
# ----------------------------------------------------------------------
llm: OpenAI-compatible LLM\nDeepSeek · OpenAI · vLLM {
  shape: cloud
  style.fill: "#fff7e6"
  style.stroke: "#d48806"
}

# ----------------------------------------------------------------------
# Edges — control flow (solid) vs observation (dashed)
# ----------------------------------------------------------------------
client.ui  -> api: "HTTPS / JSON"
client.cli -> api: "HTTPS / JSON"

api -> state: "write desired state" {style.stroke-width: 2}

reconcilers -> state: "watch · lease · advance" {
  style.stroke-dash: 3
  style.stroke-width: 2
}

reconcilers.run      -> services.parse
reconcilers.chapter  -> services.ctx
reconcilers.packet   -> services.trans
reconcilers.review   -> services.qa
reconcilers.review   -> services.repair
reconcilers.incident -> services.repair
reconcilers.export   -> services.export

services -> state: "commit result · emit audit event" {style.stroke-width: 2}

services.trans -> llm: "chat.completions\nstructured output"
services.qa    -> llm: "optional judge" {style.stroke-dash: 3}
```

> **The key insight:** nothing in the data plane holds state. Kill any worker, any controller, any backend process — the next reconcile tick reads PostgreSQL, sees what's missing, and resumes. **No orchestration bus, no task queue, no in-memory state to lose.**

---

## ⚙️ Configuration

Only `OPENAI_API_KEY` is required. Copy `.env.example` for the full list.

| Variable | Default | Note |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** Any OpenAI-compatible provider |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | Change to swap providers |
| `BOOK_AGENT_TRANSLATION_MODEL` | `deepseek-chat` | Model identifier |
| `BOOK_AGENT_TRANSLATION_BACKEND` | `openai_compatible` | Or `echo` for dry-run |
| `BOOK_AGENT_DATABASE_URL` | `postgresql+psycopg://…@localhost:55432/book_agent` | Docker and `dev.sh` both use port `55432` |
| `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS` | `120` | Per-request LLM timeout |
| `BOOK_AGENT_TRANSLATION_MAX_RETRIES` | `2` | Transient-failure retry budget |
| `BOOK_AGENT_TRANSLATION_MAX_OUTPUT_TOKENS` | `8192` | Per-call output cap |
| Per-run `max_parallel_workers` | `8` | Set via the run budget field in UI or API |

### Drop-in provider recipes

<details>
<summary><b>DeepSeek</b> (default — cheap, fast, great Chinese)</summary>

```dotenv
OPENAI_API_KEY=sk-your-deepseek-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
BOOK_AGENT_TRANSLATION_MODEL=deepseek-chat
```
</details>

<details>
<summary><b>OpenAI GPT-4o</b></summary>

```dotenv
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1
BOOK_AGENT_TRANSLATION_MODEL=gpt-4o
```
</details>

<details>
<summary><b>Local vLLM / Ollama</b></summary>

```dotenv
OPENAI_API_KEY=not-needed-but-required-by-client
OPENAI_BASE_URL=http://localhost:8000/v1
BOOK_AGENT_TRANSLATION_MODEL=qwen2.5-14b-instruct
```
</details>

---

## 🌐 API Reference

Full interactive docs at `http://localhost:58000/v1/docs` (Swagger) or `/v1/redoc`.

### Translation lifecycle

```http
POST /v1/documents/bootstrap       # upload → parse → segment
POST /v1/runs                      # create translation run (budget, scope)
GET  /v1/runs/{id}                 # status, stage progress
GET  /v1/runs/{id}/events          # paginated audit trail
POST /v1/runs/{id}/pause           # graceful pause at next safe point
POST /v1/runs/{id}/resume          # resume from checkpoint
POST /v1/runs/{id}/retry           # reset retryable_failed items
POST /v1/runs/{id}/cancel          # hard stop
POST /v1/documents/{id}/review     # run QA review
POST /v1/documents/{id}/export     # emit a deliverable
```

The frontend polls `/v1/runs/{id}` every 2.5 s — the UI is a live view of PostgreSQL, not a cached snapshot.

### <a id="cli"></a>CLI — same surface, zero HTTP

```bash
uv run book-agent bootstrap --source-path ./books/my-book.epub
uv run book-agent translate --document-id <DOCUMENT_ID>
uv run book-agent review    --document-id <DOCUMENT_ID>
uv run book-agent export    --document-id <DOCUMENT_ID> --export-type bilingual_markdown
```

---

## 📦 Export Formats

| Format | Description |
|---|---|
| `bilingual_markdown` | Side-by-side EN / ZH Markdown — the primary high-fidelity format |
| `bilingual_html` | Side-by-side EN / ZH HTML |
| `merged_markdown` | Interleaved paragraph-level bilingual Markdown |
| `merged_html` | Interleaved paragraph-level bilingual HTML |
| `zh_epub` | Chinese-only EPUB |
| `zh_pdf` | Chinese-only PDF |
| `rebuilt_epub` | Rebuilt bilingual EPUB preserving original structure |
| `review_package` | Per-chapter review JSON with quality metrics |
| `jsonl` | Sentence-aligned JSONL — great for fine-tuning datasets |

> Book Agent produces **faithful Markdown/HTML/EPUB** that mirrors the source structure (headings, code, figures, lists). It does **not** overwrite source PDFs in place — a deliberate choice to avoid CJK overflow and font-fitting artefacts.

---

## 🧰 Tech Stack

| Layer | Stack |
|---|---|
| **Backend** | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · Alembic |
| **Frontend** | React 18 · TypeScript · Vite · React Query · React Router |
| **Database** | PostgreSQL 16 (sole storage — no Redis, no MQ) |
| **PDF** | PyMuPDF |
| **LLM** | `httpx` → any OpenAI-compatible endpoint, structured output |
| **Deploy** | Docker Compose · uvicorn · [uv](https://docs.astral.sh/uv/) |

---

## 🧑‍💻 Development

```bash
# Full test suite
uv run pytest

# Lint
uv run ruff check src/ tests/

# Database migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# Frontend
cd frontend && npm install && npm run dev
```

Project layout:

```
book-agent/
├── src/book_agent/
│   ├── app/              # FastAPI app · routes · background runtime
│   ├── core/             # config · logging · IDs
│   ├── domain/           # ORM models · enums · parsers · segmentation
│   ├── infra/            # DB session · repositories
│   ├── orchestrator/     # bootstrap · state machine · rule engine
│   ├── services/         # translation · review · export · repair
│   ├── workers/          # LLM provider abstraction
│   └── cli.py
├── frontend/             # React + TS + Vite
├── alembic/              # migrations
├── tests/                # pytest
├── compose.yaml          # Postgres + app
└── dev.sh                # one-shot local launcher
```

---

## ❓ FAQ

<details>
<summary><b>What happens if I kill the process mid-translation?</b></summary>

Nothing is lost. On restart, controllers read PostgreSQL, reclaim expired leases, and resume at the exact packet where they stopped. Packets that were in flight go back to `ready`; completed packets stay completed.
</details>

<details>
<summary><b>How is this different from just chunking a book and looping over the OpenAI API?</b></summary>

Naive chunk-and-loop has no answer to: transient 429s in the middle of a 10-hour run, terminology drift across 30 chapters, LLM hallucination QA, parallelism safety, pause/resume, or cost tracking. Book Agent treats each of those as a first-class concern backed by schema.
</details>

<details>
<summary><b>Can I run it fully offline?</b></summary>

Yes. Point `OPENAI_BASE_URL` at a local [vLLM](https://github.com/vllm-project/vllm) or [Ollama](https://ollama.com/) server. PostgreSQL and the app have no external network dependencies.
</details>

<details>
<summary><b>How much does a full book cost?</b></summary>

Highly variable. A 300-page technical book on DeepSeek-chat is typically $0.30–$1.50. On GPT-4o it's 20–40× that. The run budget lets you cap spend per run.
</details>

<details>
<summary><b>Does it handle scanned PDFs?</b></summary>

Text-based PDFs are stable. Scanned PDFs go through an experimental OCR pipeline with confidence scoring — quality depends on the scan.
</details>

---

## 🗺 Roadmap

- [ ] Streaming SSE for live progress (replacing 2.5 s poll)
- [ ] Multi-target languages (ZH → JP, ZH → EN)
- [ ] RAG-assisted term resolution from a user glossary
- [ ] Cost dashboard with per-chapter attribution
- [ ] Self-hosted `PatchProposal` review workflow

---

## 🤝 Contributing

PRs welcome. Please:

1. Open an issue first for non-trivial changes.
2. Run `uv run pytest` and `uv run ruff check` before pushing.
3. Keep commits focused — one logical change per commit.

---

## 📝 License

[MIT](./LICENSE) — do whatever you want, just don't blame us.

<div align="center">

**Built for people who translate books, not pages.**

</div>
