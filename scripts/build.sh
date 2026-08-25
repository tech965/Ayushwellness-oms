#!/usr/bin/env bash
# Production build for the frontend, and an import smoke test for the backend
# (FastAPI has no separate "build" step).
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh

echo "==> Backend: import smoke test"
(cd "$API_DIR" && "$PYBIN" -c "from app.main import app")

echo "==> Frontend: next build"
npm --prefix apps/web run build
