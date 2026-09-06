#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
"$PYTHON_BIN" -m pip install --upgrade pip

if [[ -f requirements.txt ]]; then
  "$PYTHON_BIN" -m pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env. Fill in credentials before starting the monitor."
fi

echo "Installation complete."
