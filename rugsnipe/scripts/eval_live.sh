#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
.venv/bin/python research/live_eval.py "${1:-data/journal_live.jsonl}"
