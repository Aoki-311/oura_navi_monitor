#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
APPLY="false"
PYTHON_BIN="python3"
CREDENTIAL_FILE=""
NEWS_USAGE_ONLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --credential-file) CREDENTIAL_FILE="$2"; shift 2 ;;
    --news-usage-only) NEWS_USAGE_ONLY="true"; shift ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILES=(create_dataset.sql create_fact_tables.sql create_aggregates.sql)
if [[ "${NEWS_USAGE_ONLY}" == "true" ]]; then
  SQL_FILES=(create_news_usage_tables.sql create_news_usage_source.sql create_source_tables.sql)
fi

echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} location=${LOCATION} dataset=${DATASET_ID}"
printf 'sql=%s\n' "${SQL_FILES[@]}"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/credential_preflight.py" \
  --credential-file "${CREDENTIAL_FILE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
source "${ROOT_DIR}/scripts/credential_shell.sh"
monitor_install_google_credential_wrappers "${CREDENTIAL_FILE}"

TMP_SQL="$(mktemp)"
trap 'rm -f "${TMP_SQL}"' EXIT
for name in "${SQL_FILES[@]}"; do
  MONITOR_PROJECT_ID="${PROJECT_ID}" MONITOR_BQ_DATASET="${DATASET_ID}" MONITOR_BQ_LOCATION="${LOCATION}" \
    PYTHONPATH="${ROOT_DIR}" "${PYTHON_BIN}" - "${name}" "${NEWS_USAGE_ONLY}" > "${TMP_SQL}" <<'PY'
import sys
from app.jobs.refresh_analytics import render_sql
from app.jobs.news_usage_ingestion import render_news_usage_sql
from app.settings import Settings
from scripts.render_runtime_env import news_usage_environment

name, news_only = sys.argv[1:]
settings = Settings()
if news_only == "true":
    expected = news_usage_environment()
    if not expected:
        raise SystemExit("news usage must be configured in deploy/cloudrun.env.yaml")
    settings = settings.model_copy(update={key.lower(): value for key, value in expected.items()})
    if settings.news_usage_configuration_status != "enabled":
        raise SystemExit("news usage release configuration is invalid")
renderer = render_news_usage_sql if name.startswith("create_news_usage_") else render_sql
print(renderer(name, settings))
PY
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query --use_legacy_sql=false < "${TMP_SQL}"
done
