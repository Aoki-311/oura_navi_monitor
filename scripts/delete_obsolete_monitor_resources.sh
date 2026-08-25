#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
DATASET_ID="oura_navi_monitor"
LOCATION="US"
APPLY="false"
CONFIRM=""
TRANSFER_CONFIG=""
HISTORY_CONFIRM=""
POLICY_IDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --dataset) DATASET_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --confirm-delete) CONFIRM="$2"; shift 2 ;;
    --transfer-config) TRANSFER_CONFIG="$2"; shift 2 ;;
    --history-confirm) HISTORY_CONFIRM="$2"; shift 2 ;;
    --policy-id) POLICY_IDS+=("$2"); shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
OBJECTS=(monitor_answer_events monitor_user_daily monitor_system_hourly monitor_dashboard_snapshots v_monitor_excluded_identities v_requests v_query_suggest_results v_query_suggest_degraded v_sync_telemetry v_ask_audit_events v_followup_resolution_events v_followup_open_result_events v_coverage_gap_workitems v_request_user_metric_events v_answer_action_events v_monitor_event_message_join_keys run_googleapis_com_varlog_system)
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan)"
echo "project=${PROJECT_ID} dataset=${DATASET_ID} object_count=${#OBJECTS[@]}"
for object in "${OBJECTS[@]}"; do echo "${PROJECT_ID}.${DATASET_ID}.${object}"; done
echo "separate resources: transfer_config=${TRANSFER_CONFIG:-REQUIRED_ON_APPLY}; four obsolete log metrics"
printf 'obsolete_policy=%s\n' "${POLICY_IDS[@]:-NONE_CONFIRMED_BY_INVENTORY}"
echo "historical raw sources retained without row deletion: run_googleapis_com_requests,run_googleapis_com_stdout,run_googleapis_com_stderr"
echo "history_deletion_gate=${HISTORY_CONFIRM:-REQUIRED_ON_APPLY_AND_MUST_HAVE_ZERO_ISSUES}"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ "${CONFIRM}" == "${PROJECT_ID}.${DATASET_ID}:${#OBJECTS[@]}" ]] || { echo "confirmation must equal ${PROJECT_ID}.${DATASET_ID}:${#OBJECTS[@]}" >&2; exit 2; }
[[ "${TRANSFER_CONFIG}" == projects/*/locations/*/transferConfigs/* ]] || { echo "--transfer-config must be the exact full resource name" >&2; exit 2; }
IFS=':' read -r HISTORY_OWNER HISTORY_START HISTORY_END HISTORY_QUESTIONS HISTORY_ANSWERS HISTORY_ISSUES HISTORY_EXTRA <<< "${HISTORY_CONFIRM}"
[[ "${HISTORY_OWNER}" == "${PROJECT_ID}.${DATASET_ID}" \
  && "${HISTORY_START}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ \
  && "${HISTORY_END}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ \
  && "${HISTORY_QUESTIONS}" =~ ^[0-9]+$ \
  && "${HISTORY_ANSWERS}" =~ ^[0-9]+$ \
  && "${HISTORY_ISSUES}" =~ ^[0-9]+$ \
  && "${HISTORY_ISSUES}" == "0" \
  && -z "${HISTORY_EXTRA}" ]] || {
  echo "--history-confirm must be the exact verified rebuild confirmation" >&2; exit 2;
}
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || { echo "approved credential is required" >&2; exit 2; }
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v bq >/dev/null 2>&1 || { echo "bq not found" >&2; exit 2; }
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }
HISTORY_VERIFIED="$(
  bq --project_id="${PROJECT_ID}" --location="${LOCATION}" query \
    --use_legacy_sql=false --format=csv --quiet \
    --parameter="history_confirm:STRING:${HISTORY_CONFIRM}" \
    "SELECT COUNTIF(source = 'history_rebuild' AND status = 'succeeded' AND published_run_id = @history_confirm) FROM \`${PROJECT_ID}.${DATASET_ID}.pipeline_state\`" \
    | tail -n 1
)"
[[ "${HISTORY_VERIFIED}" =~ ^[1-9][0-9]*$ ]] || {
  echo "obsolete deletion requires the matching verified history rebuild marker" >&2; exit 2;
}
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
for metric in lcs_rag_app_qs_total lcs_rag_app_qs_degraded lcs_rag_app_restore_total lcs_rag_app_restore_failed; do
  if gcloud --project="${PROJECT_ID}" logging metrics describe "${metric}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" logging metrics delete "${metric}" --quiet
  fi
done
for policy in "${POLICY_IDS[@]}"; do
  gcloud --project="${PROJECT_ID}" monitoring policies delete "${policy}" --quiet
done
