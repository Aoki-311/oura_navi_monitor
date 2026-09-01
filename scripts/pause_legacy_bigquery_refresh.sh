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
PREFLIGHT_RECEIPT=""
PREFLIGHT_RECEIPT_OUTPUT=""
PREFLIGHT="false"
APPLY="false"
CREDENTIAL_FILE=""

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
    --preflight-receipt) PREFLIGHT_RECEIPT="$2"; shift 2 ;;
    --preflight-receipt-output) PREFLIGHT_RECEIPT_OUTPUT="$2"; shift 2 ;;
    --preflight) PREFLIGHT="true"; shift ;;
    --apply) APPLY="true"; shift ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
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
OLD_SCHEDULER_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_name)')"
SCHEDULER_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_cron)')"
SCHEDULER_TRIGGER_SOURCE="scheduler_hourly"
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

[[ "${APPLY}" != "true" || "${PREFLIGHT}" != "true" ]] || {
  echo "--preflight and --apply are mutually exclusive" >&2; exit 2;
}
if [[ "${APPLY}" == "true" ]]; then
  MODE="apply"
elif [[ "${PREFLIGHT}" == "true" ]]; then
  MODE="preflight"
else
  MODE="plan"
fi
echo "mode=${MODE}"
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

if [[ "${APPLY}" != "true" && "${PREFLIGHT}" != "true" ]]; then exit 0; fi
[[ -n "${CANONICAL_START_AT_UTC}" ]] || {
  echo "--canonical-start-at is required on preflight/apply" >&2; exit 2;
}
[[ "${EXPECTED_QUERY_SHA256}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "--expected-query-sha256 is required on preflight/apply" >&2; exit 2;
}
[[ -n "${EXPECTED_DTS_SERVICE_ACCOUNT}" && -n "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "--expected-dts-service-account and --expected-scheduler-service-account are required on preflight/apply" >&2; exit 2;
}
[[ "${EXPECTED_DTS_SERVICE_ACCOUNT}" != "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "legacy DTS writer and canonical Scheduler invoker identities must be distinct" >&2; exit 2;
}
[[ -n "${DEPENDENCY_RECEIPT}" && -f "${DEPENDENCY_RECEIPT}" ]] || {
  echo "--dependency-receipt is required on preflight/apply" >&2; exit 2;
}
[[ -n "${ACTIVATION_RECEIPT}" && -f "${ACTIVATION_RECEIPT}" ]] || {
  echo "--activation-receipt is required on preflight/apply" >&2; exit 2;
}
if [[ "${PREFLIGHT}" == "true" ]]; then
  [[ -n "${PREFLIGHT_RECEIPT_OUTPUT}" ]] || {
    echo "--preflight-receipt-output is required on preflight" >&2; exit 2;
  }
  [[ ! -e "${PREFLIGHT_RECEIPT_OUTPUT}" ]] || {
    echo "preflight receipt output already exists" >&2; exit 2;
  }
  [[ -d "$(dirname "${PREFLIGHT_RECEIPT_OUTPUT}")" ]] || {
    echo "preflight receipt output parent does not exist" >&2; exit 2;
  }
else
  [[ "${CONFIRM_PAUSE}" == "${REQUIRED_CONFIRM}" ]] || {
    echo "--confirm-pause must equal ${REQUIRED_CONFIRM}" >&2; exit 2;
  }
  [[ -n "${PREFLIGHT_RECEIPT}" && -f "${PREFLIGHT_RECEIPT}" ]] || {
    echo "--preflight-receipt is required on apply" >&2; exit 2;
  }
  [[ -n "${SNAPSHOT_OUTPUT}" ]] || { echo "--snapshot-output is required on apply" >&2; exit 2; }
  [[ -d "$(dirname "${SNAPSHOT_OUTPUT}")" ]] || { echo "snapshot output parent does not exist" >&2; exit 2; }
fi
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"

query_legacy_table_inventory() {
  CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${CREDENTIAL_FILE}" \
  GOOGLE_APPLICATION_CREDENTIALS="${CREDENTIAL_FILE}" \
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
if not isinstance(payload.get("disabled"), bool):
    raise SystemExit("transfer config disabled state is not a boolean")
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
TRANSFER_DISABLED="$(TRANSFER_JSON="${TRANSFER_JSON}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["TRANSFER_JSON"])
print("true" if payload["disabled"] is True else "false")
PY
)"

classify_pause_state() {
  CURRENT_TRANSFER_JSON="$1" python3 "${ROOT_DIR}/scripts/dts_pause_receipt_state.py" \
    --path "${SNAPSHOT_OUTPUT}" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --location "${LOCATION}" \
    --region "${REGION}" \
    --transfer-config "${TRANSFER_CONFIG}" \
    --canonical-start-at "${CANONICAL_START_AT_UTC}" \
    --expected-query-sha256 "${EXPECTED_QUERY_SHA256}" \
    --expected-dts-service-account "${EXPECTED_DTS_SERVICE_ACCOUNT}" \
    --expected-scheduler-service-account "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" \
    --activation-image "${ACTIVATION_IMAGE}" \
    --activation-job-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
    --dependency-receipt "${DEPENDENCY_RECEIPT}" \
    --activation-receipt "${ACTIVATION_RECEIPT}" \
    --preflight-receipt "${PREFLIGHT_RECEIPT}"
}

SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
validate_scheduler_json() {
  SCHEDULER_JSON="$1" python3 - \
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
    raise SystemExit(f"canonical hourly scheduler does not match governed policy: {actual}")
print(
    f"{actual['state']},{actual['schedule']},{actual['timezone']},"
    f"{actual['uri']},{actual['service_account']},"
    f"{actual['deadline']},retries={actual['retries']}"
)
PY
}
SCHEDULER_SUMMARY="$(validate_scheduler_json "${SCHEDULER_JSON}")"

JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${JOB_JSON}" python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${ACTIVATION_IMAGE}" \
  --expected-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null

query_canonical_gate() {
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
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
     AND trigger_source = '${SCHEDULER_TRIGGER_SOURCE}'
     AND NULLIF(execution_id, '') IS NOT NULL
     AND NOT STARTS_WITH(execution_id, 'local-')
   ORDER BY started_at"
}
GATE_JSON="$(query_canonical_gate)"

EXECUTIONS_JSON="$(gcloud --project="${PROJECT_ID}" run jobs executions list \
  --job="${JOB_NAME}" --region="${REGION}" --limit=100 --format=json)"
SCHEDULER_ATTEMPTS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${SCHEDULER_NAME}\" AND jsonPayload.\"@type\"=\"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\" AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"
RUN_JOB_AUDITS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "protoPayload.serviceName=\"run.googleapis.com\" AND (protoPayload.methodName=\"/Jobs.RunJob\" OR protoPayload.methodName=\"google.cloud.run.v2.Jobs.RunJob\") AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"

validate_execution_gate() {
  python3 "${ROOT_DIR}/scripts/validate_scheduler_execution_gate.py" \
    --runs <(printf '%s' "${GATE_JSON}") \
    --executions <(printf '%s' "${EXECUTIONS_JSON}") \
    --attempts <(printf '%s' "${SCHEDULER_ATTEMPTS_JSON}") \
    --audits <(printf '%s' "${RUN_JOB_AUDITS_JSON}") \
    --minimum-span-minutes "${MIN_EXECUTION_SPAN_MINUTES}" \
    --stale-after-minutes "${STALE_AFTER_MINUTES}" \
    --expected-job-uri "${EXPECTED_JOB_URI}" \
    --expected-image "${ACTIVATION_IMAGE}" \
    --expected-job-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" --region "${REGION}" --job "${JOB_NAME}" \
    --scheduler "${SCHEDULER_NAME}" \
    --scheduler-service-account "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}"
}
GATE_SUMMARY="$(validate_execution_gate)"
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

if [[ "${PREFLIGHT}" == "true" ]]; then
  PREFLIGHT_TRANSFER_JSON="${TRANSFER_JSON}" \
  PREFLIGHT_SCHEDULER_JSON="${SCHEDULER_JSON}" \
  PREFLIGHT_JOB_JSON="${JOB_JSON}" \
  PREFLIGHT_GATE_JSON="${GATE_JSON}" \
  PREFLIGHT_EXECUTIONS_JSON="${EXECUTIONS_JSON}" \
  PREFLIGHT_ATTEMPTS_JSON="${SCHEDULER_ATTEMPTS_JSON}" \
  PREFLIGHT_RUN_JOB_AUDITS_JSON="${RUN_JOB_AUDITS_JSON}" \
  PREFLIGHT_TRANSFER_RUNS_JSON="${TRANSFER_RUNS_BEFORE}" \
  PREFLIGHT_LEGACY_TABLES_JSON="${LEGACY_TABLES_BEFORE}" \
  PREFLIGHT_DEPENDENCY_RECEIPT="${DEPENDENCY_RECEIPT}" \
  PREFLIGHT_ACTIVATION_RECEIPT="${ACTIVATION_RECEIPT}" python3 - \
    "${PREFLIGHT_RECEIPT_OUTPUT}" "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" \
    "${REGION}" "${TRANSFER_CONFIG}" "${CANONICAL_START_AT_UTC}" \
    "${EXPECTED_QUERY_SHA256}" "${EXPECTED_DTS_SERVICE_ACCOUNT}" \
    "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" "${ACTIVATION_IMAGE}" \
    "${ACTIVATION_JOB_SERVICE_ACCOUNT}" "${TRANSFER_VALIDATION_SUMMARY}" \
    "${SCHEDULER_SUMMARY}" "${GATE_SUMMARY}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

(
    path,
    project,
    dataset,
    location,
    region,
    transfer_config,
    canonical_start_at,
    expected_query_sha256,
    expected_dts_service_account,
    expected_scheduler_service_account,
    activation_image,
    activation_job_service_account,
    transfer_validation_summary,
    scheduler_summary,
    canonical_gate_summary,
) = sys.argv[1:]

def sha256_file(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

payload = {
    "receipt_type": "monitor_legacy_dts_pause_preflight_v1",
    "project": project,
    "dataset": dataset,
    "location": location,
    "region": region,
    "transfer_config_resource": transfer_config,
    "canonical_start_at": canonical_start_at,
    "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "expected_query_sha256": expected_query_sha256,
    "expected_dts_service_account": expected_dts_service_account,
    "expected_scheduler_service_account": expected_scheduler_service_account,
    "activation_image": activation_image,
    "activation_job_service_account": activation_job_service_account,
    "dependency_receipt_sha256": sha256_file(
        os.environ["PREFLIGHT_DEPENDENCY_RECEIPT"]
    ),
    "activation_receipt_sha256": sha256_file(
        os.environ["PREFLIGHT_ACTIVATION_RECEIPT"]
    ),
    "validated_read_only_gates": {
        "legacy_transfer_contract": transfer_validation_summary,
        "canonical_scheduler_contract": scheduler_summary,
        "canonical_three_run_provenance": canonical_gate_summary,
        "legacy_in_flight_runs": "none",
        "legacy_table_inventory": "captured",
    },
    "transfer_config_readback": json.loads(os.environ["PREFLIGHT_TRANSFER_JSON"]),
    "canonical_scheduler_readback": json.loads(
        os.environ["PREFLIGHT_SCHEDULER_JSON"]
    ),
    "canonical_job_readback": json.loads(os.environ["PREFLIGHT_JOB_JSON"]),
    "canonical_runs": json.loads(os.environ["PREFLIGHT_GATE_JSON"]),
    "canonical_executions": json.loads(os.environ["PREFLIGHT_EXECUTIONS_JSON"]),
    "canonical_scheduler_attempts": json.loads(
        os.environ["PREFLIGHT_ATTEMPTS_JSON"]
    ),
    "canonical_run_job_audits": json.loads(
        os.environ["PREFLIGHT_RUN_JOB_AUDITS_JSON"]
    ),
    "legacy_transfer_runs": json.loads(
        os.environ["PREFLIGHT_TRANSFER_RUNS_JSON"]
    ),
    "legacy_tables": json.loads(os.environ["PREFLIGHT_LEGACY_TABLES_JSON"]),
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY
  echo "legacy_dts_pause_preflight=complete receipt=${PREFLIGHT_RECEIPT_OUTPUT}"
  exit 0
fi

PREFLIGHT_RECEIPT_SHA256="$(python3 - "${PREFLIGHT_RECEIPT}" "${DEPENDENCY_RECEIPT}" \
  "${ACTIVATION_RECEIPT}" "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" \
  "${REGION}" "${TRANSFER_CONFIG}" "${CANONICAL_START_AT_UTC}" \
  "${EXPECTED_QUERY_SHA256}" "${EXPECTED_DTS_SERVICE_ACCOUNT}" \
  "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" "${ACTIVATION_IMAGE}" \
  "${ACTIVATION_JOB_SERVICE_ACCOUNT}" "${SNAPSHOT_OUTPUT}" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

(
    path,
    dependency_receipt,
    activation_receipt,
    project,
    dataset,
    location,
    region,
    transfer_config,
    canonical_start_at,
    expected_query_sha256,
    expected_dts_service_account,
    expected_scheduler_service_account,
    activation_image,
    activation_job_service_account,
    snapshot_path,
) = sys.argv[1:]

def sha256_file(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
expected = {
    "receipt_type": "monitor_legacy_dts_pause_preflight_v1",
    "project": project,
    "dataset": dataset,
    "location": location,
    "region": region,
    "transfer_config_resource": transfer_config,
    "canonical_start_at": canonical_start_at,
    "expected_query_sha256": expected_query_sha256,
    "expected_dts_service_account": expected_dts_service_account,
    "expected_scheduler_service_account": expected_scheduler_service_account,
    "activation_image": activation_image,
    "activation_job_service_account": activation_job_service_account,
    "dependency_receipt_sha256": sha256_file(dependency_receipt),
    "activation_receipt_sha256": sha256_file(activation_receipt),
}
if any(receipt.get(key) != value for key, value in expected.items()):
    raise SystemExit("preflight receipt does not match the current DTS pause chain")
gates = receipt.get("validated_read_only_gates") or {}
required_gates = {
    "legacy_transfer_contract",
    "canonical_scheduler_contract",
    "canonical_three_run_provenance",
    "legacy_in_flight_runs",
    "legacy_table_inventory",
}
if not required_gates.issubset(gates) or any(not gates.get(key) for key in required_gates):
    raise SystemExit("preflight receipt does not contain every read-only gate")
try:
    captured_at = datetime.fromisoformat(
        str(receipt.get("captured_at") or "").replace("Z", "+00:00")
    )
except ValueError as exc:
    raise SystemExit("preflight receipt timestamp is invalid") from exc
if captured_at.tzinfo is None:
    raise SystemExit("preflight receipt timestamp has no timezone")
captured_at = captured_at.astimezone(timezone.utc)
receipt_sha256 = sha256_file(path)
snapshot = Path(snapshot_path)
if snapshot.exists():
    try:
        state = json.loads(snapshot.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("existing DTS pause state is invalid") from exc
    if not isinstance(state, dict):
        raise SystemExit("existing DTS pause state is not an object")
    if state.get("preflight_receipt_sha256") != receipt_sha256:
        raise SystemExit("existing DTS pause state is bound to another preflight")
    if state.get("preflight_receipt") != receipt:
        raise SystemExit("existing DTS pause state embeds another preflight")
    try:
        intent_at = datetime.fromisoformat(
            str(state.get("captured_at") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise SystemExit("DTS pause intent timestamp is invalid") from exc
    if intent_at.tzinfo is None:
        raise SystemExit("DTS pause intent timestamp has no timezone")
    intent_at = intent_at.astimezone(timezone.utc)
    if captured_at > intent_at + timedelta(minutes=1):
        raise SystemExit("preflight receipt postdates the DTS pause intent")
    if intent_at - captured_at > timedelta(minutes=60):
        raise SystemExit("preflight receipt was already stale when intent was created")
else:
    now = datetime.now(timezone.utc)
    if captured_at > now + timedelta(minutes=1):
        raise SystemExit("preflight receipt timestamp is in the future")
    if now - captured_at > timedelta(minutes=60):
        raise SystemExit("preflight receipt is older than 60 minutes; run preflight again")
print(receipt_sha256)
PY
)"

if [[ ! -e "${SNAPSHOT_OUTPUT}" ]]; then
  SNAPSHOT_TRANSFER_JSON="${TRANSFER_JSON}" SNAPSHOT_GATE_JSON="${GATE_JSON}" \
  SNAPSHOT_SCHEDULER_STATE="${SCHEDULER_SUMMARY}" \
  SNAPSHOT_EXECUTIONS_JSON="${EXECUTIONS_JSON}" \
  SNAPSHOT_ATTEMPTS_JSON="${SCHEDULER_ATTEMPTS_JSON}" \
  SNAPSHOT_RUN_JOB_AUDITS_JSON="${RUN_JOB_AUDITS_JSON}" \
  SNAPSHOT_TRANSFER_RUNS_JSON="${TRANSFER_RUNS_BEFORE}" \
  SNAPSHOT_LEGACY_TABLES_JSON="${LEGACY_TABLES_BEFORE}" \
  SNAPSHOT_ACTIVATION_RECEIPT="${ACTIVATION_RECEIPT}" \
  SNAPSHOT_DEPENDENCY_RECEIPT="${DEPENDENCY_RECEIPT}" \
  SNAPSHOT_PREFLIGHT_RECEIPT="${PREFLIGHT_RECEIPT}" \
  SNAPSHOT_PREFLIGHT_RECEIPT_SHA256="${PREFLIGHT_RECEIPT_SHA256}" python3 - \
    "${SNAPSHOT_OUTPUT}" "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" "${REGION}" \
    "${TRANSFER_CONFIG}" "${CANONICAL_START_AT_UTC}" \
    "${EXPECTED_QUERY_SHA256}" "${EXPECTED_DTS_SERVICE_ACCOUNT}" \
    "${EXPECTED_SCHEDULER_SERVICE_ACCOUNT}" "${ACTIVATION_IMAGE}" \
    "${ACTIVATION_JOB_SERVICE_ACCOUNT}" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    project,
    dataset,
    location,
    region,
    transfer_config,
    canonical_start_at,
    expected_query_sha256,
    expected_dts_service_account,
    expected_scheduler_service_account,
    activation_image,
    activation_job_service_account,
) = sys.argv[1:]

def sha256_file(file_path):
    return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

transfer_before = json.loads(os.environ["SNAPSHOT_TRANSFER_JSON"])
if transfer_before.get("disabled") is not False:
    raise SystemExit("a new DTS pause intent requires an enabled transfer config")
payload = {
    "receipt_type": "monitor_legacy_dts_pause_intent_v1",
    "state": "intent",
    "project": project,
    "dataset": dataset,
    "location": location,
    "region": region,
    "transfer_config_resource": transfer_config,
    "canonical_start_at": canonical_start_at,
    "expected_query_sha256": expected_query_sha256,
    "expected_dts_service_account": expected_dts_service_account,
    "expected_scheduler_service_account": expected_scheduler_service_account,
    "activation_image": activation_image,
    "activation_job_service_account": activation_job_service_account,
    "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "transfer_config_before": transfer_before,
    "canonical_dependency_gate": json.loads(os.environ["SNAPSHOT_GATE_JSON"]),
    "canonical_scheduler_state": os.environ["SNAPSHOT_SCHEDULER_STATE"],
    "canonical_executions": json.loads(os.environ["SNAPSHOT_EXECUTIONS_JSON"]),
    "canonical_scheduler_attempts": json.loads(os.environ["SNAPSHOT_ATTEMPTS_JSON"]),
    "canonical_run_job_audits": json.loads(
        os.environ["SNAPSHOT_RUN_JOB_AUDITS_JSON"]
    ),
    "legacy_transfer_runs_before": json.loads(os.environ["SNAPSHOT_TRANSFER_RUNS_JSON"]),
    "legacy_tables_before": json.loads(os.environ["SNAPSHOT_LEGACY_TABLES_JSON"]),
    "dependency_receipt_sha256": sha256_file(os.environ["SNAPSHOT_DEPENDENCY_RECEIPT"]),
    "activation_receipt_sha256": sha256_file(os.environ["SNAPSHOT_ACTIVATION_RECEIPT"]),
}
with open(os.environ["SNAPSHOT_DEPENDENCY_RECEIPT"], encoding="utf-8") as handle:
    payload["dependency_receipt"] = json.load(handle)
with open(os.environ["SNAPSHOT_ACTIVATION_RECEIPT"], encoding="utf-8") as handle:
    payload["activation_receipt"] = json.load(handle)
with open(os.environ["SNAPSHOT_PREFLIGHT_RECEIPT"], encoding="utf-8") as handle:
    payload["preflight_receipt"] = json.load(handle)
payload["preflight_receipt_sha256"] = os.environ["SNAPSHOT_PREFLIGHT_RECEIPT_SHA256"]
payload["intent_payload_sha256"] = hashlib.sha256(
    canonical(payload).encode("utf-8")
).hexdigest()
destination = Path(path)
encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
descriptor, temporary = tempfile.mkstemp(
    prefix=destination.name + ".intent-",
    suffix=".tmp",
    dir=str(destination.parent),
)
try:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise SystemExit("DTS pause state appeared during intent publication") from exc
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
fi

PAUSE_STATE="$(classify_pause_state "${TRANSFER_JSON}")"
IFS=$'\t' read -r DTS_LOCK_INTENT DTS_LOCK_STATIC DTS_LOCK_TARGET <<< "$(python3 - "${SNAPSHOT_OUTPUT}" <<'PY'
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
intent = state.get("lock_intent_payload_sha256") or state.get("intent_payload_sha256")
if not isinstance(intent, str) or len(intent) != 64:
    raise SystemExit("DTS pause receipt has no stable lock intent")
static = {
    key: state.get(key)
    for key in (
        "project", "region", "transfer_config_resource", "canonical_start_at",
        "expected_query_sha256", "activation_image",
        "activation_job_service_account", "expected_scheduler_service_account",
    )
}
canonical = json.dumps(static, separators=(",", ":"), sort_keys=True)
print("\t".join((
    intent,
    hashlib.sha256(canonical.encode()).hexdigest(),
    state["transfer_config_resource"],
)))
PY
)"
DTS_LOCK_ARGS=(
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --resource-key "refresh-chain:${JOB_NAME}:${OLD_SCHEDULER_NAME}:${SCHEDULER_NAME}"
  --operation-kind legacy-dts-pause
  --target-key "${DTS_LOCK_TARGET}"
  --intent-payload-sha256 "${DTS_LOCK_INTENT}"
  --static-contract-sha256 "${DTS_LOCK_STATIC}"
  --firestore-database lcs-user-data
  --release-lock-collection monitor_release_locks
)
python3 "${ROOT_DIR}/scripts/release_operation_lock.py" acquire \
  --credential-file "${CREDENTIAL_FILE}" \
  "${DTS_LOCK_ARGS[@]}" >/dev/null

# Local receipts are not live authority. Re-read every mutable dependency under
# the global CAS immediately before the DTS mutation.
TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${TRANSFER_CONFIG}")"
PAUSE_STATE="$(classify_pause_state "${TRANSFER_JSON}")"
SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
SCHEDULER_SUMMARY="$(validate_scheduler_json "${SCHEDULER_JSON}")"
JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${JOB_JSON}" python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${ACTIVATION_IMAGE}" \
  --expected-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" \
  --source-service "${SOURCE_SERVICE}" --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null
GATE_JSON="$(query_canonical_gate)"
EXECUTIONS_JSON="$(gcloud --project="${PROJECT_ID}" run jobs executions list \
  --job="${JOB_NAME}" --region="${REGION}" --limit=100 --format=json)"
SCHEDULER_ATTEMPTS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${SCHEDULER_NAME}\" AND jsonPayload.\"@type\"=\"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\" AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"
RUN_JOB_AUDITS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "protoPayload.serviceName=\"run.googleapis.com\" AND (protoPayload.methodName=\"/Jobs.RunJob\" OR protoPayload.methodName=\"google.cloud.run.v2.Jobs.RunJob\") AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"
GATE_SUMMARY="$(validate_execution_gate)"
TRANSFER_RUNS_BEFORE="$(bq --project_id="${PROJECT_ID}" ls --transfer_run \
  --transfer_location="${LOCATION}" --format=prettyjson "${TRANSFER_CONFIG}")"
TRANSFER_RUNS_JSON="${TRANSFER_RUNS_BEFORE}" python3 - <<'PY'
import json
import os
runs = json.loads(os.environ["TRANSFER_RUNS_JSON"])
if any(str(item.get("state") or "").upper() in {"PENDING", "RUNNING"} for item in runs):
    raise SystemExit("legacy DTS acquired an in-flight run before pause")
PY
LEGACY_TABLES_UNDER_LOCK="$(query_legacy_table_inventory)"
if [[ "${PAUSE_STATE}" == "final" ]]; then
  CURRENT_LEGACY_TABLES_JSON="${LEGACY_TABLES_UNDER_LOCK}" python3 - \
    "${SNAPSHOT_OUTPUT}" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    receipt = json.load(handle)
expected = receipt.get("legacy_tables_after")
current = json.loads(os.environ["CURRENT_LEGACY_TABLES_JSON"])
if expected != current:
    raise SystemExit(
        "completed DTS pause receipt live legacy table inventory drifted"
    )
PY
  python3 "${ROOT_DIR}/scripts/release_operation_lock.py" release \
    --credential-file "${CREDENTIAL_FILE}" \
    "${DTS_LOCK_ARGS[@]}" >/dev/null
  echo "legacy DTS automatic scheduling already paused; exact receipt verified"
  exit 0
fi

UPDATE_RETURN_CODE=-1
if [[ "${PAUSE_STATE}" == "pre" ]]; then
  set +e
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" update \
    --transfer_config --no_auto_scheduling "${TRANSFER_CONFIG}"
  UPDATE_RETURN_CODE=$?
  set -e
elif [[ "${PAUSE_STATE}" != "post" ]]; then
  echo "DTS pause state is neither safely enabled nor already disabled" >&2
  exit 2
fi

TRANSFER_AFTER="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${TRANSFER_CONFIG}")"
POST_PAUSE_STATE="$(classify_pause_state "${TRANSFER_AFTER}")"
if [[ "${POST_PAUSE_STATE}" != "post" ]]; then
  echo "legacy DTS did not reach exact disabled state; intent retained" >&2
  exit 2
fi
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
# Re-read the same live Scheduler, Job, gate, and exact execution provenance
# after the mutation. A changed dependency leaves the durable intent and lock
# in place for investigated, exact recovery instead of publishing a false final.
SCHEDULER_AFTER="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
validate_scheduler_json "${SCHEDULER_AFTER}" >/dev/null
JOB_AFTER="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${JOB_AFTER}" python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${ACTIVATION_IMAGE}" \
  --expected-service-account "${ACTIVATION_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" --location "${LOCATION}" \
  --source-service "${SOURCE_SERVICE}" --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null
GATE_JSON="$(query_canonical_gate)"
EXECUTIONS_JSON="$(gcloud --project="${PROJECT_ID}" run jobs executions list \
  --job="${JOB_NAME}" --region="${REGION}" --limit=100 --format=json)"
SCHEDULER_ATTEMPTS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${SCHEDULER_NAME}\" AND jsonPayload.\"@type\"=\"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\" AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"
RUN_JOB_AUDITS_JSON="$(gcloud --project="${PROJECT_ID}" logging read \
  "protoPayload.serviceName=\"run.googleapis.com\" AND (protoPayload.methodName=\"/Jobs.RunJob\" OR protoPayload.methodName=\"google.cloud.run.v2.Jobs.RunJob\") AND timestamp>=\"${CANONICAL_START_AT_UTC}\"" \
  --limit=100 --order=asc --format=json)"
validate_execution_gate >/dev/null
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
  python3 - "${SNAPSHOT_OUTPUT}" "${UPDATE_RETURN_CODE}" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = sys.argv[1]
update_return_code = int(sys.argv[2])
destination = Path(path)
raw = destination.read_bytes()
payload = json.loads(raw)
if payload.get("receipt_type") != "monitor_legacy_dts_pause_intent_v1" or payload.get("state") != "intent":
    raise SystemExit("only an exact DTS pause intent can be finalized")

def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

observed_integrity = str(payload.get("intent_payload_sha256") or "")
unsigned = dict(payload)
unsigned.pop("intent_payload_sha256", None)
if observed_integrity != hashlib.sha256(canonical(unsigned).encode("utf-8")).hexdigest():
    raise SystemExit("DTS pause intent integrity mismatch during finalization")
before_tables = {
    item["table"]: item for item in payload.get("legacy_tables_before") or []
}
after_tables = {
    item["table"]: item
    for item in json.loads(os.environ["SNAPSHOT_LEGACY_TABLES_AFTER_JSON"])
}
if before_tables.keys() != after_tables.keys():
    raise SystemExit("legacy table inventory drifted since DTS pause intent")
for table in before_tables:
    for field in ("lastModifiedTime", "numRows"):
        if before_tables[table].get(field) != after_tables[table].get(field):
            raise SystemExit(f"legacy table {table} changed since DTS pause intent")
payload["receipt_type"] = "monitor_legacy_dts_pause_v2"
payload["state"] = "complete"
payload["lock_intent_payload_sha256"] = observed_integrity
payload["paused_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["transfer_config_after"] = json.loads(os.environ["SNAPSHOT_TRANSFER_AFTER_JSON"])
payload["legacy_transfer_runs_after"] = json.loads(
    os.environ["SNAPSHOT_TRANSFER_RUNS_AFTER_JSON"]
)
payload["legacy_tables_after"] = json.loads(
    os.environ["SNAPSHOT_LEGACY_TABLES_AFTER_JSON"]
)
payload["update_command_return_code"] = update_return_code
payload.pop("intent_payload_sha256", None)
payload["intent_payload_sha256"] = hashlib.sha256(
    canonical(payload).encode("utf-8")
).hexdigest()
encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
descriptor, temporary = tempfile.mkstemp(
    prefix=destination.name + ".final-",
    suffix=".tmp",
    dir=str(destination.parent),
)
try:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    if destination.read_bytes() != raw:
        raise SystemExit("DTS pause intent changed during finalization")
    os.replace(temporary, destination)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
python3 "${ROOT_DIR}/scripts/release_operation_lock.py" release \
  --credential-file "${CREDENTIAL_FILE}" \
  "${DTS_LOCK_ARGS[@]}" >/dev/null
echo "legacy DTS automatic scheduling paused; config and legacy tables retained"
