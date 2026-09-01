from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import scheduler_activation_receipt_state as activation_state


ROOT = Path(__file__).resolve().parents[1]
JOB_SERVICE_ACCOUNT = "monitor-refresh@test-project.iam.gserviceaccount.com"
OLD_SCHEDULER_SERVICE_ACCOUNT = (
    "monitor-old-scheduler@test-project.iam.gserviceaccount.com"
)
NEW_SCHEDULER_SERVICE_ACCOUNT = (
    "monitor-new-scheduler@test-project.iam.gserviceaccount.com"
)
IMAGE = (
    "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
    + "b" * 64
)
JOB_URI = (
    "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/"
    "namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
)


def _job_json() -> dict[str, object]:
    return {
        "template": {
            "template": {
                "taskCount": 1,
                "parallelism": 1,
                "template": {
                    "serviceAccount": JOB_SERVICE_ACCOUNT,
                    "maxRetries": 1,
                    "timeout": "1800s",
                    "containers": [
                        {
                            "image": IMAGE,
                            "command": ["python"],
                            "args": [
                                "-m",
                                "app.jobs.refresh_analytics",
                                "--apply",
                                "--trigger-source",
                                "scheduler_hourly",
                            ],
                            "env": [
                                {
                                    "name": "MONITOR_PROJECT_ID",
                                    "value": "test-project",
                                },
                                {
                                    "name": "MONITOR_BQ_DATASET",
                                    "value": "oura_navi_monitor",
                                },
                                {"name": "MONITOR_BQ_LOCATION", "value": "US"},
                                {
                                    "name": "MONITOR_SOURCE_SERVICE",
                                    "value": "lcs-rag-app",
                                },
                                {
                                    "name": "MONITOR_ANALYTICS_START_AT",
                                    "value": "2026-03-16T00:00:00Z",
                                },
                            ],
                        }
                    ],
                },
            }
        }
    }


def _validated_job_contract() -> dict[str, object]:
    return {
        "image": IMAGE,
        "serviceAccount": JOB_SERVICE_ACCOUNT,
        "command": ["python"],
        "args": [
            "-m",
            "app.jobs.refresh_analytics",
            "--apply",
            "--trigger-source",
            "scheduler_hourly",
        ],
        "environment": {
            "MONITOR_PROJECT_ID": "test-project",
            "MONITOR_BQ_DATASET": "oura_navi_monitor",
            "MONITOR_BQ_LOCATION": "US",
            "MONITOR_SOURCE_SERVICE": "lcs-rag-app",
            "MONITOR_ANALYTICS_START_AT": "2026-03-16T00:00:00Z",
        },
        "taskCount": 1,
        "parallelism": 1,
        "maxRetries": 1,
        "timeoutSeconds": 1800,
    }


def _reconciliation() -> list[dict[str, str]]:
    return [
        {
            "successful_run_count": "1",
            "blocking_failure_count": "0",
            "canonical_question_count": "1",
            "matched_question_count": "1",
            "canonical_answer_count": "1",
            "matched_answer_count": "1",
            "canonical_action_count": "1",
            "matched_action_count": "1",
        }
    ]


@dataclass(frozen=True)
class ActivationHarness:
    env: dict[str, str]
    arguments: list[str]
    receipt: Path
    new_enabled: Path
    operation_log: Path


