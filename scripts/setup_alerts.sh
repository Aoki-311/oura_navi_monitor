#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
SOURCE_SERVICE="lcs-rag-app"
CHANNEL=""
APPLY="false"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WARNING_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.no_success_warning_minutes)')"
CRITICAL_MINUTES="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.no_success_critical_minutes)')"
JOB_NAME="$(PYTHONPATH="${ROOT_DIR}" python3 -c 'from app.refresh_policy import REFRESH_POLICY; print(REFRESH_POLICY.job_name)')"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --source-service) SOURCE_SERVICE="$2"; shift 2 ;;
    --notification-channel) CHANNEL="$2"; shift 2 ;;
    --apply) APPLY="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${PROJECT_ID}" ]] || { echo "--project is required" >&2; exit 2; }
echo "mode=$([[ "${APPLY}" == "true" ]] && echo apply || echo plan) project=${PROJECT_ID}"
echo "metrics=lcs_rag_app_5xx_count,lcs_rag_app_answer_failed,lcs_rag_app_monitor_event_failed,oura_navi_monitor_refresh_success,oura_navi_monitor_refresh_failed,oura_navi_monitor_refresh_locked,oura_navi_monitor_rows_quarantined,oura_navi_monitor_axis_unmeasured"
echo "policies=HTTP 5xx,answer failure spike,analytics event emission failure,pipeline refresh failure,lease contention,row quarantine,producer axis issue,pipeline stale warning,pipeline stale critical"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ "${CHANNEL}" == projects/*/notificationChannels/* ]] || { echo "--notification-channel must be an existing exact resource" >&2; exit 2; }
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || { echo "approved credential is required" >&2; exit 2; }
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && "${GOOGLE_APPLICATION_CREDENTIALS}" != "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]]; then
  echo "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential" >&2; exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}"
command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found" >&2; exit 2; }

upsert_metric() {
  local name="$1" filter="$2"
  if gcloud --project="${PROJECT_ID}" logging metrics describe "${name}" >/dev/null 2>&1; then
    gcloud --project="${PROJECT_ID}" logging metrics update "${name}" --log-filter="${filter}" --description="${name}"
  else
    gcloud --project="${PROJECT_ID}" logging metrics create "${name}" --log-filter="${filter}" --description="${name}"
  fi
}
upsert_metric "lcs_rag_app_5xx_count" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND logName=\"projects/${PROJECT_ID}/logs/run.googleapis.com%2Frequests\" AND httpRequest.status>=500"
upsert_metric "lcs_rag_app_answer_failed" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND jsonPayload.monitor_event=true AND jsonPayload.event_family=\"answer_completed\" AND (jsonPayload.payload_json=~\"\\\"terminal\\\":\\\"(error|cancelled)\\\"\" OR jsonPayload.payload_json=~\"\\\"runtime_status\\\":\\\"(failed|cancelled)\\\"\")"
upsert_metric "lcs_rag_app_monitor_event_failed" "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SOURCE_SERVICE}\" AND textPayload:\"monitor_event_not_emitted\""
upsert_metric "oura_navi_monitor_refresh_success" "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status=\"succeeded\""
upsert_metric "oura_navi_monitor_refresh_failed" "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status=\"failed\""
upsert_metric "oura_navi_monitor_refresh_locked" "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status=\"skipped_locked\""
upsert_metric "oura_navi_monitor_rows_quarantined" "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\" AND jsonPayload.monitor_pipeline_quality_event=true AND jsonPayload.disposition=\"row_quarantined\""
upsert_metric "oura_navi_monitor_axis_unmeasured" "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\" AND jsonPayload.monitor_pipeline_quality_event=true AND jsonPayload.disposition=\"axis_unmeasured\""

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
python3 - "${TMP_DIR}" "${CHANNEL}" "${WARNING_MINUTES}" "${CRITICAL_MINUTES}" "${JOB_NAME}" <<'PY'
import json, pathlib, sys
root, channel = pathlib.Path(sys.argv[1]), sys.argv[2]
warning_seconds = int(sys.argv[3]) * 60
critical_seconds = int(sys.argv[4]) * 60
job_name = sys.argv[5]
job_filter = f' AND resource.labels.job_name="{job_name}"'
policies = {
  "http-5xx.json": {"displayName":"OurA Monitor - HTTP 5xx","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"5xx >= 3 in 10m","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_5xx_count\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":3,"duration":"0s","aggregations":[{"alignmentPeriod":"600s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "answer-failure.json": {"displayName":"OurA Monitor - answer failure spike","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"answer failures >= 5 in 15m","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_answer_failed\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":5,"duration":"0s","aggregations":[{"alignmentPeriod":"900s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "event-emission-failure.json": {"displayName":"OurA Monitor - analytics event emission failure","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"analytics event failed","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_monitor_event_failed\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"300s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "refresh-failure.json": {"displayName":"OurA Monitor - refresh failure","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"refresh failed","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_failed\" AND resource.type=\"cloud_run_job\"" + job_filter,"comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"300s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "refresh-locked.json": {"displayName":"OurA Monitor - lease contention","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"publisher lease contention","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_locked\" AND resource.type=\"cloud_run_job\"" + job_filter,"comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"300s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "row-quarantine.json": {"displayName":"OurA Monitor - source row quarantine","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"source rows were quarantined","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_rows_quarantined\" AND resource.type=\"cloud_run_job\"" + job_filter,"comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"10800s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "axis-unmeasured.json": {"displayName":"OurA Monitor - producer analytics axis issue","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"producer analytics axes were isolated","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_axis_unmeasured\" AND resource.type=\"cloud_run_job\"" + job_filter,"comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"10800s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "refresh-stale-warning.json": {"displayName":"OurA Monitor - data stale warning","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":f"no successful refresh for {warning_seconds // 60}m","conditionAbsent":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_success\" AND resource.type=\"cloud_run_job\"" + job_filter,"duration":f"{warning_seconds}s","aggregations":[{"alignmentPeriod":"1800s","perSeriesAligner":"ALIGN_SUM"}]}}]},
  "refresh-stale-critical.json": {"displayName":"OurA Monitor - data stale critical","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":f"no successful refresh for {critical_seconds // 60}m","conditionAbsent":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_success\" AND resource.type=\"cloud_run_job\"" + job_filter,"duration":f"{critical_seconds}s","aggregations":[{"alignmentPeriod":"1800s","perSeriesAligner":"ALIGN_SUM"}]}}]},
}
for name, payload in policies.items(): (root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
PY
for file in "${TMP_DIR}"/*.json; do
  display_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["displayName"])' "${file}")"
  existing_policies="$(gcloud --project="${PROJECT_ID}" monitoring policies list --filter="displayName=\"${display_name}\"" --format='value(name)')"
  existing_count="$(printf '%s\n' "${existing_policies}" | awk 'NF { count += 1 } END { print count + 0 }')"
  if (( existing_count > 1 )); then
    echo "multiple policies share displayName=${display_name}; refusing ambiguous update" >&2
    exit 2
  fi
  existing="$(printf '%s\n' "${existing_policies}" | awk 'NF { print; exit }')"
  if [[ -n "${existing}" ]]; then
    gcloud --project="${PROJECT_ID}" monitoring policies update "${existing}" --policy-from-file="${file}"
  else
    gcloud --project="${PROJECT_ID}" monitoring policies create --policy-from-file="${file}"
  fi
done

LEGACY_POLICIES_JSON="$(gcloud --project="${PROJECT_ID}" monitoring policies list \
  --filter='displayName:"OurA Monitor"' --format=json)"
LEGACY_POLICIES_JSON="${LEGACY_POLICIES_JSON}" python3 - "${TMP_DIR}" <<'PY'
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
policies = json.loads(os.environ["LEGACY_POLICIES_JSON"])
for index, policy in enumerate(policies if isinstance(policies, list) else []):
    if policy.get("enabled") is False:
        continue
    conditions = policy.get("conditions") or []
    durations = {
        str((condition.get("conditionAbsent") or {}).get("duration") or "")
        for condition in conditions
        if isinstance(condition, dict)
    }
    if "2700s" not in durations:
        continue
    policy["enabled"] = False
    (root / f"legacy-stale-{index}.json").write_text(
        json.dumps(policy, ensure_ascii=False),
        encoding="utf-8",
    )
PY
for legacy_file in "${TMP_DIR}"/legacy-stale-*.json; do
  [[ -e "${legacy_file}" ]] || continue
  legacy_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "${legacy_file}")"
  gcloud --project="${PROJECT_ID}" monitoring policies update "${legacy_name}" \
    --policy-from-file="${legacy_file}"
  echo "legacy_stale_policy=disabled name=${legacy_name}"
done
