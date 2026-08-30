#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
STAGE="freeze-old"
DATASET_ID="oura_navi_monitor"
LOCATION="US"
REGION="us-central1"
SOURCE_SERVICE="lcs-rag-app"
SNAPSHOT_OUTPUT=""
BACKFILL_RECEIPT=""
ACTIVATION_RECEIPT_OUTPUT=""
EXPECTED_JOB_SERVICE_ACCOUNT=""
EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT=""
EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT=""
CONFIRM_CUTOVER=""
APPLY="false"
CREDENTIAL_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2 ;;
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --snapshot-output) SNAPSHOT_OUTPUT="$2"; shift 2 ;;
    --backfill-receipt) BACKFILL_RECEIPT="$2"; shift 2 ;;
    --activation-receipt-output) ACTIVATION_RECEIPT_OUTPUT="$2"; shift 2 ;;
    --expected-job-service-account) EXPECTED_JOB_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-old-scheduler-service-account) EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-new-scheduler-service-account) EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --confirm-cutover) CONFIRM_CUTOVER="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
[[ "${STAGE}" == "freeze-old" || "${STAGE}" == "freeze" || "${STAGE}" == "activate" ]] || {
  echo "--stage must be freeze-old, freeze or activate" >&2; exit 2;
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
NEW_SCHEDULER="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
OLD_SCHEDULER="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_name)')"
NEW_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_cron)')"
TIMEZONE="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.timezone)')"
NEW_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_attempt_deadline_seconds)')"
OLD_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_attempt_deadline_seconds)')"
MAX_RETRY_ATTEMPTS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_max_retry_attempts)')"
STALE_AFTER_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.freshness_stale_after_minutes)')"
OLD_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_cron)')"
EXPECTED_JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
if [[ "${STAGE}" == "freeze-old" ]]; then
  REQUIRED_CONFIRM="projects/${PROJECT_ID}/locations/${REGION}/jobs/${OLD_SCHEDULER}:freeze-before-job-deploy"
  ACTION="pause the old scheduler before deploying a new refresh Job image"
elif [[ "${STAGE}" == "freeze" ]]; then
  REQUIRED_CONFIRM="projects/${PROJECT_ID}/locations/${REGION}/jobs/${OLD_SCHEDULER}:freeze-for-backfill"
  ACTION="pause both schedulers, then verify no refresh lease"
else
  REQUIRED_CONFIRM="projects/${PROJECT_ID}/locations/${REGION}/jobs/${NEW_SCHEDULER}:activate-after-backfill"
  ACTION="verify fresh backfill and no refresh lease, then resume new scheduler"
fi

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan) stage=${STAGE}"
echo "old_scheduler=${OLD_SCHEDULER} schedule=${OLD_CRON} desired_state=PAUSED"
if [[ "${STAGE}" == "activate" ]]; then NEW_DESIRED_STATE="ENABLED"; else NEW_DESIRED_STATE="PAUSED"; fi
echo "new_scheduler=${NEW_SCHEDULER} schedule=${NEW_CRON} timezone=${TIMEZONE} desired_state=${NEW_DESIRED_STATE}"
echo "action=${ACTION}"
echo "required_confirmation=${REQUIRED_CONFIRM}"

