#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
APPLY="false"
PYTHON_BIN="python3"
CREDENTIAL_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "publish canonical raw source views for ${PROJECT_ID}.${DATASET_ID}"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"
for table in run_googleapis_com_requests run_googleapis_com_stdout; do
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show "${PROJECT_ID}:${DATASET_ID}.${table}" >/dev/null || {
    echo "raw table not ready: ${PROJECT_ID}.${DATASET_ID}.${table}" >&2
    exit 2
  }
done
TMP_SQL="$(mktemp)"
trap 'rm -f "${TMP_SQL}"' EXIT
MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -c "from app.jobs.refresh_analytics import render_sql; from app.settings import Settings; print(render_sql('create_source_tables.sql', Settings()))" > "${TMP_SQL}"
bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query --use_legacy_sql=false < "${TMP_SQL}"
