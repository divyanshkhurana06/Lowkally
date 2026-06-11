#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a
source .venv/bin/activate 2>/dev/null || true

pip install -q -r forge/agent/requirements.txt

lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true

mkdir -p forge/data forge/workspace

echo "Starting FORGE agent :8080"
cd forge/agent && python server.py &
AGENT_PID=$!

echo "Waiting for agent health..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
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

echo "Starting FORGE UI :3000"
cd "$ROOT/forge/frontend"
echo "API_URL=http://127.0.0.1:8080" > .env.local
[ -d node_modules ] || npm install
npm run dev &

echo ""
echo "FORGE → http://localhost:3000"
echo "API   → http://localhost:8080/health"
wait
