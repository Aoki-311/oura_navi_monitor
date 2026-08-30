#!/usr/bin/env bash

# Shell-local wrappers. The credential is attached only to the direct Google
# client process; callers must not export either credential variable.
monitor_install_google_credential_wrappers() {
  local approved_file="$1"
  [[ -n "${approved_file}" ]] || {
    echo "approved credential file is required" >&2
    return 2
  }
  MONITOR_APPROVED_CREDENTIAL_FILE="${approved_file}"
}

gcloud() {
  CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${MONITOR_APPROVED_CREDENTIAL_FILE:?}" \
  GOOGLE_APPLICATION_CREDENTIALS="${MONITOR_APPROVED_CREDENTIAL_FILE:?}" \
    command gcloud "$@"
}

bq() {
  CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${MONITOR_APPROVED_CREDENTIAL_FILE:?}" \
  GOOGLE_APPLICATION_CREDENTIALS="${MONITOR_APPROVED_CREDENTIAL_FILE:?}" \
    command bq "$@"
}
