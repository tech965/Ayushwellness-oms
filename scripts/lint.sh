#!/usr/bin/env bash
# Lint backend (ruff) and frontend (eslint).
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh

echo "==> Backend: ruff"
(cd "$API_DIR" && "$PYBIN" -m ruff check app tests)

echo "==> Frontend: eslint"
npm --prefix apps/web run lint
