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
echo "destructive retirement is intentionally disabled in this recovery workflow" >&2
echo "pause legacy DTS scheduling and retain its config, tables, metrics and policies for observation" >&2
echo "any later deletion requires a separately reviewed tool, retention period and explicit authorization" >&2
exit 2
