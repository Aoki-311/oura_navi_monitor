#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
REGION="us-central1"
SOURCE_SERVICE="lcs-rag-app"
TRANSFER_CONFIG=""
SNAPSHOT_OUTPUT=""
CANONICAL_START_AT=""
CONFIRM_PAUSE=""
EXPECTED_QUERY_SHA256=""
EXPECTED_DTS_SERVICE_ACCOUNT=""
EXPECTED_SCHEDULER_SERVICE_ACCOUNT=""
DEPENDENCY_RECEIPT=""
ACTIVATION_RECEIPT=""
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --transfer-config) TRANSFER_CONFIG="$2"; shift 2 ;;
    --snapshot-output) SNAPSHOT_OUTPUT="$2"; shift 2 ;;
    --canonical-start-at) CANONICAL_START_AT="$2"; shift 2 ;;
    --confirm-pause) CONFIRM_PAUSE="$2"; shift 2 ;;
    --expected-query-sha256) EXPECTED_QUERY_SHA256="$2"; shift 2 ;;
    --expected-dts-service-account) EXPECTED_DTS_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-scheduler-service-account) EXPECTED_SCHEDULER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --dependency-receipt) DEPENDENCY_RECEIPT="$2"; shift 2 ;;
    --activation-receipt) ACTIVATION_RECEIPT="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
if [[ "${TRANSFER_CONFIG}" =~ ^projects/[^/]+/locations/([^/]+)/transferConfigs/[^/]+$ ]]; then
  TRANSFER_RESOURCE_LOCATION="${BASH_REMATCH[1]}"
else
  echo "--transfer-config must be one exact full transferConfigs resource" >&2
  exit 2
fi
EXPECTED_TRANSFER_RESOURCE_LOCATION="$(printf '%s' "${LOCATION}" | tr '[:upper:]' '[:lower:]')"
ACTUAL_TRANSFER_RESOURCE_LOCATION="$(printf '%s' "${TRANSFER_RESOURCE_LOCATION}" | tr '[:upper:]' '[:lower:]')"
[[ "${ACTUAL_TRANSFER_RESOURCE_LOCATION}" == "${EXPECTED_TRANSFER_RESOURCE_LOCATION}" ]] || {
  echo "--transfer-config location ${TRANSFER_RESOURCE_LOCATION} does not match ${LOCATION}" >&2
  exit 2
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
SCHEDULER_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
SCHEDULER_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_cron)')"
SCHEDULER_TIMEZONE="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.timezone)')"
SCHEDULER_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_attempt_deadline_seconds)')"
SCHEDULER_MAX_RETRY_ATTEMPTS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_max_retry_attempts)')"
CADENCE_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.cadence_minutes)')"
STALE_AFTER_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.freshness_stale_after_minutes)')"
JOB_TIMEOUT_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')"
MIN_EXECUTION_SPAN_MINUTES="$((CADENCE_MINUTES * 2 - JOB_TIMEOUT_MINUTES))"
EXPECTED_JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
CANONICAL_START_AT_UTC=""
if [[ -n "${CANONICAL_START_AT}" ]]; then
  CANONICAL_START_AT_UTC="$(python3 - "${CANONICAL_START_AT}" <<'PY'
import sys
from datetime import datetime, timezone

try:
    value = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
except ValueError as exc:
    raise SystemExit("--canonical-start-at must be ISO-8601") from exc
if value.tzinfo is None:
    raise SystemExit("--canonical-start-at must include a timezone")
print(value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
PY
)"
fi
CONFIRM_START="${CANONICAL_START_AT_UTC:-<canonical-start-at>}"
REQUIRED_CONFIRM="${TRANSFER_CONFIG}:pause-after-canonical-3:${CONFIRM_START}"

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "legacy_transfer=${TRANSFER_CONFIG}"
echo "canonical_scheduler=${SCHEDULER_NAME} schedule=${SCHEDULER_CRON}"
echo "canonical_start_at=${CANONICAL_START_AT_UTC:-required-on-apply}"
echo "action=pause automatic scheduling only; retain transfer config and legacy tables"
echo "required_confirmation=${REQUIRED_CONFIRM}"

