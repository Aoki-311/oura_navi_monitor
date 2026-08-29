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
DATASET = "oura_navi_monitor"
LOCATION = "US"


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


def _run(
    tmp_path: Path,
    *,
    business_accepted: bool = True,
    include_schema_receipt: bool = True,
    include_api_receipt: bool = True,
    include_backfill_receipt: bool = True,
    backfill_job_image: str = IMAGE,
    api_routines_readable: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
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
                "loggedInBrowserAcceptance": True,
                "historicalDataAcceptance": True,
                "businessAcceptance": business_accepted,
                "acceptedBy": "test-operator",
                "capturedAt": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    schema_receipt = tmp_path / "schema.json"
    schema_receipt.write_text(
        json.dumps(
            {
                "receiptType": "monitor_data_contract_v1",
                "project": PROJECT,
                "dataset": DATASET,
                "location": LOCATION,
                "gitSha": GIT_SHA,
                "image": IMAGE,
                "schemaReady": True,
                "sourceViewsReady": True,
                "apiRoutinesReady": True,
                "apiRoutinesReadable": api_routines_readable,
                "apiRoutineReads": {
                    "dashboard_events": {"readable": True},
                    "dashboard_user_list": {"readable": True},
                },
                "publishedStateReadable": True,
                "capturedAt": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    api_receipt = tmp_path / "api.json"
    api_receipt.write_text(
        json.dumps(
            {
                "receiptType": "monitor_candidate_api_v1",
                "project": PROJECT,
                "region": REGION,
                "service": SERVICE,
                "revision": REVISION,
                "image": IMAGE,
                "gitSha": GIT_SHA,
                "serviceAccount": SERVICE_ACCOUNT,
                "authenticatedApiAcceptance": True,
                "endpointStatus": {
                    "overview": 200,
                    "regions": 200,
                    "users": 200,
                    "userDetail": 200,
                },
                "overviewHistoryVisible": True,
                "userHistoryVisible": True,
                "sourceDiagnosticsExplicit": True,
                "verifiedBy": "test-operator",
                "capturedAt": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    backfill_receipt = tmp_path / "backfill.json"
    backfill_receipt.write_text(
        json.dumps(
            {
                "project": PROJECT,
                "region": REGION,
                "dataset": DATASET,
                "location": LOCATION,
                "expected_image": IMAGE,
                "target_at": "2026-08-29T00:00:00Z",
                "validated_job_contract": {
                    "image": backfill_job_image,
                    "serviceAccount": "refresh-writer@test-project.iam.gserviceaccount.com",
                },
                "execution": {
                    "metadata": {"name": "backfill-execution-1"},
                    "status": {
                        "succeededCount": 1,
                        "failedCount": 0,
                        "conditions": [{"type": "Completed", "status": "True"}],
                    },
                },
                "pipeline_after": [
                    {
                        "source": "published",
                        "status": "succeeded",
                        "published_run_id": "run-after",
                        "data_through": "2026-08-29T00:00:00Z",
                        "lease_active": "false",
                    }
                ],
                "reconciliation": [
                    {
                        "successful_run_count": 1,
                        "canonical_question_count": 4,
                        "matched_question_count": 4,
                        "canonical_answer_count": 3,
                        "matched_answer_count": 3,
                        "canonical_action_count": 2,
                        "matched_action_count": 2,
                        "blocking_failure_count": 0,
                    }
                ],
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
    arguments = [
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
            "--dataset",
            DATASET,
            "--location",
            LOCATION,
            "--acceptance-receipt",
            str(acceptance),
            "--snapshot-output",
            str(snapshot),
            "--confirm-promotion",
            f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/revisions/{REVISION}:100",
            "--apply",
        ]
    receipt_arguments = []
    if include_schema_receipt:
        receipt_arguments.extend(["--schema-receipt", str(schema_receipt)])
    if include_api_receipt:
        receipt_arguments.extend(["--api-receipt", str(api_receipt)])
    if include_backfill_receipt:
        receipt_arguments.extend(["--backfill-receipt", str(backfill_receipt)])
    arguments[arguments.index("--acceptance-receipt"):arguments.index("--acceptance-receipt")] = receipt_arguments
    result = subprocess.run(
        arguments,
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
    assert len(receipt["schemaReceiptSha256"]) == 64
    assert len(receipt["apiReceiptSha256"]) == 64
    assert len(receipt["backfillReceiptSha256"]) == 64


def test_promotion_stops_before_traffic_when_business_acceptance_is_missing(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path, business_accepted=False)

    assert result.returncode != 0
    assert "business candidate acceptance is missing" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_promotion_stops_before_traffic_when_any_data_gate_receipt_is_missing(
    tmp_path: Path,
) -> None:
    cases = (
        ("schema", {"include_schema_receipt": False}),
        ("api", {"include_api_receipt": False}),
        ("backfill", {"include_backfill_receipt": False}),
    )
    for name, options in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        result, snapshot, marker = _run(case_dir, **options)

        assert result.returncode != 0
        assert f"--{name}-receipt is required on apply" in result.stderr
        assert not marker.exists()
        assert not snapshot.exists()


def test_promotion_rejects_backfill_from_a_different_job_image(tmp_path: Path) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        backfill_job_image=(
            f"us-central1-docker.pkg.dev/{PROJECT}/repo/monitor@sha256:"
            + "c" * 64
        ),
    )

    assert result.returncode != 0
    assert "backfill Job did not use the candidate image digest" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_promotion_rejects_a_schema_receipt_that_only_saw_routine_names(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path, api_routines_readable=False)

    assert result.returncode != 0
    assert "schema receipt is missing apiRoutinesReadable" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()
