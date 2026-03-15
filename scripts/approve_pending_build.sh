#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
BUILD_ID="${1:-}"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }

gcloud config set project "${PROJECT_ID}" >/dev/null

if [[ -z "${BUILD_ID}" ]]; then
  echo "Pending approval builds:"
  gcloud beta builds list \
    --project="${PROJECT_ID}" \
    --filter='status=PENDING' \
    --format='table(id,status,createTime,substitutions.TRIGGER_NAME)' || true
  echo
  echo "Usage:"
  echo "  ./scripts/approve_pending_build.sh <BUILD_ID>"
  echo "  ./scripts/approve_pending_build.sh <BUILD_ID> reject"
  exit 0
fi

ACTION="${2:-approve}"
if [[ "${ACTION}" == "reject" ]]; then
  gcloud beta builds reject "${BUILD_ID}" --project="${PROJECT_ID}"
  echo "Rejected build: ${BUILD_ID}"
else
  gcloud beta builds approve "${BUILD_ID}" --project="${PROJECT_ID}"
  echo "Approved build: ${BUILD_ID}"
fi
