#!/usr/bin/env bash
# Create a new Alembic revision. Usage: scripts/db-migrate.sh "add orders table"
set -euo pipefail
cd "$(dirname "$0")/.."
API_DIR="$(pwd)/apps/api"
source scripts/_venv-python.sh
MSG="${1:?Usage: scripts/db-migrate.sh \"migration message\"}"
cd "$API_DIR"
"$PYBIN" -m alembic revision --autogenerate -m "$MSG"
