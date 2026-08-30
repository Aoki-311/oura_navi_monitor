#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
REGION="us-central1"
SERVICE_NAME="oura-navi-monitor"
DATASET_ID="oura_navi_monitor"
LOCATION="US"
SOURCE_SERVICE="lcs-rag-app"
FIRESTORE_DATABASE="lcs-user-data"
RELEASE_LOCK_COLLECTION="monitor_release_locks"
REVISION=""
EXPECTED_IMAGE=""
EXPECTED_GIT_SHA=""
EXPECTED_BUILD_ID=""
EXPECTED_SERVICE_ACCOUNT=""
EXPECTED_JOB_SERVICE_ACCOUNT=""
LEGACY_TRANSFER_RESOURCE=""
SCHEMA_RECEIPT=""
API_RECEIPT=""
BACKFILL_RECEIPT=""
ACCEPTANCE_RECEIPT=""
ACTIVATION_RECEIPT=""
DTS_PAUSE_SNAPSHOT=""
DTS_45M_RECEIPT=""
DTS_72H_RECEIPT=""
SNAPSHOT_OUTPUT=""
CONFIRM_PROMOTION=""
APPLY="false"
CREDENTIAL_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE_NAME="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --firestore-database) FIRESTORE_DATABASE="$2"; shift 2 ;;
    --release-lock-collection) RELEASE_LOCK_COLLECTION="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --expected-image) EXPECTED_IMAGE="$2"; shift 2 ;;
    --expected-git-sha) EXPECTED_GIT_SHA="$2"; shift 2 ;;
    --expected-build-id) EXPECTED_BUILD_ID="$2"; shift 2 ;;
    --expected-service-account) EXPECTED_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --expected-job-service-account) EXPECTED_JOB_SERVICE_ACCOUNT="$2"; shift 2 ;;
    --legacy-transfer-resource) LEGACY_TRANSFER_RESOURCE="$2"; shift 2 ;;
    --schema-receipt) SCHEMA_RECEIPT="$2"; shift 2 ;;
    --api-receipt) API_RECEIPT="$2"; shift 2 ;;
    --backfill-receipt) BACKFILL_RECEIPT="$2"; shift 2 ;;
    --acceptance-receipt) ACCEPTANCE_RECEIPT="$2"; shift 2 ;;
    --activation-receipt) ACTIVATION_RECEIPT="$2"; shift 2 ;;
    --dts-pause-snapshot) DTS_PAUSE_SNAPSHOT="$2"; shift 2 ;;
    --dts-45m-receipt) DTS_45M_RECEIPT="$2"; shift 2 ;;
    --dts-72h-receipt) DTS_72H_RECEIPT="$2"; shift 2 ;;
    --snapshot-output) SNAPSHOT_OUTPUT="$2"; shift 2 ;;
    --confirm-promotion) CONFIRM_PROMOTION="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
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
[[ "${EXPECTED_BUILD_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
  echo "--expected-build-id must be the exact Cloud Build UUID" >&2
  exit 2
}
[[ "${EXPECTED_SERVICE_ACCOUNT}" =~ ^[a-z0-9-]+@${PROJECT_ID}\.iam\.gserviceaccount\.com$ ]] || {
  echo "--expected-service-account must be the exact runtime identity" >&2
  exit 2
}
[[ "${EXPECTED_JOB_SERVICE_ACCOUNT}" =~ ^[a-z0-9-]+@${PROJECT_ID}\.iam\.gserviceaccount\.com$ ]] || {
  echo "--expected-job-service-account must be the exact refresh Job identity" >&2
  exit 2
}
[[ "${LEGACY_TRANSFER_RESOURCE}" =~ ^projects/${PROJECT_ID}/locations/[^/[:space:]]+/transferConfigs/[^/[:space:]]+$ ]] || {
  echo "--legacy-transfer-resource must be one exact transfer config in the selected project" >&2
  exit 2
}
[[ "${FIRESTORE_DATABASE}" == "lcs-user-data" ]] || {
  echo "--firestore-database must equal the governed named database lcs-user-data" >&2
  exit 2
}
[[ "${RELEASE_LOCK_COLLECTION}" == "monitor_release_locks" ]] || {
  echo "--release-lock-collection must equal the governed collection monitor_release_locks" >&2
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
[[ -n "${ACTIVATION_RECEIPT}" && -f "${ACTIVATION_RECEIPT}" ]] || {
  echo "--activation-receipt is required on apply" >&2
  exit 2
}
[[ -n "${DTS_PAUSE_SNAPSHOT}" && -f "${DTS_PAUSE_SNAPSHOT}" ]] || {
  echo "--dts-pause-snapshot is required on apply" >&2
  exit 2
}
[[ -n "${DTS_45M_RECEIPT}" && -f "${DTS_45M_RECEIPT}" ]] || {
  echo "--dts-45m-receipt is required on apply" >&2
  exit 2
}
[[ -n "${DTS_72H_RECEIPT}" && -f "${DTS_72H_RECEIPT}" ]] || {
  echo "--dts-72h-receipt is required on apply" >&2
  exit 2
}
[[ -n "${SNAPSHOT_OUTPUT}" && -d "$(dirname "${SNAPSHOT_OUTPUT}")" ]] || {
  echo "--snapshot-output must be a file in an existing directory" >&2
  exit 2
}
python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
SCHEDULER_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.scheduler_name)')"
JOB_TIMEOUT_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_timeout_minutes)')"

