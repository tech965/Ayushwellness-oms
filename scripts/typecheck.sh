#!/usr/bin/env bash
# Type-check backend (mypy) and frontend (tsc).
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh

echo "==> Backend: mypy"
(cd "$API_DIR" && "$PYBIN" -m mypy app)

echo "==> Frontend: tsc"
npm --prefix apps/web run typecheck
