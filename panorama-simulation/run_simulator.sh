#!/usr/bin/env bash
# Convenience launcher: starts MediaMTX (if not already running) then the
# panorama simulator server, using the project venv.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="/home/miko/Documents/miko/monkey-project/final_project/panorama-simulation/venv3.12/bin/python"
MEDIAMTX="${MEDIAMTX:-/home/miko/Applications/mediamtx}"
MEDIAMTX_CONFIG="${MEDIAMTX_CONFIG:-/home/miko/Applications/mediamtx.yml}"

mtx_pid=""
if ! pgrep -x mediamtx >/dev/null 2>&1; then
  if [[ -x "$MEDIAMTX" ]]; then
    echo "[run] starting MediaMTX..."
    "$MEDIAMTX" "$MEDIAMTX_CONFIG" >/tmp/mediamtx.log 2>&1 &
    mtx_pid=$!
    sleep 1.5
  else
    echo "[run] WARNING: MediaMTX binary not found at $MEDIAMTX; assuming it is already running."
  fi
else
  echo "[run] MediaMTX already running."
fi

cleanup() {
  echo "[run] shutting down..."
  [[ -n "$mtx_pid" ]] && kill "$mtx_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[run] starting simulator server..."
exec "$PY" server.py --config config.yaml