validate_fresh_candidate_receipts() {
  env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_CURRENT}" \
    REVISION_JSON="${REVISION_CURRENT}" \
    python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" freshness \
    "${PROMOTION_STATE_ARGS[@]}" >/dev/null
}

CURRENT_SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
CURRENT_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${CURRENT_JOB_JSON}" python3 \
  "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${EXPECTED_IMAGE}" \
  --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null

CURRENT_TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${LEGACY_TRANSFER_RESOURCE}")"


SERVICE_CURRENT="$(gcloud --project="${PROJECT_ID}" run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --format=json)"
REVISION_CURRENT="$(gcloud --project="${PROJECT_ID}" run revisions describe "${REVISION}" \
  --region="${REGION}" --format=json)"
PROMOTION_STATE_ARGS=(
  --path "${SNAPSHOT_OUTPUT}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --service "${SERVICE_NAME}"
  --revision "${REVISION}"
  --image "${EXPECTED_IMAGE}"
  --git-sha "${EXPECTED_GIT_SHA}"
  --build-id "${EXPECTED_BUILD_ID}"
  --service-account "${EXPECTED_SERVICE_ACCOUNT}"
  --expected-job-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}"
  --legacy-transfer-resource "${LEGACY_TRANSFER_RESOURCE}"
  --dataset "${DATASET_ID}"
  --location "${LOCATION}"
  --source-service "${SOURCE_SERVICE}"
  --job "${JOB_NAME}"
  --scheduler "${SCHEDULER_NAME}"
  --job-timeout-minutes "${JOB_TIMEOUT_MINUTES}"
  --firestore-database "${FIRESTORE_DATABASE}"
  --release-lock-collection "${RELEASE_LOCK_COLLECTION}"
  --schema-receipt "${SCHEMA_RECEIPT}"
  --api-receipt "${API_RECEIPT}"
  --backfill-receipt "${BACKFILL_RECEIPT}"
  --acceptance-receipt "${ACCEPTANCE_RECEIPT}"
  --activation-receipt "${ACTIVATION_RECEIPT}"
  --dts-pause-snapshot "${DTS_PAUSE_SNAPSHOT}"
  --dts-45m-receipt "${DTS_45M_RECEIPT}"
  --dts-72h-receipt "${DTS_72H_RECEIPT}"
)

STATE_ENV=(
  "CURRENT_SCHEDULER_JSON=${CURRENT_SCHEDULER_JSON}"
  "CURRENT_JOB_JSON=${CURRENT_JOB_JSON}"
  "CURRENT_TRANSFER_JSON=${CURRENT_TRANSFER_JSON}"
)

env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_CURRENT}" REVISION_JSON="${REVISION_CURRENT}" \
  python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" prepare \
  "${PROMOTION_STATE_ARGS[@]}" >/dev/null

