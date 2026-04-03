#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "[forge-v2:init] warning hygiene validator expects exactly one smoke-log path" >&2
  exit 1
fi

SMOKE_LOG="$1"

if [ ! -f "$SMOKE_LOG" ]; then
  echo "[forge-v2:init] smoke log not found: $SMOKE_LOG" >&2
  exit 1
fi

FORBIDDEN_WARNING_PATTERN='on_event is deprecated|ResourceWarning: unclosed database'

if rg -n "$FORBIDDEN_WARNING_PATTERN" "$SMOKE_LOG" >/dev/null; then
  echo "[forge-v2:init] forbidden warning output detected in default smoke" >&2
  rg -n "$FORBIDDEN_WARNING_PATTERN" "$SMOKE_LOG" >&2
  exit 1
fi

echo "[forge-v2:init] smoke warning hygiene validated"
