#!/usr/bin/env bash
# FREE deploy path (Google Cloud hackathon):
#   Agent → Google Cloud Run (free tier, long bootstrap runs)
#   UI    → Vercel hobby (free) OR Cloud Run UI (recommended — no 60s stream limit)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .env ] && set -a && source .env && set +a

MODE="${1:-help}"

echo "Lowkally — free cloud deploy (no Render payment)"
echo ""

case "$MODE" in
  agent)
    if ! command -v gcloud >/dev/null 2>&1; then
      echo "Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
      echo "Then: gcloud auth login && gcloud config set project YOUR_PROJECT_ID"
      exit 1
    fi
    : "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
    export APP_URL="${APP_URL:-https://YOUR-VERCEL-URL.vercel.app}"
    export CORS_ORIGINS="${CORS_ORIGINS:-$APP_URL}"
    bash forge/deploy/cloud-run-agent.sh
    ;;
  ui-vercel)
    if ! command -v vercel >/dev/null 2>&1; then
      echo "Install: npm i -g vercel"
      exit 1
    fi
    : "${AGENT_URL:?Set AGENT_URL to Cloud Run agent URL first (bash scripts/deploy-vercel-gcp.sh agent)}"
    if [[ "${APP_URL:-}" == http://localhost* ]] || [ -z "${APP_URL:-}" ]; then
      echo "Set APP_URL in .env to your Vercel URL (e.g. https://lowkally.vercel.app)"
      exit 1
    fi
    cd forge/frontend
    vercel link --yes 2>/dev/null || vercel link
    printf '%s' "$AGENT_URL" | vercel env add API_URL production --force 2>/dev/null \
      || printf '%s' "$AGENT_URL" | vercel env add API_URL production
    vercel deploy --prod --yes
    echo ""
    echo "Then on Cloud Run agent set: APP_URL=$APP_URL  CORS_ORIGINS=$APP_URL"
    echo "WARNING: Vercel Hobby limits API routes to 60s — long bootstraps may timeout."
    echo "Use ui-cloudrun instead for full runs."
    ;;
  ui-cloudrun)
    if ! command -v gcloud >/dev/null 2>&1; then
      echo "Install gcloud first."
      exit 1
    fi
    : "${AGENT_URL:?Set AGENT_URL after deploying agent}"
    bash forge/deploy/cloud-run-ui.sh
    ;;
  help|*)
    cat <<'EOF'
Usage:
  1) Deploy agent (Cloud Run — free tier):
       export GOOGLE_CLOUD_PROJECT=your-gcp-project
       export APP_URL=https://YOUR-UI-URL   # set after UI deploy, or use placeholder then update
       bash scripts/deploy-vercel-gcp.sh agent
       export AGENT_URL=https://lowkally-agent-xxxxx.run.app

  2a) UI on Cloud Run (RECOMMENDED — no stream timeout):
       bash scripts/deploy-vercel-gcp.sh ui-cloudrun
       # Copy UI URL → set APP_URL + CORS_ORIGINS on agent, redeploy agent

  2b) UI on Vercel (free hobby — bootstrap may hit 60s limit):
       export APP_URL=https://your-project.vercel.app
       bash scripts/deploy-vercel-gcp.sh ui-vercel

Why not Render?
  Our blueprint used paid "starter" plans. Render free tier may still ask for a card
  ($1 hold only). Vercel hobby + GCP Cloud Run is $0 for hackathon demos.

OAuth callbacks (use your public UI URL):
  {APP_URL}/api/auth/github/callback
  {APP_URL}/api/auth/gitlab/callback
  {APP_URL}/api/auth/google/callback
EOF
    ;;
esac
