#!/usr/bin/env bash
# Deploy FORGE agent to Google Cloud Run
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="forge-agent"
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

cd "$ROOT/forge"
gcloud builds submit --tag "$IMAGE" -f agent/Dockerfile .
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 900 \
  --set-env-vars "GEMINI_MODEL=gemini-2.5-flash,APP_URL=${APP_URL:-http://localhost:3000}" \
  --set-secrets "GOOGLE_API_KEY=GOOGLE_API_KEY:latest,JWT_SECRET=JWT_SECRET:latest" \
  --update-env-vars "FORGE_WORKSPACE=/data/workspace,FORGE_DATA_DIR=/data/db,CORS_ORIGINS=${CORS_ORIGINS:-*}"

echo "Deployed: $(gcloud run services describe $SERVICE --region $REGION --format='value(status.url)')"
