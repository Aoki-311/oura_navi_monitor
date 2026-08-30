#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
BUILD_REGION="us-central1"
BUILD_ID=""
ACTION="approve"
APPLY="false"
CREDENTIAL_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) BUILD_REGION="$2"; shift 2 ;;
    --build-id) BUILD_ID="$2"; shift 2 ;;
    --action) ACTION="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
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

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
[[ -x "${PYTHON_BIN}" ]] || { echo "repository Python runtime not found" >&2; exit 2; }

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/cloud_build_approval.py" \
  --credential-file "${CREDENTIAL_FILE}" \
  --project "${PROJECT_ID}" \
  --region "${BUILD_REGION}" \
  --build-id "${BUILD_ID}" \
  --action "${ACTION}"
