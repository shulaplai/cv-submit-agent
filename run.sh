#!/usr/bin/env bash
# One-command launcher: build frontend (if changed) + start backend on :8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "==> creating venv + installing backend deps"
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
  .venv/bin/playwright install chromium
fi

if [ ! -d frontend/node_modules ]; then
  echo "==> installing frontend deps"
  (cd frontend && npm install --no-audit --no-fund)
fi

echo "==> building frontend"
(cd frontend && npm run build)

if [ ! -f .env ]; then
  echo "==> .env not found — copying .env.example (fill in your keys!)"
  cp .env.example .env
fi

echo "==> starting backend on http://127.0.0.1:8000"
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
