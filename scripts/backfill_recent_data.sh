#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
REGION="us-central1"
FREEZE_SNAPSHOT=""
JOB_DEPLOY_RECEIPT=""
RECEIPT_OUTPUT=""
EXPECTED_IMAGE=""
EXPECTED_JOB_SERVICE_ACCOUNT=""
EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT=""
EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT=""
SOURCE_SERVICE="lcs-rag-app"
CONFIRM_BACKFILL=""
APPLY="false"
CREDENTIAL_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --freeze-snapshot) FREEZE_SNAPSHOT="$2"; shift 2 ;;
    --job-deploy-receipt) JOB_DEPLOY_RECEIPT="$2"; shift 2 ;;
    --receipt-output) RECEIPT_OUTPUT="$2"; shift 2 ;;
    --expected-image) EXPECTED_IMAGE="$2"; shift 2 ;;
    --expected-job-service-account) EXPECTED_JOB_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-old-scheduler-service-account) EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-new-scheduler-service-account) EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --confirm-backfill) CONFIRM_BACKFILL="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
NEW_SCHEDULER="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
OLD_SCHEDULER="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_name)')"
NEW_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_cron)')"
TIMEZONE="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.timezone)')"
NEW_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_attempt_deadline_seconds)')"
OLD_ATTEMPT_DEADLINE_SECONDS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_attempt_deadline_seconds)')"
MAX_RETRY_ATTEMPTS="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_max_retry_attempts)')"
DELAY_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.expected_delay_minutes)')"
JOB_TIMEOUT_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')"
OLD_CRON="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.legacy_scheduler_cron)')"
EXPECTED_JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
CONFIRM_IMAGE="${EXPECTED_IMAGE:-<expected-image>}"
CONFIRM_SERVICE_ACCOUNT="${EXPECTED_JOB_SERVICE_ACCOUNT:-<expected-job-service-account>}"
REQUIRED_CONFIRM="projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:backfill-until-current:${CONFIRM_IMAGE}:${CONFIRM_SERVICE_ACCOUNT}"

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "job=${JOB_NAME} expected_image=${EXPECTED_IMAGE:-required-on-apply} expected_job_service_account=${EXPECTED_JOB_SERVICE_ACCOUNT:-required-on-apply}"
echo "guard=old and new schedulers must both be PAUSED"
echo "action=run the canonical refresh owner with --until-current; do not edit raw rows or watermark"
echo "required_confirmation=${REQUIRED_CONFIRM}"

