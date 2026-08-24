#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID=""
TRIGGER_REGION="us-central1"
TRIGGER_NAME="oura-navi-monitor"
REPO_OWNER="Aoki-311"
REPO_NAME="oura_navi_monitor"
BRANCH_PATTERN="^main$"
SERVICE_ACCOUNT=""
IAP_AUDIENCE=""
APPLY="false"
VERIFY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) TRIGGER_REGION="$2"; shift 2 ;;
    --name) TRIGGER_NAME="$2"; shift 2 ;;
    --repo-owner) REPO_OWNER="$2"; shift 2 ;;
    --repo-name) REPO_NAME="$2"; shift 2 ;;
    --branch-pattern) BRANCH_PATTERN="$2"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    --iap-audience) IAP_AUDIENCE="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    --verify) VERIFY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" && -n "${SERVICE_ACCOUNT}" && -n "${IAP_AUDIENCE}" ]] || {
  echo "--project, exact --service-account and exact --iap-audience are required" >&2
  exit 2
}

[[ "${APPLY}" != "true" || "${VERIFY}" != "true" ]] || {
  echo "--apply and --verify are mutually exclusive" >&2
  exit 2
}

PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "${PYTHON_BIN}" ]] || { echo "python3 is required" >&2; exit 2; }
if ! IAP_VALIDATION="$(
  PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" -c \
    'import sys
from scripts.render_runtime_env import validate_iap_audience
try:
    print(validate_iap_audience(sys.argv[1]))
except ValueError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(2)' \
    "${IAP_AUDIENCE}" 2>&1
)"; then
  echo "${IAP_VALIDATION}" >&2
  exit 2
fi
IAP_AUDIENCE="${IAP_VALIDATION}"

INCLUDED_FILES="app/**,frontend/**,deploy/**,scripts/**,sql/**,tests/**,e2e/**,Dockerfile,requirements.txt,requirements-dev.txt,cloudbuild.yaml,.env.example"
IGNORED_FILES="**/.venv/**,**/__pycache__/**,**/*.pyc,**/.DS_Store,docs/**,**/*.md"
if [[ "${APPLY}" == "true" ]]; then
  MODE="apply"
elif [[ "${VERIFY}" == "true" ]]; then
  MODE="verify"
else
  MODE="plan"
fi
echo "mode=${MODE}"
echo "project=${PROJECT_ID} region=${TRIGGER_REGION} trigger=${TRIGGER_NAME}"
echo "repo=${REPO_OWNER}/${REPO_NAME} branch=${BRANCH_PATTERN} service_account=${SERVICE_ACCOUNT}"
echo "iap_audience=${IAP_AUDIENCE}"
if [[ "${APPLY}" != "true" ]]; then
  if [[ "${VERIFY}" != "true" ]]; then
    echo "trigger_contract_verified=false"
    echo "next_push_build_ready=false"
    echo "action_required=run --verify for read-only cloud evidence or separately authorize --apply"
    exit 0
  fi
fi

[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || {
  echo "approved credential is required" >&2
  exit 2
}
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }

trigger_id="$(
  gcloud --project="${PROJECT_ID}" builds triggers list \
    --region="${TRIGGER_REGION}" \
    --filter="name=${TRIGGER_NAME}" \
    --format="value(id)" \
    | head -n 1
)"

if [[ "${VERIFY}" == "true" && -z "${trigger_id}" ]]; then
  echo "trigger not found: ${TRIGGER_NAME}" >&2
  exit 2
fi

common_args=(
  --project="${PROJECT_ID}"
  --region="${TRIGGER_REGION}"
  --description="CI creates a no-traffic OurA Navi Monitor candidate"
  --repo-owner="${REPO_OWNER}"
  --repo-name="${REPO_NAME}"
  --branch-pattern="${BRANCH_PATTERN}"
  --build-config="cloudbuild.yaml"
  --included-files="${INCLUDED_FILES}"
  --ignored-files="${IGNORED_FILES}"
  --service-account="${SERVICE_ACCOUNT}"
  --substitutions="_IAP_AUDIENCE=${IAP_AUDIENCE}"
  --include-logs-with-status
  --require-approval
)

if [[ -n "${trigger_id}" ]]; then
  if [[ "${APPLY}" == "true" ]]; then
    gcloud builds triggers update github "${trigger_id}" "${common_args[@]}"
  fi
else
  gcloud builds triggers create github --name="${TRIGGER_NAME}" "${common_args[@]}"
fi

trigger_id="$(
  gcloud --project="${PROJECT_ID}" builds triggers list \
    --region="${TRIGGER_REGION}" \
    --filter="name=${TRIGGER_NAME}" \
    --format="value(id)" \
    | head -n 1
)"
[[ -n "${trigger_id}" ]] || { echo "trigger readback failed" >&2; exit 2; }

TRIGGER_JSON="$(mktemp)"
trap 'rm -f "${TRIGGER_JSON}"' EXIT
gcloud --project="${PROJECT_ID}" builds triggers describe "${trigger_id}" \
  --region="${TRIGGER_REGION}" --format=json > "${TRIGGER_JSON}"
PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" \
  "${ROOT_DIR}/scripts/verify_github_trigger_contract.py" \
  --trigger-json "${TRIGGER_JSON}" \
  --project "${PROJECT_ID}" \
  --name "${TRIGGER_NAME}" \
  --repo-owner "${REPO_OWNER}" \
  --repo-name "${REPO_NAME}" \
  --branch-pattern "${BRANCH_PATTERN}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --iap-audience "${IAP_AUDIENCE}" \
  --included-files "${INCLUDED_FILES}" \
  --ignored-files "${IGNORED_FILES}"
echo "next_push_build_ready=true"
