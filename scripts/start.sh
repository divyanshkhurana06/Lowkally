#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a
source .venv/bin/activate 2>/dev/null || true

# Local dev must use localhost URLs (ignore production APP_URL from .env)
export APP_URL=http://localhost:3000
export CORS_ORIGINS=http://localhost:3000
export API_URL=http://127.0.0.1:8080

pip install -q -r forge/agent/requirements.txt

lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true

mkdir -p forge/data forge/workspace

echo "Starting Lowkally agent :8080"
cd "$ROOT/forge/agent"
nohup python server.py > /tmp/lowkally-agent.log 2>&1 &
AGENT_PID=$!

echo "Waiting for agent health..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 \
    && curl -sf http://127.0.0.1:8080/api/auth/me >/dev/null 2>&1; then
    echo "Agent ready."
    break
  fi
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "Agent failed to start." >&2
    exit 1
  fi
  sleep 1
done
if ! curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
  echo "Timed out waiting for agent on :8080" >&2
  exit 1
fi

echo "Starting Lowkally UI :3000"
cd "$ROOT/forge/frontend"
printf 'API_URL=http://127.0.0.1:8080\n' > .env.local
[ -d node_modules ] || npm install
nohup npm run dev > /tmp/lowkally-ui.log 2>&1 &
UI_PID=$!

for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:3000 >/dev/null 2>&1; then
    echo "UI ready."
    break
  fi
  if ! kill -0 "$UI_PID" 2>/dev/null; then
    echo "UI failed to start — see /tmp/lowkally-ui.log" >&2
    tail -20 /tmp/lowkally-ui.log >&2 || true
    exit 1
  fi
  sleep 1
done

echo ""
echo "Lowkally → http://localhost:3000"
echo "API   → http://localhost:8080/health"
wait
