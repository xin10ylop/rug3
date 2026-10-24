#!/usr/bin/env bash
# Send this bot wallet's whole SOL balance to DESTINATION (asks for confirmation).
#   ./scripts/sweep.sh DESTINATION
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "usage: ./scripts/sweep.sh DESTINATION_ADDRESS"; exit 1; }
if [ -f data/live.pid ] && kill -0 "$(cat data/live.pid)" 2>/dev/null; then echo "the bot is running: ./scripts/stop_live.sh first"; exit 1; fi
set -a; . ./.env; set +a
RUST_BACKTRACE=0 bot/target/release/rugbot sweep wallet.json "$1"
