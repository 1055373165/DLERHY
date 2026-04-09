# Book Agent

An LLM-powered translation agent that translates English books (PDF / EPUB) into high-quality Chinese, with sentence-level traceability, context-aware packet management, automated QA review, and multi-format export.

## Features

- **PDF & EPUB Support** — Parses text-based PDFs and EPUB files into a structured intermediate representation (chapters → blocks → sentences)
- **Context-Aware Translation** — Builds translation packets with surrounding context, term glossaries, entity registries, chapter briefs, and discourse bridges for coherent, consistent output
- **OpenAI-Compatible LLM Backend** — Works with any OpenAI-compatible API (DeepSeek, OpenAI, local models, etc.) — just set an API key and base URL
- **Automated QA & Review** — Post-translation quality checks with issue detection, severity scoring, and automated repair actions
- **Run Control Plane** — Budget-aware execution with pause / resume / cancel / retry, parallel workers, and audit trails
- **Translation Memory** — Per-chapter memory snapshots (termbases, entity registries, style deltas) that evolve as translation progresses
- **Multi-Format Export** — Bilingual HTML/Markdown, merged documents, rebuilt EPUB/PDF, Chinese-only outputs, review packages, and JSONL
- **Web Dashboard** — React frontend for document management, run monitoring, and deliverable downloads
- **CLI & REST API** — Full workflow accessible via CLI commands or a documented FastAPI REST API

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      Web Dashboard                      │
│               (React + TypeScript + Vite)                │
└──────────────────────────┬──────────────────────────────┘
                           │ /v1/*
┌──────────────────────────▼──────────────────────────────┐
│                    FastAPI Backend                       │
│  ┌─────────┐  ┌──────────┐  ┌───────┐  ┌────────────┐  │
│  │Documents│  │   Runs   │  │Actions│  │   Health   │  │
│  └────┬────┘  └────┬─────┘  └───┬───┘  └────────────┘  │
│       │            │            │                        │
│  ┌────▼────────────▼────────────▼───────────────────┐   │
│  │            Workflow Service Layer                 │   │
│  │  Bootstrap → Translate → Review → Export         │   │
│  └────┬─────────────────────────────────────────────┘   │
│       │                                                 │
│  ┌────▼─────────────────────────────────────────────┐   │
│  │              Domain & Orchestration               │   │
│  │  State Machine · Rule Engine · Context Compiler   │   │
│  │  Memory Service · Incident Triage · Repair Agent  │   │
│  └────┬─────────────────────────────────────────────┘   │
│       │                                                 │
│  ┌────▼──────────┐  ┌───────────────────────────────┐   │
│  │  Infra / DB   │  │   Translation Worker          │   │
│  │ SQLAlchemy +  │  │  OpenAI-Compatible Provider   │   │
│  │ Alembic       │  │  (DeepSeek, GPT, local, etc.) │   │
│  └───────────────┘  └───────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Translation Pipeline:**

```
Ingest → Parse → Segment → Packetize → Translate → QA Review → Export
                                ↑                       │
                                └── Repair / Rerun ─────┘
```

## Quick Start

### Prerequisites

- **Python 3.12+**
- **Node.js 18+** (for the frontend, optional)
- **An OpenAI-compatible API key** (e.g. [DeepSeek](https://platform.deepseek.com/), OpenAI, etc.)

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/book-agent.git
cd book-agent
```

### 2. Configure the LLM Provider

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Then edit `.env` — **only `OPENAI_API_KEY` is required**:

```dotenv
# === Required: Your LLM API Key ===
OPENAI_API_KEY=sk-your-api-key-here

# === Optional: LLM Provider Settings ===
# Default: DeepSeek. Change to any OpenAI-compatible endpoint.
OPENAI_BASE_URL=https://api.deepseek.com/v1
BOOK_AGENT_TRANSLATION_MODEL=deepseek-chat
BOOK_AGENT_TRANSLATION_BACKEND=openai_compatible

# === Optional: Timeout & Retry ===
BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS=120
BOOK_AGENT_TRANSLATION_MAX_RETRIES=2
```

> **Tip:** To use OpenAI GPT models instead, set:
> ```dotenv
> OPENAI_BASE_URL=https://api.openai.com/v1
> BOOK_AGENT_TRANSLATION_MODEL=gpt-4o
> ```

### 3. Start the Development Server

```bash
./dev.sh
```

This single command will:
1. Install Python dependencies (via [uv](https://docs.astral.sh/uv/) if available, or pip)
2. Start the backend with hot-reload on `http://127.0.0.1:8999`
3. Start the frontend with HMR on `http://127.0.0.1:4173`
4. Use a zero-config SQLite database (no setup needed)

Open your browser at **http://127.0.0.1:4173** to access the web dashboard, or visit **http://127.0.0.1:8999/v1/docs** for the interactive API documentation.

### 4. Translate Your First Book

**Option A — Via the Web Dashboard:**

1. Open http://127.0.0.1:4173
2. Upload a PDF or EPUB file from the Library page
3. The system will automatically bootstrap (parse + segment) the document
4. Create a translation run from the Workspace page
5. Monitor progress and download exports from the Deliverables page

**Option B — Via the CLI:**

```bash
# Bootstrap (ingest + parse + segment) a book
uv run book-agent bootstrap --source-path ./books/my-book.epub

# Translate all packets
uv run book-agent translate --document-id <DOCUMENT_ID>

# Run QA review
uv run book-agent review --document-id <DOCUMENT_ID>

# Export to bilingual Markdown
uv run book-agent export --document-id <DOCUMENT_ID> --export-type bilingual_markdown
```

**Option C — Via the REST API:**

```bash
# Upload and bootstrap
curl -X POST http://127.0.0.1:8999/v1/documents/bootstrap \
  -F "file=@./books/my-book.epub"

# Create a translation run
curl -X POST http://127.0.0.1:8999/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"document_id": "<DOCUMENT_ID>", "run_type": "translate_full"}'

# Check run status
curl http://127.0.0.1:8999/v1/runs/<RUN_ID>

# Export
curl -X POST http://127.0.0.1:8999/v1/documents/<DOCUMENT_ID>/export \
  -H "Content-Type: application/json" \
  -d '{"export_type": "bilingual_markdown"}'
```

## Installation

### Local Development (Recommended)

```bash
# Using uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# Or using pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Using PostgreSQL (Optional)

By default the app uses SQLite. For concurrent workloads or production use, switch to PostgreSQL:

```bash
# Start with PostgreSQL via Docker
./dev.sh pg
```

This starts a PostgreSQL container and runs Alembic migrations automatically. Or configure manually:

```dotenv
BOOK_AGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5433/book_agent
```

### Docker Compose (Production)

```bash
# Build and start all services
docker compose up -d

# The app is available at http://localhost:58000
# API docs at http://localhost:58000/v1/docs
```

## Configuration Reference

All settings use the `BOOK_AGENT_` prefix and can be set via environment variables or the `.env` file.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | API key for the LLM provider |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | LLM provider base URL |
| `BOOK_AGENT_TRANSLATION_BACKEND` | `echo` | Translation backend: `openai_compatible` or `echo` (dry-run) |
| `BOOK_AGENT_TRANSLATION_MODEL` | `echo-worker` | Model name to use for translation |
| `BOOK_AGENT_TRANSLATION_TIMEOUT_SECONDS` | `60` | Request timeout per LLM call |
| `BOOK_AGENT_TRANSLATION_MAX_RETRIES` | `1` | Max retry attempts on transient failures |
| `BOOK_AGENT_TRANSLATION_MAX_OUTPUT_TOKENS` | `8192` | Max output tokens per LLM call |
| `BOOK_AGENT_DATABASE_URL` | `sqlite:///./artifacts/book-agent.db` | Database connection string |
| `BOOK_AGENT_LOG_LEVEL` | `INFO` | Logging level |
| `BOOK_AGENT_CORS_ALLOW_ORIGINS` | `[]` | Allowed CORS origins (comma-separated or JSON array) |

### Minimal `.env` for DeepSeek

```dotenv
OPENAI_API_KEY=sk-your-deepseek-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
BOOK_AGENT_TRANSLATION_BACKEND=openai_compatible
BOOK_AGENT_TRANSLATION_MODEL=deepseek-chat
```

### Minimal `.env` for OpenAI

```dotenv
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1
BOOK_AGENT_TRANSLATION_BACKEND=openai_compatible
BOOK_AGENT_TRANSLATION_MODEL=gpt-4o
```

## Supported Formats

### Input

| Format | Status | Notes |
|---|---|---|
| EPUB | Stable | Full chapter/section detection, heading hierarchy |
| PDF (text-based) | Stable | Structural analysis, code/table/figure detection |
| PDF (scanned/OCR) | Experimental | OCR pipeline with confidence scoring |

### Export

| Format | Description |
|---|---|
| `bilingual_markdown` | Side-by-side English/Chinese Markdown |
| `bilingual_html` | Side-by-side English/Chinese HTML |
| `merged_markdown` | Interleaved bilingual Markdown |
| `merged_html` | Interleaved bilingual HTML |
| `zh_epub` | Chinese-only EPUB |
| `zh_pdf` | Chinese-only PDF |
| `rebuilt_epub` | Rebuilt bilingual EPUB |
| `review_package` | Per-chapter review JSON with quality metrics |
| `jsonl` | Machine-readable sentence-aligned JSONL |

## Project Structure

```
book-agent/
├── src/book_agent/          # Core Python package
│   ├── app/                 # FastAPI application
│   │   ├── api/routes/      #   REST API endpoints
│   │   ├── runtime/         #   Background run executor & controllers
│   │   └── ui/              #   Frontend serving
│   ├── core/                # Config, logging, ID generation
│   ├── domain/              # Domain models & enums
│   │   ├── models/          #   SQLAlchemy ORM models
│   │   ├── structure/       #   PDF & EPUB parsers
│   │   └── segmentation/    #   Sentence segmentation
│   ├── infra/               # Database session & repositories
│   ├── orchestrator/        # Bootstrap, state machine, rule engine
│   ├── schemas/             # Pydantic API schemas
│   ├── services/            # Business logic layer
│   │   ├── translation.py   #   Translation orchestration
│   │   ├── review.py        #   QA review pipeline
│   │   ├── export.py        #   Multi-format export
│   │   └── recovery_matrix.py # Automated error recovery
│   ├── workers/             # LLM translation worker abstraction
│   │   └── providers/       #   OpenAI-compatible provider
│   └── cli.py               # CLI entry point
├── frontend/                # React + TypeScript + Vite
│   └── src/
│       ├── features/        #   Library, Workspace, Runs, Deliverables
│       └── lib/             #   API client & workflow helpers
├── alembic/                 # Database migrations
├── scripts/                 # Utility & batch scripts
├── tests/                   # Pytest test suite
├── compose.yaml             # Docker Compose (PostgreSQL + app)
├── Dockerfile               # Production container image
├── dev.sh                   # Local development launcher
├── pyproject.toml           # Python project metadata
└── .env                     # Environment configuration (git-ignored)
```

## API Documentation

When the server is running, interactive API docs are available at:

- **Swagger UI:** http://127.0.0.1:8999/v1/docs
- **ReDoc:** http://127.0.0.1:8999/v1/redoc

### Key Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/documents/bootstrap` | Upload and bootstrap a document |
| `GET` | `/v1/documents/{id}/summary` | Get document translation summary |
| `POST` | `/v1/documents/{id}/translate` | Translate document packets |
| `POST` | `/v1/documents/{id}/review` | Run QA review |
| `POST` | `/v1/documents/{id}/export` | Export translated document |
| `POST` | `/v1/runs` | Create a new translation run |
| `GET` | `/v1/runs/{id}` | Get run status and progress |
| `POST` | `/v1/runs/{id}/pause` | Pause a running translation |
| `POST` | `/v1/runs/{id}/resume` | Resume a paused run |
| `POST` | `/v1/runs/{id}/cancel` | Cancel a run |
| `GET` | `/v1/health` | Health check |

## Development

### Running Tests

```bash
# Run the full test suite
uv run pytest

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_bootstrap_pipeline.py
```

### Linting

```bash
uv run ruff check src/ tests/
```

### Database Migrations

```bash
# Run pending migrations (PostgreSQL only)
uv run alembic upgrade head

# Create a new migration
uv run alembic revision --autogenerate -m "description"
```

### Frontend Development

```bash
cd frontend
npm install
npm run dev      # Start dev server with HMR
npm run build    # Production build
npm run test     # Run tests
```

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic
- **Frontend:** React 19, TypeScript, Vite 7, React Query, React Router
- **Database:** PostgreSQL 16 (production) / SQLite (development)
- **LLM Integration:** OpenAI-compatible API (DeepSeek, OpenAI, etc.)
- **PDF Parsing:** PyMuPDF
- **Containerization:** Docker, Docker Compose

## License

This project is provided as-is for research and personal use.