if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ -n "${EXPECTED_JOB_SERVICE_ACCOUNT}" && -n "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" && -n "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "all expected Job, old-Scheduler and new-Scheduler service accounts are required on apply" >&2; exit 2;
}
[[ "${EXPECTED_JOB_SERVICE_ACCOUNT}" != "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "refresh writer and new Scheduler invoker identities must be distinct" >&2; exit 2;
}
[[ "${CONFIRM_CUTOVER}" == "${REQUIRED_CONFIRM}" ]] || {
  echo "--confirm-cutover must equal ${REQUIRED_CONFIRM}" >&2; exit 2;
}
[[ -n "${SNAPSHOT_OUTPUT}" ]] || { echo "--snapshot-output is required on apply" >&2; exit 2; }
[[ -d "$(dirname "${SNAPSHOT_OUTPUT}")" ]] || { echo "snapshot output parent does not exist" >&2; exit 2; }
if [[ "${STAGE}" != "freeze-old" && ! -e "${SNAPSHOT_OUTPUT}" ]]; then
  echo "${STAGE} requires the snapshot created by the freeze-old stage" >&2; exit 2
fi
if [[ "${STAGE}" == "activate" ]]; then
  [[ -n "${ACTIVATION_RECEIPT_OUTPUT}" ]] || {
    echo "activate requires --activation-receipt-output" >&2; exit 2;
  }
  [[ -d "$(dirname "${ACTIVATION_RECEIPT_OUTPUT}")" ]] || {
    echo "activation receipt output parent does not exist" >&2; exit 2;
  }
fi
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"

describe_scheduler() {
  gcloud --project="${PROJECT_ID}" scheduler jobs describe "$1" \
    --location="${REGION}" --format=json
}

validate_schedulers() {
  local old_json="$1" new_json="$2"
  OLD_SCHEDULER_JSON="${old_json}" NEW_SCHEDULER_JSON="${new_json}" python3 - \
    "${OLD_CRON}" "${NEW_CRON}" "${TIMEZONE}" "${EXPECTED_JOB_URI}" \
    "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" \
    "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" \
    "${OLD_ATTEMPT_DEADLINE_SECONDS}" \
    "${NEW_ATTEMPT_DEADLINE_SECONDS}" "${MAX_RETRY_ATTEMPTS}" <<'PY'
import json
import os
import sys

old = json.loads(os.environ["OLD_SCHEDULER_JSON"])
new = json.loads(os.environ["NEW_SCHEDULER_JSON"])
old_cron, new_cron, timezone, expected_uri, expected_old_service_account, expected_new_service_account, old_deadline, new_deadline, expected_retries = sys.argv[1:]

def validate(payload, label, cron, deadline, expected_service_account):
    state = payload.get("state")
    if state not in {"ENABLED", "PAUSED"}:
        raise SystemExit(f"{label} scheduler has unexpected state: {state}")
    if payload.get("schedule") != cron:
        raise SystemExit(f"{label} scheduler has unexpected schedule")
    if payload.get("timeZone") != timezone:
        raise SystemExit(f"{label} scheduler has unexpected timezone")
    if (payload.get("httpTarget") or {}).get("uri") != expected_uri:
        raise SystemExit(f"{label} scheduler targets an unexpected Cloud Run Job")
    oauth = (payload.get("httpTarget") or {}).get("oauthToken") or {}
    if oauth.get("serviceAccountEmail") != expected_service_account:
        raise SystemExit(f"{label} scheduler uses an unexpected invoker identity")
    if str(payload.get("attemptDeadline") or "") != f"{int(deadline)}s":
        raise SystemExit(f"{label} scheduler has an unexpected attempt deadline")
    retries = int((payload.get("retryConfig") or {}).get("retryCount") or 0)
    if retries != int(expected_retries):
        raise SystemExit(f"{label} scheduler has an unexpected retry count")
    return state

print(
    f"{validate(old, 'old', old_cron, old_deadline, expected_old_service_account)},"
    f"{validate(new, 'new', new_cron, new_deadline, expected_new_service_account)}"
)
PY
}

validate_old_scheduler() {
  local old_json="$1"
  OLD_SCHEDULER_JSON="${old_json}" python3 - \
    "${OLD_CRON}" "${TIMEZONE}" "${EXPECTED_JOB_URI}" \
    "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" "${OLD_ATTEMPT_DEADLINE_SECONDS}" \
    "${MAX_RETRY_ATTEMPTS}" <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["OLD_SCHEDULER_JSON"])
cron, timezone, expected_uri, expected_service_account, deadline, expected_retries = sys.argv[1:]
state = payload.get("state")
if state not in {"ENABLED", "PAUSED"}:
    raise SystemExit(f"old scheduler has unexpected state: {state}")
if payload.get("schedule") != cron:
    raise SystemExit("old scheduler has unexpected schedule")
if payload.get("timeZone") != timezone:
    raise SystemExit("old scheduler has unexpected timezone")
if (payload.get("httpTarget") or {}).get("uri") != expected_uri:
    raise SystemExit("old scheduler targets an unexpected Cloud Run Job")
oauth = (payload.get("httpTarget") or {}).get("oauthToken") or {}
if oauth.get("serviceAccountEmail") != expected_service_account:
    raise SystemExit("old scheduler uses an unexpected invoker identity")
if str(payload.get("attemptDeadline") or "") != f"{int(deadline)}s":
    raise SystemExit("old scheduler has an unexpected attempt deadline")
retries = int((payload.get("retryConfig") or {}).get("retryCount") or 0)
if retries != int(expected_retries):
    raise SystemExit("old scheduler has an unexpected retry count")
print(state)
PY
}

query_execution_inventory() {
  gcloud --project="${PROJECT_ID}" run jobs executions list \
    --job="${JOB_NAME}" --region="${REGION}" --limit=100 --format=json
}

