#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lcs-developer-483404}"
SOURCE_SERVICE="${SOURCE_SERVICE:-lcs-rag-app}"
ALERT_EMAILS="${ALERT_EMAILS:-2401145@tc.terumo.co.jp,2304371@tc.terumo.co.jp,0800781@tc.terumo.co.jp}"

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found"; exit 1; }

gcloud config set project "${PROJECT_ID}" >/dev/null

run_monitoring_cmd() {
  if gcloud monitoring channels list --help >/dev/null 2>&1; then
    gcloud monitoring "$@"
  else
    gcloud beta monitoring "$@"
  fi
}

CHANNEL_IDS=()
IFS=',' read -r -a EMAIL_ARR <<< "${ALERT_EMAILS}"
for raw in "${EMAIL_ARR[@]}"; do
  email="$(echo "${raw}" | xargs)"
  [[ -z "${email}" ]] && continue
  existing="$(run_monitoring_cmd channels list --format='value(name)' --filter="type=email AND labels.email_address=${email}" | head -n1 || true)"
  if [[ -n "${existing}" ]]; then
    CHANNEL_IDS+=("${existing}")
    continue
  fi
  created="$(run_monitoring_cmd channels create \
    --display-name="OurA Monitor ${email}" \
    --type=email \
    --channel-labels=email_address="${email}" \
    --format='value(name)' 2>/tmp/oura_monitor_alert_err.log || true)"
  if [[ -z "${created}" ]]; then
    echo "Failed to create notification channel for ${email}."
    echo "Hint: caller likely lacks Monitoring IAM (notification channels/policies write)."
    cat /tmp/oura_monitor_alert_err.log
    rm -f /tmp/oura_monitor_alert_err.log
    exit 1
  fi
  CHANNEL_IDS+=("${created}")
done

if [[ ${#CHANNEL_IDS[@]} -eq 0 ]]; then
  echo "No notification channels found/created"
  exit 1
fi

channels_json="$(printf '"%s",' "${CHANNEL_IDS[@]}")"
channels_json="[${channels_json%,}]"

create_policy_if_missing() {
  local display_name="$1"
  local policy_json="$2"
  local existing
  existing="$(run_monitoring_cmd policies list --filter="displayName=\"${display_name}\"" --format='value(name)' | head -n1 || true)"
  if [[ -n "${existing}" ]]; then
    echo "Policy exists: ${display_name}"
    return
  fi
  local tmp
  tmp="$(mktemp)"
  cat > "${tmp}" <<< "${policy_json}"
  if ! run_monitoring_cmd policies create --policy-from-file="${tmp}" >/dev/null 2>/tmp/oura_monitor_alert_err.log; then
    echo "Failed to create alert policy: ${display_name}"
    echo "Hint: caller likely lacks Monitoring IAM (alert policies write)."
    cat /tmp/oura_monitor_alert_err.log
    rm -f /tmp/oura_monitor_alert_err.log
    rm -f "${tmp}"
    exit 1
  fi
  rm -f "${tmp}"
  echo "Policy created: ${display_name}"
}

create_policy_if_missing "OurA Monitor - 5xx warning" "
{
  \"displayName\": \"OurA Monitor - 5xx warning\",
  \"combiner\": \"OR\",
  \"enabled\": true,
  \"notificationChannels\": ${channels_json},
  \"conditions\": [{
    \"displayName\": \"5xx >= 3 in 10m\",
    \"conditionThreshold\": {
      \"filter\": \"metric.type=\\\"logging.googleapis.com/user/lcs_rag_app_5xx_count\\\" resource.type=\\\"global\\\"\",
      \"comparison\": \"COMPARISON_GE\",
      \"thresholdValue\": 3,
      \"duration\": \"0s\",
      \"aggregations\": [{
        \"alignmentPeriod\": \"600s\",
        \"perSeriesAligner\": \"ALIGN_SUM\"
      }],
      \"trigger\": { \"count\": 1 }
    }
  }]
}
"

create_policy_if_missing "OurA Monitor - QS degraded high" "
{
  \"displayName\": \"OurA Monitor - QS degraded high\",
  \"combiner\": \"OR\",
  \"enabled\": true,
  \"notificationChannels\": ${channels_json},
  \"conditions\": [{
    \"displayName\": \"qs_degraded >= 15 in 30m\",
    \"conditionThreshold\": {
      \"filter\": \"metric.type=\\\"logging.googleapis.com/user/lcs_rag_app_qs_degraded\\\" resource.type=\\\"global\\\"\",
      \"comparison\": \"COMPARISON_GE\",
      \"thresholdValue\": 15,
      \"duration\": \"0s\",
      \"aggregations\": [{
        \"alignmentPeriod\": \"1800s\",
        \"perSeriesAligner\": \"ALIGN_SUM\"
      }],
      \"trigger\": { \"count\": 1 }
    }
  }]
}
"

create_policy_if_missing "OurA Monitor - restore failed high" "
{
  \"displayName\": \"OurA Monitor - restore failed high\",
  \"combiner\": \"OR\",
  \"enabled\": true,
  \"notificationChannels\": ${channels_json},
  \"conditions\": [{
    \"displayName\": \"restore_failed >= 3 in 30m\",
    \"conditionThreshold\": {
      \"filter\": \"metric.type=\\\"logging.googleapis.com/user/lcs_rag_app_restore_failed\\\" resource.type=\\\"global\\\"\",
      \"comparison\": \"COMPARISON_GE\",
      \"thresholdValue\": 3,
      \"duration\": \"0s\",
      \"aggregations\": [{
        \"alignmentPeriod\": \"1800s\",
        \"perSeriesAligner\": \"ALIGN_SUM\"
      }],
      \"trigger\": { \"count\": 1 }
    }
  }]
}
"

echo "Alerts setup complete."
