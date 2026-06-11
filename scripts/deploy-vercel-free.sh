#!/usr/bin/env bash
# Free deploy (no GCP card): Agent → Hugging Face Spaces, UI → Vercel Hobby
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a

echo "=== Lowkally free deploy (HF agent + Vercel UI) ==="
echo ""

# --- Agent on Hugging Face ---
if [ -z "${HF_TOKEN:-}" ] && [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  echo "HF_TOKEN missing. Get a WRITE token: https://huggingface.co/settings/tokens"
  echo "  export HF_TOKEN=hf_..."
  exit 1
fi

export APP_URL="${APP_URL:-https://lowkally.vercel.app}"
export CORS_ORIGINS="${CORS_ORIGINS:-$APP_URL}"

echo ">>> Deploying agent to Hugging Face Spaces..."
python3 scripts/deploy_hf_agent.py
AGENT_URL="$(cat .deploy-agent-url | tr -d '\n')"
export AGENT_URL
echo "Agent: $AGENT_URL"

# --- UI on Vercel ---
if ! command -v vercel >/dev/null 2>&1; then
  echo "Install Vercel CLI: npm i -g vercel"
  exit 1
fi

echo ""
echo ">>> Deploying UI to Vercel..."
cd forge/frontend
vercel link --yes 2>/dev/null || vercel link --yes
printf '%s' "$AGENT_URL" | vercel env rm API_URL production --yes 2>/dev/null || true
printf '%s' "$AGENT_URL" | vercel env add API_URL production --force
DEPLOY_OUT="$(vercel deploy --prod --yes 2>&1)"
echo "$DEPLOY_OUT"
UI_URL="$(echo "$DEPLOY_OUT" | grep -Eo 'https://[a-zA-Z0-9.-]+\.vercel\.app' | tail -1)"
if [ -z "$UI_URL" ]; then
  UI_URL="$(vercel inspect --prod 2>/dev/null | grep -Eo 'https://[a-zA-Z0-9.-]+\.vercel\.app' | head -1 || true)"
fi
[ -z "$UI_URL" ] && UI_URL="${APP_URL}"

cd "$ROOT"
echo ""
echo ">>> Updating agent APP_URL / CORS to $UI_URL"
export APP_URL="$UI_URL"
export CORS_ORIGINS="$UI_URL"
python3 scripts/deploy_hf_agent.py >/dev/null

echo ""
echo "=============================================="
echo "  LIVE UI:    $UI_URL"
echo "  AGENT:      $AGENT_URL"
echo "=============================================="
echo ""
echo "OAuth callback URLs (update in GitHub / GitLab / Google):"
echo "  $UI_URL/api/auth/github/callback"
echo "  $UI_URL/api/auth/gitlab/callback"
echo "  $UI_URL/api/auth/google/callback"
echo ""
echo "Save APP_URL in .env: APP_URL=$UI_URL"
