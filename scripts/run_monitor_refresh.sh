#!/usr/bin/env bash
set -euo pipefail

APPLY="false"
UNTIL_CURRENT="false"
PYTHON_BIN="python3"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --until-current) UNTIL_CURRENT="true"; shift ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${APPLY}" != "true" ]]; then
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -m app.jobs.refresh_analytics
  exit 0
fi
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE must point to the approved credential" >&2; exit 2;
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
ARGS=(--apply)
if [[ "${UNTIL_CURRENT}" == "true" ]]; then ARGS+=(--until-current); fi
PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -m app.jobs.refresh_analytics "${ARGS[@]}"
