#!/usr/bin/env bash
# Sourced by other scripts/*.sh to resolve the backend venv's python binary
# regardless of OS. Sets $PYBIN.
set -euo pipefail

if [ -f "$API_DIR/.venv/Scripts/python.exe" ]; then
  PYBIN="$API_DIR/.venv/Scripts/python.exe"
elif [ -f "$API_DIR/.venv/bin/python" ]; then
  PYBIN="$API_DIR/.venv/bin/python"
else
  echo "No virtualenv found at $API_DIR/.venv — run scripts/setup.sh first." >&2
  exit 1
fi
