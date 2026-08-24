#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E_DIR="${ROOT_DIR}/e2e"
BASE_URL="${MONITOR_E2E_BASE_URL:-http://127.0.0.1:8099}"
ADMIN_EMAIL="${MONITOR_E2E_ADMIN_EMAIL:-2401145@tc.terumo.co.jp}"
TEST_TARGET="${MONITOR_E2E_TEST_TARGET:-tests}"

command -v npm >/dev/null 2>&1 || { echo "npm not found" >&2; exit 2; }

cleanup() {
  if [[ -n "${UVICORN_PID:-}" ]]; then
    kill "${UVICORN_PID}" >/dev/null 2>&1 || true
    wait "${UVICORN_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ ! -d "${E2E_DIR}/node_modules/@playwright/test" ]]; then
  echo "e2e dependencies are missing; run npm ci in ${E2E_DIR}" >&2
  exit 2
fi

if [[ -z "${MONITOR_E2E_BASE_URL:-}" ]]; then
  echo "Start local dashboard: ${BASE_URL}"
  cd "${ROOT_DIR}"
  MONITOR_ALLOW_UNVERIFIED_LOCAL=true \
  MONITOR_ADMIN_ALLOWLIST="${ADMIN_EMAIL}" \
  "${ROOT_DIR}/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8099 \
    >"${TMPDIR:-/tmp}/oura_monitor_e2e_uvicorn.log" 2>&1 &
  UVICORN_PID=$!
  for _attempt in {1..30}; do
    if curl --fail --silent --output /dev/null "${BASE_URL}/api/health"; then
      break
    fi
    sleep 0.2
  done
  curl --fail --silent --output /dev/null "${BASE_URL}/api/health" || {
    echo "local dashboard did not become ready" >&2
    exit 2
  }
fi

cd "${E2E_DIR}"
MONITOR_E2E_BASE_URL="${BASE_URL}" \
MONITOR_E2E_ADMIN_EMAIL="${ADMIN_EMAIL}" \
npx playwright test "${TEST_TARGET}"
