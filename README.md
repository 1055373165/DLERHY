<div align="center">

# 📚 Book Agent

### Whole-book English → Chinese translation agent
**EPUB / PDF in, HTML · Markdown · EPUB · PDF out — with packet-level retries, resumable runs, and deterministic review-driven repair.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 19](https://img.shields.io/badge/frontend-React%20%2B%20TS-61DAFB?logo=react&logoColor=white)](https://react.dev/)
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

https://github.com/user-attachments/assets/5d9a9d35-07d6-4543-95b3-59040fb04d43

## 🚀 Quick Start

### Option 1 · Docker (recommended, zero setup)

```bash
git clone https://github.com/1055373165/DLERHY.git
cd DLERHY
cp .env.example .env              # edit: OPENAI_API_KEY=sk-...
docker compose up -d              # → http://localhost:58000
```

Docker Compose brings up PostgreSQL 16, runs the Alembic migrations in a one-shot `migrate` container, then starts the app.
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
| In-memory state lost on crash / deploy | **Database-backed runs** — work items and leases live in PostgreSQL. After a crash, expired leases are reclaimed and the run continues from the packets that remain. |
| "LLM fixes itself" ends in loops | **Deterministic fix routing** — review issues map to 12 named actions (`RERUN_PACKET`, `UPDATE_TERMBASE_THEN_RERUN_TARGETED`, `REPARSE_CHAPTER`, …). No black-box self-correction. |
| Terminology drifts between chapters | **Translation memory per chapter** — termbases, entity registries, style deltas, and discourse bridges are compiled into every packet's context. |
| Runs block on one slow chapter | **8-way parallelism with lane isolation** — PostgreSQL guarantees concurrency safety; per-chapter lanes keep context consistent. |
| Vendor lock-in to one LLM | **Any OpenAI-compatible backend** — DeepSeek, OpenAI, Moonshot, vLLM, Ollama. Swap `OPENAI_BASE_URL` + `BOOK_AGENT_TRANSLATION_MODEL`. |
| Silent failures you only notice at export | **Failure classification and budgets** — errors are classified into retry / pause / fail, stalled leases are reclaimed, and run budgets (cost, tokens, wall clock, no progress) pause or fail a run. |
| Opaque pipelines you can't inspect | **Full audit trail** — every state transition, every LLM call, every fix action is a row in `audit_events`. The UI is a live view of Postgres, not a cached snapshot. |

### At a glance

- ✅ **One required env var** — `OPENAI_API_KEY`.
- ✅ **Sole data store is PostgreSQL** — no Redis, no Kafka, no task queue.
- ✅ **Pause · Resume · Retry · Cancel** are first-class REST verbs.
- ✅ **Seven export formats** — bilingual chapter HTML, Chinese reading edition HTML/Markdown, rebuilt and Chinese EPUB, rebuilt PDF, review package JSON.
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

A background run executor ticks over each active run: it derives stage status from rows in PostgreSQL, seeds and leases work items for the next stage, and settles the run once every stage has evidence. Because the ledger lives in the database, a restarted process picks up where the last one stopped.

---

## 🏛 Architecture

The system is organised in four horizontal tiers — **Client**, **API / Run control**, **Run executor & Services**, and **State** — plus one external dependency (**LLM provider**). The only source of truth is PostgreSQL.

![d2 (2)](https://github.com/user-attachments/assets/ea145a8b-7e3b-45bf-b505-e3434b32d11f)

> **The key insight:** run progress is a ledger in PostgreSQL, not in memory. Kill the backend process and the next executor tick reads what is missing and resumes. **No orchestration bus, no external task queue.**

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
POST /v1/documents/{id}/translate  # enqueue a translate_targeted run (202)
POST /v1/documents/{id}/review     # enqueue a review_full run (202)
POST /v1/documents/{id}/export     # enqueue an export_full run (202)
GET  /v1/documents/{id}/exports/download?export_type=…  # serve an existing export
```

The document actions return the created run immediately; poll `/v1/runs/{id}` for the result.
Downloads never generate exports — enqueue an export run first.

The frontend polls `/v1/runs/{id}` every 2.5 s — the UI is a live view of PostgreSQL, not a cached snapshot.

### <a id="cli"></a>CLI — same surface, zero HTTP

```bash
uv run book-agent bootstrap --source-path ./books/my-book.epub
uv run book-agent translate --document-id <DOCUMENT_ID>
uv run book-agent review    --document-id <DOCUMENT_ID>
uv run book-agent export    --document-id <DOCUMENT_ID> --export-type merged_markdown
```

---

## 📦 Export Formats

| Format | Description |
|---|---|
| `bilingual_html` | Per-chapter side-by-side EN / ZH HTML |
| `merged_html` | Whole-book Chinese reading edition (HTML) |
| `merged_markdown` | Whole-book Chinese reading edition (Markdown) |
| `rebuilt_epub` | EPUB rebuilt from the translated document (EPUB sources) |
| `zh_epub` | Source EPUB with text replaced by the translation (EPUB sources) |
| `rebuilt_pdf` | PDF printed from the merged HTML (requires Playwright) |
| `review_package` | Per-chapter review JSON with quality metrics |

`bilingual_markdown`, `zh_pdf` and `jsonl` exist in the enum but are not implemented yet.

> Book Agent produces **faithful Markdown/HTML/EPUB** that mirrors the source structure (headings, code, figures, lists). It does **not** overwrite source PDFs in place — a deliberate choice to avoid CJK overflow and font-fitting artefacts.

---

## 🧰 Tech Stack

| Layer | Stack |
|---|---|
| **Backend** | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · Alembic |
| **Frontend** | React 19 · TypeScript · Vite · React Query · React Router |
| **Database** | PostgreSQL 16 (sole storage — no Redis, no MQ) |
| **PDF** | PyMuPDF |
| **LLM** | `httpx` → any OpenAI-compatible endpoint, structured output |
| **Deploy** | Docker Compose · uvicorn · [uv](https://docs.astral.sh/uv/) |

---

## 🧑‍💻 Development

```bash
# Full test suite: one interpreter per test file (the supported way; also what CI runs)
scripts/run_tests_per_file.sh

# A few files while iterating
uv run pytest tests/test_export_golden.py tests/test_cli.py

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
│   ├── orchestrator/     # stage status · stage gates · rule engine
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

Nothing is lost. On restart, the run executor reads PostgreSQL, reclaims expired leases, and resumes with the packets that are not translated yet. Packets that were in flight are retried; completed packets stay completed.
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

- [x] Streaming SSE for live progress (replacing 2.5 s poll) — `GET /v1/runs/{id}/stream` over Postgres `LISTEN`
- [ ] RAG-assisted term resolution from a user glossary
- [x] Cost dashboard with per-chapter attribution — `GET /v1/runs/{id}/cost` aggregated from `llm.call.completed` events

---

## 🤝 Contributing

PRs welcome. Please:

1. Open an issue first for non-trivial changes.
2. Run `uv run pytest` and `uv run ruff check` before pushing.
3. Keep commits focused — one logical change per commit.

---

## 📝 License

MIT — do whatever you want, just don't blame us.

<div align="center">

**Built for people who translate books, not pages.**

</div>
