#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID=""
SOURCE_SERVICE="lcs-rag-app"
CHANNEL=""
APPLY="false"
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
echo "metrics=lcs_rag_app_5xx_count,lcs_rag_app_answer_failed,lcs_rag_app_monitor_event_failed,oura_navi_monitor_refresh_success,oura_navi_monitor_refresh_failed"
echo "policies=HTTP 5xx,answer failure spike,analytics event emission failure,pipeline refresh failure,pipeline stale"
if [[ "${APPLY}" != "true" ]]; then exit 0; fi
[[ "${CHANNEL}" == projects/*/notificationChannels/* ]] || { echo "--notification-channel must be an existing exact resource" >&2; exit 2; }
[[ -n "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}" && -f "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE}" ]] || { echo "approved credential is required" >&2; exit 2; }
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
upsert_metric "oura_navi_monitor_refresh_success" "resource.type=\"cloud_run_job\" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status=\"succeeded\""
upsert_metric "oura_navi_monitor_refresh_failed" "resource.type=\"cloud_run_job\" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status=\"failed\""

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
python3 - "${TMP_DIR}" "${CHANNEL}" <<'PY'
import json, pathlib, sys
root, channel = pathlib.Path(sys.argv[1]), sys.argv[2]
policies = {
  "http-5xx.json": {"displayName":"OurA Monitor - HTTP 5xx","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"5xx >= 3 in 10m","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_5xx_count\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":3,"duration":"0s","aggregations":[{"alignmentPeriod":"600s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "answer-failure.json": {"displayName":"OurA Monitor - answer failure spike","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"answer failures >= 5 in 15m","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_answer_failed\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":5,"duration":"0s","aggregations":[{"alignmentPeriod":"900s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "event-emission-failure.json": {"displayName":"OurA Monitor - analytics event emission failure","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"analytics event failed","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/lcs_rag_app_monitor_event_failed\" AND resource.type=\"cloud_run_revision\"","comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"300s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "refresh-failure.json": {"displayName":"OurA Monitor - refresh failure","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"refresh failed","conditionThreshold":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_failed\" AND resource.type=\"cloud_run_job\"","comparison":"COMPARISON_GE","thresholdValue":1,"duration":"0s","aggregations":[{"alignmentPeriod":"900s","perSeriesAligner":"ALIGN_SUM"}],"trigger":{"count":1}}}]},
  "refresh-stale.json": {"displayName":"OurA Monitor - data stale","combiner":"OR","enabled":True,"notificationChannels":[channel],"conditions":[{"displayName":"no successful refresh for 45m","conditionAbsent":{"filter":"metric.type=\"logging.googleapis.com/user/oura_navi_monitor_refresh_success\" AND resource.type=\"cloud_run_job\"","duration":"2700s","aggregations":[{"alignmentPeriod":"900s","perSeriesAligner":"ALIGN_SUM"}]}}]},
}
for name, payload in policies.items(): (root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
PY
for file in "${TMP_DIR}"/*.json; do
  display_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["displayName"])' "${file}")"
  existing="$(gcloud --project="${PROJECT_ID}" monitoring policies list --filter="displayName=\"${display_name}\"" --format='value(name)' | head -n 1)"
  if [[ -n "${existing}" ]]; then
    gcloud --project="${PROJECT_ID}" monitoring policies update "${existing}" --policy-from-file="${file}"
  else
    gcloud --project="${PROJECT_ID}" monitoring policies create --policy-from-file="${file}"
  fi
done
