#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
REGION="us-central1"
SERVICE_NAME="oura-navi-monitor"
DATASET_ID="oura_navi_monitor"
LOCATION="US"
REVISION=""
EXPECTED_IMAGE=""
EXPECTED_GIT_SHA=""
EXPECTED_SERVICE_ACCOUNT=""
SCHEMA_RECEIPT=""
API_RECEIPT=""
BACKFILL_RECEIPT=""
ACCEPTANCE_RECEIPT=""
SNAPSHOT_OUTPUT=""
CONFIRM_PROMOTION=""
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE_NAME="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --expected-image) EXPECTED_IMAGE="$2"; shift 2 ;;
    --expected-git-sha) EXPECTED_GIT_SHA="$2"; shift 2 ;;
    --expected-service-account) EXPECTED_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --schema-receipt) SCHEMA_RECEIPT="$2"; shift 2 ;;
    --api-receipt) API_RECEIPT="$2"; shift 2 ;;
    --backfill-receipt) BACKFILL_RECEIPT="$2"; shift 2 ;;
    --acceptance-receipt) ACCEPTANCE_RECEIPT="$2"; shift 2 ;;
    --snapshot-output) SNAPSHOT_OUTPUT="$2"; shift 2 ;;
    --confirm-promotion) CONFIRM_PROMOTION="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" && -n "${REVISION}" ]] || {
  echo "--project and exact --revision are required" >&2
  exit 2
}
REQUIRED_CONFIRM="projects/${PROJECT_ID}/locations/${REGION}/services/${SERVICE_NAME}/revisions/${REVISION}:100"

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "service=${SERVICE_NAME} region=${REGION} revision=${REVISION} traffic=100%"
echo "required_confirmation=${REQUIRED_CONFIRM}"
echo "Precondition: additive schema, reconciled backfill, authenticated API, logged-in candidate and business acceptance all have exact target-bound receipts."
if [[ "${APPLY}" != "true" ]]; then
  exit 0
fi

[[ "${CONFIRM_PROMOTION}" == "${REQUIRED_CONFIRM}" ]] || {
  echo "--confirm-promotion must equal ${REQUIRED_CONFIRM}" >&2
  exit 2
}
[[ "${EXPECTED_IMAGE}" =~ ^${REGION}-docker\.pkg\.dev/${PROJECT_ID}/[^/@]+/[^/@]+@sha256:[0-9a-f]{64}$ ]] || {
  echo "--expected-image must be the immutable candidate digest in the selected project and region" >&2
  exit 2
}
[[ "${EXPECTED_GIT_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "--expected-git-sha must be the full candidate Git SHA" >&2
  exit 2
}
[[ "${EXPECTED_SERVICE_ACCOUNT}" =~ ^[a-z0-9-]+@${PROJECT_ID}\.iam\.gserviceaccount\.com$ ]] || {
  echo "--expected-service-account must be the exact runtime identity" >&2
  exit 2
}
[[ -n "${SCHEMA_RECEIPT}" && -f "${SCHEMA_RECEIPT}" ]] || {
  echo "--schema-receipt is required on apply" >&2
  exit 2
}
[[ -n "${API_RECEIPT}" && -f "${API_RECEIPT}" ]] || {
  echo "--api-receipt is required on apply" >&2
  exit 2
}
[[ -n "${BACKFILL_RECEIPT}" && -f "${BACKFILL_RECEIPT}" ]] || {
  echo "--backfill-receipt is required on apply" >&2
  exit 2
}
[[ -n "${ACCEPTANCE_RECEIPT}" && -f "${ACCEPTANCE_RECEIPT}" ]] || {
  echo "--acceptance-receipt is required on apply" >&2
  exit 2
}
[[ -n "${SNAPSHOT_OUTPUT}" && ! -e "${SNAPSHOT_OUTPUT}" && -d "$(dirname "${SNAPSHOT_OUTPUT}")" ]] || {
  echo "--snapshot-output must be a new file in an existing directory" >&2
  exit 2
}
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2
  exit 2
}
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2
  exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }

python3 - "${SCHEMA_RECEIPT}" "${API_RECEIPT}" "${BACKFILL_RECEIPT}" \
  "${ACCEPTANCE_RECEIPT}" "${PROJECT_ID}" "${REGION}" "${SERVICE_NAME}" \
  "${DATASET_ID}" "${LOCATION}" \
  "${REVISION}" "${EXPECTED_IMAGE}" "${EXPECTED_GIT_SHA}" \
  "${EXPECTED_SERVICE_ACCOUNT}" <<'PY'
import json
import sys

schema_path, api_path, backfill_path, acceptance_path, project, region, service, dataset, location, revision, image, git_sha, service_account = sys.argv[1:]

def read(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"receipt is not a JSON object: {path}")
    return payload

schema = read(schema_path)
schema_expected = {
    "receiptType": "monitor_data_contract_v1",
    "project": project,
    "dataset": dataset,
    "location": location,
    "gitSha": git_sha,
    "image": image,
}
if any(schema.get(key) != value for key, value in schema_expected.items()):
    raise SystemExit("schema receipt does not match this exact candidate and dataset")
