#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-}"
PUSH_LOG="${2:-}"

[[ "${TAG}" =~ ^[0-9a-f]{40}$ ]] || {
  echo "docker push receipt requires one full Git SHA tag" >&2
  exit 2
}
[[ -f "${PUSH_LOG}" ]] || {
  echo "docker push receipt log is missing" >&2
  exit 2
}

DIGEST="$(
  sed -n -E \
    "s/^${TAG}: digest: (sha256:[0-9a-f]{64}) size: [0-9]+$/\\1/p" \
    "${PUSH_LOG}"
)"
[[ "${DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "docker push receipt must contain exactly one immutable sha256 digest for ${TAG}" >&2
  exit 2
}

printf '%s\n' "${DIGEST}"
