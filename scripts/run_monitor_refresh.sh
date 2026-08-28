#!/usr/bin/env bash
set -euo pipefail

APPLY="false"
UNTIL_CURRENT="false"
PYTHON_BIN="python3"
LOCAL_DEV="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --until-current) UNTIL_CURRENT="true"; shift ;;
    --apply) APPLY="true"; shift ;;
    --local-dev) LOCAL_DEV="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${APPLY}" != "true" ]]; then
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -m app.jobs.refresh_analytics
  exit 0
fi
[[ "${LOCAL_DEV}" == "true" ]] || {
  echo "direct apply is local/dev only; use the frozen Cloud Run Job backfill workflow" >&2
  exit 2
}
[[ "${MONITOR_PROJECT_ID:-}" != "lcs-developer-483404" ]] || {
  echo "direct refresh is forbidden for the production project" >&2
  exit 2
}
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE must point to the approved credential" >&2; exit 2;
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
ARGS=(--apply --trigger-source manual)
if [[ "${UNTIL_CURRENT}" == "true" ]]; then ARGS+=(--until-current); fi
PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -m app.jobs.refresh_analytics "${ARGS[@]}"
