#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-oura-navi-monitor}"
RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-lcs-agent@lcs-developer-483404.iam.gserviceaccount.com}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:$(date +%Y%m%d-%H%M%S)}"
BUILD_MODE="${BUILD_MODE:-auto}" # auto | cloudbuild | docker
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/cloudrun.env.yaml"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }

gcloud config set project "${PROJECT_ID}" >/dev/null

build_with_docker() {
  command -v docker >/dev/null 2>&1 || { echo "docker not found for local build fallback"; exit 1; }
  echo "Build image with local Docker (linux/amd64): ${IMAGE}"
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null
  if docker buildx version >/dev/null 2>&1; then
    docker buildx build --platform linux/amd64 -t "${IMAGE}" "${ROOT_DIR}" --push
  else
    docker build --platform linux/amd64 -t "${IMAGE}" "${ROOT_DIR}"
    docker push "${IMAGE}"
  fi
}

build_with_cloudbuild() {
  echo "Build image with Cloud Build: ${IMAGE}"
  gcloud builds submit "${ROOT_DIR}" --tag "${IMAGE}"
}

if [[ "${BUILD_MODE}" == "cloudbuild" ]]; then
  build_with_cloudbuild
elif [[ "${BUILD_MODE}" == "docker" ]]; then
  build_with_docker
else
  if ! build_with_cloudbuild; then
    echo "Cloud Build failed; falling back to local Docker build."
    build_with_docker
  fi
fi

echo "Deploy Cloud Run service: ${SERVICE_NAME}"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
  --no-allow-unauthenticated \
  --env-vars-file "${ENV_FILE}"

echo "Deploy done."
