#!/usr/bin/env bash
# ------------------------------------------------------------------
#  book-agent unified service script
#
#  Usage:
#    ./service.sh                 # Toggle all services: start if stopped, stop if running
#    ./service.sh start          # Start all services
#    ./service.sh stop           # Stop all services
#    ./service.sh restart        # Restart all services
#    ./service.sh status         # Show service status
#    ./service.sh start postgres # Start with PostgreSQL via Docker Compose
#    ./service.sh start --reload # Start backend with uvicorn auto-reload
#
#  Notes:
#    - The current Web UI is served directly by FastAPI, so there is no
#      separate frontend process by default.
#    - If a standalone frontend is added later, set BOOK_AGENT_FRONTEND_CMD
#      (and optionally BOOK_AGENT_FRONTEND_DIR) to let this script manage it.
# ------------------------------------------------------------------
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PID_FILE="$ROOT_DIR/.server.pid"
FRONTEND_PID_FILE="$ROOT_DIR/.frontend.pid"
BACKEND_LOG_FILE="${BOOK_AGENT_BACKEND_LOG:-$ROOT_DIR/artifacts/server.log}"
FRONTEND_LOG_FILE="${BOOK_AGENT_FRONTEND_LOG:-$ROOT_DIR/artifacts/frontend.log}"

HOST="${BOOK_AGENT_HOST:-127.0.0.1}"
PORT="${BOOK_AGENT_PORT:-8999}"
FRONTEND_CMD="${BOOK_AGENT_FRONTEND_CMD:-}"
FRONTEND_DIR="${BOOK_AGENT_FRONTEND_DIR:-$ROOT_DIR}"
FRONTEND_NAME="${BOOK_AGENT_FRONTEND_NAME:-frontend}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[book-agent]${NC} $*"; }
warn()  { echo -e "${YELLOW}[book-agent]${NC} $*"; }
error() { echo -e "${RED}[book-agent]${NC} $*" >&2; }

usage() {
    cat <<'EOF'
Usage: ./service.sh [start|stop|restart|status] [postgres] [--reload] [--keep-db]

Actions:
  (no action)    Toggle all services: start if stopped, stop if running
  start          Start all services
  stop           Stop all services
  restart        Restart all services
  status         Show current service status

Options:
  postgres, pg   Start PostgreSQL via Docker Compose before backend startup
  --reload, -r   Enable uvicorn auto-reload (development only)
  --keep-db      When stopping, keep PostgreSQL container running
  -h, --help     Show this help

Environment overrides:
  BOOK_AGENT_HOST          default 127.0.0.1
  BOOK_AGENT_PORT          default 8999
  BOOK_AGENT_FRONTEND_CMD  optional standalone frontend start command
  BOOK_AGENT_FRONTEND_DIR  optional working directory for frontend command
  BOOK_AGENT_FRONTEND_NAME optional label for status output

Examples:
  ./service.sh
  ./service.sh start postgres
  ./service.sh restart --reload
  ./service.sh stop --keep-db
EOF
}

ACTION="toggle"
MODE="sqlite"
RELOAD=""
KEEP_DB=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        start|stop|restart|status|toggle)
            ACTION="$1"
            ;;
        postgres|pg)
            MODE="postgres"
            ;;
        --reload|-r)
            RELOAD="--reload"
            ;;
        --keep-db)
            KEEP_DB=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

mkdir -p "$ROOT_DIR/artifacts/exports" "$ROOT_DIR/artifacts/uploads"
mkdir -p "$(dirname "$BACKEND_LOG_FILE")" "$(dirname "$FRONTEND_LOG_FILE")"

RUNTIME_READY=false
PYTHON_CMD=()
UVICORN_CMD=()
ALEMBIC_CMD=()

prepare_runtime() {
    if [[ "$RUNTIME_READY" == true ]]; then
        return
    fi

    if command -v uv >/dev/null 2>&1; then
        PYTHON_CMD=(uv run python)
        UVICORN_CMD=(uv run uvicorn)
        ALEMBIC_CMD=(uv run alembic)
        info "Syncing dependencies (uv)..."
        uv sync --quiet
    else
        if [[ ! -d "$ROOT_DIR/.venv" ]] && [[ -z "${VIRTUAL_ENV:-}" ]]; then
            warn "uv not found. Creating .venv and installing dependencies..."
            python3 -m venv "$ROOT_DIR/.venv"
            "$ROOT_DIR/.venv/bin/pip" install -e ".[dev]" -q
        fi
        PYTHON_BIN="${VIRTUAL_ENV:-$ROOT_DIR/.venv}/bin/python"
        PYTHON_CMD=("$PYTHON_BIN")
        UVICORN_CMD=("$PYTHON_BIN" -m uvicorn)
        ALEMBIC_CMD=("$PYTHON_BIN" -m alembic)
    fi

    RUNTIME_READY=true
}

