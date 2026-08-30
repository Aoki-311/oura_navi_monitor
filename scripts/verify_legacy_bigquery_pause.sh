#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
REGION="us-central1"
SOURCE_SERVICE="lcs-rag-app"
PAUSE_SNAPSHOT=""
RECEIPT_OUTPUT=""
MIN_OBSERVATION_MINUTES="45"
VERIFY="false"
CREDENTIAL_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --pause-snapshot) PAUSE_SNAPSHOT="$2"; shift 2 ;;
    --receipt-output) RECEIPT_OUTPUT="$2"; shift 2 ;;
    --min-observation-minutes) MIN_OBSERVATION_MINUTES="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --verify) VERIFY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
[[ "${MIN_OBSERVATION_MINUTES}" =~ ^[0-9]+$ && "${MIN_OBSERVATION_MINUTES}" -ge 45 ]] || {
  echo "--min-observation-minutes must be at least 45" >&2; exit 2;
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
SCHEDULER_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
JOB_TIMEOUT_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')"
STALE_AFTER_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.freshness_stale_after_minutes)')"

echo "mode=$([[ "${VERIFY}" == "true" ]] && echo verify || echo plan)"
echo "pause_snapshot=${PAUSE_SNAPSHOT:-required-on-verify} min_observation_minutes=${MIN_OBSERVATION_MINUTES}"
echo "checks=transfer-disabled,no-new-transfer-run,legacy-tables-unchanged,canonical-job-and-scheduler-unchanged,pipeline-fresh"
if [[ "${VERIFY}" != "true" ]]; then exit 0; fi

[[ -n "${PAUSE_SNAPSHOT}" && -f "${PAUSE_SNAPSHOT}" ]] || {
  echo "--pause-snapshot is required on verify" >&2; exit 2;
}
[[ -n "${RECEIPT_OUTPUT}" ]] || { echo "--receipt-output is required on verify" >&2; exit 2; }
[[ ! -e "${RECEIPT_OUTPUT}" ]] || { echo "verification receipt already exists" >&2; exit 2; }
[[ -d "$(dirname "${RECEIPT_OUTPUT}")" ]] || {
  echo "verification receipt parent does not exist" >&2; exit 2;
}
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"

SNAPSHOT_VALUES="$(python3 - "${PAUSE_SNAPSHOT}" "${PROJECT_ID}" "${DATASET_ID}" \
  "${LOCATION}" "${REGION}" "${MIN_OBSERVATION_MINUTES}" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, project, dataset, location, region, minimum = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    snapshot = json.load(handle)
expected = {
    "project": project,
    "dataset": dataset,
    "location": location,
    "region": region,
}
if any(snapshot.get(key) != value for key, value in expected.items()):
    raise SystemExit("pause snapshot does not match this verification target")
if not snapshot.get("transfer_config_resource"):
    raise SystemExit("pause snapshot has no transfer config resource")
if not snapshot.get("paused_at") or not snapshot.get("transfer_config_after"):
    raise SystemExit("pause snapshot does not prove a completed DTS pause")
if not snapshot.get("legacy_tables_after") or not snapshot.get("activation_receipt"):
    raise SystemExit("pause snapshot lacks table or activation provenance")
paused_at = datetime.fromisoformat(str(snapshot["paused_at"]).replace("Z", "+00:00"))
elapsed = int((datetime.now(timezone.utc) - paused_at.astimezone(timezone.utc)).total_seconds() // 60)
if elapsed < int(minimum):
    raise SystemExit(f"observation window is only {elapsed} minutes; {minimum} required")
activation = snapshot["activation_receipt"]
print(json.dumps({
    "pausedAt": snapshot["paused_at"],
    "elapsedMinutes": elapsed,
    "transferConfig": snapshot["transfer_config_resource"],
    "canonicalStartAt": snapshot.get("canonical_start_at") or "",
    "activation": activation,
    "expectedTransfer": snapshot["transfer_config_after"],
    "expectedTables": snapshot["legacy_tables_after"],
}, sort_keys=True))
PY
)"

