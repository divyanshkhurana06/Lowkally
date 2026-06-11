#!/usr/bin/env bash
# Vercel UI + local agent exposed via Cloudflare quick tunnel (no card, no HF token).
# Agent must keep running on this machine while you demo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a
source .venv/bin/activate 2>/dev/null || true

command -v cloudflared >/dev/null || { echo "Install: brew install cloudflared"; exit 1; }
command -v vercel >/dev/null || { echo "Install: npm i -g vercel"; exit 1; }

lsof -ti:8080 | xargs kill -9 2>/dev/null || true
mkdir -p forge/data forge/workspace logs
pip install -q -r forge/agent/requirements.txt

echo ">>> Starting agent on :8080"
cd forge/agent
APP_URL="${APP_URL:-https://lowkally.vercel.app}" \
CORS_ORIGINS="${APP_URL:-https://lowkally.vercel.app}" \
python server.py >"$ROOT/logs/agent.log" 2>&1 &
AGENT_PID=$!
cd "$ROOT"

for i in $(seq 1 40); do
  curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:8080/health >/dev/null || { echo "Agent failed. See logs/agent.log"; exit 1; }

echo ">>> Starting Cloudflare tunnel"
cloudflared tunnel --url http://127.0.0.1:8080 >"$ROOT/logs/tunnel.log" 2>&1 &
TUNNEL_PID=$!
AGENT_URL=""
for i in $(seq 1 30); do
  AGENT_URL="$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$ROOT/logs/tunnel.log" | head -1 || true)"
  [ -n "$AGENT_URL" ] && break
  sleep 1
done
[ -z "$AGENT_URL" ] && { echo "Tunnel failed. See logs/tunnel.log"; kill $AGENT_PID $TUNNEL_PID 2>/dev/null; exit 1; }
echo "$AGENT_URL" >"$ROOT/.deploy-agent-url"
echo "Agent (tunnel): $AGENT_URL"

echo ">>> Deploying Vercel UI"
cd forge/frontend
vercel link --yes 2>/dev/null || vercel link --yes
printf '%s' "$AGENT_URL" | vercel env rm API_URL production --yes 2>/dev/null || true
printf '%s' "$AGENT_URL" | vercel env add API_URL production --force
DEPLOY_OUT="$(vercel deploy --prod --yes 2>&1)"
echo "$DEPLOY_OUT"
UI_URL="$(echo "$DEPLOY_OUT" | grep -Eo 'https://[a-zA-Z0-9.-]+\.vercel\.app' | tail -1)"
[ -z "$UI_URL" ] && UI_URL="$(vercel ls --yes 2>/dev/null | grep -Eo 'https://[a-zA-Z0-9.-]+\.vercel\.app' | head -1 || true)"
cd "$ROOT"

[ -z "$UI_URL" ] && { echo "Could not detect Vercel URL"; exit 1; }

echo ">>> Restarting agent with APP_URL=$UI_URL"
kill "$AGENT_PID" 2>/dev/null || true
sleep 1
cd forge/agent
APP_URL="$UI_URL" CORS_ORIGINS="$UI_URL" python server.py >"$ROOT/logs/agent.log" 2>&1 &
NEW_AGENT_PID=$!
echo "$NEW_AGENT_PID" >"$ROOT/logs/agent.pid"
echo "$TUNNEL_PID" >"$ROOT/logs/tunnel.pid"
cd "$ROOT"

for i in $(seq 1 20); do
  curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && break
  sleep 1
done

echo "$UI_URL" >"$ROOT/.deploy-ui-url"
echo ""
echo "UI_URL=$UI_URL"
echo "AGENT_URL=$AGENT_URL"
echo "PIDs: agent + tunnel (keep this terminal / laptop running)"
