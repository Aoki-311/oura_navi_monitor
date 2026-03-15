#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
REGION="${REGION:-us-central1}"
SOURCE_SERVICE="${SOURCE_SERVICE:-lcs-rag-app}"
BQ_DATASET="${BQ_DATASET:-oura_navi_monitor}"
BQ_LOCATION="${BQ_LOCATION:-US}"
SINK_NAME="${SINK_NAME:-oura_navi_monitor_sink}"
RETENTION_DAYS="${RETENTION_DAYS:-180}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SQL_TEMPLATE="${ROOT_DIR}/sql/create_views.sql"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }
command -v bq >/dev/null 2>&1 || { echo "bq not found"; exit 1; }

echo "[1/7] Set gcloud project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "[2/7] Ensure BigQuery dataset: ${PROJECT_ID}:${BQ_DATASET}"
if ! bq --location="${BQ_LOCATION}" show --dataset "${PROJECT_ID}:${BQ_DATASET}" >/dev/null 2>&1; then
  bq --location="${BQ_LOCATION}" mk --dataset --description "OurA Navi monitor logs dataset" "${PROJECT_ID}:${BQ_DATASET}"
fi

echo "[3/7] Set BigQuery retention: ${RETENTION_DAYS} days"
RETENTION_SECONDS="$(( RETENTION_DAYS * 24 * 60 * 60 ))"
bq --location="${BQ_LOCATION}" update --dataset --default_table_expiration "${RETENTION_SECONDS}" "${PROJECT_ID}:${BQ_DATASET}" >/dev/null

for table in run_googleapis_com_requests run_googleapis_com_stdout run_googleapis_com_stderr; do
  if bq --location="${BQ_LOCATION}" show --format=prettyjson "${PROJECT_ID}:${BQ_DATASET}.${table}" >/dev/null 2>&1; then
    bq --location="${BQ_LOCATION}" update --time_partitioning_expiration "${RETENTION_SECONDS}" "${PROJECT_ID}:${BQ_DATASET}.${table}" >/dev/null || true
  fi
done

echo "[4/7] Create or update Logging Sink: ${SINK_NAME}"
SINK_FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com\""
SINK_DEST="bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${BQ_DATASET}"
if gcloud logging sinks describe "${SINK_NAME}" >/dev/null 2>&1; then
  gcloud logging sinks update "${SINK_NAME}" "${SINK_DEST}" --log-filter="${SINK_FILTER}" --use-partitioned-tables >/dev/null
else
  gcloud logging sinks create "${SINK_NAME}" "${SINK_DEST}" --log-filter="${SINK_FILTER}" --use-partitioned-tables >/dev/null
fi

WRITER_IDENTITY="$(gcloud logging sinks describe "${SINK_NAME}" --format='value(writerIdentity)')"
echo "[5/7] Grant sink writer BigQuery Data Editor role: ${WRITER_IDENTITY}"
if gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member="${WRITER_IDENTITY}" --role="roles/bigquery.dataEditor" >/dev/null 2>&1; then
  echo "Project-level IAM binding applied."
else
  echo "Project-level IAM binding failed; applying dataset-level grant fallback."
  bq --location="${BQ_LOCATION}" query --use_legacy_sql=false \
    "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`${PROJECT_ID}.${BQ_DATASET}\` TO '${WRITER_IDENTITY}'" >/dev/null
  echo "Dataset-level grant applied."
fi

echo "[6/7] Create helper BigQuery views"
TMP_SQL="$(mktemp)"
sed \
  -e "s/__PROJECT_ID__/${PROJECT_ID}/g" \
  -e "s/__DATASET_ID__/${BQ_DATASET}/g" \
  -e "s/__SERVICE_NAME__/${SOURCE_SERVICE}/g" \
  "${SQL_TEMPLATE}" > "${TMP_SQL}"
bq --location="${BQ_LOCATION}" query --use_legacy_sql=false < "${TMP_SQL}" >/dev/null
rm -f "${TMP_SQL}"

echo "[7/7] Create log-based metrics for alerting"
create_or_update_metric() {
  local name="$1"
  local filter="$2"
  if gcloud logging metrics describe "${name}" >/dev/null 2>&1; then
    gcloud logging metrics update "${name}" --description="${name}" --log-filter="${filter}" >/dev/null
  else
    gcloud logging metrics create "${name}" --description="${name}" --log-filter="${filter}" >/dev/null
  fi
}

create_or_update_metric "lcs_rag_app_5xx_count" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com%2Frequests\" AND httpRequest.status>=500"
create_or_update_metric "lcs_rag_app_qs_total" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com%2Fstdout\" AND textPayload=~\"^query_suggest_result \""
create_or_update_metric "lcs_rag_app_qs_degraded" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com%2Fstdout\" AND textPayload=~\"^query_suggest_result .* stage=degraded \""
create_or_update_metric "lcs_rag_app_restore_total" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com%2Fstdout\" AND textPayload=~\"^chat_sync_telemetry .*event=restore_\""
create_or_update_metric "lcs_rag_app_restore_failed" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName:\"run.googleapis.com%2Fstdout\" AND textPayload=~\"^chat_sync_telemetry .*event=restore_failed\""

echo "Bootstrap complete."
echo "Next: run scripts/setup_alerts.sh to create notification channels and conservative alert policies."