json_value() {
  SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c "import json,os; print(json.loads(os.environ['SNAPSHOT_VALUES'])[$1])"
}
TRANSFER_CONFIG="$(json_value "'transferConfig'")"
PAUSED_AT="$(json_value "'pausedAt'")"
ACTIVATION_IMAGE="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.loads(os.environ["SNAPSHOT_VALUES"])["activation"]["image"])')"
JOB_SERVICE_ACCOUNT="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.loads(os.environ["SNAPSHOT_VALUES"])["activation"]["expected_job_service_account"])')"

TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${TRANSFER_CONFIG}")"
EXPECTED_TRANSFER_JSON="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.dumps(json.loads(os.environ["SNAPSHOT_VALUES"])["expectedTransfer"]))')"
TRANSFER_JSON="${TRANSFER_JSON}" EXPECTED_TRANSFER_JSON="${EXPECTED_TRANSFER_JSON}" python3 - <<'PY'
import json
import os

current = json.loads(os.environ["TRANSFER_JSON"])
expected = json.loads(os.environ["EXPECTED_TRANSFER_JSON"])
if current.get("disabled") is not True:
    raise SystemExit("legacy DTS is no longer disabled")
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
    if current.get(key) != expected.get(key):
        raise SystemExit("legacy DTS drifted after pause: " + key)
PY

TRANSFER_RUNS_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" ls \
  --transfer_run --transfer_location="${LOCATION}" --format=prettyjson \
  "${TRANSFER_CONFIG}")"
TRANSFER_RUNS_JSON="${TRANSFER_RUNS_JSON}" python3 - "${PAUSED_AT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

paused_at = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).astimezone(timezone.utc)
runs = json.loads(os.environ["TRANSFER_RUNS_JSON"])
if not isinstance(runs, list):
    raise SystemExit("legacy DTS run inventory is not a list")
for item in runs:
    state = str(item.get("state") or "").upper()
    if state in {"PENDING", "RUNNING"}:
        raise SystemExit("legacy DTS has an in-flight run after pause")
    timestamp = item.get("runTime") or item.get("scheduleTime") or item.get("startTime")
    if not timestamp:
        continue
    observed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
    if observed > paused_at:
        raise SystemExit("legacy DTS produced a run after automatic scheduling was paused")
PY

