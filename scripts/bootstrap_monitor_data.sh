#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
APPLY="false"
PYTHON_BIN="python3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILES=(create_dataset.sql create_fact_tables.sql create_aggregates.sql)

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} location=${LOCATION} dataset=${DATASET_ID}"
printf 'sql=%s\n' "${SQL_FILES[@]}"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi

[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE must point to the approved credential" >&2; exit 2;
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }

TMP_SQL="$(mktemp)"
trap 'rm -f "${TMP_SQL}"' EXIT
for name in "${SQL_FILES[@]}"; do
  MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
    PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -c "from app.jobs.refresh_analytics import render_sql; from app.settings import Settings; print(render_sql('${name}', Settings()))" > "${TMP_SQL}"
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query --use_legacy_sql=false < "${TMP_SQL}"
done
