#!/usr/bin/env bash
# Deploy Lowkally UI (Next.js) to Google Cloud Run — pairs with cloud-run-agent.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="lowkally-ui"
AGENT_URL="${AGENT_URL:?Set AGENT_URL to your Cloud Run agent URL (https://...)}"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

cd "$ROOT/forge/frontend"
gcloud builds submit . \
  --config="$ROOT/forge/deploy/cloudbuild-ui.yaml" \
  --substitutions="_IMAGE=$IMAGE,_API_URL=$AGENT_URL"
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 900 \
  --set-env-vars "API_URL=${AGENT_URL},NODE_ENV=production"

UI_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "UI deployed: $UI_URL"
echo "Update agent: APP_URL=$UI_URL  CORS_ORIGINS=$UI_URL"
echo "OAuth callbacks: $UI_URL/api/auth/{github,gitlab,google}/callback"
