#!/usr/bin/env bash
# Format backend (black) and frontend (prettier) in place.
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh

echo "==> Backend: black"
(cd "$API_DIR" && "$PYBIN" -m black app tests)

echo "==> Frontend: prettier"
npm --prefix apps/web run format