read_pid() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    tr -d '[:space:]' < "$pid_file"
}

pid_alive() {
    local pid="$1"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

cleanup_stale_pid() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid="$(read_pid "$pid_file" || true)"
        if [[ -z "$pid" ]] || ! pid_alive "$pid"; then
            rm -f "$pid_file"
        fi
    fi
}

backend_running() {
    cleanup_stale_pid "$BACKEND_PID_FILE"
    local pid
    pid="$(read_pid "$BACKEND_PID_FILE" || true)"
    pid_alive "$pid"
}

frontend_configured() {
    [[ -n "$FRONTEND_CMD" ]]
}

frontend_running() {
    if ! frontend_configured; then
        return 1
    fi
    cleanup_stale_pid "$FRONTEND_PID_FILE"
    local pid
    pid="$(read_pid "$FRONTEND_PID_FILE" || true)"
    pid_alive "$pid"
}

any_service_running() {
    backend_running || frontend_running
}

stop_pid_file() {
    local pid_file="$1"
    local label="$2"
    local pid
    pid="$(read_pid "$pid_file" || true)"
    if [[ -z "$pid" ]]; then
        return 0
    fi
    if ! pid_alive "$pid"; then
        rm -f "$pid_file"
        return 0
    fi

    info "Stopping ${label} (PID: $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        pid_alive "$pid" || break
        sleep 0.5
    done
    if pid_alive "$pid"; then
        warn "${label} graceful shutdown timed out, force killing..."
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    info "${label} stopped."
}

kill_orphan_backend() {
    local orphan_pids
    orphan_pids="$(pgrep -f "book_agent.app.main:app.*--port ${PORT}" 2>/dev/null || true)"
    if [[ -n "$orphan_pids" ]]; then
        warn "Found orphan backend process(es): $orphan_pids"
        echo "$orphan_pids" | xargs kill 2>/dev/null || true
    fi
}

start_postgres_if_needed() {
    if [[ "$MODE" != "postgres" ]]; then
        info "Using SQLite database (pass ${BOLD}postgres${NC} to use PostgreSQL)."
        return
    fi

    if ! command -v docker >/dev/null 2>&1; then
        error "Docker is required for PostgreSQL mode but was not found."
        exit 1
    fi

    info "Starting PostgreSQL via Docker Compose..."
    docker compose up -d postgres

    info "Waiting for PostgreSQL to be ready..."
    local retries=0
    local max_retries=30
    until docker compose exec -T postgres pg_isready -U postgres -d book_agent >/dev/null 2>&1; do
        retries=$((retries + 1))
        if [[ $retries -ge $max_retries ]]; then
            error "PostgreSQL failed to become ready after ${max_retries}s."
            exit 1
        fi
        sleep 1
    done

    export BOOK_AGENT_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"
    info "Running Alembic migrations..."
    PYTHONPATH="$ROOT_DIR/src" "${ALEMBIC_CMD[@]}" upgrade head
    info "PostgreSQL is ready."
}

start_backend() {
    if backend_running; then
        local pid
        pid="$(read_pid "$BACKEND_PID_FILE")"
        error "Backend already running (PID: $pid)."
        exit 1
    fi

    info "Starting backend on ${CYAN}http://${HOST}:${PORT}${NC} ..."
    PYTHONPATH="$ROOT_DIR/src" \
        nohup "${UVICORN_CMD[@]}" book_agent.app.main:app \
            --host "$HOST" \
            --port "$PORT" \
            $RELOAD \
            >> "$BACKEND_LOG_FILE" 2>&1 &

    local pid=$!
    echo "$pid" > "$BACKEND_PID_FILE"
    sleep 1
    if ! pid_alive "$pid"; then
        rm -f "$BACKEND_PID_FILE"
        error "Backend failed to start. Check logs: $BACKEND_LOG_FILE"
        tail -20 "$BACKEND_LOG_FILE" 2>/dev/null || true
        exit 1
    fi

    info "Backend is running (PID: $pid)."
}

