#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python research/probe_eval.py "${1:-data/journal_probe.jsonl}"
