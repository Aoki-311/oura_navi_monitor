#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
ANALYTICS_START_AT=""
APPLY="false"
PYTHON_BIN="python3"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --analytics-start-at) ANALYTICS_START_AT="$2"; shift 2 ;;
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
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
"${ROOT_DIR}/scripts/bootstrap_monitor_data.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" --python "${PYTHON_BIN}" --apply
"${ROOT_DIR}/scripts/publish_monitor_views.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" --python "${PYTHON_BIN}" --apply
MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
MONITOR_ANALYTICS_START_AT="${ANALYTICS_START_AT}" "${ROOT_DIR}/scripts/run_monitor_refresh.sh" --python "${PYTHON_BIN}" --apply
