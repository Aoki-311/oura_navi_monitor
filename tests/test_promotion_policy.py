from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "promote_candidate.sh"
PROJECT = "test-project"
REGION = "us-central1"
SERVICE = "oura-navi-monitor"
REVISION = "oura-navi-monitor-00099-test"
GIT_SHA = "a" * 40
IMAGE = (
    "us-central1-docker.pkg.dev/test-project/cloud-run-source-deploy/"
    f"oura-navi-monitor@sha256:{'b' * 64}"
)
SERVICE_ACCOUNT = "monitor-web@test-project.iam.gserviceaccount.com"


def _write_fake_gcloud(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "updated"
    script = fake_bin / "gcloud"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" run services describe "* ]]; then
  if [[ -e "${FAKE_UPDATE_MARKER}" ]]; then
    printf '%s\n' "${FAKE_SERVICE_AFTER}"
  else
    printf '%s\n' "${FAKE_SERVICE_BEFORE}"
  fi
elif [[ " $* " == *" run revisions describe "* ]]; then
  printf '%s\n' "${FAKE_REVISION_JSON}"
elif [[ " $* " == *" run services update-traffic "* ]]; then
  : > "${FAKE_UPDATE_MARKER}"
  printf '{}\n'
else
  echo "unexpected gcloud command: $*" >&2
  exit 91
fi
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return fake_bin, marker


def _payloads() -> tuple[dict, dict, dict]:
    before = {
        "metadata": {"name": SERVICE},
        "status": {
            "traffic": [
                {"revisionName": "oura-navi-monitor-00098-old", "percent": 100},
                {"revisionName": REVISION, "tag": "candidate", "percent": 0},
            ]
        },
    }
    after = {
        "metadata": {"name": SERVICE},
        "status": {
            "traffic": [
                {"revisionName": REVISION, "percent": 100},
                {"revisionName": REVISION, "tag": "candidate"},
            ]
        },
    }
    revision = {
        "metadata": {"name": REVISION, "labels": {"git-sha": GIT_SHA}},
        "spec": {
            "serviceAccountName": SERVICE_ACCOUNT,
            "containers": [{"image": IMAGE}],
        },
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    return before, after, revision


def _run(tmp_path: Path, *, business_accepted: bool = True) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake_bin, marker = _write_fake_gcloud(tmp_path)
    credential = tmp_path / "approved.json"
    credential.write_text("{}", encoding="utf-8")
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(
        json.dumps(
            {
                "project": PROJECT,
                "region": REGION,
                "service": SERVICE,
                "revision": REVISION,
                "image": IMAGE,
                "gitSha": GIT_SHA,
                "serviceAccount": SERVICE_ACCOUNT,
                "authenticatedAcceptance": True,
                "businessAcceptance": business_accepted,
                "acceptedBy": "test-operator",
                "capturedAt": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "promotion.json"
    before, after, revision = _payloads()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_UPDATE_MARKER": str(marker),
        "FAKE_SERVICE_BEFORE": json.dumps(before),
        "FAKE_SERVICE_AFTER": json.dumps(after),
        "FAKE_REVISION_JSON": json.dumps(revision),
    }
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--service",
            SERVICE,
            "--revision",
            REVISION,
            "--expected-image",
            IMAGE,
            "--expected-git-sha",
            GIT_SHA,
            "--expected-service-account",
            SERVICE_ACCOUNT,
            "--acceptance-receipt",
            str(acceptance),
            "--snapshot-output",
            str(snapshot),
            "--confirm-promotion",
            f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/revisions/{REVISION}:100",
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, snapshot, marker


def test_promotion_binds_acceptance_identity_and_exact_traffic_readback(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert marker.exists()
    receipt = json.loads(snapshot.read_text(encoding="utf-8"))
    assert receipt["targetRevision"] == REVISION
    assert receipt["serviceBefore"]["status"]["traffic"][0]["percent"] == 100
    assert receipt["serviceAfter"]["status"]["traffic"][0] == {
        "revisionName": REVISION,
        "percent": 100,
    }
    assert len(receipt["acceptanceReceiptSha256"]) == 64


def test_promotion_stops_before_traffic_when_business_acceptance_is_missing(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path, business_accepted=False)

    assert result.returncode != 0
    assert "business candidate acceptance is missing" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()