# Classify the read-only pre-lock state so stale evidence is rejected before a
# new traffic mutation acquires the shared cloud lock. Post/final states do not
# authorize a new traffic mutation, so they skip this gate; post can continue
# only after a separately audited lock release, while exact final may recover.
PRELOCK_PROMOTION_STATE="$(env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_CURRENT}" \
  REVISION_JSON="${REVISION_CURRENT}" \
  python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" classify \
  "${PROMOTION_STATE_ARGS[@]}")"
if [[ "${PRELOCK_PROMOTION_STATE}" == "pre" ]]; then
  validate_fresh_candidate_receipts
elif [[ "${PRELOCK_PROMOTION_STATE}" != "post" && "${PRELOCK_PROMOTION_STATE}" != "final" ]]; then
  echo "promotion_state_invalid: unexpected pre-lock live state" >&2
  exit 2
fi

# This Firestore transaction is the cross-host CAS owner for the service.
# A pre/post intent never auto-recovers an existing lock: without a provider
# fencing token, the script cannot prove that the original holder has stopped.
# A completed receipt may recover only to perform final readback and release;
# that branch never calls update-traffic.
PROMOTION_LOCK_ARGS=(
  acquire
  --credential-file "${CREDENTIAL_FILE}"
  --promotion-state "${SNAPSHOT_OUTPUT}"
)
if [[ "${PRELOCK_PROMOTION_STATE}" == "final" ]]; then
  PROMOTION_LOCK_ARGS+=(--allow-final-recovery)
elif [[ "${PRELOCK_PROMOTION_STATE}" == "post" ]]; then
  PROMOTION_LOCK_ARGS+=(--allow-post-recovery)
fi
PROMOTION_LOCK_STATE="$(PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${ROOT_DIR}" python3 \
  "${ROOT_DIR}/scripts/promotion_release_lock.py" \
  "${PROMOTION_LOCK_ARGS[@]}")"
echo "promotion_lock=${PROMOTION_LOCK_STATE}"

# Re-read all promotion and refresh authorities after the cloud CAS is held.
# The pre-lock reads only create/recover the durable intent; they never
# authorize traffic mutation across a TOCTOU window.
CURRENT_SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
CURRENT_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${CURRENT_JOB_JSON}" python3 \
  "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${EXPECTED_IMAGE}" \
  --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null
CURRENT_TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${LEGACY_TRANSFER_RESOURCE}")"
SERVICE_CURRENT="$(gcloud --project="${PROJECT_ID}" run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --format=json)"
REVISION_CURRENT="$(gcloud --project="${PROJECT_ID}" run revisions describe "${REVISION}" \
  --region="${REGION}" --format=json)"
STATE_ENV=(
  "CURRENT_SCHEDULER_JSON=${CURRENT_SCHEDULER_JSON}"
  "CURRENT_JOB_JSON=${CURRENT_JOB_JSON}"
  "CURRENT_TRANSFER_JSON=${CURRENT_TRANSFER_JSON}"
)

PROMOTION_STATE="$(env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_CURRENT}" \
  REVISION_JSON="${REVISION_CURRENT}" \
  python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" classify \
  "${PROMOTION_STATE_ARGS[@]}")"

# Freeze the action authorized by the pre-lock readback. A post/final recovery
# is a readback/finalization operation and can never be reinterpreted as a new
# traffic mutation if the service changes while the lock is being acquired.
if [[ "${PROMOTION_STATE}" != "${PRELOCK_PROMOTION_STATE}" ]]; then
  echo "promotion_state_invalid: live state changed while acquiring the promotion lock" >&2
  exit 2
fi

if [[ "${PROMOTION_STATE}" == "final" ]]; then
  PROMOTION_LOCK_RELEASE="$(PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${ROOT_DIR}" python3 \
    "${ROOT_DIR}/scripts/promotion_release_lock.py" release \
    --credential-file "${CREDENTIAL_FILE}" \
    --promotion-state "${SNAPSHOT_OUTPUT}")"
  echo "promotion_lock=${PROMOTION_LOCK_RELEASE}"
  echo "promotion=already-complete revision=${REVISION} traffic=100 snapshot=${SNAPSHOT_OUTPUT}"
  exit 0
