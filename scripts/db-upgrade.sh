#!/usr/bin/env bash
# Apply all pending Alembic migrations.
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh
cd "$API_DIR"
"$PYBIN" -m alembic upgrade head
