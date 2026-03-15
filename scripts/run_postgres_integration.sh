#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker compose up -d postgres
docker compose run --rm --no-deps app alembic upgrade head
docker compose run --rm --no-deps -e BOOK_AGENT_RUN_PG_TESTS=1 app python -m unittest tests.test_postgres_workflow_integration -v