fi

UPDATE_RETURN_CODE=-1
if [[ "${PROMOTION_STATE}" == "pre" ]]; then
  # This is the actual traffic authorization point. Recompute freshness from
  # the exact receipt bytes bound into the durable intent after every slow
  # lock-held readback and immediately before update-traffic. Failure retains
  # the lock so no concurrent execution can delete another holder's authority.
  validate_fresh_candidate_receipts
  set +e
  gcloud --project="${PROJECT_ID}" run services update-traffic "${SERVICE_NAME}" \
    --region="${REGION}" \
    --to-revisions="${REVISION}=100"
  UPDATE_RETURN_CODE=$?
  set -e
elif [[ "${PROMOTION_STATE}" != "post" ]]; then
  echo "promotion_state_invalid: unexpected live state" >&2
  exit 2
fi

# Re-read the refresh authorities as well as both Cloud Run resources after the
# possible state change. The global promotion lock remains held, and a sibling
# refresh drift must not be hidden by a successful traffic update.
CURRENT_SCHEDULER_JSON="$(gcloud --project="${PROJECT_ID}" scheduler jobs describe \
  "${SCHEDULER_NAME}" --location="${REGION}" --format=json)"
CURRENT_JOB_JSON="$(gcloud --project="${PROJECT_ID}" run jobs describe "${JOB_NAME}" \
  --region="${REGION}" --format=json)"
JOB_DESCRIPTION_JSON="${CURRENT_JOB_JSON}" python3 \
  "${ROOT_DIR}/scripts/validate_refresh_job.py" \
  --expected-image "${EXPECTED_IMAGE}" \
  --expected-service-account "${EXPECTED_JOB_SERVICE_ACCOUNT}" \
  --project "${PROJECT_ID}" --dataset "${DATASET_ID}" \
  --location "${LOCATION}" --source-service "${SOURCE_SERVICE}" \
  --timeout-minutes "${JOB_TIMEOUT_MINUTES}" >/dev/null
CURRENT_TRANSFER_JSON="$(bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show \
  --transfer_config --format=prettyjson "${LEGACY_TRANSFER_RESOURCE}")"
STATE_ENV=(
  "CURRENT_SCHEDULER_JSON=${CURRENT_SCHEDULER_JSON}"
  "CURRENT_JOB_JSON=${CURRENT_JOB_JSON}"
  "CURRENT_TRANSFER_JSON=${CURRENT_TRANSFER_JSON}"
)
SERVICE_AFTER="$(gcloud --project="${PROJECT_ID}" run services describe "${SERVICE_NAME}" \
  --region="${REGION}" --format=json)"
REVISION_AFTER="$(gcloud --project="${PROJECT_ID}" run revisions describe "${REVISION}" \
  --region="${REGION}" --format=json)"
POST_STATE="$(env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_AFTER}" \
  REVISION_JSON="${REVISION_AFTER}" \
  python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" classify \
  "${PROMOTION_STATE_ARGS[@]}")"
if [[ "${POST_STATE}" != "post" ]]; then
  echo "promotion_state_invalid: traffic update did not reach exact target=100; intent retained" >&2
  exit 2
fi

env "${STATE_ENV[@]}" SERVICE_JSON="${SERVICE_AFTER}" REVISION_JSON="${REVISION_AFTER}" \
  python3 "${ROOT_DIR}/scripts/promotion_receipt_state.py" finalize \
  "${PROMOTION_STATE_ARGS[@]}" \
  --update-return-code "${UPDATE_RETURN_CODE}"

PROMOTION_LOCK_RELEASE="$(PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${ROOT_DIR}" python3 \
  "${ROOT_DIR}/scripts/promotion_release_lock.py" release \
  --credential-file "${CREDENTIAL_FILE}" \
  --promotion-state "${SNAPSHOT_OUTPUT}")"
echo "promotion_lock=${PROMOTION_LOCK_RELEASE}"

echo "promotion=complete revision=${REVISION} traffic=100 snapshot=${SNAPSHOT_OUTPUT}"
