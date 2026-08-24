#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
TRIGGER_REGION="us-central1"
TRIGGER_NAME="oura-navi-monitor"
REPO_OWNER="Aoki-311"
REPO_NAME="oura_navi_monitor"
BRANCH_PATTERN="^main$"
SERVICE_ACCOUNT=""
APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) TRIGGER_REGION="$2"; shift 2 ;;
    --name) TRIGGER_NAME="$2"; shift 2 ;;
    --repo-owner) REPO_OWNER="$2"; shift 2 ;;
    --repo-name) REPO_NAME="$2"; shift 2 ;;
    --branch-pattern) BRANCH_PATTERN="$2"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" && -n "${SERVICE_ACCOUNT}" ]] || {
  echo "--project and exact --service-account are required" >&2
  exit 2
}

INCLUDED_FILES="app/**,frontend/**,deploy/**,scripts/**,sql/**,tests/**,e2e/**,Dockerfile,requirements.txt,requirements-dev.txt,cloudbuild.yaml,.env.example"
IGNORED_FILES="**/.venv/**,**/__pycache__/**,**/*.pyc,**/.DS_Store,docs/**,**/*.md"
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} region=${TRIGGER_REGION} trigger=${TRIGGER_NAME}"
echo "repo=${REPO_OWNER}/${REPO_NAME} branch=${BRANCH_PATTERN} service_account=${SERVICE_ACCOUNT}"
if [[ "${APPLY}" != "true" ]]; then
  exit 0
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
  --include-logs-with-status
  --require-approval
)

if [[ -n "${trigger_id}" ]]; then
  gcloud builds triggers update github "${trigger_id}" "${common_args[@]}"
else
  gcloud builds triggers create github --name="${TRIGGER_NAME}" "${common_args[@]}"
fi
