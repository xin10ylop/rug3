#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f data/live.pid ] || { echo "not running (no data/live.pid)"; exit 0; }
P=$(cat data/live.pid)
if kill -0 "$P" 2>/dev/null && grep -q rugbot "/proc/$P/cmdline" 2>/dev/null; then
  kill "$P"; echo "rugbot PID $P: no new buys; waiting for open positions to exit..."
  while kill -0 "$P" 2>/dev/null; do sleep 1; done
  echo "stopped ($(grep -o '"open_positions_left":[0-9]*' data/journal_live.jsonl | tail -1))"
else echo "PID $P is not running"; fi
rm -f data/live.pid
