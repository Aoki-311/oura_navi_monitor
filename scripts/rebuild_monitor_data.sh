#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
ANALYTICS_START_AT=""
HISTORY_CONFIRM=""
APPLY="false"
PYTHON_BIN="python3"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --analytics-start-at) ANALYTICS_START_AT="$2"; shift 2 ;;
    --history-confirm) HISTORY_CONFIRM="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${PROJECT_ID}" && -n "${ANALYTICS_START_AT}" ]] || { echo "--project and --analytics-start-at are required" >&2; exit 2; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "canonical rebuild project=${PROJECT_ID} dataset=${DATASET_ID} start=${ANALYTICS_START_AT}"
echo "No backup, shadow dataset, compatibility view, or old-table fallback will be created."
echo "order=read_only_history_preflight,canonical_tables,source_views,history_apply,bounded_incremental_catchup,semantic_publish"
if [[ "${APPLY}" != "true" ]]; then
  echo "Apply requires --history-confirm from: PYTHONPATH=${ROOT_DIR} MONITOR_PROJECT_ID=${PROJECT_ID} MONITOR_BQ_DATASET=${DATASET_ID} MONITOR_BQ_LOCATION=${LOCATION} MONITOR_ANALYTICS_START_AT=${ANALYTICS_START_AT} ${PYTHON_BIN} -m app.jobs.rebuild_history"
  exit 0
fi
[[ -n "${HISTORY_CONFIRM}" ]] || { echo "--history-confirm is required on apply" >&2; exit 2; }
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2; exit 2;
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }

PREFLIGHT_JSON="$(
  MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" \
  MONITOR_BQ_LOCATION="${LOCATION}" MONITOR_ANALYTICS_START_AT="${ANALYTICS_START_AT}" \
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" -m app.jobs.rebuild_history
)"
PREFLIGHT_CONFIRM="$(
  printf '%s' "${PREFLIGHT_JSON}" | "${PYTHON_BIN}" -c \
    'import json, sys; print(json.load(sys.stdin)["requiredConfirmation"])'
)"
[[ "${PREFLIGHT_CONFIRM}" == "${HISTORY_CONFIRM}" ]] || {
  echo "history confirmation changed; rerun plan and review ${PREFLIGHT_CONFIRM}" >&2
  exit 2
}

"${ROOT_DIR}/scripts/bootstrap_monitor_data.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" --python "${PYTHON_BIN}" --apply
"${ROOT_DIR}/scripts/publish_monitor_views.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" --python "${PYTHON_BIN}" --apply
MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
MONITOR_ANALYTICS_START_AT="${ANALYTICS_START_AT}" PYTHONPATH="${ROOT_DIR}" \
  "${PYTHON_BIN}" -m app.jobs.rebuild_history --apply --confirm "${HISTORY_CONFIRM}"
MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
MONITOR_ANALYTICS_START_AT="${ANALYTICS_START_AT}" \
  "${ROOT_DIR}/scripts/run_monitor_refresh.sh" --python "${PYTHON_BIN}" --apply --until-current

TMP_SQL="$(mktemp)"
trap 'rm -f "${TMP_SQL}"' EXIT
MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" \
MONITOR_BQ_LOCATION="${LOCATION}" PYTHONPATH="${ROOT_DIR}" \
  "${PYTHON_BIN}" -c \
  "from app.jobs.refresh_analytics import render_sql; from app.settings import Settings; print(render_sql('create_api_views.sql', Settings()))" \
  > "${TMP_SQL}"
bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
  --use_legacy_sql=false < "${TMP_SQL}"
