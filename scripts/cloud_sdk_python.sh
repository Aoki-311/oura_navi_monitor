#!/usr/bin/env bash
# Cloud SDK images may expose only their bundled Python, not python3 on PATH.
set -euo pipefail

[[ "$#" -gt 0 ]] || {
  echo 'A Python script or argument is required' >&2
  exit 2
}

if ! cloud_sdk_python="$(gcloud info --format='value(basic.python_location)')"; then
  echo 'Unable to resolve the Cloud SDK Python interpreter' >&2
  exit 2
fi
[[ "${cloud_sdk_python}" == /* && -f "${cloud_sdk_python}" && -x "${cloud_sdk_python}" ]] || {
  echo 'Cloud SDK must report an absolute executable Python path' >&2
  exit 2
}
if ! "${cloud_sdk_python}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'; then
  echo 'Cloud SDK Python 3.10 or newer is required' >&2
  exit 2
fi

exec "${cloud_sdk_python}" "$@"