start_frontend() {
    if ! frontend_configured; then
        info "Frontend UI is currently served by FastAPI; no separate frontend process to start."
        return
    fi

    if frontend_running; then
        local pid
        pid="$(read_pid "$FRONTEND_PID_FILE")"
        error "${FRONTEND_NAME} already running (PID: $pid)."
        exit 1
    fi

    info "Starting ${FRONTEND_NAME} using: ${FRONTEND_CMD}"
    (
        cd "$FRONTEND_DIR"
        nohup /bin/bash -lc "$FRONTEND_CMD" >> "$FRONTEND_LOG_FILE" 2>&1 &
        echo "$!" > "$FRONTEND_PID_FILE"
    )

    local pid
    pid="$(read_pid "$FRONTEND_PID_FILE" || true)"
    sleep 1
    if [[ -z "$pid" ]] || ! pid_alive "$pid"; then
        rm -f "$FRONTEND_PID_FILE"
        error "${FRONTEND_NAME} failed to start. Check logs: $FRONTEND_LOG_FILE"
        tail -20 "$FRONTEND_LOG_FILE" 2>/dev/null || true
        exit 1
    fi

    info "${FRONTEND_NAME} is running (PID: $pid)."
}

stop_frontend() {
    if ! frontend_configured; then
        info "Frontend UI is embedded in backend; no separate frontend process to stop."
        return
    fi
    stop_pid_file "$FRONTEND_PID_FILE" "$FRONTEND_NAME"
}

stop_backend() {
    if [[ -f "$BACKEND_PID_FILE" ]]; then
        stop_pid_file "$BACKEND_PID_FILE" "backend"
    else
        warn "No backend PID file found."
        kill_orphan_backend
    fi
}

stop_postgres_if_needed() {
    if [[ "$KEEP_DB" == true ]] || ! command -v docker >/dev/null 2>&1; then
        return
    fi
    if docker compose ps --status running 2>/dev/null | grep -q postgres; then
        info "Stopping PostgreSQL container..."
        docker compose stop postgres >/dev/null
        info "PostgreSQL stopped. Data remains in the Docker volume."
    fi
}

print_status() {
    cleanup_stale_pid "$BACKEND_PID_FILE"
    cleanup_stale_pid "$FRONTEND_PID_FILE"

    if backend_running; then
        info "Backend:  running (PID: $(read_pid "$BACKEND_PID_FILE"))"
        info "          ${CYAN}http://${HOST}:${PORT}${NC}"
        info "          docs -> ${CYAN}http://${HOST}:${PORT}/v1/docs${NC}"
        info "          logs -> $BACKEND_LOG_FILE"
    else
        warn "Backend:  stopped"
    fi

    if frontend_configured; then
        if frontend_running; then
            info "Frontend: running (PID: $(read_pid "$FRONTEND_PID_FILE"))"
            info "          logs -> $FRONTEND_LOG_FILE"
        else
            warn "Frontend: stopped"
        fi
    else
        info "Frontend: embedded in backend FastAPI UI (no standalone process configured)"
    fi

    if command -v docker >/dev/null 2>&1 && docker compose ps --status running 2>/dev/null | grep -q postgres; then
        info "Database: PostgreSQL container is running"
    else
        info "Database: SQLite mode or PostgreSQL container stopped"
    fi
}

start_all() {
    prepare_runtime
    start_postgres_if_needed
    start_backend
    start_frontend

    echo ""
    info "${BOLD}book-agent services are running${NC}"
    info "  Homepage: ${CYAN}http://${HOST}:${PORT}${NC}"
    info "  API Docs: ${CYAN}http://${HOST}:${PORT}/v1/docs${NC}"
    info "  Logs:     $BACKEND_LOG_FILE"
    if frontend_configured; then
        info "  Frontend: $FRONTEND_LOG_FILE"
    fi
    info "  Status:   ${BOLD}./service.sh status${NC}"
}

stop_all() {
    stop_frontend
    stop_backend
    stop_postgres_if_needed
    info "${BOLD}Done.${NC}"
}

case "$ACTION" in
    toggle)
        if any_service_running; then
            stop_all
        else
            start_all
        fi
        ;;
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        start_all
        ;;
    status)
        print_status
        ;;
esac
