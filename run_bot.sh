#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/realtonypark/Developer/printer"
cd "$ROOT"

if [[ ! -f .env.local ]]; then
  echo "Missing .env.local (copy from .env.example and set real credentials)." >&2
  exit 1
fi

python3 -m pip install -e ".[dev]"
exec python3 -m src.main --env-file .env.local