if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ -n "${EXPECTED_IMAGE}" ]] || { echo "--expected-image is required on apply" >&2; exit 2; }
[[ -n "${EXPECTED_JOB_SERVICE_ACCOUNT}" && -n "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" && -n "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "all expected Job, old-Scheduler and new-Scheduler service accounts are required on apply" >&2; exit 2;
}
[[ "${EXPECTED_JOB_SERVICE_ACCOUNT}" != "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" ]] || {
  echo "refresh writer and new Scheduler invoker identities must be distinct" >&2; exit 2;
}
[[ "${EXPECTED_IMAGE}" =~ ^${REGION}-docker\.pkg\.dev/${PROJECT_ID}/[^/@]+/[^/@]+@sha256:[0-9a-f]{64}$ ]] || {
  echo "--expected-image must be an immutable Artifact Registry digest in the selected project and region" >&2
  exit 2
}
[[ "${CONFIRM_BACKFILL}" == "${REQUIRED_CONFIRM}" ]] || {
  echo "--confirm-backfill must equal ${REQUIRED_CONFIRM}" >&2; exit 2;
}
[[ -n "${FREEZE_SNAPSHOT}" && -f "${FREEZE_SNAPSHOT}" ]] || {
  echo "--freeze-snapshot must be the receipt created by the freeze stage" >&2; exit 2;
}
[[ -n "${JOB_DEPLOY_RECEIPT}" && -f "${JOB_DEPLOY_RECEIPT}" ]] || {
  echo "--job-deploy-receipt is required on apply" >&2; exit 2;
}
[[ -n "${RECEIPT_OUTPUT}" ]] || { echo "--receipt-output is required on apply" >&2; exit 2; }
[[ ! -e "${RECEIPT_OUTPUT}" ]] || { echo "receipt output already exists" >&2; exit 2; }
[[ -d "$(dirname "${RECEIPT_OUTPUT}")" ]] || { echo "receipt output parent does not exist" >&2; exit 2; }
python3 "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"

python3 - "${JOB_DEPLOY_RECEIPT}" "${PROJECT_ID}" "${REGION}" \
  "${DATASET_ID}" "${LOCATION}" "${SOURCE_SERVICE}" "${JOB_NAME}" \
  "${NEW_SCHEDULER}" "${EXPECTED_IMAGE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" <<'PY'
import json
import sys

(
    path,
    project,
    region,
    dataset,
    location,
    source_service,
    job,
    scheduler,
    image,
    job_service_account,
    scheduler_service_account,
) = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
expected = {
    "receipt_type": "monitor_refresh_job_deploy_v1",
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "source_service": source_service,
    "job": job,
    "scheduler": scheduler,
    "image": image,
    "expected_job_service_account": job_service_account,
    "expected_scheduler_service_account": scheduler_service_account,
}
if any(receipt.get(key) != value for key, value in expected.items()):
    raise SystemExit("refresh Job deploy receipt does not match this backfill")
contract = receipt.get("validated_job_contract") or {}
if contract.get("image") != image or contract.get("serviceAccount") != job_service_account:
    raise SystemExit("refresh Job deploy receipt has an invalid Job contract")
scheduler_readback = receipt.get("scheduler_readback") or {}
if scheduler_readback.get("state") != "PAUSED":
    raise SystemExit("refresh Job deploy receipt did not leave the Scheduler paused")
if not receipt.get("captured_at"):
    raise SystemExit("refresh Job deploy receipt has no capture time")
PY

python3 - "${FREEZE_SNAPSHOT}" "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" \
  "${LOCATION}" "${SOURCE_SERVICE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" \
  "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" \
  "${OLD_SCHEDULER}" "${NEW_SCHEDULER}" <<'PY'
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
if any(payload.get(key) != value for key, value in expected.items()):
    raise SystemExit("freeze snapshot does not match this backfill")
if not payload.get("freeze_started_at"):
    raise SystemExit("freeze snapshot has no freeze timestamp")
if not payload.get("freeze_verified_at"):
    raise SystemExit("freeze snapshot has no writer-quiescence verification")
if payload.get("active_bigquery_writers_at_freeze") not in ([], None):
    raise SystemExit("freeze snapshot contains active BigQuery writers")
if payload.get("activation_started_at"):
    raise SystemExit("new scheduler was already activated; freeze again before backfill")
PY

describe_scheduler() {
  gcloud --project="${PROJECT_ID}" scheduler jobs describe "$1" \
    --location="${REGION}" --format=json
}

OLD_JSON="$(describe_scheduler "${OLD_SCHEDULER}")"
NEW_JSON="$(describe_scheduler "${NEW_SCHEDULER}")"
OLD_SCHEDULER_JSON="${OLD_JSON}" NEW_SCHEDULER_JSON="${NEW_JSON}" python3 - \
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
old_cron, new_cron, timezone, expected_uri, old_service_account, new_service_account, old_deadline, new_deadline, expected_retries = sys.argv[1:]
for payload, label, cron, deadline, expected_service_account in (
    (old, "old", old_cron, old_deadline, old_service_account),
    (new, "new", new_cron, new_deadline, new_service_account),
):
    if payload.get("state") != "PAUSED":
        raise SystemExit(f"{label} scheduler must be PAUSED before backfill")
    if payload.get("schedule") != cron or payload.get("timeZone") != timezone:
        raise SystemExit(f"{label} scheduler does not match the governed policy")
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
PY

JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" --region="${REGION}" --format=json)"
JOB_VALIDATION_JSON="$(JOB_DESCRIPTION_JSON="${JOB_JSON}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --expected-image "${EXPECTED_IMAGE}" \
    --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "${JOB_TIMEOUT_MINUTES}")"
echo "refresh_job_contract=verified"

