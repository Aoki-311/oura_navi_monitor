#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-lcs-agent@lcs-developer-483404.iam.gserviceaccount.com}"
KEY_NAME="${KEY_NAME:-lcs-rag-app}"
OUTPUT_DIR="${OUTPUT_DIR:-credentials}"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }

gcloud config set project "${PROJECT_ID}" >/dev/null
mkdir -p "${OUTPUT_DIR}"
KEY_PATH="${OUTPUT_DIR}/${KEY_NAME}.json"

gcloud iam service-accounts keys create "${KEY_PATH}" --iam-account="${RUNTIME_SERVICE_ACCOUNT}"
echo "Service account key created: ${KEY_PATH}"
echo "Use only for bootstrap, then switch runtime and CI/CD to keyless auth."
