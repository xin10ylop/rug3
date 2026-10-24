#!/usr/bin/env bash
# Race-capability probe: no-trade txs (compute budget + tip) sent the instant a create is seen pre-execution,
# with the same fee rank and paths as a real buy. Exits by itself after probe_max probes, then prints the verdict.
#   ./scripts/probe.sh          # 200 probes, Helius SWQoS + Triton Cascade   (~0.006 SOL)
#   ./scripts/probe.sh max      # 60 probes,  Helius "Max" (0.001 SOL tip)    (~0.06 SOL)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env (run ./scripts/install.sh)"; exit 1; }
set -a; . ./.env; set +a
case "${1:-swqos}" in
  max) CFG=bot/configs/probe_max.json; J=data/journal_probe_max.jsonl ;;
  *)   CFG=bot/configs/probe.json;     J=data/journal_probe.jsonl ;;
esac
[ -f "$J" ] && mv "$J" "${J%.jsonl}.$(date +%Y%m%d-%H%M%S).jsonl"
echo "== probing with $CFG (wallet $(bot/target/release/rugbot pubkey wallet.json)); journal $J"
bot/target/release/rugbot "$CFG" > data/logs/probe_stdout.log 2> data/logs/probe_stderr.log || {
  echo "bot exited with an error:"; tail -5 data/logs/probe_stderr.log; exit 1; }
echo "== probes done; evaluating (waits ~20s for finalization)"
sleep 20
.venv/bin/python research/probe_eval.py "$J"