for key in (
    "schemaReady",
    "sourceViewsReady",
    "apiRoutinesReady",
    "apiRoutinesReadable",
    "publishedStateReadable",
):
    if schema.get(key) is not True:
        raise SystemExit(f"schema receipt is missing {key}")
routine_reads = schema.get("apiRoutineReads") or {}
for routine_name in ("dashboard_events", "dashboard_user_list"):
    if (routine_reads.get(routine_name) or {}).get("readable") is not True:
        raise SystemExit(f"schema receipt has no real read for {routine_name}")
if not schema.get("capturedAt"):
    raise SystemExit("schema receipt has no capture time")

api = read(api_path)
api_expected = {
    "receiptType": "monitor_candidate_api_v1",
    "project": project,
    "region": region,
    "service": service,
    "revision": revision,
    "image": image,
    "gitSha": git_sha,
    "serviceAccount": service_account,
}
if any(api.get(key) != value for key, value in api_expected.items()):
    raise SystemExit("API receipt does not match this exact candidate")
if api.get("authenticatedApiAcceptance") is not True:
    raise SystemExit("authenticated candidate API acceptance is missing")
statuses = api.get("endpointStatus") or {}
for endpoint in ("overview", "regions", "users", "userDetail"):
    if statuses.get(endpoint) != 200:
        raise SystemExit(f"candidate API receipt is missing a 200 readback for {endpoint}")
for key in ("overviewHistoryVisible", "userHistoryVisible", "sourceDiagnosticsExplicit"):
    if api.get(key) is not True:
        raise SystemExit(f"candidate API receipt is missing {key}")
if not api.get("capturedAt") or not api.get("verifiedBy"):
    raise SystemExit("candidate API receipt has no verifier or capture time")

backfill = read(backfill_path)
backfill_expected = {
    "project": project,
    "region": region,
    "dataset": dataset,
    "location": location,
    "expected_image": image,
}
if any(backfill.get(key) != value for key, value in backfill_expected.items()):
    raise SystemExit("backfill receipt does not match this release target")
execution = backfill.get("execution") or {}
execution_status = execution.get("status") if isinstance(execution.get("status"), dict) else execution
execution_name = str(
    execution.get("name")
    or (execution.get("metadata") or {}).get("name")
    or ""
)
if not execution_name or int(execution_status.get("succeededCount") or 0) != 1 or int(execution_status.get("failedCount") or 0) != 0:
    raise SystemExit("backfill receipt has no terminal successful execution")
completed = next(
    (
        item
        for item in execution_status.get("conditions", [])
        if isinstance(item, dict) and item.get("type") == "Completed"
    ),
    None,
)
if completed is not None and str(completed.get("status") or "").lower() != "true":
    raise SystemExit("backfill receipt execution is not terminal-successful")
job_contract = backfill.get("validated_job_contract") or {}
if job_contract.get("image") != image:
    raise SystemExit("backfill Job did not use the candidate image digest")
if not backfill.get("target_at"):
    raise SystemExit("backfill receipt has no frozen target watermark")
pipeline_after = backfill.get("pipeline_after") or []
if not isinstance(pipeline_after, list) or len(pipeline_after) != 1:
    raise SystemExit("backfill receipt has no single published readback")
published = pipeline_after[0]
lease_active = str(published.get("lease_active") or "").strip().lower()
if (
    published.get("source") != "published"
    or published.get("status") != "succeeded"
    or not published.get("published_run_id")
    or not published.get("data_through")
    or lease_active not in {"false", "0"}
):
    raise SystemExit("backfill receipt does not contain a released successful publication")
reconciliation = backfill.get("reconciliation") or []
if not isinstance(reconciliation, list) or len(reconciliation) != 1:
    raise SystemExit("backfill receipt has no reconciliation readback")
row = reconciliation[0]
def count(name):
    try:
        return int(row.get(name) or 0)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"backfill receipt has invalid {name}") from error
if count("successful_run_count") < 1 or count("blocking_failure_count") != 0:
    raise SystemExit("backfill receipt has no clean successful canonical run")
for family in ("question", "answer", "action"):
    if count(f"canonical_{family}_count") != count(f"matched_{family}_count"):
        raise SystemExit(f"backfill receipt does not reconcile {family} facts")

receipt = read(acceptance_path)
expected = {
    "project": project,
    "region": region,
    "service": service,
    "revision": revision,
    "image": image,
    "gitSha": git_sha,
    "serviceAccount": service_account,
}
if any(receipt.get(key) != value for key, value in expected.items()):
    raise SystemExit("acceptance receipt does not match this exact candidate")
if receipt.get("authenticatedAcceptance") is not True:
    raise SystemExit("authenticated candidate acceptance is missing")
if receipt.get("loggedInBrowserAcceptance") is not True:
    raise SystemExit("logged-in browser candidate acceptance is missing")
if receipt.get("historicalDataAcceptance") is not True:
    raise SystemExit("historical data candidate acceptance is missing")
