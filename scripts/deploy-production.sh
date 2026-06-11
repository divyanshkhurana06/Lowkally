#!/usr/bin/env bash
# Deploy Lowkally to the cloud (no local laptop runtime).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "Missing .env — copy .env.example and fill secrets first." >&2
  exit 1
fi
set -a && source .env && set +a

TARGET="${1:-render}"

require_env() {
  local key="$1"
  if [ -z "${!key:-}" ]; then
    echo "Set $key in .env before deploying." >&2
    exit 1
  fi
}

require_env GOOGLE_API_KEY
require_env JWT_SECRET

case "$TARGET" in
  render)
    echo "=== Render (recommended — agent + UI, one domain via UI proxy) ==="
    echo ""
    echo "1. Push this repo to GitHub (if not already):"
    echo "   git remote add origin https://github.com/divyanshkhurana06/Lowkally.git"
    echo "   git push -u origin main"
    echo ""
    echo "2. Open https://dashboard.render.com/blueprints"
    echo "   → New Blueprint Instance → connect repo → apply render.yaml"
    echo ""
    echo "3. In Render dashboard, set secret env vars on lowkally-agent:"
    echo "   GOOGLE_API_KEY, GITLAB_PERSONAL_ACCESS_TOKEN,"
    echo "   GITHUB_CLIENT_ID/SECRET, GITLAB_CLIENT_ID/SECRET, GOOGLE_OAUTH_*"
    echo ""
    echo "4. After deploy, copy your UI URL (https://lowkally-ui-xxx.onrender.com)"
    echo "   Update OAuth app callbacks:"
    echo "   {APP_URL}/api/auth/github/callback"
    echo "   {APP_URL}/api/auth/gitlab/callback"
    echo "   {APP_URL}/api/auth/google/callback"
  ;;

  vercel)
    require_env APP_URL
    if [[ "$APP_URL" == http://localhost* ]]; then
      echo "Set APP_URL in .env to your public Vercel URL before deploying." >&2
      exit 1
    fi
    require_env API_URL
    echo "=== Vercel UI (agent must already be running at API_URL) ==="
    cd forge/frontend
    vercel link --yes 2>/dev/null || vercel link
    vercel env rm API_URL production --yes 2>/dev/null || true
    printf '%s' "$API_URL" | vercel env add API_URL production
    vercel deploy --prod --yes
    echo ""
    echo "Set agent APP_URL=$APP_URL and CORS_ORIGINS=$APP_URL on your agent host."
  ;;

  docker-vps)
    require_env APP_URL
    require_env CORS_ORIGINS
    echo "=== Docker Compose on a VPS (single machine, public URL) ==="
    docker compose -f docker-compose.prod.yml up -d --build
    echo "Open $APP_URL (point DNS at this server's IP)"
  ;;

  *)
    echo "Usage: bash scripts/deploy-production.sh [render|vercel|docker-vps]" >&2
    exit 1
  ;;
esac