def _activation_harness(tmp_path: Path) -> ActivationHarness:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    new_enabled = tmp_path / "new-enabled"
    operation_log = tmp_path / "operations.log"
    readback_failure = tmp_path / "post-resume-readback-failed"
    credential = tmp_path / "approved-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)

    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" scheduler jobs describe "* ]]; then
  if [[ -e "${FAKE_NEW_ENABLED}" && "${FAKE_FAIL_POST_RESUME_READBACK_ONCE:-false}" == "true" && ! -e "${FAKE_READBACK_FAILURE}" ]]; then
    : > "${FAKE_READBACK_FAILURE}"
    echo "synthetic post-resume readback interruption" >&2
    exit 77
  fi
  if [[ " $* " == *" oura-navi-monitor-refresh-quarter-hour "* ]]; then
    state="PAUSED"
    schedule="*/15 * * * *"
    deadline="30s"
    service_account="${FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT}"
  else
    state="PAUSED"
    [[ ! -e "${FAKE_NEW_ENABLED}" ]] || state="ENABLED"
    schedule="${FAKE_NEW_SCHEDULE:-5 * * * *}"
    deadline="60s"
    service_account="${FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT}"
  fi
  printf '{"state":"%s","schedule":"%s","timeZone":"Asia/Tokyo","attemptDeadline":"%s","retryConfig":{"retryCount":0},"httpTarget":{"uri":"%s","oauthToken":{"serviceAccountEmail":"%s"}}}\n' "${state}" "${schedule}" "${deadline}" "${FAKE_JOB_URI}" "${service_account}"
elif [[ " $* " == *" scheduler jobs resume "* ]]; then
  printf '%s\n' resume-new >> "${FAKE_OPERATION_LOG}"
  if [[ "${FAKE_RESUME_APPLIES:-true}" == "true" ]]; then
    : > "${FAKE_NEW_ENABLED}"
  fi
  exit "${FAKE_RESUME_RETURN_CODE:-0}"
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\n' "${FAKE_JOB_JSON}"
else
  echo "unexpected gcloud command: $*" >&2
  exit 91
