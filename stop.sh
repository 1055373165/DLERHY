#!/usr/bin/env bash
# ------------------------------------------------------------------
#  book-agent  one-click stop script
#
#  Usage:
#    ./stop.sh           # Stop server (and PostgreSQL if running)
#    ./stop.sh --keep-db # Stop server but keep PostgreSQL running
# ------------------------------------------------------------------
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/.server.pid"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[book-agent]${NC} $*"; }
warn()  { echo -e "${YELLOW}[book-agent]${NC} $*"; }

KEEP_DB=false
for arg in "$@"; do
    case "$arg" in
        --keep-db) KEEP_DB=true ;;
        -h|--help)
            echo "Usage: ./stop.sh [--keep-db]"
            echo ""
            echo "  (no args)   Stop server and PostgreSQL container"
            echo "  --keep-db   Stop server but keep PostgreSQL running"
            exit 0
            ;;
    esac
done

# ---- Stop uvicorn ---------------------------------------------------
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        info "Stopping server (PID: $PID)..."
        kill "$PID"

        # Wait for graceful shutdown (up to 5s)
        for _ in $(seq 1 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.5
        done

        # Force kill if still alive
        if kill -0 "$PID" 2>/dev/null; then
            warn "Graceful shutdown timed out, force killing..."
            kill -9 "$PID" 2>/dev/null || true
        fi

        info "Server stopped."
    else
        warn "Process $PID not found (already stopped)."
    fi
    rm -f "$PID_FILE"
else
    warn "No PID file found. Server may not be running."
    # Try to find and kill orphan uvicorn processes for this project
    ORPHAN_PIDS=$(pgrep -f "uvicorn book_agent.app.main:app" 2>/dev/null || true)
    if [[ -n "$ORPHAN_PIDS" ]]; then
        warn "Found orphan uvicorn process(es): $ORPHAN_PIDS"
        echo "$ORPHAN_PIDS" | xargs kill 2>/dev/null || true
        info "Orphan process(es) killed."
    fi
fi

# ---- Stop PostgreSQL container (if running) -------------------------
if [[ "$KEEP_DB" == false ]] && command -v docker &>/dev/null; then
    if docker compose ps --status running 2>/dev/null | grep -q postgres; then
        info "Stopping PostgreSQL container..."
        docker compose stop postgres
        info "PostgreSQL stopped. Data is preserved in the Docker volume."
    fi
fi

info "${BOLD}Done.${NC}"
