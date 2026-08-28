#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
REGION="us-central1"
SERVICE_NAME="oura-navi-monitor"
REVISION=""
EXPECTED_IMAGE=""
EXPECTED_GIT_SHA=""
EXPECTED_SERVICE_ACCOUNT=""
ACCEPTANCE_RECEIPT=""
SNAPSHOT_OUTPUT=""
CONFIRM_PROMOTION=""
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE_NAME="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --expected-image) EXPECTED_IMAGE="$2"; shift 2 ;;
    --expected-git-sha) EXPECTED_GIT_SHA="$2"; shift 2 ;;
    --expected-service-account) EXPECTED_SERVICE_ACCOUNT="$2"; shift 2 ;;
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
echo "Precondition: authenticated candidate acceptance and business acceptance are both recorded in one target-bound receipt."
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

python3 - "${ACCEPTANCE_RECEIPT}" "${PROJECT_ID}" "${REGION}" "${SERVICE_NAME}" \
  "${REVISION}" "${EXPECTED_IMAGE}" "${EXPECTED_GIT_SHA}" \
  "${EXPECTED_SERVICE_ACCOUNT}" <<'PY'
import json
import sys

path, project, region, service, revision, image, git_sha, service_account = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    receipt = json.load(handle)
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
  "${EXPECTED_SERVICE_ACCOUNT}" "${ACCEPTANCE_RECEIPT}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

path, project, region, service, revision, image, git_sha, service_account, acceptance_path = sys.argv[1:]
with open(acceptance_path, "rb") as handle:
    acceptance_sha = hashlib.sha256(handle.read()).hexdigest()
payload = {
    "project": project,
    "region": region,
    "service": service,
    "targetRevision": revision,
    "image": image,
    "gitSha": git_sha,
    "serviceAccount": service_account,
    "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "acceptanceReceiptSha256": acceptance_sha,
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
