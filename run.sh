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
  echo "==> .env not found — copying .env.example"
  cp .env.example .env
fi

# ---- pre-flight config check (warnings only, does not block) ----
echo "==> 檢查 .env 配置…"
MISSING=0
check_env() {
  local key="$1"; local label="$2"
  local val
  val=$(.venv/bin/python -c "
import re, pathlib
try:
    env = pathlib.Path('.env').read_text(encoding='utf-8')
    m = re.search(r'^${key}=(.*)$', env, re.M)
    print((m.group(1).strip() if m else ''))
except FileNotFoundError:
    print('')
" 2>/dev/null || echo "")
  if [ -z "$val" ]; then
    echo "  ⚠ 未設定：${label}（.env 嘅 ${key}）"
    MISSING=1
  fi
}
check_env "LLM_API_KEY" "LLM API key（DeepSeek）"
check_env "CV_EN_PATH" "英文 CV 路徑"
check_env "CV_ZH_PATH" "中文 CV 路徑"
if [ "$MISSING" = "1" ]; then
  echo "  （可以照起機，但 LLM 功能未開 — 可以喺設定頁直接填 key 同 CV 路徑）"
fi

# ---- start backend, then auto-open the UI ----
echo "==> 啟動中 http://127.0.0.1:8000（自動開瀏覽器）"
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
SERVER_PID=$!

# wait for the server to respond, then open the browser
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
    break
  fi
  sleep 0.5
done
open http://127.0.0.1:8000 2>/dev/null || true

# keep the server in the foreground (Ctrl+C to stop)
wait $SERVER_PID
