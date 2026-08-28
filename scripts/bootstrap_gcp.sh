#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
STAGE="prepare"
REGION="us-central1"
DATASET_ID="oura_navi_monitor"
LOCATION="US"
SOURCE_SERVICE="lcs-rag-app"
SINK_NAME="oura_navi_monitor_sink"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
SCHEDULER_REFRESH="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
SCHEDULER_LEGACY="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_name)')"
SCHEDULER_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_cron)')"
SCHEDULER_BOOTSTRAP_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import safe_scheduler_bootstrap_cron; print(safe_scheduler_bootstrap_cron())')"
SCHEDULER_TIMEZONE="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.timezone)')"
SCHEDULER_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_attempt_deadline_seconds)')"
SCHEDULER_MAX_RETRY_ATTEMPTS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_max_retry_attempts)')"
JOB_TIMEOUT_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')"
RUNTIME_SERVICE_ACCOUNT=""
SCHEDULER_INVOKER_SERVICE_ACCOUNT=""
IMAGE=""
ANALYTICS_START_AT=""
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
    --scheduler-invoker-service-account) SCHEDULER_INVOKER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --analytics-start-at) ANALYTICS_START_AT="$2"; shift 2 ;;
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
  [[ -n "${RUNTIME_SERVICE_ACCOUNT}" && -n "${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" && -n "${IMAGE}" && -n "${ANALYTICS_START_AT}" ]] || {
    echo "activate requires refresh-writer and scheduler-invoker service accounts, plus --image and --analytics-start-at" >&2; exit 2;
  }
  for service_account in "${RUNTIME_SERVICE_ACCOUNT}" "${SCHEDULER_INVOKER_SERVICE_ACCOUNT}"; do
    [[ "${service_account}" =~ ^[a-z0-9-]+@${PROJECT_ID}\.iam\.gserviceaccount\.com$ ]] || {
      echo "every runtime identity must be one exact service account in ${PROJECT_ID}" >&2; exit 2;
    }
  done
  [[ "${RUNTIME_SERVICE_ACCOUNT}" != "${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" ]] || {
    echo "refresh writer and scheduler invoker identities must be distinct" >&2; exit 2;
  }
  [[ "${IMAGE}" =~ ^${REGION}-docker\.pkg\.dev/${PROJECT_ID}/[^/@]+/[^/@]+@sha256:[0-9a-f]{64}$ ]] || {
    echo "activate --image must be an immutable Artifact Registry digest in the selected project and region" >&2
    exit 2
  }
