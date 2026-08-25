#!/usr/bin/env bash
# Run the FastAPI backend with autoreload.
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh
cd "$API_DIR"
"$PYBIN" -m uvicorn app.main:app --reload
