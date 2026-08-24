#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
REGION="us-central1"
SERVICE_NAME="oura-navi-monitor"
REVISION=""
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE_NAME="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" && -n "${REVISION}" ]] || {
  echo "--project and exact --revision are required" >&2
  exit 2
}

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "service=${SERVICE_NAME} region=${REGION} revision=${REVISION} traffic=100%"
echo "Precondition: authenticated candidate acceptance and business acceptance are both recorded."
if [[ "${APPLY}" != "true" ]]; then
  exit 0
fi

[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2
  exit 2
}
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
gcloud --project="${PROJECT_ID}" run services update-traffic "${SERVICE_NAME}" \
  --region="${REGION}" \
  --to-revisions="${REVISION}=100"
