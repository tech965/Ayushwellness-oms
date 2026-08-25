#!/usr/bin/env bash
# One-time local bootstrap: Python venv + backend deps, frontend deps.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Creating backend virtualenv (apps/api/.venv)"
python -m venv apps/api/.venv || py -3.12 -m venv apps/api/.venv

if [ -f apps/api/.venv/Scripts/python.exe ]; then
  PYBIN=apps/api/.venv/Scripts/python.exe
else
  PYBIN=apps/api/.venv/bin/python
fi

echo "==> Installing backend dependencies"
"$PYBIN" -m pip install --upgrade pip
"$PYBIN" -m pip install -e "apps/api[dev]"

echo "==> Installing frontend dependencies"
npm --prefix apps/web install

echo "==> Done. Copy .env.example to .env and apps/web/.env.example to apps/web/.env.local before running the app."
