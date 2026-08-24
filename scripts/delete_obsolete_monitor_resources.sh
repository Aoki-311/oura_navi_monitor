#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
APPLY="false"
CONFIRM=""
TRANSFER_CONFIG=""
ANALYTICS_START_AT=""
POLICY_IDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --confirm-delete) CONFIRM="$2"; shift 2 ;;
    --transfer-config) TRANSFER_CONFIG="$2"; shift 2 ;;
    --analytics-start-at) ANALYTICS_START_AT="$2"; shift 2 ;;
    --policy-id) POLICY_IDS+=("$2"); shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
OBJECTS=(monitor_answer_events monitor_user_daily monitor_system_hourly monitor_dashboard_snapshots v_monitor_excluded_identities v_requests v_query_suggest_results v_query_suggest_degraded v_sync_telemetry v_ask_audit_events v_followup_resolution_events v_followup_open_result_events v_coverage_gap_workitems v_request_user_metric_events v_answer_action_events v_monitor_event_message_join_keys run_googleapis_com_stderr)
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} dataset=${DATASET_ID} object_count=${#OBJECTS[@]}"
for object in "${OBJECTS[@]}"; do echo "${PROJECT_ID}.${DATASET_ID}.${object}"; done
echo "separate resources: transfer_config=${TRANSFER_CONFIG:-REQUIRED_ON_APPLY}; four obsolete log metrics"
printf 'obsolete_policy=%s\n' "${POLICY_IDS[@]:-NONE_CONFIRMED_BY_INVENTORY}"
echo "canonical raw tables retained: run_googleapis_com_requests,run_googleapis_com_stdout"
echo "raw rows before analytics_start_at=${ANALYTICS_START_AT:-REQUIRED_ON_APPLY} will be deleted in place"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ "${CONFIRM}" == "${PROJECT_ID}.${DATASET_ID}:${#OBJECTS[@]}" ]] || { echo "confirmation must equal ${PROJECT_ID}.${DATASET_ID}:${#OBJECTS[@]}" >&2; exit 2; }
[[ "${TRANSFER_CONFIG}" == projects/*/locations/*/transferConfigs/* ]] || { echo "--transfer-config must be the exact full resource name" >&2; exit 2; }
[[ "${ANALYTICS_START_AT}" == ????-??-??T??:??:??Z ]] || { echo "--analytics-start-at must be an exact UTC second such as 2026-08-24T00:00:00Z" >&2; exit 2; }
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || { echo "approved credential is required" >&2; exit 2; }
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
for policy in "${POLICY_IDS[@]}"; do
  [[ "${policy}" == "projects/${PROJECT_ID}/alertPolicies/"* ]] || {
    echo "--policy-id must be an exact policy in projects/${PROJECT_ID}" >&2; exit 2;
  }
done
bq --project_id="${PROJECT_ID}" --location="${LOCATION}" rm -f --transfer_config "${TRANSFER_CONFIG}"
for object in "${OBJECTS[@]}"; do
  full="${PROJECT_ID}:${DATASET_ID}.${object}"
  if bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show "${full}" >/dev/null 2>&1; then
    bq --project_id="${PROJECT_ID}" --location="${LOCATION}" rm -f "${full}"
  fi
done
for raw_table in run_googleapis_com_requests run_googleapis_com_stdout; do
  full="${PROJECT_ID}:${DATASET_ID}.${raw_table}"
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" show "${full}" >/dev/null || {
    echo "canonical raw table missing: ${PROJECT_ID}.${DATASET_ID}.${raw_table}" >&2; exit 2
  }
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query --use_legacy_sql=false \
    --parameter="analytics_start_at:TIMESTAMP:${ANALYTICS_START_AT}" \
    "DELETE FROM \`${PROJECT_ID}.${DATASET_ID}.${raw_table}\` WHERE timestamp < @analytics_start_at"
done
for metric in lcs_rag_app_qs_total lcs_rag_app_qs_degraded lcs_rag_app_restore_total lcs_rag_app_restore_failed; do
  if gcloud --project="${PROJECT_ID}" logging metrics describe "${metric}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" logging metrics delete "${metric}" --quiet
  fi
done
for policy in "${POLICY_IDS[@]}"; do
  gcloud --project="${PROJECT_ID}" monitoring policies delete "${policy}" --quiet
done
