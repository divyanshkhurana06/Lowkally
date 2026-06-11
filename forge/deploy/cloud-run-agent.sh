#!/usr/bin/env bash
# Deploy Lowkally agent to Google Cloud Run (free tier friendly)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${CLOUD_RUN_AGENT_SERVICE:-lowkally-agent}"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

[ -f "$ROOT/.env" ] && set -a && source "$ROOT/.env" && set +a

APP_URL="${APP_URL:-http://localhost:3000}"
CORS_ORIGINS="${CORS_ORIGINS:-$APP_URL}"

cd "$ROOT/forge"
gcloud builds submit . \
  --config="$ROOT/forge/deploy/cloudbuild-agent.yaml" \
  --substitutions="_IMAGE=$IMAGE"

ENV_VARS="GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash}"
ENV_VARS+=",APP_URL=${APP_URL}"
ENV_VARS+=",CORS_ORIGINS=${CORS_ORIGINS}"
ENV_VARS+=",FORGE_WORKSPACE=/data/workspace"
ENV_VARS+=",FORGE_DATA_DIR=/data/db"
# Keep ADK + GitLab MCP visible for hackathon judges; pipeline fallback if ADK times out or quota hits.
ENV_VARS+=",FORGE_BUILD_TIMEOUT=600"
ENV_VARS+=",FORGE_MAX_ITERATIONS=8"
ENV_VARS+=",FORGE_ADK_TIMEOUT=60"
ENV_VARS+=",NODE_OPTIONS=--max-old-space-size=3072"
ENV_VARS+=",GITLAB_API_URL=${GITLAB_API_URL:-https://gitlab.com/api/v4}"
[ -n "${GITHUB_ISSUES_URL:-}" ] && ENV_VARS+=",GITHUB_ISSUES_URL=${GITHUB_ISSUES_URL}"

ENV_VARS+=",GOOGLE_API_KEY=${GOOGLE_API_KEY:?}"
ENV_VARS+=",JWT_SECRET=${JWT_SECRET:?}"
ENV_VARS+=",GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID:-}"
ENV_VARS+=",GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET:-}"
ENV_VARS+=",GITLAB_CLIENT_ID=${GITLAB_CLIENT_ID:-}"
ENV_VARS+=",GITLAB_CLIENT_SECRET=${GITLAB_CLIENT_SECRET:-}"
ENV_VARS+=",GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID:-}"
ENV_VARS+=",GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET:-}"
ENV_VARS+=",GITLAB_PERSONAL_ACCESS_TOKEN=${GITLAB_PERSONAL_ACCESS_TOKEN:-}"
ENV_VARS+=",GITHUB_ISSUES_TOKEN=${GITHUB_ISSUES_TOKEN:-}"
ENV_VARS+=",GITLAB_OAUTH_SCOPES=${GITLAB_OAUTH_SCOPES:-read_user read_api}"

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 4Gi \
  --timeout 900 \
  --cpu 2 \
  --max-instances 1 \
  --no-cpu-throttling \
  --set-env-vars "$ENV_VARS"

AGENT_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "Agent deployed: $AGENT_URL"
echo "export AGENT_URL=$AGENT_URL"
