#!/usr/bin/env bash
# Start the dev server with auto-reload.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
