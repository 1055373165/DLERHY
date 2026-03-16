#!/usr/bin/env bash
# ------------------------------------------------------------------
#  book-agent  one-click start script
#
#  Usage:
#    ./start.sh              # SQLite mode (default, zero setup)
#    ./start.sh postgres     # PostgreSQL mode (requires Docker)
#    ./start.sh --reload     # SQLite + auto-reload for development
#
#  Environment overrides:
#    BOOK_AGENT_HOST   default 127.0.0.1
#    BOOK_AGENT_PORT   default 8000
# ------------------------------------------------------------------
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_FILE="$ROOT_DIR/.server.pid"
LOG_FILE="$ROOT_DIR/artifacts/server.log"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[book-agent]${NC} $*"; }
warn()  { echo -e "${YELLOW}[book-agent]${NC} $*"; }
error() { echo -e "${RED}[book-agent]${NC} $*" >&2; }

# ---- Parse arguments ------------------------------------------------
MODE="sqlite"
RELOAD=""
for arg in "$@"; do
    case "$arg" in
        postgres|pg)  MODE="postgres" ;;
        --reload|-r)  RELOAD="--reload" ;;
        -h|--help)
            echo "Usage: ./start.sh [postgres] [--reload]"
            echo ""
            echo "  (no args)    Start with SQLite (zero-config default)"
            echo "  postgres     Start PostgreSQL via Docker Compose first"
            echo "  --reload     Enable uvicorn auto-reload (development)"
            exit 0
            ;;
        *) error "Unknown argument: $arg"; exit 1 ;;
    esac
done

HOST="${BOOK_AGENT_HOST:-127.0.0.1}"
PORT="${BOOK_AGENT_PORT:-8000}"

# ---- Guard: already running? ----------------------------------------
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        error "Server already running (PID: $OLD_PID). Run ${BOLD}./stop.sh${NC} first."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# ---- Ensure directories --------------------------------------------
mkdir -p "$ROOT_DIR/artifacts/exports" "$ROOT_DIR/artifacts/uploads"

# ---- Detect package manager -----------------------------------------
if command -v uv &>/dev/null; then
    RUN="uv run"
    info "Syncing dependencies (uv)..."
    uv sync --quiet
else
    RUN="python"
    if [[ ! -d "$ROOT_DIR/.venv" ]] && [[ -z "${VIRTUAL_ENV:-}" ]]; then
        warn "uv not found. Creating venv and installing with pip..."
        python3 -m venv "$ROOT_DIR/.venv"
        source "$ROOT_DIR/.venv/bin/activate"
        pip install -e ".[dev]" -q
    fi
fi

# ---- Database setup -------------------------------------------------
if [[ "$MODE" == "postgres" ]]; then
    if ! command -v docker &>/dev/null; then
        error "Docker is required for PostgreSQL mode but not found."
        exit 1
    fi

    info "Starting PostgreSQL via Docker Compose..."
    docker compose up -d postgres

    info "Waiting for PostgreSQL to be ready..."
    RETRIES=0
    MAX_RETRIES=30
    until docker compose exec -T postgres pg_isready -U postgres -d book_agent &>/dev/null; do
        RETRIES=$((RETRIES + 1))
        if [[ $RETRIES -ge $MAX_RETRIES ]]; then
            error "PostgreSQL failed to become ready after ${MAX_RETRIES}s."
            exit 1
        fi
        sleep 1
    done
    info "PostgreSQL is ready."

    export BOOK_AGENT_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"

    info "Running Alembic migrations..."
    PYTHONPATH="$ROOT_DIR/src" $RUN alembic upgrade head
    info "Migrations complete."
else
    info "Using SQLite database (pass ${BOLD}postgres${NC} to use PostgreSQL)."
fi

# ---- Start uvicorn --------------------------------------------------
info "Starting server on ${CYAN}http://${HOST}:${PORT}${NC} ..."

PYTHONPATH="$ROOT_DIR/src" \
    nohup $RUN uvicorn book_agent.app.main:app \
        --host "$HOST" \
        --port "$PORT" \
        $RELOAD \
        >> "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# ---- Wait briefly and verify the process is alive -------------------
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    error "Server failed to start. Check logs: $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
fi

echo ""
info "${BOLD}book-agent is running${NC}"
info "  PID:      $SERVER_PID"
info "  Homepage: ${CYAN}http://${HOST}:${PORT}${NC}"
info "  API Docs: ${CYAN}http://${HOST}:${PORT}/v1/docs${NC}"
info "  Logs:     $LOG_FILE"
info "  Stop:     ${BOLD}./stop.sh${NC}"
