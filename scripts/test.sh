#!/usr/bin/env bash
# Run backend (pytest) and frontend (vitest) test suites.
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh

echo "==> Backend: pytest"
(cd "$API_DIR" && "$PYBIN" -m pytest)

echo "==> Frontend: vitest"
npm --prefix apps/web run test
