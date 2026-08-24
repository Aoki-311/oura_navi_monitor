#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
BUILD_REGION="us-central1"
BUILD_ID=""
ACTION="approve"
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) BUILD_REGION="$2"; shift 2 ;;
    --build-id) BUILD_ID="$2"; shift 2 ;;
    --action) ACTION="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" && -n "${BUILD_ID}" ]] || {
  echo "--project and exact --build-id are required" >&2
  exit 2
}
[[ "${ACTION}" == "approve" || "${ACTION}" == "reject" ]] || {
  echo "--action must be approve or reject" >&2
  exit 2
}

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} region=${BUILD_REGION} build=${BUILD_ID} action=${ACTION}"
if [[ "${APPLY}" != "true" ]]; then
  exit 0
fi

[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2
  exit 2
}
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
gcloud --project="${PROJECT_ID}" beta builds "${ACTION}" "${BUILD_ID}" \
  --region="${BUILD_REGION}"
