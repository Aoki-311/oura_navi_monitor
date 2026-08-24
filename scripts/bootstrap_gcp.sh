#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
STAGE="prepare"
REGION="us-central1"
DATASET_ID="oura_navi_monitor"
LOCATION="US"
SOURCE_SERVICE="lcs-rag-app"
SINK_NAME="oura_navi_monitor_sink"
JOB_NAME="oura-navi-monitor-refresh"
SCHEDULER_QUARTER="oura-navi-monitor-refresh-quarter-hour"
RUNTIME_SERVICE_ACCOUNT=""
IMAGE=""
ANALYTICS_START_AT=""
IDENTITY_SECRET="oura-navi-monitor-identity-hmac"
FIRESTORE_DATABASE="lcs-user-data"
ADMIN_CHANGE_COLLECTION="monitor_admin_changes"
EXPORT_COLLECTION="monitor_export_jobs"
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2 ;;
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --runtime-service-account) RUNTIME_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --analytics-start-at) ANALYTICS_START_AT="$2"; shift 2 ;;
    --identity-secret) IDENTITY_SECRET="$2"; shift 2 ;;
    --firestore-database) FIRESTORE_DATABASE="$2"; shift 2 ;;
    --admin-change-collection) ADMIN_CHANGE_COLLECTION="$2"; shift 2 ;;
    --export-collection) EXPORT_COLLECTION="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
[[ "${STAGE}" == "prepare" || "${STAGE}" == "activate" ]] || { echo "--stage must be prepare or activate" >&2; exit 2; }
if [[ "${STAGE}" == "activate" ]]; then
  [[ -n "${RUNTIME_SERVICE_ACCOUNT}" && -n "${IMAGE}" && -n "${ANALYTICS_START_AT}" ]] || {
    echo "activate requires --runtime-service-account, --image and --analytics-start-at" >&2; exit 2;
  }
fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan) stage=${STAGE}"
echo "dataset=${PROJECT_ID}.${DATASET_ID} sink=${SINK_NAME} job=${JOB_NAME}"
echo "scheduler=${SCHEDULER_QUARTER} secret=${IDENTITY_SECRET} ttl_collections=${ADMIN_CHANGE_COLLECTION},${EXPORT_COLLECTION}"
if [[ "${APPLY}" != "true" ]]; then
  if [[ "${STAGE}" == "prepare" ]]; then
    echo "prepare=ttl,canonical_tables,logging_sink_writer"
    "${ROOT_DIR}/scripts/bootstrap_monitor_data.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}"
  else
    echo "activate=verified_source_views,refresh_job,scheduler analytics_start_at=${ANALYTICS_START_AT}"
  fi
  exit 0
fi
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || { echo "approved credential is required" >&2; exit 2; }
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
gcloud --project="${PROJECT_ID}" secrets describe "${IDENTITY_SECRET}" >/dev/null

if [[ "${STAGE}" == "prepare" ]]; then
  for collection_group in "${ADMIN_CHANGE_COLLECTION}" "${EXPORT_COLLECTION}"; do
    gcloud --project="${PROJECT_ID}" firestore fields ttls update expires_at \
      --collection-group="${collection_group}" \
      --database="${FIRESTORE_DATABASE}" \
      --enable-ttl \
      --quiet
  done

  "${ROOT_DIR}/scripts/bootstrap_monitor_data.sh" --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" --python "${ROOT_DIR}/.venv/bin/python" --apply

  DESTINATION="bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET_ID}"
  FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND (logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Frequests\" OR (logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Fstdout\" AND jsonPayload.monitor_event=true))"
  if gcloud --project="${PROJECT_ID}" logging sinks describe "${SINK_NAME}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" logging sinks update "${SINK_NAME}" "${DESTINATION}" --log-filter="${FILTER}" --use-partitioned-tables
  else
    gcloud --project="${PROJECT_ID}" logging sinks create "${SINK_NAME}" "${DESTINATION}" --log-filter="${FILTER}" --use-partitioned-tables
  fi
  WRITER_IDENTITY="$(gcloud --project="${PROJECT_ID}" logging sinks describe "${SINK_NAME}" --format='value(writerIdentity)')"
  [[ -n "${WRITER_IDENTITY}" ]] || { echo "sink writer identity is empty" >&2; exit 2; }
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query --use_legacy_sql=false \
    "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`${PROJECT_ID}.${DATASET_ID}\` TO \"${WRITER_IDENTITY}\""
  echo "prepare complete; do not activate refresh until candidate events, source views and one rebuild pass"
  exit 0
fi

for object in monitor_event_source http_request_source pipeline_state pipeline_runs; do
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show "${PROJECT_ID}:${DATASET_ID}.${object}" >/dev/null || {
    echo "canonical activation prerequisite missing: ${PROJECT_ID}.${DATASET_ID}.${object}" >&2; exit 2;
  }
done
PUBLISHED_READY="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
  --use_legacy_sql=false --format=csv --quiet \
  "SELECT COUNTIF(source = 'published' AND status = 'succeeded' AND data_through >= TIMESTAMP('${ANALYTICS_START_AT}')) FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_state\`" | tail -n 1)"
[[ "${PUBLISHED_READY}" =~ ^[1-9][0-9]*$ ]] || {
  echo "activation requires one successful canonical rebuild at or after ${ANALYTICS_START_AT}" >&2; exit 2;
}
RUNTIME_ENV="$(mktemp)"
trap 'rm -f "${RUNTIME_ENV}"' EXIT
"${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scripts/render_runtime_env.py" \
  --source "${ROOT_DIR}/deploy/cloudrun.env.yaml" \
  --output "${RUNTIME_ENV}" \
  --analytics-start-at "${ANALYTICS_START_AT}"

gcloud --project="${PROJECT_ID}" run jobs deploy "${JOB_NAME}" \
  --region="${REGION}" --image="${IMAGE}" --service-account="${RUNTIME_SERVICE_ACCOUNT}" \
  --command=python --args=-m,app.jobs.refresh_analytics,--apply \
  --env-vars-file="${RUNTIME_ENV}" \
  --set-secrets="MONITOR_IDENTITY_HMAC_KEY=${IDENTITY_SECRET}:latest" --max-retries=1 --task-timeout=30m

JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
upsert_scheduler() {
  local name="$1" schedule="$2"
  if gcloud --project="${PROJECT_ID}" scheduler jobs describe "${name}" --location="${REGION}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" scheduler jobs update http "${name}" --location="${REGION}" --schedule="${schedule}" --uri="${JOB_URI}" --http-method=POST --oauth-service-account-email="${RUNTIME_SERVICE_ACCOUNT}" --time-zone="Asia/Tokyo"
  else
    gcloud --project="${PROJECT_ID}" scheduler jobs create http "${name}" --location="${REGION}" --schedule="${schedule}" --uri="${JOB_URI}" --http-method=POST --oauth-service-account-email="${RUNTIME_SERVICE_ACCOUNT}" --time-zone="Asia/Tokyo"
  fi
}
upsert_scheduler "${SCHEDULER_QUARTER}" "*/15 * * * *"