if rg -n \
  'monitor_answer_events|monitor_user_daily|monitor_system_hourly|monitor_dashboard_snapshots' \
  "${ROOT_DIR}/app/repositories" "${ROOT_DIR}/app/services" \
  "${ROOT_DIR}/app/routers" "${ROOT_DIR}/frontend" \
  "${ROOT_DIR}/sql/create_api_views.sql" "${ROOT_DIR}/sql/merge_incremental.sql"; then
  echo "runtime dependency on a legacy DTS table is still present" >&2
  exit 2
fi

if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ -n "${CANONICAL_START_AT_UTC}" ]] || {
  echo "--canonical-start-at is required on apply" >&2; exit 2;
}
[[ "${EXPECTED_QUERY_SHA256}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "--expected-query-sha256 is required on apply" >&2; exit 2;
}
[[ -n "${EXPECTED_DTS_SERVICE_ACCOUNT}" && -n "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "--expected-dts-service-account and --expected-scheduler-service-account are required on apply" >&2; exit 2;
}
[[ "${EXPECTED_DTS_SERVICE_ACCOUNT}" != "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "legacy DTS writer and canonical Scheduler invoker identities must be distinct" >&2; exit 2;
}
[[ -n "${DEPENDENCY_RECEIPT}" && -f "${DEPENDENCY_RECEIPT}" ]] || {
  echo "--dependency-receipt is required on apply" >&2; exit 2;
}
[[ -n "${ACTIVATION_RECEIPT}" && -f "${ACTIVATION_RECEIPT}" ]] || {
  echo "--activation-receipt is required on apply" >&2; exit 2;
}
[[ "${CONFIRM_PAUSE}" == "${REQUIRED_CONFIRM}" ]] || {
  echo "--confirm-pause must equal ${REQUIRED_CONFIRM}" >&2; exit 2;
}
[[ -n "${SNAPSHOT_OUTPUT}" ]] || { echo "--snapshot-output is required on apply" >&2; exit 2; }
[[ ! -e "${SNAPSHOT_OUTPUT}" ]] || { echo "snapshot output already exists" >&2; exit 2; }
[[ -d "$(dirname "${SNAPSHOT_OUTPUT}")" ]] || { echo "snapshot output parent does not exist" >&2; exit 2; }
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2; exit 2;
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }

query_legacy_table_inventory() {
  python3 - "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" <<'PY'
import json
import subprocess
import sys

project, dataset, location = sys.argv[1:]
tables = (
    "monitor_answer_events",
    "monitor_user_daily",
    "monitor_system_hourly",
    "monitor_dashboard_snapshots",
)
inventory = []
for table in tables:
    completed = subprocess.run(
        [
            "bq",
            f"--project_id={project}",
            f"--location={location}",
            "show",
            "--format=prettyjson",
            f"{project}:{dataset}.{table}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    reference = payload.get("tableReference") or {}
    if reference.get("tableId") != table:
        raise SystemExit("legacy table readback returned an unexpected table")
    if not payload.get("lastModifiedTime"):
        raise SystemExit("legacy table readback has no modification timestamp")
    inventory.append(
        {
            "table": table,
            "lastModifiedTime": str(payload.get("lastModifiedTime") or ""),
            "numRows": str(payload.get("numRows") or ""),
            "etag": str(payload.get("etag") or ""),
        }
    )
print(json.dumps(inventory, sort_keys=True))
PY
}

PROJECT_NUMBER="$(gcloud --project="${PROJECT_ID}" projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
TRANSFER_PROJECT="${TRANSFER_CONFIG#projects/}"
TRANSFER_PROJECT="${TRANSFER_PROJECT%%/*}"
[[ "${TRANSFER_PROJECT}" == "${PROJECT_ID}" || "${TRANSFER_PROJECT}" == "${PROJECT_NUMBER}" ]] || {
  echo "transfer config belongs to a different project" >&2; exit 2;
}

ACTIVATION_VALUES="$(python3 - "${ACTIVATION_RECEIPT}" "${PROJECT_ID}" \
  "${REGION}" "${DATASET_ID}" "${LOCATION}" "${SOURCE_SERVICE}" \
  "${JOB_NAME}" "${SCHEDULER_NAME}" "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" \
  "${CANONICAL_START_AT_UTC}" <<'PY'
import json
import re
import sys

path, project, region, dataset, location, source_service, job, scheduler, scheduler_service_account, canonical_start_at = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
expected = {
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "source_service": source_service,
    "job": job,
    "new_scheduler": scheduler,
    "expected_new_scheduler_service_account": scheduler_service_account,
    "canonical_start_at": canonical_start_at,
}
if any(receipt.get(key) != value for key, value in expected.items()):
    raise SystemExit("activation receipt does not match this DTS retirement")
image = str(receipt.get("image") or "")
job_service_account = str(receipt.get("expected_job_service_account") or "")
if not re.fullmatch(
    rf"{re.escape(region)}-docker\.pkg\.dev/{re.escape(project)}/[^/@]+/[^/@]+@sha256:[0-9a-f]{{64}}",
    image,
):
    raise SystemExit("activation receipt has no immutable Monitor image")
if not job_service_account:
    raise SystemExit("activation receipt has no refresh writer identity")
old = receipt.get("old_scheduler_readback") or {}
new = receipt.get("new_scheduler_readback") or {}
if old.get("state") != "PAUSED" or new.get("state") != "ENABLED":
    raise SystemExit("activation receipt does not contain the final Scheduler states")
if not receipt.get("freeze_snapshot_sha256") or not receipt.get("backfill_receipt_sha256"):
    raise SystemExit("activation receipt has no frozen provenance hashes")
print(json.dumps({"image": image, "jobServiceAccount": job_service_account}))
PY
)"
ACTIVATION_IMAGE="$(ACTIVATION_VALUES="${ACTIVATION_VALUES}" python3 -c 'import json,os; print(json.loads(os.environ["ACTIVATION_VALUES"])["image"])')"
ACTIVATION_JOB_SERVICE_ACCOUNT="$(ACTIVATION_VALUES="${ACTIVATION_VALUES}" python3 -c 'import json,os; print(json.loads(os.environ["ACTIVATION_VALUES"])["jobServiceAccount"])')"

python3 - "${DEPENDENCY_RECEIPT}" "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" \
  "${TRANSFER_CONFIG}" "${EXPECTED_QUERY_SHA256}" <<'PY'
import json
import sys

path, project, dataset, location, transfer, query_sha = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
expected = {
    "project": project,
    "dataset": dataset,
    "location": location,
    "transferConfig": transfer,
    "querySha256": query_sha,
}
if any(receipt.get(key) != value for key, value in expected.items()):
    raise SystemExit("dependency receipt does not match this DTS retirement")
for key in (
    "codeReferenceCount",
    "bigQueryObjectReferenceCount",
    "queryJobReferenceCount",
    "nonQueryReadReferenceCount",
    "unknownConsumerCount",
):
    if int(receipt.get(key, -1)) != 0:
        raise SystemExit(f"dependency receipt has unresolved {key}")
if receipt.get("dataAccessAuditCoverage") != "verified":
    raise SystemExit("dependency receipt lacks verified Data Access audit coverage")
if receipt.get("externalOwnerConfirmation") is not True:
    raise SystemExit("dependency receipt lacks external owner confirmation")
if int(receipt.get("lookbackDays") or 0) < 30:
    raise SystemExit("dependency receipt must cover at least 30 days")
if not receipt.get("capturedAt"):
    raise SystemExit("dependency receipt has no capture timestamp")
PY

TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show --transfer_config --format=prettyjson "${TRANSFER_CONFIG}")"
TRANSFER_VALIDATION_SUMMARY="$(TRANSFER_JSON="${TRANSFER_JSON}" python3 - \
  "${TRANSFER_CONFIG}" "${PROJECT_ID}" "${DATASET_ID}" "${EXPECTED_QUERY_SHA256}" \
  "${EXPECTED_DTS_SERVICE_ACCOUNT}" <<'PY'
import json
import hashlib
import os
import re
import sys

payload = json.loads(os.environ["TRANSFER_JSON"])
resource, project, dataset, expected_query_sha, expected_service_account = sys.argv[1:]
if payload.get("name") and payload.get("name") != resource:
    raise SystemExit("transfer config readback has an unexpected resource name")
if payload.get("dataSourceId") != "scheduled_query":
    raise SystemExit("transfer config is not a scheduled query")
if bool(payload.get("disabled")):
    raise SystemExit("transfer config is already disabled")
schedule = str(payload.get("schedule") or "").strip().lower()
if not (
    re.fullmatch(r"every\s+15\s+minutes?", schedule)
    or schedule == "*/15 * * * *"
):
    raise SystemExit("transfer config is not the legacy 15-minute schedule")
params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
if str(payload.get("displayName") or "") != "oura_navi_monitor_aggregate_refresh":
    raise SystemExit("transfer config display name is not the retired Monitor owner")
destination_dataset = str(payload.get("destinationDatasetId") or "")
if destination_dataset and destination_dataset != dataset:
    raise SystemExit("transfer config destination dataset is unexpected")
owner_info = payload.get("ownerInfo") if isinstance(payload.get("ownerInfo"), dict) else {}
actual_service_account = str(
    payload.get("serviceAccountName") or owner_info.get("email") or ""
)
if actual_service_account != expected_service_account:
    raise SystemExit("transfer config service account is unexpected")
query = str(params.get("query") or "")
actual_query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
if actual_query_sha != expected_query_sha:
    raise SystemExit("transfer config query hash changed after dependency review")
query_contract = " ".join(
    str(params.get(field) or "")
    for field in ("query", "destination_table_name_template")
).lower()
legacy_objects = (
    "monitor_answer_events",
    "monitor_user_daily",
    "monitor_system_hourly",
    "monitor_dashboard_snapshots",
)
missing = [
    name
    for name in legacy_objects
    if f"{project}.{dataset}.{name}" not in query_contract
]
if missing:
    raise SystemExit(
        "transfer config does not own every fully qualified retired Monitor table: "
        + ",".join(missing)
    )
print(f"schedule={schedule} legacy_owner=verified query_sha256={actual_query_sha}")
PY
)"
echo "legacy_transfer_gate=${TRANSFER_VALIDATION_SUMMARY}"

SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
SCHEDULER_SUMMARY="$(SCHEDULER_JSON="${SCHEDULER_JSON}" python3 - \
  "${SCHEDULER_CRON}" "${SCHEDULER_TIMEZONE}" "${EXPECTED_JOB_URI}" \
  "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" "${SCHEDULER_ATTEMPT_DEADLINE_SECONDS}" \
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
    "state": "ENABLED",
    "schedule": cron,
    "timezone": timezone,
    "uri": uri,
    "service_account": service_account,
    "deadline": f"{int(deadline)}s",
    "retries": int(retries),
}
if actual != expected:
    raise SystemExit(f"canonical three-hour scheduler does not match governed policy: {actual}")
