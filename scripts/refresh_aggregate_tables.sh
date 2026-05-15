#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
BQ_DATASET="${BQ_DATASET:-oura_navi_monitor}"
BQ_LOCATION="${BQ_LOCATION:-US}"
SOURCE_SERVICE="${SOURCE_SERVICE:-lcs-rag-app}"
ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS="${ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS:-2026-05-15T03:59:21Z}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SQL_TEMPLATE="${ROOT_DIR}/sql/create_aggregate_tables.sql"

command -v bq >/dev/null 2>&1 || { echo "bq not found"; exit 1; }

TMP_SQL="$(mktemp)"
sed \
  -e "s/__PROJECT_ID__/${PROJECT_ID}/g" \
  -e "s/__DATASET_ID__/${BQ_DATASET}/g" \
  -e "s/__SERVICE_NAME__/${SOURCE_SERVICE}/g" \
  -e "s|__ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS__|${ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS}|g" \
  "${SQL_TEMPLATE}" > "${TMP_SQL}"

bq --location="${BQ_LOCATION}" query --use_legacy_sql=false < "${TMP_SQL}"
rm -f "${TMP_SQL}"

echo "Aggregate tables refreshed: ${PROJECT_ID}.${BQ_DATASET}.monitor_answer_events, ${PROJECT_ID}.${BQ_DATASET}.monitor_user_daily, ${PROJECT_ID}.${BQ_DATASET}.monitor_system_hourly, ${PROJECT_ID}.${BQ_DATASET}.monitor_dashboard_snapshots"
