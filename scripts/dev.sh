#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"
DEV_LOG="$DATA_DIR/dev-server.log"
API_LOG="$DATA_DIR/dev-api.log"
WEB_LOG="$DATA_DIR/dev-web.log"
API_URL="http://127.0.0.1:8091/api/health"
API_PID=""

mkdir -p "$DATA_DIR"

log() {
  local event="$1"
  local status="$2"
  shift 2
  printf 'ts=%s event=%s status=%s %s\n' "$(date -Is)" "$event" "$status" "$*" | tee -a "$DEV_LOG"
}

cleanup() {
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    log "api.stop" "ok" "pid=$API_PID"
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

if curl -fsS "$API_URL" >/dev/null 2>&1; then
  log "api.reuse" "ok" "url=$API_URL"
else
  log "api.start" "ok" "host=0.0.0.0 port=8091 log=$API_LOG"
  uv run uvicorn rag_sync.api:app --host 0.0.0.0 --port 8091 >>"$API_LOG" 2>&1 &
  API_PID="$!"

  api_ready="false"
  for _ in {1..30}; do
    if curl -fsS "$API_URL" >/dev/null 2>&1; then
      api_ready="true"
      break
    fi
    if ! kill -0 "$API_PID" 2>/dev/null; then
      log "api.start" "error" "pid=$API_PID log=$API_LOG"
      tail -n 40 "$API_LOG" || true
      exit 1
    fi
    sleep 0.5
  done

  if [[ "$api_ready" != "true" ]]; then
    log "api.health" "error" "url=$API_URL timeout_seconds=15 log=$API_LOG"
    exit 1
  fi
  log "api.health" "ok" "url=$API_URL pid=$API_PID"
fi

log "web.start" "ok" "host=0.0.0.0 port=5174 log=$WEB_LOG"
npm --prefix "$ROOT_DIR/web" run dev -- --port 5174 2>&1 | tee -a "$WEB_LOG"
exit "${PIPESTATUS[0]}"
