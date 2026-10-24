#!/usr/bin/env bash
# rugsnipe installer: everything stays inside this directory (own venv, own wallet, own data).
# Nothing is installed system-wide except rustup (per-user, only if cargo is missing).
set -euo pipefail
cd "$(dirname "$0")/.."
HERE="$(pwd)"
echo "== installing rugsnipe in $HERE"

if ! command -v cargo >/dev/null 2>&1; then
  if [ -f "$HOME/.cargo/env" ]; then . "$HOME/.cargo/env"; fi
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "== cargo not found: installing rustup for $(whoami) (minimal profile)"
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
  . "$HOME/.cargo/env"
fi
echo "== building bot (local target dir: bot/target)"
(cd bot && cargo build --release)

echo "== python venv (local: .venv)"
if ! python3 -m venv .venv 2>/dev/null; then
  echo "python3 venv module missing. On Ubuntu/Debian: sudo apt-get install -y python3-venv   (then re-run)"; exit 1
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r research/requirements.txt
if [ "${1:-}" = "--research" ]; then .venv/bin/pip install -q -r research/requirements-research.txt; fi

mkdir -p data/live data/bf data/rec data/logs
if [ ! -f .env ]; then
  cp env.example .env && chmod 600 .env
  echo "== created $HERE/.env -> put your Triton RPC URL and x-token in it"
fi
if [ ! -f wallet.json ]; then
  bot/target/release/rugbot keygen wallet.json >/dev/null
  echo "== created a NEW dedicated wallet: $HERE/wallet.json (keep it private; back it up)"
fi
echo
echo "rugsnipe wallet address: $(bot/target/release/rugbot pubkey wallet.json)"
echo "fund it with ~0.1 SOL for the probe, then run: ./scripts/probe.sh"
