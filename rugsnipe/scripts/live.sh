#!/usr/bin/env bash
# Gated live micro test: real buys on armed bot fingerprints, only when the slot leader is < 1200 km away and the
# create reached us early in its slot. 0.03 SOL/trade, max 3 open, stops for the day at -0.25 SOL.
#   ./scripts/live.sh            # start in the background (PID in data/live.pid)
#   ./scripts/stop_live.sh       # stop buying, let open positions exit (<= ~60 s), then quit
#   ./scripts/eval_live.sh       # on-chain PnL, race outcomes, sim-vs-live
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (run ./scripts/install.sh)"; exit 1; }
set -a; . ./.env; set +a
CFG=bot/configs/live_gated.json
if [ -f data/live.pid ] && kill -0 "$(cat data/live.pid)" 2>/dev/null; then echo "already running (PID $(cat data/live.pid))"; exit 1; fi
if [ -z "$(find data/live/leaders.json -mmin -360 2>/dev/null)" ]; then
  echo "== locating leaders (data/live/leaders.json)"; .venv/bin/python research/leaders.py
fi
echo "== live gated test: wallet $(bot/target/release/rugbot pubkey wallet.json), journal data/journal_live.jsonl"
RUST_BACKTRACE=0 nohup bot/target/release/rugbot "$CFG" > data/logs/live_stdout.log 2> data/logs/live_stderr.log &
echo $! > data/live.pid
sleep 8
if kill -0 "$(cat data/live.pid)" 2>/dev/null; then
  echo "running (PID $(cat data/live.pid)); follow with: tail -f data/logs/live_stdout.log | grep -E 'buy_|sell_|skip'"
else
  echo "bot exited:"; grep -m3 -E "Error|error" data/logs/live_stderr.log || tail -5 data/logs/live_stderr.log; exit 1
fi
