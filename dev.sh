#!/usr/bin/env bash
# ==============================================================
#  book-agent 本地开发启动脚本
#
#  与线上容器化部署 (docker compose up) 的区别:
#    - 后端使用 uvicorn --reload 热重载，修改代码即时生效
#    - 前端使用 Vite HMR，修改组件即时刷新
#    - 所有日志直接输出到终端，Ctrl+C 一键停止
#    - 本地开发统一使用 Docker PostgreSQL，避免本机环境漂移
#
#  Usage:
#    ./dev.sh              # Docker PostgreSQL + 后端热重载 + 前端 HMR
#    ./dev.sh --no-fe      # Docker PostgreSQL + 仅启动后端
# ==============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# ---- 配置 ----
HOST="${BOOK_AGENT_HOST:-127.0.0.1}"
BACKEND_PORT="${BOOK_AGENT_PORT:-8999}"
FRONTEND_PORT="${BOOK_AGENT_FRONTEND_PORT:-4173}"
COMPOSE_PG_PORT="${BOOK_AGENT_COMPOSE_POSTGRES_PORT:-55432}"
COMPOSE_PG_URL="postgresql+psycopg://postgres:postgres@localhost:${COMPOSE_PG_PORT}/book_agent"

# ---- 颜色 ----
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${GREEN}[dev]${NC} $*"; }
warn()  { echo -e "${YELLOW}[dev]${NC} $*"; }
error() { echo -e "${RED}[dev]${NC} $*" >&2; }

is_project_frontend_command() {
    local cmd="${1:-}"
    [[ -n "$cmd" ]] || return 1
    [[ "$cmd" == *"$ROOT_DIR/frontend/node_modules/.bin/vite"* ]]
}

is_project_backend_command() {
    local cmd="${1:-}"
    [[ -n "$cmd" ]] || return 1
    [[ "$cmd" == *"book_agent.app.main:app"* ]]
}

stop_existing_pid() {
    local pid="$1"
    local label="$2"
    if [[ -z "$pid" ]]; then
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    info "停止已存在的${label} (PID: $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    warn "${label} 未在预期时间内退出，强制结束..."
    kill -9 "$pid" 2>/dev/null || true
}

cleanup_existing_dev_instances() {
    local line pid cmd
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        pid="${line%% *}"
        cmd="${line#* }"
        [[ "$pid" == "$$" ]] && continue

        if is_project_backend_command "$cmd" && [[ "$cmd" == *"--port ${BACKEND_PORT}"* ]]; then
            stop_existing_pid "$pid" "book-agent 后端"
            continue
        fi

        if [[ "$NO_FRONTEND" != true ]] \
            && is_project_frontend_command "$cmd"; then
            stop_existing_pid "$pid" "book-agent 前端"
            continue
        fi
    done < <(ps -ax -o pid=,command= 2>/dev/null || true)
}

FRONTEND_ALREADY_RUNNING=false

ensure_port_available() {
    local port="$1"
    local label="$2"
    local listener_pids
    local pid cmd
    listener_pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN -n -P 2>/dev/null || true)"
    if [[ -z "$listener_pids" ]]; then
        return 0
    fi
    if [[ "$label" == "前端" ]]; then
        while IFS= read -r pid; do
            [[ -n "$pid" ]] || continue
            cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
            if is_project_frontend_command "$cmd"; then
                FRONTEND_ALREADY_RUNNING=true
                warn "检测到本项目的前端开发服务器已在端口 $port 运行，复用现有进程 (PID: $pid)。"
                return 0
            fi
        done <<< "$listener_pids"
    fi
    error "${label} 端口 $port 仍被占用。为避免误杀非本项目进程，dev.sh 已停止启动。"
    ps -fp $(echo "$listener_pids" | tr '\n' ',' | sed 's/,$//') 2>/dev/null || true
    exit 1
}

# ---- 参数解析 ----
NO_FRONTEND=false

for arg in "$@"; do
    case "$arg" in
        --no-fe|--no-frontend)  NO_FRONTEND=true ;;
        -h|--help)
            cat <<EOF
book-agent 本地开发启动脚本

Usage: ./dev.sh [--no-fe]

  --no-fe          不启动前端开发服务器
  -h, --help       显示帮助

启动后:
  后端 API:   http://$HOST:$BACKEND_PORT      (uvicorn --reload)
  API 文档:   http://$HOST:$BACKEND_PORT/v1/docs
  前端页面:   http://$HOST:$FRONTEND_PORT     (Vite HMR)

线上部署请使用: docker compose up -d
EOF
            exit 0
            ;;
        *) error "未知参数: $arg"; exit 1 ;;
    esac
done

