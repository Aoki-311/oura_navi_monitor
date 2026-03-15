#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E_DIR="${ROOT_DIR}/e2e"
BASE_URL="${MONITOR_E2E_BASE_URL:-http://127.0.0.1:8099}"
ADMIN_EMAIL="${MONITOR_E2E_ADMIN_EMAIL:-2401145@tc.terumo.co.jp}"

command -v npm >/dev/null 2>&1 || { echo "npm not found"; exit 1; }

cleanup() {
  if [[ -n "${UVICORN_PID:-}" ]]; then
    kill "${UVICORN_PID}" >/dev/null 2>&1 || true
    wait "${UVICORN_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/4] Install e2e dependencies"
cd "${E2E_DIR}"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

echo "[2/4] Ensure Playwright chromium is installed"
npx playwright install chromium

if [[ -z "${MONITOR_E2E_BASE_URL:-}" ]]; then
  echo "[3/4] Start local backend for e2e: ${BASE_URL}"
  cd "${ROOT_DIR}"
  MONITOR_ALLOW_UNVERIFIED_LOCAL=true \
  MONITOR_IAP_STRICT=true \
  MONITOR_ADMIN_ALLOWLIST="${ADMIN_EMAIL}" \
  "${ROOT_DIR}/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8099 >/tmp/oura_monitor_e2e_uvicorn.log 2>&1 &
  UVICORN_PID=$!
  sleep 2
else
  echo "[3/4] Use external base URL: ${BASE_URL}"
fi

echo "[4/4] Run chart stability e2e"
cd "${E2E_DIR}"
MONITOR_E2E_BASE_URL="${BASE_URL}" \
MONITOR_E2E_ADMIN_EMAIL="${ADMIN_EMAIL}" \
npx playwright test tests/chart-stability.spec.js

echo "E2E chart stability test passed."
