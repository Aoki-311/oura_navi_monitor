#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
TRIGGER_REGION="${TRIGGER_REGION:-us-central1}"
TRIGGER_NAME="${TRIGGER_NAME:-oura-navi-monitor-main}"
TRIGGER_DESCRIPTION="${TRIGGER_DESCRIPTION:-CI/CD for oura_navi_monitor (manual approval required)}"
REPO_OWNER="${REPO_OWNER:-Aoki-311}"
REPO_NAME="${REPO_NAME:-oura_navi_monitor}"
BRANCH_PATTERN="${BRANCH_PATTERN:-^main$}"
BUILD_CONFIG="${BUILD_CONFIG:-cloudbuild.yaml}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-projects/${PROJECT_ID}/serviceAccounts/lcs-agent@lcs-developer-483404.iam.gserviceaccount.com}"
INCLUDED_FILES="${INCLUDED_FILES:-app/**,frontend/**,deploy/**,scripts/**,sql/**,Dockerfile,requirements.txt,cloudbuild.yaml,.env.example}"
IGNORED_FILES="${IGNORED_FILES:-**/.venv/**,**/__pycache__/**,**/*.pyc,**/.DS_Store,tests/**,docs/**,**/*.md}"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }

gcloud config set project "${PROJECT_ID}" >/dev/null

trigger_id="$(
  gcloud builds triggers list \
    --project="${PROJECT_ID}" \
    --region="${TRIGGER_REGION}" \
    --filter="name=${TRIGGER_NAME}" \
    --format="value(id)" \
    | head -n1
)"

if [[ -n "${trigger_id}" ]]; then
  echo "Updating existing trigger: ${TRIGGER_NAME} (${trigger_id})"
  gcloud builds triggers update github "${trigger_id}" \
    --project="${PROJECT_ID}" \
    --region="${TRIGGER_REGION}" \
    --description="${TRIGGER_DESCRIPTION}" \
    --repo-owner="${REPO_OWNER}" \
    --repo-name="${REPO_NAME}" \
    --branch-pattern="${BRANCH_PATTERN}" \
    --build-config="${BUILD_CONFIG}" \
    --included-files="${INCLUDED_FILES}" \
    --ignored-files="${IGNORED_FILES}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --include-logs-with-status \
    --require-approval
else
  echo "Creating trigger: ${TRIGGER_NAME}"
  set +e
  create_out="$(
    gcloud builds triggers create github \
      --project="${PROJECT_ID}" \
      --region="${TRIGGER_REGION}" \
      --name="${TRIGGER_NAME}" \
      --description="${TRIGGER_DESCRIPTION}" \
      --repo-owner="${REPO_OWNER}" \
      --repo-name="${REPO_NAME}" \
      --branch-pattern="${BRANCH_PATTERN}" \
      --build-config="${BUILD_CONFIG}" \
      --included-files="${INCLUDED_FILES}" \
      --ignored-files="${IGNORED_FILES}" \
      --service-account="${SERVICE_ACCOUNT}" \
      --include-logs-with-status \
      --require-approval 2>&1
  )"
  code=$?
  set -e
  if [[ ${code} -ne 0 ]]; then
    echo "${create_out}"
    if echo "${create_out}" | grep -q "Repository mapping does not exist"; then
      echo
      echo "Repository mapping is missing."
      echo "Open this URL once with a human admin account, connect ${REPO_OWNER}/${REPO_NAME}, then rerun this script:"
      echo "https://console.cloud.google.com/cloud-build/triggers;region=global/connect?project=${PROJECT_ID}"
    fi
    exit "${code}"
  fi
  echo "${create_out}"
fi

echo
echo "Trigger ready. Any push to ${BRANCH_PATTERN} will start a build in PENDING_APPROVAL."
echo "Included files: ${INCLUDED_FILES}"
echo "Ignored files:  ${IGNORED_FILES}"
echo "Approve deploy with:"
echo "  gcloud beta builds approve <BUILD_ID> --project=${PROJECT_ID}"
