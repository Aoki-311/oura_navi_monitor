from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import promotion_receipt_state


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "promote_candidate.sh"
LOCK_SCRIPT = ROOT / "scripts" / "promotion_release_lock.py"
PROJECT = "test-project"
REGION = "us-central1"
SERVICE = "oura-navi-monitor"
GIT_SHA = "a" * 40
BUILD_ID = "12345678-1234-1234-1234-123456789abc"
REVISION = f"{SERVICE}-{GIT_SHA[:7]}-{BUILD_ID[:8]}"
IMAGE = (
    "us-central1-docker.pkg.dev/test-project/cloud-run-source-deploy/"
    f"oura-navi-monitor@sha256:{'b' * 64}"
)
SERVICE_ACCOUNT = "monitor-web@test-project.iam.gserviceaccount.com"
REFRESH_SERVICE_ACCOUNT = "refresh-writer@test-project.iam.gserviceaccount.com"
SCHEDULER_SERVICE_ACCOUNT = "scheduler@test-project.iam.gserviceaccount.com"
DATASET = "oura_navi_monitor"
LOCATION = "US"
FIRESTORE_DATABASE = "lcs-user-data"
LOCK_COLLECTION = "monitor_release_locks"


def _scheduler_json() -> dict:
    return {
        "state": "ENABLED",
        "schedule": "5 */3 * * *",
        "timeZone": "Asia/Tokyo",
        "attemptDeadline": "60s",
        "retryConfig": {"retryCount": 0},
        "httpTarget": {
            "uri": (
                "https://us-central1-run.googleapis.com/apis/run.googleapis.com/"
                "v1/namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
            ),
            "oauthToken": {"serviceAccountEmail": SCHEDULER_SERVICE_ACCOUNT},
        },
    }


