#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
BQ_DATASET="${BQ_DATASET:-oura_navi_monitor}"
BQ_LOCATION="${BQ_LOCATION:-US}"
SOURCE_SERVICE="${SOURCE_SERVICE:-lcs-rag-app}"
DISPLAY_NAME="${DISPLAY_NAME:-oura_navi_monitor_aggregate_refresh}"
SCHEDULE="${SCHEDULE:-every 15 minutes}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SQL_TEMPLATE="${ROOT_DIR}/sql/create_aggregate_tables.sql"

command -v bq >/dev/null 2>&1 || { echo "bq not found"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }

TMP_SQL="$(mktemp)"
TMP_CONFIGS="$(mktemp)"
trap 'rm -f "${TMP_SQL}" "${TMP_CONFIGS}"' EXIT

sed \
  -e "s/__PROJECT_ID__/${PROJECT_ID}/g" \
  -e "s/__DATASET_ID__/${BQ_DATASET}/g" \
  -e "s/__SERVICE_NAME__/${SOURCE_SERVICE}/g" \
  "${SQL_TEMPLATE}" > "${TMP_SQL}"

PARAMS_JSON="$(python3 - "${TMP_SQL}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    query = fh.read()
print(json.dumps({"query": query}, separators=(",", ":")))
PY
)"

bq --project_id="${PROJECT_ID}" ls \
  --transfer_config \
  --transfer_location="${BQ_LOCATION}" \
  --format=prettyjson > "${TMP_CONFIGS}" 2>/dev/null || echo "[]" > "${TMP_CONFIGS}"

CONFIG_INFO="$(
  python3 - "${DISPLAY_NAME}" "${TMP_CONFIGS}" <<'PY'
import json
import sys

display_name = sys.argv[1]
config_path = sys.argv[2]
try:
    with open(config_path, "r", encoding="utf-8") as fh:
        configs = json.load(fh)
except Exception:
    configs = []
for config in configs or []:
    if config.get("displayName") == display_name:
        print(f"{config.get('name', '')}\t{config.get('destinationDatasetId', '')}")
        break
PY
)"

CONFIG_NAME="${CONFIG_INFO%%$'\t'*}"
CONFIG_DESTINATION_DATASET=""
if [[ "${CONFIG_INFO}" == *$'\t'* ]]; then
  CONFIG_DESTINATION_DATASET="${CONFIG_INFO#*$'\t'}"
fi

if [[ -n "${CONFIG_NAME}" && -n "${CONFIG_DESTINATION_DATASET}" ]]; then
  echo "Existing aggregate scheduled query has a destination dataset; recreating it for DDL script mode."
  bq rm -f --transfer_config "${CONFIG_NAME}"
  CONFIG_NAME=""
fi

if [[ -n "${CONFIG_NAME}" ]]; then
  if [[ -n "${SERVICE_ACCOUNT_NAME}" ]]; then
    bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" update \
      --transfer_config \
      --display_name="${DISPLAY_NAME}" \
      --params="${PARAMS_JSON}" \
      --schedule="${SCHEDULE}" \
      --use_legacy_sql=false \
      --service_account_name="${SERVICE_ACCOUNT_NAME}" \
      "${CONFIG_NAME}"
  else
    bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" update \
      --transfer_config \
      --display_name="${DISPLAY_NAME}" \
      --params="${PARAMS_JSON}" \
      --schedule="${SCHEDULE}" \
      --use_legacy_sql=false \
      "${CONFIG_NAME}"
  fi
  echo "Aggregate scheduled query updated: ${CONFIG_NAME}"
else
  if [[ -n "${SERVICE_ACCOUNT_NAME}" ]]; then
    bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" mk \
      --transfer_config \
      --display_name="${DISPLAY_NAME}" \
      --data_source=scheduled_query \
      --params="${PARAMS_JSON}" \
      --schedule="${SCHEDULE}" \
      --use_legacy_sql=false \
      --service_account_name="${SERVICE_ACCOUNT_NAME}"
  else
    bq --project_id="${PROJECT_ID}" --location="${BQ_LOCATION}" mk \
      --transfer_config \
      --display_name="${DISPLAY_NAME}" \
      --data_source=scheduled_query \
      --params="${PARAMS_JSON}" \
      --schedule="${SCHEDULE}" \
      --use_legacy_sql=false
  fi
  echo "Aggregate scheduled query created: ${DISPLAY_NAME}"
fi

echo "Schedule: ${SCHEDULE}"
echo "Query source: ${SQL_TEMPLATE}"