if receipt.get("businessAcceptance") is not True:
    raise SystemExit("business candidate acceptance is missing")
if not receipt.get("capturedAt") or not receipt.get("acceptedBy"):
    raise SystemExit("acceptance receipt has no operator or capture time")
PY

SERVICE_BEFORE="$(gcloud --project="${PROJECT_ID}" run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --format=json)"
REVISION_BEFORE="$(gcloud --project="${PROJECT_ID}" run revisions describe "${REVISION}" \
  --region="${REGION}" --format=json)"
SERVICE_JSON="${SERVICE_BEFORE}" REVISION_JSON="${REVISION_BEFORE}" python3 - \
  "${SERVICE_NAME}" "${REVISION}" "${EXPECTED_IMAGE}" "${EXPECTED_GIT_SHA}" \
  "${EXPECTED_SERVICE_ACCOUNT}" <<'PY'
import json
import os
import sys

service_name, revision_name, image, git_sha, service_account = sys.argv[1:]
service = json.loads(os.environ["SERVICE_JSON"])
revision = json.loads(os.environ["REVISION_JSON"])
actual_service = str((service.get("metadata") or {}).get("name") or service.get("name") or "")
if actual_service and actual_service != service_name:
    raise SystemExit("service readback returned another service")
actual_name = str((revision.get("metadata") or {}).get("name") or revision.get("name") or "")
if actual_name and actual_name != revision_name:
    raise SystemExit("candidate revision readback returned another revision")
spec = revision.get("spec") or {}
containers = spec.get("containers") or []
if len(containers) != 1 or containers[0].get("image") != image:
    raise SystemExit("candidate image digest readback failed")
if spec.get("serviceAccountName") != service_account:
    raise SystemExit("candidate runtime identity readback failed")
labels = (revision.get("metadata") or {}).get("labels") or {}
if labels.get("git-sha") != git_sha:
    raise SystemExit("candidate full Git SHA label readback failed")
conditions = (revision.get("status") or {}).get("conditions") or []
if not any(item.get("type") == "Ready" and str(item.get("status")).lower() == "true" for item in conditions):
    raise SystemExit("candidate revision is not Ready")
traffic = (service.get("status") or {}).get("traffic") or []
if any(item.get("revisionName") == revision_name and int(item.get("percent") or 0) > 0 for item in traffic):
    raise SystemExit("candidate already has production traffic; refresh the release plan")
PY

SERVICE_JSON="${SERVICE_BEFORE}" REVISION_JSON="${REVISION_BEFORE}" python3 - \
  "${SNAPSHOT_OUTPUT}" "${PROJECT_ID}" "${REGION}" "${SERVICE_NAME}" \
  "${REVISION}" "${EXPECTED_IMAGE}" "${EXPECTED_GIT_SHA}" \
  "${EXPECTED_SERVICE_ACCOUNT}" "${SCHEMA_RECEIPT}" "${API_RECEIPT}" \
  "${BACKFILL_RECEIPT}" "${ACCEPTANCE_RECEIPT}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

path, project, region, service, revision, image, git_sha, service_account, schema_path, api_path, backfill_path, acceptance_path = sys.argv[1:]
def digest(receipt_path):
    with open(receipt_path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()
payload = {
    "project": project,
    "region": region,
    "service": service,
    "targetRevision": revision,
    "image": image,
    "gitSha": git_sha,
    "serviceAccount": service_account,
    "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "schemaReceiptSha256": digest(schema_path),
    "apiReceiptSha256": digest(api_path),
    "backfillReceiptSha256": digest(backfill_path),
    "acceptanceReceiptSha256": digest(acceptance_path),
    "serviceBefore": json.loads(os.environ["SERVICE_JSON"]),
    "revisionBefore": json.loads(os.environ["REVISION_JSON"]),
}
with open(path, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY

gcloud --project="${PROJECT_ID}" run services update-traffic "${SERVICE_NAME}" \
  --region="${REGION}" \
  --to-revisions="${REVISION}=100"

SERVICE_AFTER="$(gcloud --project="${PROJECT_ID}" run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --format=json)"
SERVICE_JSON="${SERVICE_AFTER}" python3 - "${REVISION}" <<'PY'
import json
import os
import sys

target = sys.argv[1]
service = json.loads(os.environ["SERVICE_JSON"])
traffic = (service.get("status") or {}).get("traffic") or []
positive = [
    (str(item.get("revisionName") or ""), int(item.get("percent") or 0))
    for item in traffic
    if int(item.get("percent") or 0) > 0
]
if not positive or any(name != target for name, _ in positive) or sum(percent for _, percent in positive) != 100:
    raise SystemExit(f"production traffic readback is not exactly {target}=100: {positive}")
PY

SERVICE_JSON="${SERVICE_AFTER}" python3 - "${SNAPSHOT_OUTPUT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["promotedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["serviceAfter"] = json.loads(os.environ["SERVICE_JSON"])
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(temporary, path)
PY

echo "promotion=complete revision=${REVISION} traffic=100 snapshot=${SNAPSHOT_OUTPUT}"