query_legacy_table_inventory() {
  CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${CREDENTIAL_FILE}" \
  GOOGLE_APPLICATION_CREDENTIALS="${CREDENTIAL_FILE}" \
    python3 - "${PROJECT_ID}" "${DATASET_ID}" "${LOCATION}" <<'PY'
import json
import subprocess
import sys

project, dataset, location = sys.argv[1:]
inventory = []
for table in (
    "monitor_answer_events",
    "monitor_user_daily",
    "monitor_system_hourly",
    "monitor_dashboard_snapshots",
):
    completed = subprocess.run(
        ["bq", f"--project_id={project}", f"--location={location}", "show", "--format=prettyjson", f"{project}:{dataset}.{table}"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    inventory.append({
        "table": table,
        "lastModifiedTime": str(payload.get("lastModifiedTime") or ""),
        "numRows": str(payload.get("numRows") or ""),
        "etag": str(payload.get("etag") or ""),
    })
print(json.dumps(inventory, sort_keys=True))
PY
}
LEGACY_TABLES_JSON="$(query_legacy_table_inventory)"
EXPECTED_TABLES_JSON="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.dumps(json.loads(os.environ["SNAPSHOT_VALUES"])["expectedTables"]))')"
LEGACY_TABLES_JSON="${LEGACY_TABLES_JSON}" EXPECTED_TABLES_JSON="${EXPECTED_TABLES_JSON}" python3 - <<'PY'
import json
import os

current = {item["table"]: item for item in json.loads(os.environ["LEGACY_TABLES_JSON"])}
expected = {item["table"]: item for item in json.loads(os.environ["EXPECTED_TABLES_JSON"])}
if current.keys() != expected.keys():
    raise SystemExit("legacy table inventory changed after pause")
for table in current:
    for field in ("lastModifiedTime", "numRows"):
        if current[table].get(field) != expected[table].get(field):
            raise SystemExit(f"legacy table {table} changed after DTS pause")
PY

SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
EXPECTED_SCHEDULER_JSON="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.dumps(json.loads(os.environ["SNAPSHOT_VALUES"])["activation"]["new_scheduler_readback"]))')"
SCHEDULER_JSON="${SCHEDULER_JSON}" EXPECTED_SCHEDULER_JSON="${EXPECTED_SCHEDULER_JSON}" python3 - <<'PY'
import json
import os

current = json.loads(os.environ["SCHEDULER_JSON"])
expected = json.loads(os.environ["EXPECTED_SCHEDULER_JSON"])
if current.get("state") != "ENABLED":
    raise SystemExit("canonical Scheduler is not enabled")
for path in (
    ("schedule",),
    ("timeZone",),
    ("attemptDeadline",),
    ("retryConfig", "retryCount"),
    ("httpTarget", "uri"),
    ("httpTarget", "oauthToken", "serviceAccountEmail"),
):
    def read(payload):
        value = payload
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        return value
    if (read(current) or 0) != (read(expected) or 0):
        raise SystemExit("canonical Scheduler drifted after activation: " + ".".join(path))
PY

JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${JOB_JSON}" python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${ACTIVATION_IMAGE}" \
  --expected-service-account "${JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null

PIPELINE_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
  --use_legacy_sql=false --format=json --quiet \
  "SELECT source, status, published_run_id,
          FORMAT_TIMESTAMP('%FT%TZ', data_through) AS data_through,
          TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), data_through, MINUTE) AS freshness_minutes
   FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_state\`
   WHERE source = 'published'")"
PIPELINE_JSON="${PIPELINE_JSON}" python3 - "${STALE_AFTER_MINUTES}" <<'PY'
import json
import os
import sys

rows = json.loads(os.environ["PIPELINE_JSON"])
if not isinstance(rows, list) or len(rows) != 1:
    raise SystemExit("published pipeline state is missing")
row = rows[0]
if row.get("status") != "succeeded" or not row.get("published_run_id"):
    raise SystemExit("canonical pipeline is not successfully published")
try:
    freshness = int(row.get("freshness_minutes"))
except (TypeError, ValueError) as exc:
    raise SystemExit("canonical freshness is missing") from exc
if freshness < 0 or freshness > int(sys.argv[1]):
    raise SystemExit("canonical pipeline is stale during DTS observation")
PY

VERIFY_TRANSFER_JSON="${TRANSFER_JSON}" VERIFY_TRANSFER_RUNS_JSON="${TRANSFER_RUNS_JSON}" \
VERIFY_TABLES_JSON="${LEGACY_TABLES_JSON}" VERIFY_SCHEDULER_JSON="${SCHEDULER_JSON}" \
VERIFY_JOB_JSON="${JOB_JSON}" VERIFY_PIPELINE_JSON="${PIPELINE_JSON}" \
VERIFY_SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 - "${RECEIPT_OUTPUT}" \
  "${PAUSE_SNAPSHOT}" "${MIN_OBSERVATION_MINUTES}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

output, snapshot_path, minimum = sys.argv[1:]
values = json.loads(os.environ["VERIFY_SNAPSHOT_VALUES"])
payload = {
    "status": "passed",
    "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "minimum_observation_minutes": int(minimum),
    "elapsed_minutes": int(values["elapsedMinutes"]),
    "pause_snapshot_sha256": hashlib.sha256(Path(snapshot_path).read_bytes()).hexdigest(),
    "transfer_config": json.loads(os.environ["VERIFY_TRANSFER_JSON"]),
    "transfer_runs": json.loads(os.environ["VERIFY_TRANSFER_RUNS_JSON"]),
    "legacy_tables": json.loads(os.environ["VERIFY_TABLES_JSON"]),
    "canonical_scheduler": json.loads(os.environ["VERIFY_SCHEDULER_JSON"]),
    "canonical_job": json.loads(os.environ["VERIFY_JOB_JSON"]),
    "canonical_pipeline": json.loads(os.environ["VERIFY_PIPELINE_JSON"]),
}
with open(output, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY

ELAPSED_MINUTES="$(SNAPSHOT_VALUES="${SNAPSHOT_VALUES}" python3 -c 'import json,os; print(json.loads(os.environ["SNAPSHOT_VALUES"])["elapsedMinutes"])')"
echo "legacy_dts_pause_verification=complete elapsed_minutes=${ELAPSED_MINUTES} receipt=${RECEIPT_OUTPUT}"
