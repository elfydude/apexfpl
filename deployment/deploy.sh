#!/usr/bin/env bash
# ==============================================================================
# ApexFPL Unified Cloud Deployment Script
# Deploys backend API container to Google Cloud Run and static frontend to Firebase
# ==============================================================================

set -euo pipefail

PROJECT_ID="vijay-tmt"
SERVICE_NAME="apexfpl"
REGION="asia-south1"

echo "============================================="
echo "🚀 ApexFPL Cloud Deployment Starting..."
echo "Project:  ${PROJECT_ID}"
echo "Service:  ${SERVICE_NAME}"
echo "Region:   ${REGION}"
echo "============================================="

# 1. Deploy to Google Cloud Run
echo "📦 [1/2] Deploying container to Google Cloud Run..."
cp deployment/Dockerfile ./Dockerfile
cp deployment/.dockerignore ./.dockerignore

trap 'rm -f ./Dockerfile ./.dockerignore ./firebase.json ./.firebaserc' EXIT

gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars PYTHONUNBUFFERED=1 \
  --quiet

rm -f ./Dockerfile ./.dockerignore

# 2. Deploy to Firebase Hosting
echo "🌐 [2/2] Deploying frontend CDN to Firebase Hosting..."
cp deployment/firebase.json ./firebase.json
cp deployment/.firebaserc ./.firebaserc

npx -y firebase-tools deploy --only hosting:apex-fpl --project "${PROJECT_ID}" --non-interactive

rm -f ./firebase.json ./.firebaserc

echo "============================================="
echo "✅ ApexFPL Deployment Complete!"
echo "Production URL: https://apex-fpl.web.app"
echo "Cloud Run URL:  https://apexfpl-376378418388.asia-south1.run.app"
echo "============================================="