fi
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan) stage=${STAGE}"
echo "dataset=${PROJECT_ID}.${DATASET_ID} sink=${SINK_NAME} job=${JOB_NAME}"
echo "scheduler=${SCHEDULER_REFRESH} schedule=${SCHEDULER_CRON} ttl_collections=${ADMIN_CHANGE_COLLECTION},${EXPORT_COLLECTION}"
if [[ "${APPLY}" != "true" ]]; then
  if [[ "${STAGE}" == "prepare" ]]; then
    echo "prepare=ttl,canonical_base_tables,logging_sink_writer"
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
  FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND (logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Frequests\" OR (logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Fstdout\" AND (jsonPayload.monitor_event=true OR textPayload=~\"(request_user_metric_json|stream_terminal_json)=\")) OR (logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Fstderr\" AND textPayload=~\"tmcs_stage_latency_json[ =]\"))"
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
for routine in dashboard_events dashboard_user_list; do
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show --routine \
    "${PROJECT_ID}:${DATASET_ID}.${routine}" >/dev/null || {
    echo "canonical activation prerequisite missing: ${PROJECT_ID}.${DATASET_ID}.${routine}" >&2; exit 2;
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
  --analytics-start-at "${ANALYTICS_START_AT}" \
  --project "${PROJECT_ID}" \
  --dataset "${DATASET_ID}" \
  --location "${LOCATION}" \
  --source-service "${SOURCE_SERVICE}"

require_paused_scheduler_if_present() {
  local name="$1" state
  if ! gcloud --project="${PROJECT_ID}" scheduler jobs describe "${name}" --location="${REGION}" >/dev/null 2>&1; then
    return 0
  fi
  state="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe "${name}" --location="${REGION}" --format='value(state)')"
  [[ "${state}" == "PAUSED" ]] || {
    echo "refusing to deploy refresh Job while scheduler ${name} is ${state}; freeze schedulers first" >&2
    exit 2
  }
}
require_paused_scheduler_if_present "${SCHEDULER_LEGACY}"
require_paused_scheduler_if_present "${SCHEDULER_REFRESH}"

gcloud --project="${PROJECT_ID}" run jobs deploy "${JOB_NAME}" \
  --region="${REGION}" --image="${IMAGE}" --service-account="${RUNTIME_SERVICE_ACCOUNT}" \
  --command=python --args=-m,app.jobs.refresh_analytics,--apply,--trigger-source,scheduler_three_hour \
  --env-vars-file="${RUNTIME_ENV}" \
  --tasks=1 --parallelism=1 --max-retries=1 \
  --task-timeout="${JOB_TIMEOUT_MINUTES}m"

DEPLOYED_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${DEPLOYED_JOB_JSON}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --expected-image "${IMAGE}" \
    --expected-service-account "${RUNTIME_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null

JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
upsert_scheduler() {
  local name="$1" schedule="$2"
  if gcloud --project="${PROJECT_ID}" scheduler jobs describe "${name}" --location="${REGION}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" scheduler jobs update http "${name}" --location="${REGION}" --schedule="${schedule}" --uri="${JOB_URI}" --http-method=POST --oauth-service-account-email="${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" --time-zone="${SCHEDULER_TIMEZONE}" --attempt-deadline="${SCHEDULER_ATTEMPT_DEADLINE_SECONDS}s" --max-retry-attempts="${SCHEDULER_MAX_RETRY_ATTEMPTS}"
  else
    # Cloud Scheduler cannot create a job directly in PAUSED state. Create it
    # on a valid calendar date whose next occurrence is over 24 hours away,
    # pause it, then install the real schedule while it remains paused.
    gcloud --project="${PROJECT_ID}" scheduler jobs create http "${name}" --location="${REGION}" --schedule="${SCHEDULER_BOOTSTRAP_CRON}" --uri="${JOB_URI}" --http-method=POST --oauth-service-account-email="${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" --time-zone="${SCHEDULER_TIMEZONE}" --attempt-deadline="${SCHEDULER_ATTEMPT_DEADLINE_SECONDS}s" --max-retry-attempts="${SCHEDULER_MAX_RETRY_ATTEMPTS}"
    gcloud --project="${PROJECT_ID}" scheduler jobs pause "${name}" --location="${REGION}"
    gcloud --project="${PROJECT_ID}" scheduler jobs update http "${name}" --location="${REGION}" --schedule="${schedule}" --uri="${JOB_URI}" --http-method=POST --oauth-service-account-email="${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" --time-zone="${SCHEDULER_TIMEZONE}" --attempt-deadline="${SCHEDULER_ATTEMPT_DEADLINE_SECONDS}s" --max-retry-attempts="${SCHEDULER_MAX_RETRY_ATTEMPTS}"
  fi
}
upsert_scheduler "${SCHEDULER_REFRESH}" "${SCHEDULER_CRON}"

SCHEDULER_READBACK="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe "${SCHEDULER_REFRESH}" \
  --location="${REGION}" --format=json)"
SCHEDULER_JSON="${SCHEDULER_READBACK}" python3 - \
  "${SCHEDULER_CRON}" "${SCHEDULER_TIMEZONE}" "${JOB_URI}" \
  "${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" "${SCHEDULER_ATTEMPT_DEADLINE_SECONDS}" \
  "${SCHEDULER_MAX_RETRY_ATTEMPTS}" <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["SCHEDULER_JSON"])
cron, timezone, uri, service_account, deadline, retries = sys.argv[1:]
actual = {
    "state": payload.get("state"),
    "schedule": payload.get("schedule"),
    "timezone": payload.get("timeZone"),
    "uri": (payload.get("httpTarget") or {}).get("uri"),
    "service_account": ((payload.get("httpTarget") or {}).get("oauthToken") or {}).get("serviceAccountEmail"),
    "deadline": str(payload.get("attemptDeadline") or ""),
    "retries": int((payload.get("retryConfig") or {}).get("retryCount") or 0),
}
expected = {
    "state": "PAUSED",
    "schedule": cron,
    "timezone": timezone,
    "uri": uri,
    "service_account": service_account,
    "deadline": f"{int(deadline)}s",
    "retries": int(retries),
}
if actual != expected:
    raise SystemExit(f"three-hour scheduler readback does not match governed policy: {actual}")
PY
echo "refresh_job_candidate=ready image=${IMAGE}"
echo "three_hour_scheduler=PAUSED schedule=${SCHEDULER_CRON} timezone=${SCHEDULER_TIMEZONE}"