fi
""",
        encoding="utf-8",
    )
    bq = fake_bin / "bq"
    bq.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAKE_BQ_MUST_NOT_RUN:-false}" == "true" ]]; then
  echo "bq must not run for an exact final receipt" >&2
  exit 88
fi
if [[ " $* " == *" query "* ]]; then
  printf '%s\n' "${FAKE_GATE_JSON}"
else
  echo "unexpected bq command: $*" >&2
  exit 91
fi
""",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    bq.chmod(0o755)

    modules = tmp_path / "fake-firestore-modules"
    package = modules / "google" / "cloud"
    package.mkdir(parents=True)
    (package / "firestore.py").write_text(
        '''import fcntl, json, os
from pathlib import Path
SERVER_TIMESTAMP = "server-time"
class Snapshot:
    def __init__(self, value): self.exists, self.value = value is not None, value
    def to_dict(self): return dict(self.value) if self.value is not None else None
class Document:
    def __init__(self, key): self.key = key
class Collection:
    def __init__(self, name): self.name = name
    def document(self, name): return Document(self.name + "/" + name)
class Transaction:
    def __init__(self): self.store, self.deleted = None, False
    def get(self, document): return iter([Snapshot(self.store.get(document.key))])
    def create(self, document, value): self.store[document.key] = dict(value)
    def delete(self, document): self.store.pop(document.key, None); self.deleted = True
class Client:
    def __init__(self, *, project, database, credentials=None):
        if project != "test-project" or database != "lcs-user-data": raise RuntimeError("scope")
    def collection(self, name): return Collection(name)
    def transaction(self): return Transaction()
def transactional(function):
    def run(transaction):
        state, mutex = Path(os.environ["FAKE_LOCK_STATE"]), Path(os.environ["FAKE_LOCK_MUTEX"])
        mutex.touch(exist_ok=True)
        with mutex.open("r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            transaction.store = json.loads(state.read_text()) if state.exists() else {}
            result = function(transaction)
            temporary = state.with_suffix(".tmp")
            temporary.write_text(json.dumps(transaction.store, sort_keys=True))
            os.replace(temporary, state)
            return result
    return run
''',
        encoding="utf-8",
    )
    (modules / "sitecustomize.py").write_text(
        '''import importlib.util, sys
from pathlib import Path
from google.oauth2 import service_account
path = Path(__file__).parent / "google" / "cloud" / "firestore.py"
spec = importlib.util.spec_from_file_location("google.cloud.firestore", path)
module = importlib.util.module_from_spec(spec)
sys.modules["google.cloud.firestore"] = module
spec.loader.exec_module(module)
service_account.Credentials.from_service_account_file = staticmethod(lambda _path: object())
''',
        encoding="utf-8",
    )

    snapshot = tmp_path / "scheduler-cutover.json"
    snapshot_payload = {
        "project": "test-project",
        "region": "us-central1",
        "dataset": "oura_navi_monitor",
        "location": "US",
        "source_service": "lcs-rag-app",
        "expected_job_service_account": JOB_SERVICE_ACCOUNT,
        "expected_old_scheduler_service_account": OLD_SCHEDULER_SERVICE_ACCOUNT,
        "expected_new_scheduler_service_account": NEW_SCHEDULER_SERVICE_ACCOUNT,
        "old_scheduler": "oura-navi-monitor-refresh-quarter-hour",
        "new_scheduler": "oura-navi-monitor-refresh-three-hour",
        "freeze_started_at": "2026-08-28T00:00:00Z",
        "freeze_verified_at": "2026-08-28T00:01:00Z",
        "active_bigquery_writers_at_freeze": [],
    }
    snapshot.write_text(json.dumps(snapshot_payload), encoding="utf-8")

    backfill = tmp_path / "backfill-receipt.json"
    backfill.write_text(
        json.dumps(
            {
                "project": "test-project",
                "region": "us-central1",
                "dataset": "oura_navi_monitor",
                "location": "US",
                "source_service": "lcs-rag-app",
                "expected_job_service_account": JOB_SERVICE_ACCOUNT,
                "expected_old_scheduler_service_account": (
                    OLD_SCHEDULER_SERVICE_ACCOUNT
                ),
                "expected_new_scheduler_service_account": (
                    NEW_SCHEDULER_SERVICE_ACCOUNT
                ),
                "job": "oura-navi-monitor-refresh",
                "expected_image": IMAGE,
                "freeze_snapshot": snapshot_payload,
                "validated_job_contract": _validated_job_contract(),
                "validated_execution_provenance": {
                    "name": "execution-1",
                    "image": IMAGE,
                    "serviceAccount": JOB_SERVICE_ACCOUNT,
                    "succeededCount": 1,
                    "failedCount": 0,
                },
                "job_deploy_receipt_sha256": "a" * 64,
                "execution": {
                    "name": "execution-1",
                    "succeededCount": 1,
                    "failedCount": 0,
                },
                "pipeline_after": [
                    {
                        "source": "published",
                        "status": "succeeded",
                        "published_run_id": "run-1",
                        "lease_active": "false",
                    }
                ],
                "reconciliation": _reconciliation(),
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "activation-receipt.json"
    gate = [
        {
            "source": "published",
            "status": "succeeded",
            "data_through": "2026-08-28T09:00:00Z",
            "freshness_minutes": "30",
            "lease_active": "false",
        }
    ]
    env = {
        **os.environ,
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_NEW_ENABLED": str(new_enabled),
        "FAKE_OPERATION_LOG": str(operation_log),
        "FAKE_READBACK_FAILURE": str(readback_failure),
        "FAKE_JOB_URI": JOB_URI,
        "FAKE_JOB_JSON": json.dumps(_job_json()),
        "FAKE_GATE_JSON": json.dumps(gate),
        "FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT": OLD_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT": NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_LOCK_STATE": str(tmp_path / "refresh-lock.json"),
        "FAKE_LOCK_MUTEX": str(tmp_path / "refresh-lock.mutex"),
        "PYTHONPATH": f"{modules}:{ROOT}",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    arguments = [
        "bash",
        str(ROOT / "scripts" / "cutover_refresh_scheduler.sh"),
        "--project",
        "test-project",
        "--stage",
        "activate",
        "--snapshot-output",
        str(snapshot),
        "--backfill-receipt",
        str(backfill),
        "--activation-receipt-output",
        str(receipt),
        "--credential-file",
        str(credential),
        "--expected-job-service-account",
        JOB_SERVICE_ACCOUNT,
        "--expected-old-scheduler-service-account",
        OLD_SCHEDULER_SERVICE_ACCOUNT,
        "--expected-new-scheduler-service-account",
        NEW_SCHEDULER_SERVICE_ACCOUNT,
        "--confirm-cutover",
        (
            "projects/test-project/locations/us-central1/jobs/"
            "oura-navi-monitor-refresh-three-hour:activate-after-backfill"
        ),
        "--apply",
    ]
    return ActivationHarness(
        env=env,
        arguments=arguments,
        receipt=receipt,
        new_enabled=new_enabled,
        operation_log=operation_log,
    )


def _run(
    harness: ActivationHarness, **environment: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        harness.arguments,
        cwd=ROOT,
        env={**harness.env, **environment},
        check=False,
        capture_output=True,
        text=True,
    )


def test_intent_publication_is_exclusive_and_never_partial(tmp_path: Path) -> None:
    path = tmp_path / "activation.json"
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def publish(marker: str) -> None:
        payload = {"receipt_type": "intent", "marker": marker}
        barrier.wait()
        results.append(activation_state._write_new_intent(path, payload))

    threads = [
        threading.Thread(target=publish, args=("first",)),
        threading.Thread(target=publish, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published["marker"] in {"first", "second"}
    activation_state._validate_integrity(published)


def test_scheduler_intent_link_failure_never_exposes_a_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "activation.json"

    def fail_link(_source: object, _target: object) -> None:
        raise OSError("synthetic link interruption")

    monkeypatch.setattr(activation_state.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic link interruption"):
        activation_state._write_new_intent(path, {"state": "intent"})

    assert not path.exists()
    assert list(tmp_path.glob(".activation.json.intent-*.tmp")) == []


def test_scheduler_final_replace_is_followed_by_parent_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "activation.json"
    path.write_text("intent", encoding="utf-8")
    expected = path.read_bytes()
    events: list[str] = []
    real_replace = activation_state.os.replace

    def record_replace(source: object, target: object) -> None:
        real_replace(source, target)
        events.append("replace")

    monkeypatch.setattr(activation_state.os, "replace", record_replace)
    monkeypatch.setattr(
        activation_state,
        "_fsync_directory",
        lambda _path: events.append("parent-fsync"),
    )
    activation_state._atomic_finalize(
        path,
        expected_raw=expected,
        payload={"receipt_type": "final", "state": "complete"},
    )

    assert events == ["replace", "parent-fsync"]
    assert json.loads(path.read_text())["state"] == "complete"


def test_activate_recovers_exact_intent_after_post_resume_interruption(
    tmp_path: Path,
) -> None:
    harness = _activation_harness(tmp_path)

    interrupted = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
    )

    assert interrupted.returncode == 77
    assert harness.new_enabled.exists()
    intent = json.loads(harness.receipt.read_text(encoding="utf-8"))
    assert intent["receipt_type"] == "monitor_scheduler_activation_intent_v1"
    assert intent["state"] == "intent"
    stale_temporary = tmp_path / ".activation-receipt.json.final-stale.tmp"
    stale_temporary.write_text("{broken", encoding="utf-8")

    recovered = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
    )

    assert recovered.returncode == 0, recovered.stderr
    final = json.loads(harness.receipt.read_text(encoding="utf-8"))
    assert final["receipt_type"] == "monitor_scheduler_activation_v2"
    assert final["state"] == "complete"
    assert final["recovered_after_interruption"] is True
    assert harness.operation_log.read_text(encoding="utf-8").splitlines() == [
        "resume-new"
    ]
    assert stale_temporary.read_text(encoding="utf-8") == "{broken"


def test_activate_accepts_nonzero_resume_when_live_readback_is_enabled(
    tmp_path: Path,
) -> None:
    harness = _activation_harness(tmp_path)

    result = _run(harness, FAKE_RESUME_RETURN_CODE="23")

    assert result.returncode == 0, result.stderr
    final = json.loads(harness.receipt.read_text(encoding="utf-8"))
    assert final["resume_command_return_code"] == 23
    assert final["recovered_after_interruption"] is False
    assert final["new_scheduler_readback"]["state"] == "ENABLED"


def test_activate_retries_exact_intent_when_resume_did_not_apply(
    tmp_path: Path,
) -> None:
    harness = _activation_harness(tmp_path)

    failed = _run(
        harness,
        FAKE_RESUME_APPLIES="false",
        FAKE_RESUME_RETURN_CODE="17",
    )

    assert failed.returncode != 0
    assert not harness.new_enabled.exists()
    assert json.loads(harness.receipt.read_text())["state"] == "intent"

    recovered = _run(harness)

    assert recovered.returncode == 0, recovered.stderr
    assert harness.new_enabled.exists()
    assert json.loads(harness.receipt.read_text())["state"] == "complete"
    assert harness.operation_log.read_text().splitlines() == [
        "resume-new",
        "resume-new",
    ]


def test_activate_ignores_a_damaged_unpublished_intent_temporary(
    tmp_path: Path,
) -> None:
    harness = _activation_harness(tmp_path)
    stale_temporary = tmp_path / ".activation-receipt.json.intent-stale.tmp"
    stale_temporary.write_text("{partial", encoding="utf-8")

    result = _run(harness)

    assert result.returncode == 0, result.stderr
    final = json.loads(harness.receipt.read_text(encoding="utf-8"))
    assert final["receipt_type"] == "monitor_scheduler_activation_v2"
    assert stale_temporary.read_text(encoding="utf-8") == "{partial"


def test_activate_stops_on_tampered_intent(tmp_path: Path) -> None:
    harness = _activation_harness(tmp_path)
    interrupted = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
    )
    assert interrupted.returncode == 77
    intent = json.loads(harness.receipt.read_text(encoding="utf-8"))
    intent["dataset"] = "tampered_dataset"
    harness.receipt.write_text(json.dumps(intent), encoding="utf-8")

    rejected = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
    )

    assert rejected.returncode != 0
    assert "state integrity mismatch" in rejected.stderr
    assert harness.operation_log.read_text(encoding="utf-8").splitlines() == [
        "resume-new"
    ]


def test_activate_stops_when_scheduler_contract_drifted_from_intent(
    tmp_path: Path,
) -> None:
    harness = _activation_harness(tmp_path)
    interrupted = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
    )
    assert interrupted.returncode == 77

    rejected = _run(
        harness,
        FAKE_FAIL_POST_RESUME_READBACK_ONCE="true",
        FAKE_NEW_SCHEDULE="6 */3 * * *",
    )

    assert rejected.returncode != 0
    assert "new scheduler has unexpected schedule" in rejected.stderr
    assert harness.operation_log.read_text(encoding="utf-8").splitlines() == [
        "resume-new"
    ]


def test_activate_stops_when_enabled_without_an_intent(tmp_path: Path) -> None:
    harness = _activation_harness(tmp_path)
    harness.new_enabled.touch()

    rejected = _run(harness, FAKE_BQ_MUST_NOT_RUN="true")

    assert rejected.returncode != 0
    assert "ENABLED without an activation intent" in rejected.stderr
    assert not harness.receipt.exists()
    assert not harness.operation_log.exists()


def test_activate_exact_final_is_idempotent(tmp_path: Path) -> None:
    harness = _activation_harness(tmp_path)
    first = _run(harness)
    assert first.returncode == 0, first.stderr
    exact_final = harness.receipt.read_bytes()

    repeated = _run(harness)

    assert repeated.returncode == 0, repeated.stderr
    assert "scheduler_activation=already_complete" in repeated.stdout
    assert harness.receipt.read_bytes() == exact_final
    assert harness.operation_log.read_text(encoding="utf-8").splitlines() == [
        "resume-new"
    ]