# ---- 启动前清理本项目遗留实例 ----
cleanup_existing_dev_instances
ensure_port_available "$BACKEND_PORT" "后端"
if [[ "$NO_FRONTEND" != true ]]; then
    ensure_port_available "$FRONTEND_PORT" "前端"
fi

# ---- 退出时清理所有子进程 ----
PIDS=()
cleanup() {
    echo ""
    info "正在停止所有服务..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    if command -v docker >/dev/null 2>&1; then
        info "PostgreSQL 容器保持运行 (手动停止: docker compose stop postgres)"
    fi
    info "已停止。"
}
trap cleanup EXIT INT TERM

# ---- 准备运行环境 ----
mkdir -p "$ROOT_DIR/artifacts/exports" "$ROOT_DIR/artifacts/uploads"

# ---- 加载 .env（数据库连接会由 dev.sh 统一覆盖为 Docker PostgreSQL）----
if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$ROOT_DIR/.env"
    set +a
    info "已加载 .env 环境变量"
fi

UVICORN_CMD=()
ALEMBIC_CMD=()

start_compose_postgres() {
    if ! command -v docker >/dev/null 2>&1; then
        error "本地开发依赖 Docker PostgreSQL，但未找到 docker 命令。"
        exit 1
    fi
    info "启动 PostgreSQL 容器..."
    docker compose up -d postgres
    info "等待 PostgreSQL 就绪..."
    retries=0
    until docker compose exec -T postgres pg_isready -U postgres -d book_agent >/dev/null 2>&1; do
        retries=$((retries + 1))
        if [[ $retries -ge 30 ]]; then
            error "PostgreSQL 30秒内未就绪，请检查 Docker。"
            exit 1
        fi
        sleep 1
    done
}

if command -v uv >/dev/null 2>&1; then
    info "同步 Python 依赖 (uv)..."
    uv sync --quiet
    UVICORN_CMD=(uv run uvicorn)
    ALEMBIC_CMD=(uv run alembic)
else
    if [[ ! -d "$ROOT_DIR/.venv" ]] && [[ -z "${VIRTUAL_ENV:-}" ]]; then
        warn "未找到 uv，创建 .venv..."
        python3 -m venv "$ROOT_DIR/.venv"
        "$ROOT_DIR/.venv/bin/pip" install -e ".[dev]" -q
    fi
    PY_BIN="${VIRTUAL_ENV:-$ROOT_DIR/.venv}/bin/python"
    UVICORN_CMD=("$PY_BIN" -m uvicorn)
    ALEMBIC_CMD=("$PY_BIN" -m alembic)
fi

# ---- 数据库 ----
export BOOK_AGENT_DATABASE_URL="$COMPOSE_PG_URL"
info "本地开发统一使用 Docker PostgreSQL: ${DIM}${BOOK_AGENT_DATABASE_URL}${NC}"
start_compose_postgres
info "运行数据库迁移..."
PYTHONPATH="$ROOT_DIR/src" "${ALEMBIC_CMD[@]}" upgrade head
info "PostgreSQL 就绪。"

# ---- 启动后端 (热重载) ----
echo ""
info "${BOLD}启动后端${NC} → ${CYAN}http://${HOST}:${BACKEND_PORT}${NC}  ${DIM}(uvicorn --reload)${NC}"
info "API 文档 → ${CYAN}http://${HOST}:${BACKEND_PORT}/v1/docs${NC}"

PYTHONPATH="$ROOT_DIR/src" "${UVICORN_CMD[@]}" book_agent.app.main:app \
    --host "$HOST" \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-dir "$ROOT_DIR/src" &
PIDS+=($!)

# ---- 启动前端 (Vite HMR) ----
if [[ "$NO_FRONTEND" != true ]] && [[ -f "$ROOT_DIR/frontend/package.json" ]]; then
    if [[ "$FRONTEND_ALREADY_RUNNING" == true ]]; then
        info "${BOLD}复用前端${NC} → ${CYAN}http://${HOST}:${FRONTEND_PORT}${NC}  ${DIM}(已在运行)${NC}"
    else
    if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
        info "安装前端依赖..."
        (cd "$ROOT_DIR/frontend" && npm install --silent)
    fi
    info "${BOLD}启动前端${NC} → ${CYAN}http://${HOST}:${FRONTEND_PORT}${NC}  ${DIM}(Vite HMR)${NC}"
    (cd "$ROOT_DIR/frontend" && exec npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT") &
    PIDS+=($!)
    fi
fi

# ---- 就绪提示 ----
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
info "${BOLD}开发环境已启动，按 Ctrl+C 停止所有服务${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# 等待所有后台进程，任一退出则触发 cleanup
wait