query_active_bigquery_writers() {
  local information_region
  information_region="$(printf '%s' "${LOCATION}" | tr '[:upper:]' '[:lower:]')"
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
    --use_legacy_sql=false --format=json --quiet \
    "SELECT job_id, state, statement_type, creation_time
     FROM \`${PROJECT_ID}.region-${information_region}.INFORMATION_SCHEMA.JOBS_BY_PROJECT\`
     WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
       AND state IN ('PENDING', 'RUNNING')
       AND statement_type IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT')
       AND REGEXP_CONTAINS(IFNULL(query, ''), r'${PROJECT_ID}[.:]${DATASET_ID}')"
}

validate_no_inflight_writers() {
  local executions_json="$1" bq_jobs_json="$2"
  EXECUTIONS_JSON="${executions_json}" ACTIVE_BQ_JOBS_JSON="${bq_jobs_json}" \
    python3 - <<'PY'
import json
import os

executions = json.loads(os.environ["EXECUTIONS_JSON"])
active = []
for item in executions if isinstance(executions, list) else []:
    status = item.get("status") if isinstance(item.get("status"), dict) else item
    terminal = bool(
        status.get("completionTime")
        or item.get("completionTime")
        or int(status.get("succeededCount") or 0) > 0
        or int(status.get("failedCount") or 0) > 0
        or int(status.get("cancelledCount") or 0) > 0
    )
    if not terminal:
        active.append(str(item.get("name") or (item.get("metadata") or {}).get("name") or "unknown"))
if active:
    raise SystemExit("refresh Job still has non-terminal executions: " + ",".join(active))
bq_jobs = json.loads(os.environ["ACTIVE_BQ_JOBS_JSON"])
if not isinstance(bq_jobs, list):
    raise SystemExit("BigQuery writer inventory is not a list")
if bq_jobs:
    names = ",".join(str(item.get("job_id") or "unknown") for item in bq_jobs)
    raise SystemExit("canonical BigQuery DML is still in flight: " + names)
print("job_executions=terminal bigquery_dml=idle")
PY
}

record_freeze_verification() {
  local executions_json="$1" bq_jobs_json="$2"
  SNAPSHOT_EXECUTIONS_JSON="${executions_json}" SNAPSHOT_BQ_JOBS_JSON="${bq_jobs_json}" \
    python3 - "${SNAPSHOT_OUTPUT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["freeze_verified_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["refresh_executions_at_freeze"] = json.loads(os.environ["SNAPSHOT_EXECUTIONS_JSON"])
payload["active_bigquery_writers_at_freeze"] = json.loads(os.environ["SNAPSHOT_BQ_JOBS_JSON"])
temporary = f"{path}.tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PY
}

invalidate_freeze_verification() {
  python3 - "${SNAPSHOT_OUTPUT}" <<'PY'
import json
import os
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
for key in (
    "freeze_verified_at",
    "refresh_executions_at_freeze",
    "active_bigquery_writers_at_freeze",
):
    payload.pop(key, None)
temporary = f"{path}.tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PY
}

query_pipeline_gate() {
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
    --use_legacy_sql=false --format=json --quiet \
    "SELECT
       source,
       status,
       data_through,
       TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), data_through, MINUTE) AS freshness_minutes,
       IF(
         NULLIF(lease_run_id, '') IS NOT NULL
           AND lease_expires_at > CURRENT_TIMESTAMP(),
         'true',
         'false'
       ) AS lease_active
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_state\`
     WHERE source = 'published'"
}

validate_pipeline_gate() {
  local gate_json="$1" gate_mode="$2"
  PIPELINE_GATE_JSON="${gate_json}" python3 - "${STALE_AFTER_MINUTES}" "${gate_mode}" <<'PY'
import json
import os
import sys

rows = json.loads(os.environ["PIPELINE_GATE_JSON"])
if not isinstance(rows, list) or len(rows) != 1:
    raise SystemExit("published pipeline gate must return exactly one row")
row = rows[0]
if row.get("source") != "published":
    raise SystemExit("published pipeline state is missing")
if str(row.get("lease_active", "")).lower() == "true":
    raise SystemExit("a refresh execution still owns the pipeline lease")
mode = sys.argv[2]
freshness = row.get("freshness_minutes")
if mode == "activate":
    if row.get("status") != "succeeded":
        raise SystemExit("published pipeline is not in a succeeded state")
    try:
        freshness = int(freshness)
    except (TypeError, ValueError) as exc:
        raise SystemExit("published pipeline freshness is missing") from exc
    if freshness < 0 or freshness > int(sys.argv[1]):
        raise SystemExit("published pipeline watermark is not fresh")
print(
    f"status={row.get('status')} data_through={row.get('data_through')} "
    f"freshness_minutes={freshness} lease_active=false"
)
PY
}

OLD_JSON="$(describe_scheduler "${OLD_SCHEDULER}")"
if [[ "${STAGE}" == "freeze-old" ]]; then
  NEW_JSON="null"
  OLD_STATE="$(validate_old_scheduler "${OLD_JSON}")"
  NEW_STATE="NOT_FOUND"
else
  NEW_JSON="$(describe_scheduler "${NEW_SCHEDULER}")"
  SCHEDULER_STATES="$(validate_schedulers "${OLD_JSON}" "${NEW_JSON}")"
  OLD_STATE="${SCHEDULER_STATES%%,*}"
  NEW_STATE="${SCHEDULER_STATES##*,}"
fi
GATE_JSON=""
if [[ "${STAGE}" != "activate" ]]; then
  GATE_JSON="$(query_pipeline_gate)"
  GATE_SUMMARY="$(validate_pipeline_gate "${GATE_JSON}" "${STAGE}")"
  echo "pre_cutover_gate=${GATE_SUMMARY}"
fi

if [[ -e "${SNAPSHOT_OUTPUT}" ]]; then
  FREEZE_STARTED_AT="$(python3 - "${SNAPSHOT_OUTPUT}" "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" "${LOCATION}" "${SOURCE_SERVICE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" "${OLD_SCHEDULER}" "${NEW_SCHEDULER}" <<'PY'
import json
import sys

path, project, region, dataset, location, source_service, job_service_account, old_service_account, new_service_account, old_name, new_name = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
expected = {
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "source_service": source_service,
    "expected_job_service_account": job_service_account,
    "expected_old_scheduler_service_account": old_service_account,
    "expected_new_scheduler_service_account": new_service_account,
    "old_scheduler": old_name,
    "new_scheduler": new_name,
}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit("existing cutover snapshot does not match this operation")
started_at = payload.get("freeze_started_at")
if not started_at:
    raise SystemExit("existing cutover snapshot has no freeze_started_at")
print(started_at)
PY
)"
  echo "snapshot=reusing ${SNAPSHOT_OUTPUT}"
else
  FREEZE_STARTED_AT="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))')"
  SNAPSHOT_OLD_JSON="${OLD_JSON}" SNAPSHOT_NEW_JSON="${NEW_JSON}" SNAPSHOT_GATE_JSON="${GATE_JSON}" \
    python3 - "${SNAPSHOT_OUTPUT}" "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" "${LOCATION}" "${SOURCE_SERVICE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" "${OLD_SCHEDULER}" "${NEW_SCHEDULER}" "${FREEZE_STARTED_AT}" <<'PY'
import json
import os
import sys

path, project, region, dataset, location, source_service, job_service_account, old_service_account, new_service_account, old_name, new_name, started_at = sys.argv[1:]
payload = {
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "source_service": source_service,
    "expected_job_service_account": job_service_account,
    "expected_old_scheduler_service_account": old_service_account,
    "expected_new_scheduler_service_account": new_service_account,
    "old_scheduler": old_name,
    "new_scheduler": new_name,
    "freeze_started_at": started_at,
    "old_scheduler_before": json.loads(os.environ["SNAPSHOT_OLD_JSON"]),
    "new_scheduler_before": json.loads(os.environ["SNAPSHOT_NEW_JSON"]),
    "pipeline_gate_before": json.loads(os.environ["SNAPSHOT_GATE_JSON"]),
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
  echo "snapshot=created ${SNAPSHOT_OUTPUT}"
fi

if [[ "${STAGE}" == "freeze-old" ]]; then
  if [[ "${OLD_STATE}" == "ENABLED" ]]; then
    gcloud --project="${PROJECT_ID}" scheduler jobs pause "${OLD_SCHEDULER}" --location="${REGION}"
    echo "old_scheduler_state=PAUSED"
  else
    echo "old_scheduler_state=already_PAUSED"
  fi

  # The old scheduler can start once between the first gate and pause call.
  # Leave it paused and require a safe retry if that execution owns the lease.
  invalidate_freeze_verification
  POST_PAUSE_GATE_JSON="$(query_pipeline_gate)"
  POST_PAUSE_GATE_SUMMARY="$(validate_pipeline_gate "${POST_PAUSE_GATE_JSON}" "freeze")"
  echo "post_pause_gate=${POST_PAUSE_GATE_SUMMARY}"
  EXECUTIONS_JSON="$(query_execution_inventory)"
  ACTIVE_BQ_JOBS_JSON="$(query_active_bigquery_writers)"
  WRITER_QUIESCENCE="$(validate_no_inflight_writers "${EXECUTIONS_JSON}" "${ACTIVE_BQ_JOBS_JSON}")"
  echo "writer_quiescence=${WRITER_QUIESCENCE}"
  OLD_AFTER="$(describe_scheduler "${OLD_SCHEDULER}")"
  OLD_AFTER_STATE="$(validate_old_scheduler "${OLD_AFTER}")"
  [[ "${OLD_AFTER_STATE}" == "PAUSED" ]] || {
    echo "old scheduler freeze readback failed: ${OLD_AFTER_STATE}" >&2; exit 2;
  }
  record_freeze_verification "${EXECUTIONS_JSON}" "${ACTIVE_BQ_JOBS_JSON}"
  echo "legacy_scheduler_freeze=complete old=PAUSED"
  echo "next_gate=deploy the refresh Job and create the new scheduler in PAUSED state"
  exit 0
fi

if [[ "${STAGE}" == "freeze" ]]; then
  if [[ "${NEW_STATE}" == "ENABLED" ]]; then
    gcloud --project="${PROJECT_ID}" scheduler jobs pause "${NEW_SCHEDULER}" --location="${REGION}"
    echo "new_scheduler_state=PAUSED"
  else
    echo "new_scheduler_state=already_PAUSED"
  fi
  if [[ "${OLD_STATE}" == "ENABLED" ]]; then
    gcloud --project="${PROJECT_ID}" scheduler jobs pause "${OLD_SCHEDULER}" --location="${REGION}"
    echo "old_scheduler_state=PAUSED"
  else
    echo "old_scheduler_state=already_PAUSED"
  fi

  # Close the race where an execution started between the first gate and pause.
  # A retry is safe and reuses the same pre-change snapshot.
  invalidate_freeze_verification
  POST_PAUSE_GATE_JSON="$(query_pipeline_gate)"
  POST_PAUSE_GATE_SUMMARY="$(validate_pipeline_gate "${POST_PAUSE_GATE_JSON}" "freeze")"
  echo "post_pause_gate=${POST_PAUSE_GATE_SUMMARY}"
  EXECUTIONS_JSON="$(query_execution_inventory)"
  ACTIVE_BQ_JOBS_JSON="$(query_active_bigquery_writers)"
  WRITER_QUIESCENCE="$(validate_no_inflight_writers "${EXECUTIONS_JSON}" "${ACTIVE_BQ_JOBS_JSON}")"
  echo "writer_quiescence=${WRITER_QUIESCENCE}"

  OLD_AFTER="$(describe_scheduler "${OLD_SCHEDULER}")"
  NEW_AFTER="$(describe_scheduler "${NEW_SCHEDULER}")"
  AFTER_STATES="$(validate_schedulers "${OLD_AFTER}" "${NEW_AFTER}")"
  [[ "${AFTER_STATES}" == "PAUSED,PAUSED" ]] || {
    echo "scheduler freeze readback failed: ${AFTER_STATES}" >&2; exit 2;
  }
  record_freeze_verification "${EXECUTIONS_JSON}" "${ACTIVE_BQ_JOBS_JSON}"
  echo "scheduler_freeze=complete old=PAUSED new=PAUSED"
  echo "next_gate=run controlled backfill and validate the published watermark"
  exit 0
fi

[[ "${OLD_STATE}" == "PAUSED" ]] || {
  echo "old scheduler must remain paused from the freeze stage" >&2; exit 2;
}
[[ -n "${BACKFILL_RECEIPT}" && -f "${BACKFILL_RECEIPT}" ]] || {
  echo "activate requires --backfill-receipt from the controlled frozen backfill" >&2
  exit 2
}

BACKFILL_IMAGE="$(python3 - "${BACKFILL_RECEIPT}" "${SNAPSHOT_OUTPUT}" \
  "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" "${LOCATION}" \
  "${SOURCE_SERVICE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" \
  "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" "${JOB_NAME}" <<'PY'
import json
import sys

receipt_path, snapshot_path, project, region, dataset, location, source_service, job_service_account, old_service_account, new_service_account, job_name = sys.argv[1:]
with open(receipt_path, encoding="utf-8") as handle:
    receipt = json.load(handle)
with open(snapshot_path, encoding="utf-8") as handle:
    snapshot = json.load(handle)
if (
    receipt.get("project") != project
    or receipt.get("region") != region
    or receipt.get("dataset") != dataset
    or receipt.get("location") != location
    or receipt.get("source_service") != source_service
    or receipt.get("expected_job_service_account") != job_service_account
    or receipt.get("expected_old_scheduler_service_account") != old_service_account
    or receipt.get("expected_new_scheduler_service_account") != new_service_account
    or snapshot.get("dataset") != dataset
    or snapshot.get("location") != location
    or snapshot.get("source_service") != source_service
    or snapshot.get("expected_job_service_account") != job_service_account
    or snapshot.get("expected_old_scheduler_service_account") != old_service_account
    or snapshot.get("expected_new_scheduler_service_account") != new_service_account
):
    raise SystemExit("backfill receipt target does not match scheduler activation")
if receipt.get("job") != job_name:
    raise SystemExit("backfill receipt names an unexpected refresh Job")
receipt_freeze = receipt.get("freeze_snapshot") or {}
for field in ("project", "region", "old_scheduler", "new_scheduler", "freeze_started_at"):
    if receipt_freeze.get(field) != snapshot.get(field):
        raise SystemExit("backfill receipt does not belong to this freeze snapshot")
execution = receipt.get("execution") or {}
status = execution.get("status") if isinstance(execution.get("status"), dict) else execution
if int(status.get("succeededCount") or 0) < 1 or int(status.get("failedCount") or 0) != 0:
    raise SystemExit("backfill receipt does not contain a successful execution")
pipeline_after = receipt.get("pipeline_after") or []
if len(pipeline_after) != 1:
    raise SystemExit("backfill receipt has no single published state")
published = pipeline_after[0]
if published.get("status") != "succeeded" or not published.get("published_run_id"):
    raise SystemExit("backfill receipt has no atomic published run")
if str(published.get("lease_active") or "").lower() == "true":
    raise SystemExit("backfill receipt still has an active lease")
image = str(receipt.get("expected_image") or "")
if "@sha256:" not in image:
    raise SystemExit("backfill receipt image is not immutable")
contract = receipt.get("validated_job_contract") or {}
if contract.get("image") != image or contract.get("serviceAccount") != job_service_account:
    raise SystemExit("backfill receipt does not contain the approved Job contract")
execution_contract = receipt.get("validated_execution_provenance") or {}
if (
    execution_contract.get("image") != image
    or execution_contract.get("serviceAccount") != job_service_account
    or int(execution_contract.get("succeededCount") or 0) < 1
    or int(execution_contract.get("failedCount") or 0) != 0
):
    raise SystemExit("backfill receipt does not contain the approved execution provenance")
deploy_receipt_sha = str(receipt.get("job_deploy_receipt_sha256") or "")
if len(deploy_receipt_sha) != 64 or any(
    character not in "0123456789abcdef" for character in deploy_receipt_sha
):
    raise SystemExit("backfill receipt has no immutable Job deploy receipt provenance")
environment = contract.get("environment") or {}
expected_environment = {
    "MONITOR_PROJECT_ID": project,
    "MONITOR_BQ_DATASET": dataset,
    "MONITOR_BQ_LOCATION": location,
    "MONITOR_SOURCE_SERVICE": source_service,
}
if any(environment.get(key) != value for key, value in expected_environment.items()):
    raise SystemExit("backfill Job wrote a different project or dataset")
reconciliation = receipt.get("reconciliation") or []
if not isinstance(reconciliation, list) or len(reconciliation) != 1:
    raise SystemExit("backfill receipt has no source-to-fact reconciliation")
audit = reconciliation[0]
def count(name):
    try:
        return int(audit.get(name) or 0)
    except (TypeError, ValueError) as exc:
        raise SystemExit("backfill reconciliation has invalid counts") from exc
if count("successful_run_count") < 1 or count("blocking_failure_count") != 0:
    raise SystemExit("backfill reconciliation did not close the canonical publish")
for family in ("question", "answer", "action"):
    if count(f"canonical_{family}_count") != count(f"matched_{family}_count"):
        raise SystemExit("backfill receipt has an unmatched " + family + " manifest")
print(image)
PY
)"

ACTIVATION_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
ACTIVATION_JOB_CONTRACT="$(JOB_DESCRIPTION_JSON="${ACTIVATION_JOB_JSON}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --expected-image "${BACKFILL_IMAGE}" \
    --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')")"
echo "backfill_receipt=verified image=${BACKFILL_IMAGE}"

ACTIVATION_STATE_ARGS=(
  --path "${ACTIVATION_RECEIPT_OUTPUT}"
  --snapshot "${SNAPSHOT_OUTPUT}"
  --backfill-receipt "${BACKFILL_RECEIPT}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --dataset "${DATASET_ID}"
  --location "${LOCATION}"
  --source-service "${SOURCE_SERVICE}"
  --job "${JOB_NAME}"
  --old-scheduler "${OLD_SCHEDULER}"
  --new-scheduler "${NEW_SCHEDULER}"
  --expected-job-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}"
  --expected-old-scheduler-service-account "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}"
  --expected-new-scheduler-service-account "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}"
  --image "${BACKFILL_IMAGE}"
)

ACTIVATION_STATE_OLD_JSON="${OLD_JSON}"
ACTIVATION_STATE_NEW_JSON="${NEW_JSON}"
ACTIVATION_STATE_JOB_JSON="${ACTIVATION_JOB_JSON}"
ACTIVATION_STATE_JOB_CONTRACT="${ACTIVATION_JOB_CONTRACT}"
ACTIVATION_GATE_JSON=""

run_activation_state() {
  CURRENT_OLD_SCHEDULER_JSON="${ACTIVATION_STATE_OLD_JSON}" \
  CURRENT_NEW_SCHEDULER_JSON="${ACTIVATION_STATE_NEW_JSON}" \
  CURRENT_JOB_JSON="${ACTIVATION_STATE_JOB_JSON}" \
  CURRENT_JOB_CONTRACT="${ACTIVATION_STATE_JOB_CONTRACT}" \
  ACTIVATION_PIPELINE_GATE_JSON="${ACTIVATION_GATE_JSON}" \
    python3 "${ROOT_DIR}/scripts/scheduler_activation_receipt_state.py" \
      "$@" "${ACTIVATION_STATE_ARGS[@]}"
}

# Publish and fsync the exact intent before any resume mutation. If the live
# Scheduler is already ENABLED, only an existing exact intent/final may own it.
if [[ ! -e "${ACTIVATION_RECEIPT_OUTPUT}" && "${NEW_STATE}" == "PAUSED" ]]; then
  ACTIVATION_GATE_JSON="$(query_pipeline_gate)"
  validate_pipeline_gate "${ACTIVATION_GATE_JSON}" "activate" >/dev/null
fi
ACTIVATION_PREPARED="$(run_activation_state prepare)"
ACTIVATION_STATE="$(ACTIVATION_PREPARED="${ACTIVATION_PREPARED}" python3 -c \
  'import json,os; print(json.loads(os.environ["ACTIVATION_PREPARED"])["state"])')"
ACTIVATION_STARTED_AT="$(ACTIVATION_PREPARED="${ACTIVATION_PREPARED}" python3 -c \
  'import json,os; print(json.loads(os.environ["ACTIVATION_PREPARED"])["canonical_start_at"])')"

IFS=$'\t' read -r ACTIVATION_LOCK_INTENT ACTIVATION_LOCK_STATIC ACTIVATION_LOCK_TARGET <<< "$(python3 - "${ACTIVATION_RECEIPT_OUTPUT}" <<'PY'
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
intent = state.get("lock_intent_payload_sha256") or state.get("state_payload_sha256")
if not isinstance(intent, str) or len(intent) != 64:
    raise SystemExit("scheduler activation receipt has no stable lock intent")
static = {
    key: state.get(key)
    for key in (
        "project", "region", "job", "old_scheduler", "new_scheduler", "image",
        "backfill_receipt_sha256", "expected_job_service_account",
        "expected_new_scheduler_service_account",
    )
}
canonical = json.dumps(static, separators=(",", ":"), sort_keys=True)
print("\t".join((
    intent,
    hashlib.sha256(canonical.encode()).hexdigest(),
    f'{state["job"]}|{state["old_scheduler"]}|{state["new_scheduler"]}',
)))
PY
)"
ACTIVATION_LOCK_ARGS=(
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --resource-key "refresh-chain:${JOB_NAME}:${OLD_SCHEDULER}:${NEW_SCHEDULER}"
  --operation-kind scheduler-activation
  --target-key "${ACTIVATION_LOCK_TARGET}"
  --intent-payload-sha256 "${ACTIVATION_LOCK_INTENT}"
  --static-contract-sha256 "${ACTIVATION_LOCK_STATIC}"
  --firestore-database lcs-user-data
  --release-lock-collection monitor_release_locks
)
python3 "${ROOT_DIR}/scripts/release_operation_lock.py" acquire \
  --credential-file "${CREDENTIAL_FILE}" \
  "${ACTIVATION_LOCK_ARGS[@]}" >/dev/null

# The local intent is not authority. Once the global CAS is held, re-read every
# live contract that can make the resume unsafe and reclassify the exact intent.
ACTIVATION_STATE_OLD_JSON="$(describe_scheduler "${OLD_SCHEDULER}")"
ACTIVATION_STATE_NEW_JSON="$(describe_scheduler "${NEW_SCHEDULER}")"
ACTIVATION_STATE_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
ACTIVATION_STATE_JOB_CONTRACT="$(JOB_DESCRIPTION_JSON="${ACTIVATION_STATE_JOB_JSON}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --expected-image "${BACKFILL_IMAGE}" \
    --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')")"
ACTIVATION_GATE_JSON="$(query_pipeline_gate)"
validate_pipeline_gate "${ACTIVATION_GATE_JSON}" "activate" >/dev/null
ACTIVATION_PREPARED="$(run_activation_state prepare)"
ACTIVATION_STATE="$(ACTIVATION_PREPARED="${ACTIVATION_PREPARED}" python3 -c \
  'import json,os; print(json.loads(os.environ["ACTIVATION_PREPARED"])["state"])')"

if [[ "${ACTIVATION_STATE}" == "final" ]]; then
  python3 "${ROOT_DIR}/scripts/release_operation_lock.py" release \
    --credential-file "${CREDENTIAL_FILE}" \
    "${ACTIVATION_LOCK_ARGS[@]}" >/dev/null
  echo "scheduler_activation=already_complete old=PAUSED new=ENABLED"
  echo "canonical_start_at=${ACTIVATION_STARTED_AT}"
  echo "activation_receipt=${ACTIVATION_RECEIPT_OUTPUT}"
  echo "next_gate=wait for three distinct canonical executions before pausing legacy BigQuery DTS"
  exit 0
fi

RESUME_RETURN_CODE=""
if [[ "${ACTIVATION_STATE}" == "pre" ]]; then
  # Re-read the gate immediately before mutation. A previously published intent
  # is resumable only while its pre-state remains safe.
  ACTIVATION_GATE_JSON="$(query_pipeline_gate)"
  GATE_SUMMARY="$(validate_pipeline_gate "${ACTIVATION_GATE_JSON}" "activate")"
  echo "pre_cutover_gate=${GATE_SUMMARY}"
  set +e
  gcloud --project="${PROJECT_ID}" scheduler jobs resume "${NEW_SCHEDULER}" --location="${REGION}"
  RESUME_RETURN_CODE="$?"
  set -e
  echo "resume_command_return_code=${RESUME_RETURN_CODE}"
else
  [[ "${ACTIVATION_STATE}" == "post" ]] || {
    echo "unexpected activation receipt state: ${ACTIVATION_STATE}" >&2; exit 2;
  }
  echo "new_scheduler_state=recovering_ENABLED_from_exact_intent"
fi

OLD_AFTER="$(describe_scheduler "${OLD_SCHEDULER}")"
NEW_AFTER="$(describe_scheduler "${NEW_SCHEDULER}")"
AFTER_STATES="$(validate_schedulers "${OLD_AFTER}" "${NEW_AFTER}")"
[[ "${AFTER_STATES}" == "PAUSED,ENABLED" ]] || {
  echo "scheduler activation readback failed: ${AFTER_STATES} resume_rc=${RESUME_RETURN_CODE:-not_observed}" >&2
  exit 2
}

# Re-read Job and gate after the mutation as well. Finalization is forbidden if
# a sibling deployment or refresh changed either contract during the resume.
ACTIVATION_JOB_AFTER="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
ACTIVATION_JOB_CONTRACT_AFTER="$(JOB_DESCRIPTION_JSON="${ACTIVATION_JOB_AFTER}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --expected-image "${BACKFILL_IMAGE}" \
    --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')")"
ACTIVATION_GATE_AFTER="$(query_pipeline_gate)"
validate_pipeline_gate "${ACTIVATION_GATE_AFTER}" "activate" >/dev/null

ACTIVATION_STATE_OLD_JSON="${OLD_AFTER}"
ACTIVATION_STATE_NEW_JSON="${NEW_AFTER}"
ACTIVATION_STATE_JOB_JSON="${ACTIVATION_JOB_AFTER}"
ACTIVATION_STATE_JOB_CONTRACT="${ACTIVATION_JOB_CONTRACT_AFTER}"
ACTIVATION_GATE_JSON="${ACTIVATION_GATE_AFTER}"
FINALIZE_ARGUMENTS=(finalize)
if [[ -n "${RESUME_RETURN_CODE}" ]]; then
  FINALIZE_ARGUMENTS+=(--resume-command-return-code "${RESUME_RETURN_CODE}")
fi
run_activation_state "${FINALIZE_ARGUMENTS[@]}" >/dev/null
python3 "${ROOT_DIR}/scripts/release_operation_lock.py" release \
  --credential-file "${CREDENTIAL_FILE}" \
  "${ACTIVATION_LOCK_ARGS[@]}" >/dev/null

echo "scheduler_activation=complete old=PAUSED new=ENABLED"
echo "canonical_start_at=${ACTIVATION_STARTED_AT}"
echo "activation_receipt=${ACTIVATION_RECEIPT_OUTPUT}"
echo "next_gate=wait for three distinct canonical executions before pausing legacy BigQuery DTS"
