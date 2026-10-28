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

# 3. Automated Version Cleanup (Retain Current + Previous 3 Versions)
echo "🧹 [3/3] Pruning old versions (retaining previous 3 versions)..."

# Prune Cloud Run revisions (keep 4 newest = 1 active + 3 previous)
OLD_REVISIONS=$(gcloud run revisions list \
  --service "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format="value(metadata.name)" \
  --sort-by="~metadata.creationTimestamp" | tail -n +5)

for REV in ${OLD_REVISIONS}; do
  if [ -n "${REV}" ]; then
    echo "Pruning inactive Cloud Run revision: ${REV}"
    gcloud run revisions delete "${REV}" \
      --project "${PROJECT_ID}" \
      --region "${REGION}" \
      --quiet || true
  fi
done

# Prune Artifact Registry container images
REPO_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}"
OLD_IMAGES=$(gcloud artifacts docker images list "${REPO_PATH}" \
  --format="value(DIGEST)" \
  --sort-by="~CREATE_TIME" 2>/dev/null | tail -n +9 || true)

for DIGEST in ${OLD_IMAGES}; do
  if [ -n "${DIGEST}" ]; then
    echo "Pruning old container image: ${DIGEST}"
    gcloud artifacts docker images delete "${REPO_PATH}@${DIGEST}" \
      --delete-tags \
      --quiet || true
  fi
done

echo "============================================="
echo "✅ ApexFPL Deployment & Cleanup Complete!"
echo "Production URL: https://apex-fpl.web.app"
echo "Cloud Run URL:  https://apexfpl-376378418388.asia-south1.run.app"
echo "============================================="