def _job_json(image: str = IMAGE) -> dict:
    return {
        "template": {
            "taskCount": 1,
            "parallelism": 1,
            "template": {
                "serviceAccount": REFRESH_SERVICE_ACCOUNT,
                "maxRetries": 1,
                "timeout": "1800s",
                "containers": [
                    {
                        "image": image,
                        "command": ["python"],
                        "args": [
                            "-m",
                            "app.jobs.refresh_analytics",
                            "--apply",
                            "--trigger-source",
                            "scheduler_three_hour",
                        ],
                        "env": [
                            {"name": "MONITOR_PROJECT_ID", "value": PROJECT},
                            {"name": "MONITOR_BQ_DATASET", "value": DATASET},
                            {"name": "MONITOR_BQ_LOCATION", "value": LOCATION},
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


def _transfer_json(*, disabled: bool = True) -> dict:
    return {
        "name": "projects/test-project/locations/us/transferConfigs/example",
        "displayName": "oura_navi_monitor_aggregate_refresh",
        "dataSourceId": "scheduled_query",
        "disabled": disabled,
        "schedule": "every 15 minutes",
        "destinationDatasetId": "",
        "ownerInfo": {"email": "legacy-dts@test-project.iam.gserviceaccount.com"},
        "userId": "opaque-owner-id",
        "params": {"query": "SELECT 1"},
    }


def _write_fake_gcloud(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    marker = tmp_path / "updated"
    service_describe_count = tmp_path / "service-describe-count"
    service_describe_count.unlink(missing_ok=True)
    script = fake_bin / "gcloud"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" run services describe "* ]]; then
  count=0
  if [[ -f "${FAKE_SERVICE_DESCRIBE_COUNT}" ]]; then
    count="$(cat "${FAKE_SERVICE_DESCRIBE_COUNT}")"
  fi
  count=$((count + 1))
  printf '%s' "${count}" > "${FAKE_SERVICE_DESCRIBE_COUNT}"
  if [[ "${count}" -eq 2 && "${FAKE_HELD_READBACK_DELAY_SECONDS:-0}" != "0" ]]; then
    sleep "${FAKE_HELD_READBACK_DELAY_SECONDS}"
  fi
  if [[ "${count}" -ge 2 && "${FAKE_SERVICE_REVERTS_UNDER_LOCK:-false}" == "true" ]]; then
    printf '%s\n' "${FAKE_SERVICE_BEFORE}"
  elif [[ -e "${FAKE_UPDATE_MARKER}" ]]; then
    printf '%s\n' "${FAKE_SERVICE_AFTER}"
  else
    printf '%s\n' "${FAKE_SERVICE_BEFORE}"
  fi
elif [[ " $* " == *" run revisions describe "* ]]; then
  if [[ -e "${FAKE_UPDATE_MARKER}" && "${FAKE_FAIL_POST_REVISION:-false}" == "true" ]]; then
    echo "synthetic revision readback interruption" >&2
    exit 88
  fi
  printf '%s\n' "${FAKE_REVISION_JSON}"
elif [[ " $* " == *" scheduler jobs describe "* ]]; then
  if [[ "${FAKE_REPLACE_SCHEMA_DURING_READBACK:-false}" == "true" && ! -e "${FAKE_SCHEMA_REPLACED_MARKER}" ]]; then
    printf '{}\n' > "${FAKE_SCHEMA_RECEIPT}"
    : > "${FAKE_SCHEMA_REPLACED_MARKER}"
  fi
  if [[ -e "${FAKE_UPDATE_MARKER}" && "${FAKE_SCHEDULER_DRIFTS_AFTER_UPDATE:-false}" == "true" ]]; then
    printf '%s\n' "${FAKE_SCHEDULER_AFTER_JSON}"
  else
    printf '%s\n' "${FAKE_SCHEDULER_JSON}"
  fi
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\n' "${FAKE_JOB_JSON}"
elif [[ " $* " == *" run services update-traffic "* ]]; then
  printf '%s\n' 'gcloud:update-traffic' >> "${FAKE_RELEASE_EVENTS}"
  if [[ "${FAKE_UPDATE_APPLIES:-true}" == "true" ]]; then
    : > "${FAKE_UPDATE_MARKER}"
  fi
  printf '{}\n'
  exit "${FAKE_UPDATE_RETURN_CODE:-0}"
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
if [[ " $* " == *" show "* && " $* " == *"--transfer_config"* ]]; then
  printf '%s\n' "${FAKE_TRANSFER_JSON}"
else
  echo "unexpected bq command: $*" >&2
  exit 92
fi
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    bq.chmod(0o755)
    return fake_bin, marker


def _write_fake_firestore(tmp_path: Path) -> Path:
    modules = tmp_path / "fake-modules"
    cloud = modules / "google" / "cloud"
    cloud.mkdir(parents=True, exist_ok=True)
    oauth2 = modules / "google" / "oauth2"
    oauth2.mkdir(parents=True, exist_ok=True)
    (modules / "google" / "__init__.py").write_text("", encoding="utf-8")
    (oauth2 / "__init__.py").write_text(
        "from . import service_account\n", encoding="utf-8"
    )
    (oauth2 / "service_account.py").write_text(
        "class Credentials:\n"
        "    @staticmethod\n"
        "    def from_service_account_file(*args, **kwargs): return object()\n",
        encoding="utf-8",
    )
    (cloud / "__init__.py").write_text(
        "from . import firestore\n", encoding="utf-8"
    )
    (cloud / "firestore.py").write_text(
        r'''from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

SERVER_TIMESTAMP = "fake-server-timestamp"


class Snapshot:
    def __init__(self, value):
        self.exists = value is not None
        self._value = value

    def to_dict(self):
        return dict(self._value) if self._value is not None else None


class Document:
    def __init__(self, collection, document):
        self.key = collection + "/" + document


class Collection:
    def __init__(self, name):
        self.name = name

    def document(self, name):
        return Document(self.name, name)


class Transaction:
    def __init__(self):
        self.store = None
        self.deleted = False

    def get(self, document):
        return iter([Snapshot(self.store.get(document.key))])

    def create(self, document, value):
        if document.key in self.store:
            raise RuntimeError("already exists")
        self.store[document.key] = dict(value)

    def delete(self, document):
        self.store.pop(document.key, None)
        self.deleted = True

    def set(self, document, value):
        self.store[document.key] = dict(value)


class Client:
    def __init__(self, *, project, database, credentials=None):
        if project != os.environ["FAKE_FIRESTORE_PROJECT"]:
            raise RuntimeError("wrong project")
        if database != os.environ["FAKE_FIRESTORE_DATABASE"]:
            raise RuntimeError("wrong database")

    def collection(self, name):
        return Collection(name)

    def transaction(self):
        return Transaction()


def transactional(function):
    def run(transaction):
        state_path = Path(os.environ["FAKE_FIRESTORE_STATE"])
        mutex_path = Path(os.environ["FAKE_FIRESTORE_MUTEX"])
        mutex_path.touch(exist_ok=True)
        with mutex_path.open("r+") as mutex:
            fcntl.flock(mutex.fileno(), fcntl.LOCK_EX)
            if state_path.exists():
                transaction.store = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                transaction.store = {}
            before = dict(transaction.store)
            result = function(transaction)
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(transaction.store, sort_keys=True), encoding="utf-8"
            )
            os.replace(temporary, state_path)
            with open(os.environ["FAKE_RELEASE_EVENTS"], "a", encoding="utf-8") as handle:
                if transaction.deleted:
                    action = "release"
                elif transaction.store != before and any(
                    value.get("lockState") == "retired"
                    for value in transaction.store.values()
                ):
                    action = "retire"
                else:
                    action = "acquire" if transaction.store != before else "recover"
                handle.write("firestore:" + action + "\n")
            fail_marker = os.environ.get("FAKE_FIRESTORE_FAIL_RELEASE_MARKER", "")
            if transaction.deleted and fail_marker and not Path(fail_marker).exists():
                Path(fail_marker).touch()
                raise RuntimeError("synthetic response loss after committed delete")
            return result
    return run
''',
        encoding="utf-8",
    )
    (modules / "sitecustomize.py").write_text(
        """import importlib.util
import sys
from pathlib import Path
from google.oauth2 import service_account

service_account.Credentials.from_service_account_file = staticmethod(
    lambda *args, **kwargs: object()
)

path = Path(__file__).parent / "google" / "cloud" / "firestore.py"
spec = importlib.util.spec_from_file_location("google.cloud.firestore", path)
module = importlib.util.module_from_spec(spec)
sys.modules["google.cloud.firestore"] = module
spec.loader.exec_module(module)
""",
        encoding="utf-8",
    )
    return modules


def _payloads() -> tuple[dict, dict, dict]:
    before_traffic = [
        {"revisionName": "oura-navi-monitor-00098-old", "percent": 100},
        {"revisionName": REVISION, "tag": "candidate", "percent": 0},
    ]
    before = {
        "metadata": {"name": SERVICE, "generation": 8},
        "spec": {"traffic": [dict(row) for row in before_traffic]},
        "status": {
            "observedGeneration": 8,
            "latestReadyRevisionName": REVISION,
            "traffic": [dict(row) for row in before_traffic],
        },
    }
    after_traffic = [
        {"revisionName": REVISION, "percent": 100},
        {"revisionName": REVISION, "tag": "candidate", "percent": 0},
    ]
    after = {
        "metadata": {"name": SERVICE, "generation": 9},
        "spec": {"traffic": [dict(row) for row in after_traffic]},
        "status": {
            "observedGeneration": 9,
            "latestReadyRevisionName": REVISION,
            "traffic": [dict(row) for row in after_traffic],
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
    current_job_image: str = IMAGE,
    current_dts_disabled: bool = True,
    api_routines_readable: bool = True,
    unreadable_schema_routine: str = "",
    observation_72h_minutes: int = 4320,
    service_before: dict | None = None,
    service_after: dict | None = None,
    revision_json: dict | None = None,
    update_applies: bool = True,
    update_return_code: int = 0,
    fail_post_revision: bool = False,
    service_reverts_under_lock: bool = False,
    scheduler_drifts_after_update: bool = False,
    scheduler_json: dict | None = None,
    job_json: dict | None = None,
    transfer_json: dict | None = None,
    fail_release_after_commit_once: bool = False,
    acceptance_age_minutes: float = 5,
    held_readback_delay_seconds: float = 0,
    pause_age_minutes: int = 73 * 60,
    observation_actual_minutes: int = 4321,
    observation_reported_minutes: int = 4321,
    preserve_candidate_receipts: bool = False,
    replace_schema_during_readback: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fake_bin, marker = _write_fake_gcloud(tmp_path)
    fake_modules = _write_fake_firestore(tmp_path)
    release_events = tmp_path / "release-events.log"
    release_events.touch(exist_ok=True)
    credential = tmp_path / "approved.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    clock = tmp_path / "promotion-test-clock.txt"
    if clock.exists():
        now = datetime.fromisoformat(clock.read_text(encoding="utf-8"))
    else:
        now = datetime.now(timezone.utc)
        clock.write_text(now.isoformat(), encoding="utf-8")
    pause_at = now - timedelta(minutes=pause_age_minutes)
    verified_45m_at = pause_at + timedelta(minutes=60)
    verified_72h_at = pause_at + timedelta(minutes=observation_actual_minutes)
    acceptance_at = now - timedelta(minutes=acceptance_age_minutes)
    iso = lambda value: value.isoformat().replace("+00:00", "Z")
    acceptance = tmp_path / "acceptance.json"
    if not preserve_candidate_receipts:
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
                "capturedAt": iso(acceptance_at),
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
                    routine_name: {
                        "readable": routine_name != unreadable_schema_routine
                    }
                    for routine_name in (
                        "dashboard_events",
                        "dashboard_user_list",
                        "dashboard_events_v2",
                        "dashboard_user_list_v2",
                    )
                },
                "publishedStateReadable": True,
                "capturedAt": "2026-08-29T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    api_receipt = tmp_path / "api.json"
    if not preserve_candidate_receipts:
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
                "capturedAt": iso(acceptance_at),
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
                    "serviceAccount": REFRESH_SERVICE_ACCOUNT,
                },
                "validated_execution_provenance": {
                    "name": "backfill-execution-1",
                    "image": backfill_job_image,
                    "serviceAccount": REFRESH_SERVICE_ACCOUNT,
                    "succeededCount": 1,
                    "failedCount": 0,
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
    scheduler = _scheduler_json()
    live_scheduler = scheduler_json or scheduler
    activation = tmp_path / "activation.json"
    activation_payload = {
        "project": PROJECT,
        "region": REGION,
        "dataset": DATASET,
        "location": LOCATION,
        "source_service": "lcs-rag-app",
        "job": "oura-navi-monitor-refresh",
        "new_scheduler": "oura-navi-monitor-refresh-three-hour",
        "image": IMAGE,
        "expected_job_service_account": REFRESH_SERVICE_ACCOUNT,
        "expected_new_scheduler_service_account": SCHEDULER_SERVICE_ACCOUNT,
        "canonical_start_at": "2026-08-28T00:00:00Z",
        "captured_at": "2026-08-28T00:00:01Z",
        "freeze_snapshot_sha256": "d" * 64,
        "backfill_receipt_sha256": hashlib.sha256(
            backfill_receipt.read_bytes()
        ).hexdigest(),
        "old_scheduler_readback": {"state": "PAUSED"},
        "new_scheduler_readback": scheduler,
    }
    activation.write_text(json.dumps(activation_payload), encoding="utf-8")
    pause_snapshot = tmp_path / "dts-pause.json"
    pause_payload = {
        "project": PROJECT,
        "region": REGION,
        "dataset": DATASET,
        "location": LOCATION,
        "canonical_start_at": activation_payload["canonical_start_at"],
        "paused_at": iso(pause_at),
        "activation_receipt": activation_payload,
        "transfer_config_resource": _transfer_json()["name"],
        "transfer_config_after": _transfer_json(),
    }
    pause_snapshot.write_text(json.dumps(pause_payload), encoding="utf-8")
    pause_sha = hashlib.sha256(pause_snapshot.read_bytes()).hexdigest()
    receipt_45m = tmp_path / "dts-45m.json"
    receipt_45m.write_text(
        json.dumps(
            {
                "status": "passed",
                "verified_at": iso(verified_45m_at),
                "minimum_observation_minutes": 45,
                "elapsed_minutes": 60,
                "pause_snapshot_sha256": pause_sha,
                "canonical_scheduler": scheduler,
                "canonical_job": _job_json(),
            }
        ),
        encoding="utf-8",
    )
    receipt_72h = tmp_path / "dts-72h.json"
    receipt_72h.write_text(
        json.dumps(
            {
                "status": "passed",
                "verified_at": iso(verified_72h_at),
                "minimum_observation_minutes": observation_72h_minutes,
                "elapsed_minutes": observation_reported_minutes,
                "pause_snapshot_sha256": pause_sha,
                "canonical_scheduler": scheduler,
                "canonical_job": _job_json(),
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "promotion.json"
    default_before, default_after, default_revision = _payloads()
    before = service_before or default_before
    after = service_after or default_after
    revision = revision_json or default_revision
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHONPATH": f"{fake_modules}:{ROOT}",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_UPDATE_MARKER": str(marker),
        "FAKE_SERVICE_DESCRIBE_COUNT": str(tmp_path / "service-describe-count"),
        "FAKE_HELD_READBACK_DELAY_SECONDS": str(held_readback_delay_seconds),
        "FAKE_RELEASE_EVENTS": str(release_events),
        "FAKE_SERVICE_BEFORE": json.dumps(before),
        "FAKE_SERVICE_AFTER": json.dumps(after),
        "FAKE_REVISION_JSON": json.dumps(revision),
        "FAKE_SCHEDULER_JSON": json.dumps(live_scheduler),
        "FAKE_SCHEDULER_AFTER_JSON": json.dumps(
            {**live_scheduler, "state": "PAUSED"}
        ),
        "FAKE_SCHEDULER_DRIFTS_AFTER_UPDATE": str(
            scheduler_drifts_after_update
        ).lower(),
        "FAKE_REPLACE_SCHEMA_DURING_READBACK": str(
            replace_schema_during_readback
        ).lower(),
        "FAKE_SCHEMA_RECEIPT": str(schema_receipt),
        "FAKE_SCHEMA_REPLACED_MARKER": str(tmp_path / "schema-replaced"),
        "FAKE_JOB_JSON": json.dumps(job_json or _job_json(current_job_image)),
        "FAKE_TRANSFER_JSON": json.dumps(
            transfer_json or _transfer_json(disabled=current_dts_disabled)
        ),
        "FAKE_FIRESTORE_PROJECT": PROJECT,
        "FAKE_FIRESTORE_DATABASE": FIRESTORE_DATABASE,
        "FAKE_FIRESTORE_STATE": str(tmp_path / "firestore-state.json"),
        "FAKE_FIRESTORE_MUTEX": str(tmp_path / "firestore-state.lock"),
        "FAKE_FIRESTORE_FAIL_RELEASE_MARKER": (
            str(tmp_path / "failed-release-once")
            if fail_release_after_commit_once
            else ""
        ),
        "FAKE_UPDATE_APPLIES": str(update_applies).lower(),
        "FAKE_UPDATE_RETURN_CODE": str(update_return_code),
        "FAKE_FAIL_POST_REVISION": str(fail_post_revision).lower(),
        "FAKE_SERVICE_REVERTS_UNDER_LOCK": str(service_reverts_under_lock).lower(),
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
            "--expected-build-id",
            BUILD_ID,
            "--expected-service-account",
            SERVICE_ACCOUNT,
            "--expected-job-service-account",
            REFRESH_SERVICE_ACCOUNT,
            "--legacy-transfer-resource",
            _transfer_json()["name"],
            "--dataset",
            DATASET,
            "--location",
            LOCATION,
            "--firestore-database",
            FIRESTORE_DATABASE,
            "--release-lock-collection",
            LOCK_COLLECTION,
            "--credential-file",
            str(credential),
            "--activation-receipt",
            str(activation),
            "--dts-pause-snapshot",
            str(pause_snapshot),
            "--dts-45m-receipt",
            str(receipt_45m),
            "--dts-72h-receipt",
            str(receipt_72h),
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


def _run_separately_authorized_lock_release(
    tmp_path: Path,
    snapshot: Path,
    *,
    disposition: str,
) -> None:
    """Exercise the real plan/confirm/apply gate after a no-active-holder audit."""

    credential = tmp_path / "approved.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "VERTEX_SERVICE_ACCOUNT_JSON",
        }
    }
    env.update(
        {
            "PYTHONPATH": f"{tmp_path / 'fake-modules'}:{ROOT}",
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
            "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
            "FAKE_FIRESTORE_PROJECT": PROJECT,
            "FAKE_FIRESTORE_DATABASE": FIRESTORE_DATABASE,
            "FAKE_FIRESTORE_STATE": str(tmp_path / "firestore-state.json"),
            "FAKE_FIRESTORE_MUTEX": str(tmp_path / "firestore-state.lock"),
            "FAKE_RELEASE_EVENTS": str(tmp_path / "release-events.log"),
            "FAKE_FIRESTORE_FAIL_RELEASE_MARKER": "",
        }
    )
    base = [
        str(LOCK_SCRIPT),
        "release",
        "--promotion-state",
        str(snapshot),
        "--credential-file",
        str(credential),
        "--intent-disposition",
        disposition,
    ]
    plan = subprocess.run(
        [sys.executable, *base],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert plan.returncode == 0, plan.stderr
    assert "mutation=none" in plan.stdout
    assert "intent_disposition=" + disposition in plan.stdout
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1
    confirmation = next(
        line.removeprefix("required_confirmation=")
        for line in plan.stdout.splitlines()
        if line.startswith("required_confirmation=")
    )
    invalid_recovery = subprocess.run(
        [
            sys.executable,
            str(LOCK_SCRIPT),
            "acquire",
            "--promotion-state",
            str(snapshot),
            "--credential-file",
            str(credential),
            "--allow-final-recovery",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid_recovery.returncode != 0
    assert "promotion_release_lock_invalid" in invalid_recovery.stderr
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1
    wrong_confirmation = subprocess.run(
        [
            sys.executable,
            *base,
            "--allow-intent-release",
            "--confirm-intent-release",
            confirmation + "-wrong",
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert wrong_confirmation.returncode != 0
    assert "promotion_release_lock_invalid" in wrong_confirmation.stderr
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1
    applied = subprocess.run(
        [
            sys.executable,
            *base,
            "--allow-intent-release",
            "--confirm-intent-release",
            confirmation,
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr
    assert applied.stdout.strip() == "retired_" + disposition
    retired_state = json.loads((tmp_path / "firestore-state.json").read_text())
    assert len(retired_state) == 1
    assert next(iter(retired_state.values()))["disposition"] == disposition


def test_promotion_binds_acceptance_identity_and_exact_traffic_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, snapshot, marker = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), result.stderr
    receipt = json.loads(snapshot.read_text(encoding="utf-8"))
    assert receipt["receiptType"] == "monitor_candidate_promotion_v2"
    assert receipt["state"] == "complete"
    assert receipt["targetRevision"] == REVISION
    assert receipt["buildId"] == BUILD_ID
    assert len(receipt["intentSha256"]) == 64
    assert len(receipt["lockIntentPayloadSha256"]) == 64
    assert receipt["lockIntentPayloadSha256"] != receipt["intentPayloadSha256"]
    assert receipt["serviceBefore"]["status"]["traffic"][0]["percent"] == 100
    assert receipt["serviceAfter"]["status"]["traffic"][0] == {
        "revisionName": REVISION,
        "percent": 100,
    }
    assert len(receipt["acceptanceReceiptSha256"]) == 64
    assert len(receipt["schemaReceiptSha256"]) == 64
    assert len(receipt["apiReceiptSha256"]) == 64
    assert len(receipt["backfillReceiptSha256"]) == 64
    assert len(receipt["activationReceiptSha256"]) == 64
    assert len(receipt["dtsPauseSnapshotSha256"]) == 64
    assert len(receipt["dts45mReceiptSha256"]) == 64
    assert len(receipt["dts72hReceiptSha256"]) == 64
    assert receipt["firestoreDatabase"] == FIRESTORE_DATABASE
    assert receipt["releaseLockCollection"] == LOCK_COLLECTION
    assert receipt["canonicalSchedulerGovernance"]["schedule"] == "5 */3 * * *"
    assert receipt["canonicalJobGovernance"]["serviceAccount"] == REFRESH_SERVICE_ACCOUNT
    assert receipt["legacyTransferGovernance"]["disabled"] is True
    assert set(receipt["initialGovernanceEvidence"]) == {
        "canonicalScheduler",
        "canonicalJob",
        "legacyTransfer",
    }
    assert json.loads((tmp_path / "firestore-state.json").read_text()) == {}
    assert (tmp_path / "release-events.log").read_text().splitlines() == [
        "firestore:acquire",
        "gcloud:update-traffic",
        "firestore:release",
    ]
    read_count = 0
    real_read_payload = promotion_receipt_state._read_payload

    def count_state_reads(path: Path):
        nonlocal read_count
        read_count += 1
        return real_read_payload(path)

    monkeypatch.setattr(promotion_receipt_state, "_read_payload", count_state_reads)
    identity, final_state = promotion_receipt_state.read_release_lock_contract(snapshot)
    assert final_state is True
    assert identity["intentPayloadSha256"] == receipt["lockIntentPayloadSha256"]
    assert read_count == 1

    tampered = json.loads(snapshot.read_text(encoding="utf-8"))
    tampered["serviceAfter"]["status"]["traffic"][0]["percent"] = 99
    tampered["intentPayloadSha256"] = promotion_receipt_state._intent_hash(tampered)
    snapshot.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="not exactly target revision at 100%"):
        promotion_receipt_state.read_release_lock_contract(snapshot)

    missing_final_field = json.loads(json.dumps(receipt))
    del missing_final_field["serviceAfter"]
    missing_final_field["intentPayloadSha256"] = promotion_receipt_state._intent_hash(
        missing_final_field
    )
    snapshot.write_text(json.dumps(missing_final_field), encoding="utf-8")
    with pytest.raises(ValueError, match="post-promotion service is not an object"):
        promotion_receipt_state.read_release_lock_contract(snapshot)

    future = json.loads(json.dumps(receipt))
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    future["promotedAt"] = future_time
    future["trafficReadbackAt"] = future_time
    future["intentPayloadSha256"] = promotion_receipt_state._intent_hash(future)
    snapshot.write_text(json.dumps(future), encoding="utf-8")
    with pytest.raises(ValueError, match="timestamps are in the future"):
        promotion_receipt_state.read_release_lock_contract(snapshot)

    broken_intent_link = json.loads(json.dumps(receipt))
    broken_intent_link["intentSha256"] = "f" * 64
    broken_intent_link["intentPayloadSha256"] = promotion_receipt_state._intent_hash(
        broken_intent_link
    )
    snapshot.write_text(json.dumps(broken_intent_link), encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind the raw intent"):
        promotion_receipt_state.read_release_lock_contract(snapshot)


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


def test_promotion_stops_before_traffic_when_72_hour_observation_is_short(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path, observation_72h_minutes=4319)

    assert result.returncode != 0
    assert "72-hour observation window is too short" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_promotion_recomputes_observation_time_and_requires_fresh_acceptance(
    tmp_path: Path,
) -> None:
    inconsistent, snapshot, marker = _run(
        tmp_path / "inconsistent",
        observation_actual_minutes=60,
        observation_reported_minutes=4321,
    )
    assert inconsistent.returncode != 0
    assert "timestamps do not cover" in inconsistent.stderr
    assert not marker.exists()
    assert not snapshot.exists()

    stale, snapshot, marker = _run(
        tmp_path / "stale",
        acceptance_age_minutes=120,
        pause_age_minutes=80 * 60,
    )
    assert stale.returncode != 0
    assert "is older than 60 minutes" in stale.stderr
    assert not marker.exists()
    # Freshness belongs to the same receipt-byte authority as validation,
    # hashing, and first intent publication. A stale new request must not
    # publish a durable intent that would bind unusable evidence forever.
    assert not snapshot.exists()
    assert (tmp_path / "stale" / "release-events.log").read_text() == ""


def test_held_lock_freshness_is_the_last_gate_before_traffic() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    prelock_gate = script.index('if [[ "${PRELOCK_PROMOTION_STATE}" == "pre" ]]')
    final_branch = script.index('if [[ "${PROMOTION_STATE}" == "final" ]]')
    held_pre_branch = script.index('if [[ "${PROMOTION_STATE}" == "pre" ]]', final_branch)
    held_lock_gate = script.index("validate_fresh_candidate_receipts", held_pre_branch)
    traffic_update = script.index("run services update-traffic", held_lock_gate)

    assert prelock_gate < final_branch < held_pre_branch < held_lock_gate < traffic_update


def test_prelock_fresh_receipts_that_expire_under_the_lock_never_change_traffic(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        acceptance_age_minutes=59.5,
        held_readback_delay_seconds=35,
        pause_age_minutes=74 * 60,
    )

    assert result.returncode != 0
    assert "is older than 60 minutes" in result.stderr
    assert not marker.exists()
    assert json.loads(snapshot.read_text())["state"] == "intent"
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1
    assert (tmp_path / "release-events.log").read_text().splitlines() == [
        "firestore:acquire"
    ]


def test_promotion_rechecks_live_job_and_dts_before_traffic(tmp_path: Path) -> None:
    wrong_image = (
        f"us-central1-docker.pkg.dev/{PROJECT}/repo/monitor@sha256:" + "d" * 64
    )
    job_result, job_snapshot, job_marker = _run(
        tmp_path / "job-drift",
        current_job_image=wrong_image,
    )
    assert job_result.returncode != 0
    assert "refresh Job image does not match" in job_result.stderr
    assert not job_marker.exists()
    assert not job_snapshot.exists()

    dts_result, dts_snapshot, dts_marker = _run(
        tmp_path / "dts-drift",
        current_dts_disabled=False,
    )
    assert dts_result.returncode != 0
    assert "legacy DTS is not disabled immediately before traffic" in dts_result.stderr
    assert not dts_marker.exists()
    assert not dts_snapshot.exists()


def test_promotion_does_not_finalize_when_scheduler_drifts_after_traffic_update(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        scheduler_drifts_after_update=True,
    )

    assert result.returncode != 0
    assert marker.exists(), result.stderr
    assert "canonical Scheduler drifted after the 72-hour receipt" in result.stderr
    intent = json.loads(snapshot.read_text(encoding="utf-8"))
    assert intent["receiptType"] == "monitor_candidate_promotion_intent_v1"
    lock_state = json.loads((tmp_path / "firestore-state.json").read_text())
    assert len(lock_state) == 1


def test_promotion_rejects_a_schema_receipt_that_only_saw_routine_names(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(tmp_path, api_routines_readable=False)

    assert result.returncode != 0
    assert "schema receipt is missing apiRoutinesReadable" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


@pytest.mark.parametrize(
    "runtime_routine",
    ("dashboard_events_v2", "dashboard_user_list_v2"),
)
def test_promotion_rejects_schema_receipt_without_real_runtime_v2_read(
    tmp_path: Path,
    runtime_routine: str,
) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        unreadable_schema_routine=runtime_routine,
    )

    assert result.returncode != 0
    assert f"schema receipt has no real read for {runtime_routine}" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_receipt_replaced_during_live_readback_is_never_bound_into_intent(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        replace_schema_during_readback=True,
    )

    assert result.returncode != 0
    assert "schema receipt does not match this exact candidate" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()
    assert not (tmp_path / "firestore-state.json").exists()


def test_pre_promotion_rejects_desired_traffic_drift_hidden_by_status(
    tmp_path: Path,
) -> None:
    drifted, _, _ = _payloads()
    drifted["spec"]["traffic"][0]["revisionName"] = "another-old-revision"

    result, snapshot, marker = _run(tmp_path, service_before=drifted)

    assert result.returncode != 0
    assert "desired and observed positive traffic disagree" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_pre_promotion_requires_exact_candidate_tag_in_both_traffic_planes(
    tmp_path: Path,
) -> None:
    drifted, _, _ = _payloads()
    del drifted["spec"]["traffic"][1]["tag"]

    result, snapshot, marker = _run(tmp_path, service_before=drifted)

    assert result.returncode != 0
    assert "desired traffic tag must resolve exactly once" in result.stderr
    assert not marker.exists()
    assert not snapshot.exists()


def test_post_readback_requires_observed_desired_traffic_generation(
    tmp_path: Path,
) -> None:
    _, drifted, _ = _payloads()
    drifted["metadata"]["generation"] = 10
    drifted["status"]["observedGeneration"] = 9

    result, snapshot, marker = _run(tmp_path, service_after=drifted)

    assert result.returncode != 0
    assert "live service drifted from both intent states" in result.stderr
    assert marker.exists()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["state"] == "intent"
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1


def test_final_recovery_rejects_unobserved_desired_traffic_drift(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    assert marker.exists()
    final_bytes = snapshot.read_bytes()
    _, drifted, _ = _payloads()
    drifted["metadata"]["generation"] = 10
    drifted["status"]["observedGeneration"] = 9

    recovered, _, _ = _run(
        tmp_path,
        service_after=drifted,
        preserve_candidate_receipts=True,
    )

    assert recovered.returncode != 0
    assert "post-promotion traffic is not exactly target revision at 100%" in recovered.stderr
    assert snapshot.read_bytes() == final_bytes
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == 1


def test_post_crash_requires_manual_lock_release_then_finalizes_without_recut(
    tmp_path: Path,
) -> None:
    interrupted, snapshot, marker = _run(tmp_path, fail_post_revision=True)

    assert interrupted.returncode != 0
    assert marker.exists()
    intent = json.loads(snapshot.read_text(encoding="utf-8"))
    assert intent["receiptType"] == "monitor_candidate_promotion_intent_v1"

    blocked, _, _ = _run(tmp_path)
    assert blocked.returncode != 0
    assert "promotion_release_lock_conflict" in blocked.stderr
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == 1

    _run_separately_authorized_lock_release(
        tmp_path,
        snapshot,
        disposition="authorized_post_recovery",
    )
    scheduler_after = _scheduler_json()
    scheduler_after.update(
        {
            "updateTime": "2026-09-01T00:02:00Z",
            "lastAttemptTime": "2026-09-01T00:01:00Z",
        }
    )
    job_after = _job_json()
    job_after.update(
        {
            "updateTime": "2026-09-01T00:02:01Z",
            "status": {"conditions": [{"type": "Ready", "lastTransitionTime": "later"}]},
        }
    )
    transfer_after = _transfer_json()
    transfer_after.update(
        {
            "updateTime": "2026-09-01T00:02:02Z",
            "nextRunTime": "2026-09-01T03:00:00Z",
        }
    )
    resumed, _, _ = _run(
        tmp_path,
        scheduler_json=scheduler_after,
        job_json=job_after,
        transfer_json=transfer_after,
    )

    assert resumed.returncode == 0, resumed.stderr
    receipt = json.loads(snapshot.read_text(encoding="utf-8"))
    assert receipt["receiptType"] == "monitor_candidate_promotion_v2"
    assert receipt["serviceAfter"]["status"]["traffic"][0] == {
        "revisionName": REVISION,
        "percent": 100,
    }
    assert "updateTime" not in receipt["initialGovernanceEvidence"]["canonicalJob"]
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == 1


def test_post_recovery_cannot_turn_into_a_second_traffic_mutation_under_lock(
    tmp_path: Path,
) -> None:
    interrupted, snapshot, marker = _run(tmp_path, fail_post_revision=True)

    assert interrupted.returncode != 0
    assert marker.exists()
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == 1
    _run_separately_authorized_lock_release(
        tmp_path,
        snapshot,
        disposition="authorized_post_recovery",
    )

    changed_under_lock, _, _ = _run(
        tmp_path,
        service_reverts_under_lock=True,
    )

    assert changed_under_lock.returncode != 0
    assert "live state changed while acquiring the promotion lock" in changed_under_lock.stderr
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == 1
    assert json.loads(snapshot.read_text(encoding="utf-8"))["state"] == "intent"
    assert len(json.loads((tmp_path / "firestore-state.json").read_text())) == 1


def test_promotion_finalizes_when_update_command_failed_but_live_traffic_applied(
    tmp_path: Path,
) -> None:
    result, snapshot, marker = _run(
        tmp_path,
        update_applies=True,
        update_return_code=17,
    )

    assert result.returncode == 0, result.stderr
    assert marker.exists()
    receipt = json.loads(snapshot.read_text(encoding="utf-8"))
    assert receipt["updateCommandReturnCode"] == 17
    assert receipt["receiptType"] == "monitor_candidate_promotion_v2"


def test_aborted_pre_intent_cannot_be_reused_after_manual_retirement(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(
        tmp_path,
        update_applies=False,
        update_return_code=19,
    )
    assert first.returncode != 0
    assert snapshot.exists()
    assert not marker.exists()

    blocked, _, _ = _run(tmp_path)
    assert blocked.returncode != 0
    assert "promotion_release_lock_conflict" in blocked.stderr
    assert not marker.exists()
    _run_separately_authorized_lock_release(
        tmp_path,
        snapshot,
        disposition="aborted_pre",
    )

    changed_before, _, _ = _payloads()
    changed_before["metadata"]["resourceVersion"] = "new-natural-readback-version"
    changed_before["status"]["conditions"] = [
        {"type": "Ready", "status": "True", "lastTransitionTime": "later"}
    ]
    reused, _, _ = _run(tmp_path, service_before=changed_before)

    assert reused.returncode != 0
    assert "retired and cannot be acquired again" in reused.stderr
    assert not marker.exists()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["state"] == "intent"
    retired_state = json.loads((tmp_path / "firestore-state.json").read_text())
    assert next(iter(retired_state.values()))["disposition"] == "aborted_pre"


def test_promotion_resume_stops_on_old_traffic_allocation_drift(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(
        tmp_path,
        update_applies=False,
        update_return_code=19,
    )
    assert first.returncode != 0
    assert snapshot.exists()
    assert not marker.exists()
    _run_separately_authorized_lock_release(
        tmp_path,
        snapshot,
        disposition="aborted_pre",
    )

    drifted_before, _, _ = _payloads()
    drifted_before["status"]["traffic"] = [
        {"revisionName": "oura-navi-monitor-00098-old", "percent": 50},
        {"revisionName": "oura-navi-monitor-00097-other", "percent": 50},
        {"revisionName": REVISION, "tag": "candidate", "percent": 0},
    ]
    resumed, _, _ = _run(tmp_path, service_before=drifted_before)

    assert resumed.returncode != 0
    assert "live service drifted from both intent states" in resumed.stderr
    assert not marker.exists()


def test_promotion_resume_stops_on_normalized_job_governance_drift(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(
        tmp_path,
        update_applies=False,
        update_return_code=19,
    )
    assert first.returncode != 0
    assert snapshot.exists()
    assert not marker.exists()

    drifted_job = _job_json()
    environment = drifted_job["template"]["template"]["containers"][0]["env"]
    next(
        row for row in environment if row["name"] == "MONITOR_ANALYTICS_START_AT"
    )["value"] = "2026-03-17T00:00:00Z"
    resumed, _, _ = _run(tmp_path, job_json=drifted_job)

    assert resumed.returncode != 0
    assert "canonicalJobGovernance" in resumed.stderr
    assert not marker.exists()
    lock_state = json.loads((tmp_path / "firestore-state.json").read_text())
    assert len(lock_state) == 1

def test_promotion_rejects_a_tampered_intent_before_retrying_traffic(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(
        tmp_path,
        update_applies=False,
        update_return_code=19,
    )
    assert first.returncode != 0
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["capturedAt"] = "tampered"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    resumed, _, _ = _run(tmp_path)

    assert resumed.returncode != 0
    assert "promotion intent integrity mismatch" in resumed.stderr
    assert not marker.exists()


def test_completed_promotion_is_idempotent_and_does_not_rewrite_receipt(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    before = snapshot.read_bytes()

    second, _, _ = _run(tmp_path)

    assert second.returncode == 0, second.stderr
    assert "promotion=already-complete" in second.stdout
    assert snapshot.read_bytes() == before
    assert marker.exists()
    assert (tmp_path / "release-events.log").read_text().splitlines() == [
        "firestore:acquire",
        "gcloud:update-traffic",
        "firestore:release",
        "firestore:acquire",
        "firestore:release",
    ]


def test_completed_receipt_can_recover_its_exact_lock_without_recutting(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    assert marker.exists()
    before = snapshot.read_bytes()
    identity = promotion_receipt_state.read_release_lock_identity(snapshot)
    document_id = hashlib.sha256(
        f"{PROJECT}|{REGION}|{SERVICE}".encode("utf-8")
    ).hexdigest()
    (tmp_path / "firestore-state.json").write_text(
        json.dumps(
            {
                f"{LOCK_COLLECTION}/{document_id}": {
                    "contractVersion": "monitor.promotion-lock.v1",
                    **identity,
                    "acquiredAt": "fake-server-timestamp",
                }
            }
        ),
        encoding="utf-8",
    )

    recovered, _, _ = _run(tmp_path)

    assert recovered.returncode == 0, recovered.stderr
    assert "promotion=already-complete" in recovered.stdout
    assert snapshot.read_bytes() == before
    events = (tmp_path / "release-events.log").read_text().splitlines()
    assert events.count("gcloud:update-traffic") == 1
    assert events[-2:] == ["firestore:recover", "firestore:release"]
    assert json.loads((tmp_path / "firestore-state.json").read_text()) == {}


def test_stale_candidate_receipts_can_only_recover_a_completed_release(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(tmp_path, pause_age_minutes=74 * 60)
    assert first.returncode == 0, first.stderr
    assert marker.exists()
    clock = datetime.fromisoformat(
        (tmp_path / "promotion-test-clock.txt").read_text(encoding="utf-8")
    )
    stale_capture = (clock - timedelta(minutes=90)).isoformat().replace(
        "+00:00", "Z"
    )
    for name in ("api.json", "acceptance.json"):
        path = tmp_path / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["capturedAt"] = stale_capture
        path.write_text(json.dumps(payload), encoding="utf-8")

    final = json.loads(snapshot.read_text(encoding="utf-8"))
    final["apiReceiptSha256"] = hashlib.sha256(
        (tmp_path / "api.json").read_bytes()
    ).hexdigest()
    final["acceptanceReceiptSha256"] = hashlib.sha256(
        (tmp_path / "acceptance.json").read_bytes()
    ).hexdigest()
    original_intent = dict(final)
    for key in (
        "intentSha256",
        "promotedAt",
        "trafficReadbackAt",
        "serviceAfter",
        "revisionAfter",
        "updateCommandReturnCode",
        "lockIntentPayloadSha256",
    ):
        original_intent.pop(key, None)
    original_intent.update(
        {
            "receiptType": "monitor_candidate_promotion_intent_v1",
            "state": "intent",
        }
    )
    original_intent["intentPayloadSha256"] = promotion_receipt_state._intent_hash(
        original_intent
    )
    raw_intent = (
        json.dumps(original_intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    final["lockIntentPayloadSha256"] = original_intent["intentPayloadSha256"]
    final["intentSha256"] = hashlib.sha256(raw_intent).hexdigest()
    final["intentPayloadSha256"] = promotion_receipt_state._intent_hash(final)
    snapshot.write_text(
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    identity = promotion_receipt_state.read_release_lock_identity(snapshot)
    document_id = hashlib.sha256(
        f"{PROJECT}|{REGION}|{SERVICE}".encode("utf-8")
    ).hexdigest()
    (tmp_path / "firestore-state.json").write_text(
        json.dumps(
            {
                f"{LOCK_COLLECTION}/{document_id}": {
                    "contractVersion": "monitor.promotion-lock.v1",
                    **identity,
                    "acquiredAt": "fake-server-timestamp",
                }
            }
        ),
        encoding="utf-8",
    )
    update_count = (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    )

    recovered, _, _ = _run(
        tmp_path,
        pause_age_minutes=74 * 60,
        preserve_candidate_receipts=True,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert "promotion=already-complete" in recovered.stdout
    assert (tmp_path / "release-events.log").read_text().splitlines().count(
        "gcloud:update-traffic"
    ) == update_count
    assert json.loads((tmp_path / "firestore-state.json").read_text()) == {}


def test_completed_local_receipt_still_stops_on_a_conflicting_service_lock(
    tmp_path: Path,
) -> None:
    first, snapshot, marker = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    before = snapshot.read_bytes()
    document_id = hashlib.sha256(
        f"{PROJECT}|{REGION}|{SERVICE}".encode("utf-8")
    ).hexdigest()
    (tmp_path / "firestore-state.json").write_text(
        json.dumps(
            {
                f"{LOCK_COLLECTION}/{document_id}": {
                    "contractVersion": "monitor.promotion-lock.v1",
                    "project": PROJECT,
                    "region": REGION,
                    "service": SERVICE,
                    "targetRevision": "another-candidate",
                    "intentPayloadSha256": "1" * 64,
                    "staticContractSha256": "2" * 64,
                    "firestoreDatabase": FIRESTORE_DATABASE,
                    "releaseLockCollection": LOCK_COLLECTION,
                    "acquiredAt": "fake-server-timestamp",
                }
            }
        ),
        encoding="utf-8",
    )

    blocked, _, _ = _run(tmp_path)

    assert blocked.returncode != 0
    assert "promotion_release_lock_conflict" in blocked.stderr
    assert snapshot.read_bytes() == before
    assert marker.exists()


def test_promotion_recovers_when_lock_release_committed_but_response_was_lost(
    tmp_path: Path,
) -> None:
    interrupted, snapshot, marker = _run(
        tmp_path,
        fail_release_after_commit_once=True,
    )

    assert interrupted.returncode != 0
    assert "provider operation failed" in interrupted.stderr
    assert marker.exists()
    assert json.loads(snapshot.read_text())["receiptType"] == (
        "monitor_candidate_promotion_v2"
    )
    assert json.loads((tmp_path / "firestore-state.json").read_text()) == {}

    resumed, _, _ = _run(tmp_path, fail_release_after_commit_once=True)

    assert resumed.returncode == 0, resumed.stderr
    assert "promotion=already-complete" in resumed.stdout
    assert json.loads((tmp_path / "firestore-state.json").read_text()) == {}
