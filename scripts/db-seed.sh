#!/usr/bin/env bash
# Run the development database seed script.
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh
cd "$API_DIR"
"$PYBIN" scripts/seed.py