print(
    f"{actual['state']},{actual['schedule']},{actual['timezone']},"
    f"{actual['uri']},{actual['service_account']},"
    f"{actual['deadline']},retries={actual['retries']}"
)
PY
)"

JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${JOB_JSON}" python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${ACTIVATION_IMAGE}" \
  --expected-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null

GATE_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
  --use_legacy_sql=false --format=json --quiet \
  --parameter="canonical_start_at:TIMESTAMP:${CANONICAL_START_AT_UTC}" \
  "SELECT
     run_id,
     execution_id,
     FORMAT_TIMESTAMP('%FT%TZ', started_at) AS started_at,
     FORMAT_TIMESTAMP('%FT%TZ', window_start) AS window_start,
     FORMAT_TIMESTAMP('%FT%TZ', window_end) AS window_end,
     TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), window_end, MINUTE) AS freshness_minutes
   FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_runs\`
   WHERE DATE(started_at) BETWEEN DATE(@canonical_start_at) AND CURRENT_DATE()
     AND started_at >= @canonical_start_at
     AND source = 'published'
     AND status = 'succeeded'
     AND trigger_source = 'scheduler_three_hour'
     AND NULLIF(execution_id, '') IS NOT NULL
     AND NOT STARTS_WITH(execution_id, 'local-')
   ORDER BY started_at")"

EXECUTIONS_JSON="$(gcloud --project="${PROJECT_ID}" run jobs executions list \
  --job="${JOB_NAME}" --region="${REGION}" --limit=100 --format=json)"
SCHEDULER_ATTEMPTS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${SCHEDULER_NAME}\" AND jsonPayload.@type=\"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\" AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"

GATE_SUMMARY="$(GATE_JSON="${GATE_JSON}" EXECUTIONS_JSON="${EXECUTIONS_JSON}" \
  SCHEDULER_NAME="${SCHEDULER_NAME}" \
  SCHEDULER_ATTEMPTS_JSON="${SCHEDULER_ATTEMPTS_JSON}" python3 - \
  "${MIN_EXECUTION_SPAN_MINUTES}" "${STALE_AFTER_MINUTES}" "${EXPECTED_JOB_URI}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

minimum_span = int(sys.argv[1])
stale_after = int(sys.argv[2])
expected_uri = sys.argv[3]
runs = json.loads(os.environ["GATE_JSON"])
executions = json.loads(os.environ["EXECUTIONS_JSON"])
attempts = json.loads(os.environ["SCHEDULER_ATTEMPTS_JSON"])
if not isinstance(runs, list) or not isinstance(executions, list) or not isinstance(attempts, list):
    raise SystemExit("canonical provenance inventory is not a list")

def parse(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)

execution_times = {}
for item in executions:
    name = str(item.get("name") or (item.get("metadata") or {}).get("name") or "")
    short = name.rsplit("/", 1)[-1]
    created = item.get("createTime") or (item.get("metadata") or {}).get("creationTimestamp")
    if short and created:
        execution_times[short] = parse(created)

attempt_times = []
for item in attempts:
    payload = item.get("jsonPayload") if isinstance(item.get("jsonPayload"), dict) else {}
    job_name = str(payload.get("jobName") or "")
    url = str(payload.get("url") or payload.get("targetUrl") or "")
    if job_name and not job_name.endswith("/" + os.environ.get("SCHEDULER_NAME", "")):
        continue
    if url and url != expected_uri:
        continue
    timestamp = item.get("timestamp") or item.get("receiveTimestamp")
    if timestamp:
        attempt_times.append(parse(timestamp))

if len(runs) < 3:
    raise SystemExit("three successful canonical runs are required")
run_execution_ids = {str(row.get("execution_id") or "").rsplit("/", 1)[-1] for row in runs}
window_ends = {str(row.get("window_end") or "") for row in runs}
if len(run_execution_ids) < 3 or len(window_ends) < 3:
    raise SystemExit("three successful canonical executions with distinct windows are required")
started = [parse(row["started_at"]) for row in runs]
span = int((max(started) - min(started)).total_seconds() // 60)
if span < minimum_span:
    raise SystemExit("canonical executions have not covered two governed cadence intervals")
freshness = min(int(row.get("freshness_minutes")) for row in runs if row.get("freshness_minutes") is not None)
if freshness < 0 or freshness > stale_after:
    raise SystemExit("canonical published watermark is not currently fresh")

matched = 0
for execution_id in run_execution_ids:
    created = execution_times.get(execution_id)
    if created is None:
        raise SystemExit("pipeline run has no matching Cloud Run execution: " + execution_id)
    if not any(abs((created - attempt).total_seconds()) <= 600 for attempt in attempt_times):
        raise SystemExit("Cloud Run execution has no matching Scheduler attempt: " + execution_id)
    matched += 1
if matched < 3:
    raise SystemExit("three Scheduler-proven executions are required")
latest = max(parse(row["window_end"]) for row in runs).isoformat().replace("+00:00", "Z")
print(
    f"runs={len(runs)} executions={len(run_execution_ids)} windows={len(window_ends)} "
    f"span_minutes={span} freshness_minutes={freshness} scheduler_proven={matched} "
    f"latest_window_end={latest}"
)
PY
)"
echo "canonical_dependency_gate=${GATE_SUMMARY}"

TRANSFER_RUNS_BEFORE="$(bq --project_id="${PROJECT_ID}" ls --transfer_run \
  --transfer_location="${LOCATION}" --format=prettyjson "${TRANSFER_CONFIG}")"
TRANSFER_RUNS_JSON="${TRANSFER_RUNS_BEFORE}" python3 - <<'PY'
import json
import os

runs = json.loads(os.environ["TRANSFER_RUNS_JSON"])
if not isinstance(runs, list):
    raise SystemExit("legacy DTS run inventory is not a list")
active = [
    str(item.get("name") or "unknown")
    for item in runs
    if str(item.get("state") or "").upper() in {"PENDING", "RUNNING"}
]
if active:
    raise SystemExit("legacy DTS still has in-flight runs: " + ",".join(active))
PY
LEGACY_TABLES_BEFORE="$(query_legacy_table_inventory)"

SNAPSHOT_TRANSFER_JSON="${TRANSFER_JSON}" SNAPSHOT_GATE_JSON="${GATE_JSON}" \
SNAPSHOT_SCHEDULER_STATE="${SCHEDULER_SUMMARY}" \
SNAPSHOT_EXECUTIONS_JSON="${EXECUTIONS_JSON}" \
SNAPSHOT_ATTEMPTS_JSON="${SCHEDULER_ATTEMPTS_JSON}" \
SNAPSHOT_TRANSFER_RUNS_JSON="${TRANSFER_RUNS_BEFORE}" \
SNAPSHOT_LEGACY_TABLES_JSON="${LEGACY_TABLES_BEFORE}" \
SNAPSHOT_ACTIVATION_RECEIPT="${ACTIVATION_RECEIPT}" \
SNAPSHOT_DEPENDENCY_RECEIPT="${DEPENDENCY_RECEIPT}" python3 - \
  "${SNAPSHOT_OUTPUT}" "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" "${REGION}" \
  "${TRANSFER_CONFIG}" "${CANONICAL_START_AT_UTC}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, project, dataset, location, region, transfer_config, canonical_start_at = sys.argv[1:]
payload = {
    "project": project,
    "dataset": dataset,
    "location": location,
    "region": region,
    "transfer_config_resource": transfer_config,
    "canonical_start_at": canonical_start_at,
    "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "transfer_config_before": json.loads(os.environ["SNAPSHOT_TRANSFER_JSON"]),
    "canonical_dependency_gate": json.loads(os.environ["SNAPSHOT_GATE_JSON"]),
    "canonical_scheduler_state": os.environ["SNAPSHOT_SCHEDULER_STATE"],
    "canonical_executions": json.loads(os.environ["SNAPSHOT_EXECUTIONS_JSON"]),
    "canonical_scheduler_attempts": json.loads(os.environ["SNAPSHOT_ATTEMPTS_JSON"]),
    "legacy_transfer_runs_before": json.loads(os.environ["SNAPSHOT_TRANSFER_RUNS_JSON"]),
    "legacy_tables_before": json.loads(os.environ["SNAPSHOT_LEGACY_TABLES_JSON"]),
}
with open(os.environ["SNAPSHOT_DEPENDENCY_RECEIPT"], encoding="utf-8") as handle:
    payload["dependency_receipt"] = json.load(handle)
with open(os.environ["SNAPSHOT_ACTIVATION_RECEIPT"], encoding="utf-8") as handle:
    payload["activation_receipt"] = json.load(handle)
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY

bq --project_id="${PROJECT_ID}" --location="${LOCATION}" update \
  --transfer_config --no_auto_scheduling "${TRANSFER_CONFIG}"

TRANSFER_AFTER="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${TRANSFER_CONFIG}")"
TRANSFER_BEFORE_JSON="${TRANSFER_JSON}" TRANSFER_AFTER_JSON="${TRANSFER_AFTER}" \
  python3 - <<'PY'
import json
import os

before = json.loads(os.environ["TRANSFER_BEFORE_JSON"])
after = json.loads(os.environ["TRANSFER_AFTER_JSON"])
if after.get("disabled") is not True:
    raise SystemExit("legacy DTS did not read back as disabled")
for key in (
    "name",
    "displayName",
    "dataSourceId",
    "destinationDatasetId",
    "schedule",
    "serviceAccountName",
    "ownerInfo",
    "userId",
    "params",
):
    if before.get(key) != after.get(key):
        raise SystemExit("legacy DTS changed beyond disabled state: " + key)
PY
TRANSFER_RUNS_AFTER="$(bq --project_id="${PROJECT_ID}" ls --transfer_run \
  --transfer_location="${LOCATION}" --format=prettyjson "${TRANSFER_CONFIG}")"
TRANSFER_RUNS_JSON="${TRANSFER_RUNS_AFTER}" python3 - <<'PY'
import json
import os

runs = json.loads(os.environ["TRANSFER_RUNS_JSON"])
if any(str(item.get("state") or "").upper() in {"PENDING", "RUNNING"} for item in runs):
    raise SystemExit("legacy DTS has an in-flight run after pause")
PY
LEGACY_TABLES_AFTER="$(query_legacy_table_inventory)"
LEGACY_TABLES_BEFORE_JSON="${LEGACY_TABLES_BEFORE}" \
LEGACY_TABLES_AFTER_JSON="${LEGACY_TABLES_AFTER}" python3 - <<'PY'
import json
import os

before = {item["table"]: item for item in json.loads(os.environ["LEGACY_TABLES_BEFORE_JSON"])}
after = {item["table"]: item for item in json.loads(os.environ["LEGACY_TABLES_AFTER_JSON"])}
if before.keys() != after.keys():
    raise SystemExit("legacy table inventory changed during DTS pause")
for table in before:
    for field in ("lastModifiedTime", "numRows"):
        if before[table].get(field) != after[table].get(field):
            raise SystemExit(f"legacy table {table} changed during DTS pause")
PY
SNAPSHOT_TRANSFER_AFTER_JSON="${TRANSFER_AFTER}" \
SNAPSHOT_TRANSFER_RUNS_AFTER_JSON="${TRANSFER_RUNS_AFTER}" \
SNAPSHOT_LEGACY_TABLES_AFTER_JSON="${LEGACY_TABLES_AFTER}" \
  python3 - "${SNAPSHOT_OUTPUT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["paused_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["transfer_config_after"] = json.loads(os.environ["SNAPSHOT_TRANSFER_AFTER_JSON"])
payload["legacy_transfer_runs_after"] = json.loads(
    os.environ["SNAPSHOT_TRANSFER_RUNS_AFTER_JSON"]
)
payload["legacy_tables_after"] = json.loads(
    os.environ["SNAPSHOT_LEGACY_TABLES_AFTER_JSON"]
)
temporary = f"{path}.tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PY
echo "legacy DTS automatic scheduling paused; config and legacy tables retained"
