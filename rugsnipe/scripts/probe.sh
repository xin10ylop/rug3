#!/usr/bin/env bash
# Race-capability probe: no-trade txs (compute budget + tip) sent the instant a create is seen pre-execution,
# with the same fee rank and paths as a real buy. Exits by itself after probe_max probes, then prints the verdict.
#   ./scripts/probe.sh          # 200 probes, Helius SWQoS + Triton Cascade   (~0.006 SOL)
#   ./scripts/probe.sh gated    # 200 probes, only creates whose slot leader is < 1200 km away and that reached us
#                               # early in their slot (the race gate a live run would use)  (~0.006 SOL, ~40 min)
#   ./scripts/probe.sh max      # 60 probes,  Helius "Max" (0.001 SOL tip)    (~0.06 SOL)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (run ./scripts/install.sh)"; exit 1; }
set -a; . ./.env; set +a
case "${1:-swqos}" in
  max)   CFG=bot/configs/probe_max.json;   J=data/journal_probe_max.jsonl ;;
  gated) CFG=bot/configs/probe_gated.json; J=data/journal_probe_gated.jsonl ;;
  *)     CFG=bot/configs/probe.json;       J=data/journal_probe.jsonl ;;
esac
if [ "${1:-}" = gated ] && [ -z "$(find data/live/leaders.json -mmin -360 2>/dev/null)" ]; then
  echo "== locating leaders (data/live/leaders.json)"; .venv/bin/python research/leaders.py
fi
[ -f "$J" ] && mv "$J" "${J%.jsonl}.$(date +%Y%m%d-%H%M%S).jsonl"
echo "== probing with $CFG (wallet $(bot/target/release/rugbot pubkey wallet.json)); journal $J"
RUST_BACKTRACE=0 bot/target/release/rugbot "$CFG" > data/logs/probe_stdout.log 2> data/logs/probe_stderr.log || {
  echo "bot exited with an error:"; grep -m3 -E "Error|error" data/logs/probe_stderr.log || head -5 data/logs/probe_stderr.log; exit 1; }
echo "== probes done; evaluating (waits ~20s for finalization)"
sleep 20
.venv/bin/python research/probe_eval.py "$J"