query_pipeline_state() {
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
    --use_legacy_sql=false --format=json --quiet \
    "SELECT
       source,
       status,
       published_run_id,
       FORMAT_TIMESTAMP('%FT%TZ', data_through) AS data_through,
       IF(
         NULLIF(lease_run_id, '') IS NOT NULL
           AND lease_expires_at > CURRENT_TIMESTAMP(),
         'true',
         'false'
       ) AS lease_active
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_state\`
     WHERE source = 'published'"
}

validate_pipeline_state() {
  local payload="$1" phase="$2" target_at="${3:-}"
  PIPELINE_STATE_JSON="${payload}" python3 - "${phase}" "${target_at}" "${DELAY_MINUTES}" <<'PY'
import json
import os
import sys
from datetime import datetime, timedelta

rows = json.loads(os.environ["PIPELINE_STATE_JSON"])
if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("source") != "published":
    raise SystemExit("published pipeline state must contain exactly one row")
row = rows[0]
if str(row.get("lease_active", "")).lower() == "true":
    raise SystemExit("a refresh execution still owns the pipeline lease")
if sys.argv[1] == "post":
    if row.get("status") != "succeeded":
        raise SystemExit("backfill did not leave the published pipeline succeeded")
    if not str(row.get("published_run_id") or "").strip():
        raise SystemExit("backfill has no atomic published run receipt")
    try:
        target = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
        data_through = datetime.fromisoformat(str(row["data_through"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("backfill readback has no valid data_through") from exc
    minimum = target - timedelta(minutes=int(sys.argv[3]) + 1)
    if data_through < minimum:
        raise SystemExit("backfill did not reach the frozen current target")
    if data_through > target + timedelta(minutes=1):
        raise SystemExit("backfill published a watermark beyond the frozen target")
print(f"status={row.get('status')} data_through={row.get('data_through')} lease_active=false")
PY
}

PRE_STATE="$(query_pipeline_state)"
echo "pre_backfill=$(validate_pipeline_state "${PRE_STATE}" "pre")"
TARGET_AT="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))')"
EXECUTION_JSON="$(gcloud --project="${PROJECT_ID}" run jobs execute "${JOB_NAME}" \
  --region="${REGION}" \
  --args=-m,app.jobs.refresh_analytics,--apply,--until-current,--trigger-source,manual_backfill,--target-at,"${TARGET_AT}" \
  --wait --format=json)"
EXECUTION_PROVENANCE_JSON="$(JOB_DESCRIPTION_JSON="${EXECUTION_JSON}" \
  python3 "${ROOT_DIR}/scripts/validate_refresh_job.py" \
    --execution-provenance-only \
    --expected-image "${EXPECTED_IMAGE}" \
    --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    --project "${PROJECT_ID}" \
    --dataset "${DATASET_ID}" \
    --location "${LOCATION}" \
    --source-service "${SOURCE_SERVICE}" \
    --timeout-minutes "${JOB_TIMEOUT_MINUTES}")"
EXECUTION_SUMMARY="$(RECEIPT_EXECUTION_JSON="${EXECUTION_JSON}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["RECEIPT_EXECUTION_JSON"])
status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
try:
    succeeded = int(status.get("succeededCount") or 0)
    failed = int(status.get("failedCount") or 0)
except (TypeError, ValueError) as exc:
    raise SystemExit("Cloud Run execution counters are invalid") from exc
completed = next(
    (
        item
        for item in status.get("conditions", [])
        if isinstance(item, dict) and item.get("type") == "Completed"
    ),
    None,
)
if succeeded < 1 or failed != 0:
    raise SystemExit("Cloud Run backfill execution did not succeed")
if completed is not None and str(completed.get("status") or "").lower() != "true":
    raise SystemExit("Cloud Run backfill execution is not terminal-successful")
name = str(payload.get("name") or (payload.get("metadata") or {}).get("name") or "")
if not name:
    raise SystemExit("Cloud Run backfill execution has no immutable name")
print(f"name={name} succeeded={succeeded} failed={failed}")
PY
)"
echo "backfill_execution=${EXECUTION_SUMMARY}"
POST_STATE="$(query_pipeline_state)"
echo "post_backfill=$(validate_pipeline_state "${POST_STATE}" "post" "${TARGET_AT}")"

EXECUTION_ID="$(RECEIPT_EXECUTION_JSON="${EXECUTION_JSON}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["RECEIPT_EXECUTION_JSON"])
name = str(payload.get("name") or (payload.get("metadata") or {}).get("name") or "")
print(name.rsplit("/", 1)[-1])
PY
)"
FREEZE_STARTED_AT="$(python3 - "${FREEZE_SNAPSHOT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["freeze_started_at"])
PY
)"
ANALYTICS_START_AT="$(JOB_VALIDATION_JSON="${JOB_VALIDATION_JSON}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["JOB_VALIDATION_JSON"])
print(payload["environment"]["MONITOR_ANALYTICS_START_AT"])
PY
)"
BACKFILL_AUDIT_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
  --use_legacy_sql=false --format=json --quiet \
  --parameter="execution_id:STRING:${EXECUTION_ID}" \
  --parameter="freeze_started_at:TIMESTAMP:${FREEZE_STARTED_AT}" \
  --parameter="analytics_start_at:TIMESTAMP:${ANALYTICS_START_AT}" \
  "WITH runs AS (
     SELECT run_id, input_rows, merged_rows, duplicate_rows
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_runs\`
     WHERE DATE(started_at) BETWEEN DATE(@freeze_started_at) AND CURRENT_DATE()
       AND (execution_id = @execution_id OR ENDS_WITH(execution_id, CONCAT('/', @execution_id)))
       AND trigger_source = 'manual_backfill'
       AND status = 'succeeded'
   ), manifest_all AS (
     SELECT run_id, source_event_hash, event_key_hash, event_family, disposition
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest\` manifest
     JOIN runs USING (run_id)
     WHERE DATE(manifest.observed_at) BETWEEN DATE(@freeze_started_at) AND CURRENT_DATE()
   ), manifest AS (
     SELECT DISTINCT event_key_hash, event_family, disposition
     FROM manifest_all
   ), conflicting_duplicate_hashes AS (
     SELECT DISTINCT source_event_hash
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_event_issues\`
     WHERE DATE(last_observed_at) BETWEEN DATE(@freeze_started_at) AND CURRENT_DATE()
       AND issue_code = 'conflicting_duplicate_event_id'
       AND disposition = 'row_quarantined'
   ), questions AS (
     SELECT DISTINCT TO_HEX(SHA256(event_id)) AS event_key_hash
     FROM \`${PROJECT_ID}.${DATASET_ID}.question_events\`
     WHERE question_date BETWEEN DATE(@analytics_start_at) AND CURRENT_DATE()
   ), answers AS (
     SELECT DISTINCT TO_HEX(SHA256(event_id)) AS event_key_hash
     FROM \`${PROJECT_ID}.${DATASET_ID}.answer_events\`
     WHERE answer_date BETWEEN DATE(@analytics_start_at) AND CURRENT_DATE()
   ), actions AS (
     SELECT DISTINCT TO_HEX(SHA256(event_id)) AS event_key_hash
     FROM \`${PROJECT_ID}.${DATASET_ID}.answer_action_events\`
     WHERE action_date BETWEEN DATE(@analytics_start_at) AND CURRENT_DATE()
   ), quality AS (
     SELECT disposition, failure_count, passed
     FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_quality_events\` quality
     JOIN runs USING (run_id)
     WHERE DATE(quality.observed_at) BETWEEN DATE(@freeze_started_at) AND CURRENT_DATE()
   )
   SELECT
     (SELECT COUNT(*) FROM runs) AS successful_run_count,
     (SELECT COALESCE(SUM(input_rows), 0) FROM runs) AS input_row_count,
     (SELECT COALESCE(SUM(merged_rows), 0) FROM runs) AS merged_row_count,
     (SELECT COALESCE(SUM(duplicate_rows), 0) FROM runs) AS duplicate_row_count,
     COUNTIF(manifest.disposition = 'row_quarantined') AS quarantined_manifest_count,
     (SELECT COUNTIF(disposition = 'deduplicated') FROM manifest_all) AS deduplicated_manifest_count,
     (SELECT COUNTIF(
        manifest_all.disposition = 'row_quarantined'
        AND conflicts.source_event_hash IS NOT NULL
      )
      FROM manifest_all
      LEFT JOIN conflicting_duplicate_hashes conflicts USING (source_event_hash)
     ) AS conflicting_duplicate_manifest_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'message_persisted') AS canonical_persistence_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'question_received') AS canonical_question_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'question_received' AND questions.event_key_hash IS NOT NULL) AS matched_question_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'answer_completed') AS canonical_answer_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'answer_completed' AND answers.event_key_hash IS NOT NULL) AS matched_answer_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'answer_action') AS canonical_action_count,
     COUNTIF(manifest.disposition = 'canonical' AND manifest.event_family = 'answer_action' AND actions.event_key_hash IS NOT NULL) AS matched_action_count,
     (SELECT COALESCE(SUM(IF(disposition = 'batch_blocking' AND passed IS NOT TRUE, failure_count, 0)), 0) FROM quality) AS blocking_failure_count,
     (SELECT COALESCE(SUM(IF(disposition = 'axis_unmeasured', failure_count, 0)), 0) FROM quality) AS axis_unmeasured_finding_count
   FROM manifest
   LEFT JOIN questions USING (event_key_hash)
   LEFT JOIN answers USING (event_key_hash)
   LEFT JOIN actions USING (event_key_hash)")"

BACKFILL_AUDIT_SUMMARY="$(BACKFILL_AUDIT_JSON="${BACKFILL_AUDIT_JSON}" python3 - <<'PY'
import json
import os

rows = json.loads(os.environ["BACKFILL_AUDIT_JSON"])
if not isinstance(rows, list) or len(rows) != 1:
    raise SystemExit("backfill reconciliation returned an unexpected result")
row = rows[0]
def count(name):
    try:
        return int(row.get(name) or 0)
    except (TypeError, ValueError) as exc:
        raise SystemExit("backfill reconciliation has an invalid " + name) from exc
if count("successful_run_count") < 1:
    raise SystemExit("backfill execution has no successful canonical pipeline run")
for family in ("question", "answer", "action"):
    canonical = count(f"canonical_{family}_count")
    matched = count(f"matched_{family}_count")
    if canonical != matched:
        raise SystemExit(f"backfill {family} manifest does not reconcile to canonical facts")
if count("blocking_failure_count") != 0:
    raise SystemExit("backfill has unresolved batch-blocking quality failures")
durably_dispositioned_duplicates = (
    count("deduplicated_manifest_count")
    + count("conflicting_duplicate_manifest_count")
)
if count("duplicate_row_count") != durably_dispositioned_duplicates:
    raise SystemExit(
        "backfill duplicate rows do not reconcile to deduplicated or quarantined manifest dispositions"
    )
print(
    "runs={runs} input={inputs} merged={merged} quarantined={quarantined} "
    "deduplicated={deduplicated} persistence={persistence} axis_findings={axis}".format(
        runs=count("successful_run_count"),
        inputs=count("input_row_count"),
        merged=count("merged_row_count"),
        quarantined=count("quarantined_manifest_count"),
        deduplicated=count("deduplicated_manifest_count"),
        persistence=count("canonical_persistence_count"),
        axis=count("axis_unmeasured_finding_count"),
    )
)
PY
)"
echo "backfill_reconciliation=${BACKFILL_AUDIT_SUMMARY}"

RECEIPT_JOB_JSON="${JOB_JSON}" RECEIPT_EXECUTION_JSON="${EXECUTION_JSON}" \
RECEIPT_JOB_VALIDATION_JSON="${JOB_VALIDATION_JSON}" \
RECEIPT_EXECUTION_PROVENANCE_JSON="${EXECUTION_PROVENANCE_JSON}" \
RECEIPT_PRE_STATE="${PRE_STATE}" RECEIPT_POST_STATE="${POST_STATE}" \
RECEIPT_AUDIT_JSON="${BACKFILL_AUDIT_JSON}" \
RECEIPT_FREEZE_SNAPSHOT="${FREEZE_SNAPSHOT}" \
RECEIPT_JOB_DEPLOY_RECEIPT="${JOB_DEPLOY_RECEIPT}" \
  python3 - "${RECEIPT_OUTPUT}" "${PROJECT_ID}" "${REGION}" "${DATASET_ID}" \
    "${LOCATION}" "${SOURCE_SERVICE}" "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
    "${EXPECTED_OLD_SCHEDULER_SERVICE_ACCOUNT}" \
    "${EXPECTED_NEW_SCHEDULER_SERVICE_ACCOUNT}" \
    "${JOB_NAME}" "${EXPECTED_IMAGE}" "${TARGET_AT}" <<'PY'
import json
import hashlib
import os
import sys

path, project, region, dataset, location, source_service, job_service_account, old_service_account, new_service_account, job_name, expected_image, target_at = sys.argv[1:]
with open(os.environ["RECEIPT_FREEZE_SNAPSHOT"], encoding="utf-8") as handle:
    freeze_snapshot = json.load(handle)
payload = {
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "source_service": source_service,
    "expected_job_service_account": job_service_account,
    "expected_old_scheduler_service_account": old_service_account,
    "expected_new_scheduler_service_account": new_service_account,
    "job": job_name,
    "expected_image": expected_image,
    "target_at": target_at,
    "freeze_snapshot": freeze_snapshot,
    "job_before": json.loads(os.environ["RECEIPT_JOB_JSON"]),
    "validated_job_contract": json.loads(os.environ["RECEIPT_JOB_VALIDATION_JSON"]),
    "validated_execution_provenance": json.loads(
        os.environ["RECEIPT_EXECUTION_PROVENANCE_JSON"]
    ),
    "execution": json.loads(os.environ["RECEIPT_EXECUTION_JSON"]),
    "pipeline_before": json.loads(os.environ["RECEIPT_PRE_STATE"]),
    "pipeline_after": json.loads(os.environ["RECEIPT_POST_STATE"]),
    "reconciliation": json.loads(os.environ["RECEIPT_AUDIT_JSON"]),
}
with open(os.environ["RECEIPT_JOB_DEPLOY_RECEIPT"], "rb") as handle:
    payload["job_deploy_receipt_sha256"] = hashlib.sha256(handle.read()).hexdigest()
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY

echo "backfill=complete receipt=${RECEIPT_OUTPUT}"
echo "next_gate=activate the three-hour scheduler with the freeze snapshot"
