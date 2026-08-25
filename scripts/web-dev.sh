#!/usr/bin/env bash
# Run the Next.js frontend dev server.
set -euo pipefail
cd "$(dirname "$0")/../apps/web"
npm run dev
