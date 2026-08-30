from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.refresh_policy import (
    REFRESH_POLICY,
    RefreshPolicy,
    next_scheduled_refresh,
    safe_scheduler_bootstrap_cron,
)
from app.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
TEST_JOB_SERVICE_ACCOUNT = (
    "monitor-refresh-writer@test-project.iam.gserviceaccount.com"
)
TEST_OLD_SCHEDULER_SERVICE_ACCOUNT = (
    "monitor-legacy-invoker@test-project.iam.gserviceaccount.com"
)
TEST_NEW_SCHEDULER_SERVICE_ACCOUNT = (
    "monitor-scheduler-invoker@test-project.iam.gserviceaccount.com"
)
TEST_DTS_SERVICE_ACCOUNT = "monitor-legacy-dts@test-project.iam.gserviceaccount.com"


def _refresh_job_json(image: str) -> dict[str, object]:
    return {
        "template": {
            "taskCount": 1,
            "parallelism": 1,
            "template": {
                "serviceAccount": TEST_JOB_SERVICE_ACCOUNT,
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


def _refresh_execution_json(
    image: str,
    *,
    name: str = "fake-refresh-execution",
    create_time: str = "2026-08-28T00:05:30Z",
    creator: str = TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
) -> dict[str, object]:
    return {
        "name": name,
        "metadata": {
            "name": name,
            "annotations": {"run.googleapis.com/creator": creator},
        },
        "createTime": create_time,
        "template": {
            "taskCount": 1,
            "template": {
                "serviceAccount": TEST_JOB_SERVICE_ACCOUNT,
                "containers": [{"image": image}],
            },
        },
        "status": {
            "succeededCount": 1,
            "failedCount": 0,
            "conditions": [{"type": "Completed", "status": "True"}],
        },
    }


def _validated_refresh_contract(image: str) -> dict[str, object]:
    return {
        "image": image,
        "serviceAccount": TEST_JOB_SERVICE_ACCOUNT,
        "command": ["python"],
        "args": [
            "-m",
            "app.jobs.refresh_analytics",
            "--apply",
            "--trigger-source",
            "scheduler_three_hour",
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


def _successful_reconciliation(
    *,
    duplicate_rows: int = 0,
    deduplicated_rows: int = 0,
    conflicting_duplicate_rows: int = 0,
) -> list[dict[str, str]]:
    return [
        {
            "successful_run_count": "1",
            "input_row_count": "3",
            "merged_row_count": "3",
            "duplicate_row_count": str(duplicate_rows),
            "quarantined_manifest_count": "0",
            "deduplicated_manifest_count": str(deduplicated_rows),
            "conflicting_duplicate_manifest_count": str(
                conflicting_duplicate_rows
            ),
            "canonical_persistence_count": "0",
            "canonical_question_count": "1",
            "matched_question_count": "1",
            "canonical_answer_count": "1",
            "matched_answer_count": "1",
            "canonical_action_count": "1",
            "matched_action_count": "1",
            "blocking_failure_count": "0",
            "axis_unmeasured_finding_count": "0",
        }
    ]


def test_three_hour_policy_is_the_single_timing_owner() -> None:
    settings = Settings()

    assert REFRESH_POLICY.scheduler_cron == "5 */3 * * *"
    assert REFRESH_POLICY.job_name == "oura-navi-monitor-refresh"
    assert REFRESH_POLICY.scheduler_name == "oura-navi-monitor-refresh-three-hour"
    assert REFRESH_POLICY.legacy_scheduler_name == (
        "oura-navi-monitor-refresh-quarter-hour"
    )
    assert REFRESH_POLICY.legacy_scheduler_cron == "*/15 * * * *"
    assert REFRESH_POLICY.scheduler_bootstrap_lead_days == 2
    assert REFRESH_POLICY.scheduler_attempt_deadline_seconds == 60
    assert REFRESH_POLICY.legacy_scheduler_attempt_deadline_seconds == 30
    assert REFRESH_POLICY.scheduler_max_retry_attempts == 0
    assert REFRESH_POLICY.timezone == "Asia/Tokyo"
    assert REFRESH_POLICY.cadence_minutes == 180
    assert REFRESH_POLICY.expected_delay_minutes == 5
    assert REFRESH_POLICY.event_future_tolerance_minutes == 10
    assert REFRESH_POLICY.overlap_minutes == 240
    assert REFRESH_POLICY.max_window_hours == 24
    assert REFRESH_POLICY.freshness_stale_after_minutes == 240
    assert REFRESH_POLICY.lease_ttl_minutes == 45
    for legacy_override in (
        "monitor_refresh_cadence_minutes",
        "monitor_refresh_delay_minutes",
        "monitor_event_future_tolerance_minutes",
        "monitor_refresh_overlap_minutes",
        "monitor_refresh_max_window_hours",
        "monitor_data_freshness_minutes",
        "monitor_refresh_lease_ttl_minutes",
    ):
        assert not hasattr(settings, legacy_override)


def test_next_refresh_uses_the_five_minute_japan_boundary() -> None:
    before_midnight_boundary = datetime(
        2026, 8, 27, 15, 4, 59, tzinfo=timezone.utc
    )
    at_midnight_boundary = datetime(2026, 8, 27, 15, 5, tzinfo=timezone.utc)

    assert next_scheduled_refresh(now=before_midnight_boundary) == datetime(
        2026, 8, 27, 15, 5, tzinfo=timezone.utc
    )
    assert next_scheduled_refresh(now=at_midnight_boundary) == datetime(
        2026, 8, 27, 18, 5, tzinfo=timezone.utc
    )


def test_scheduler_bootstrap_uses_a_valid_date_more_than_one_day_ahead() -> None:
    assert safe_scheduler_bootstrap_cron(
        now=datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)
    ) == "0 0 30 8 *"
    assert safe_scheduler_bootstrap_cron(
        now=datetime(2026, 12, 30, 14, 59, tzinfo=timezone.utc)
    ) == "0 0 1 1 *"


def test_policy_rejects_a_lease_that_can_expire_before_job_timeout() -> None:
    try:
        RefreshPolicy(lease_ttl_minutes=30, job_timeout_minutes=30)
    except ValueError as exc:
        assert "lease TTL" in str(exc)
    else:
        raise AssertionError("unsafe lease policy must be rejected")


def test_bootstrap_and_alerts_read_the_governed_policy() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap_gcp.sh").read_text(encoding="utf-8")
    alerts = (ROOT / "scripts" / "setup_alerts.sh").read_text(encoding="utf-8")

    assert "REFRESH_POLICY.scheduler_cron" in bootstrap
    assert "REFRESH_POLICY.job_timeout_minutes" in bootstrap
    assert "safe_scheduler_bootstrap_cron" in bootstrap
    assert 'scheduler jobs pause "${name}"' in bootstrap
    assert "require_paused_scheduler_if_present" in bootstrap
    assert bootstrap.index("require_paused_scheduler_if_present") < bootstrap.index(
        "run jobs deploy"
    )
    assert "REFRESH_POLICY.no_success_warning_minutes" in alerts
    assert "REFRESH_POLICY.no_success_critical_minutes" in alerts
    assert "refresh-every-15m" not in bootstrap
    assert "--trigger-source,scheduler_three_hour" in bootstrap


def test_refresh_job_deploy_requires_exact_confirmation_and_writes_receipt(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bootstrap-bin"
    fake_bin.mkdir(exist_ok=True)
    mutation_marker = tmp_path / "job-deployed"
    credential = tmp_path / "approved.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    image = (
        "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
        + "9" * 64
    )
    job_uri = (
        "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/"
        "namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
    )
    scheduler = {
        "state": "PAUSED",
        "schedule": "5 */3 * * *",
        "timeZone": "Asia/Tokyo",
        "attemptDeadline": "60s",
        "retryConfig": {"retryCount": 0},
        "httpTarget": {
            "uri": job_uri,
            "oauthToken": {
                "serviceAccountEmail": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT
            },
        },
    }
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" scheduler jobs describe "* && " $* " == *"--format=value(state)"* ]]; then
  printf '%s\n' 'PAUSED'
elif [[ " $* " == *" scheduler jobs describe "* && " $* " == *"--format=json"* ]]; then
  printf '%s\n' "${FAKE_SCHEDULER_JSON}"
elif [[ " $* " == *" scheduler jobs describe "* ]]; then
  printf '%s\n' '{}'
elif [[ " $* " == *" run jobs deploy "* ]]; then
  : > "${FAKE_MUTATION_MARKER}"
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\n' "${FAKE_JOB_JSON}"
elif [[ " $* " == *" scheduler jobs update http "* ]]; then
  true
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
if [[ " $* " == *" query "* ]]; then
  printf '%s\n' '1'
elif [[ " $* " == *" show "* ]]; then
  true
else
  exit 91
fi
""",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    bq.chmod(0o755)
    env = {
        **os.environ,
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_MUTATION_MARKER": str(mutation_marker),
        "FAKE_JOB_JSON": json.dumps(_refresh_job_json(image)),
        "FAKE_SCHEDULER_JSON": json.dumps(scheduler),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    base_arguments = [
        "bash",
        str(ROOT / "scripts" / "bootstrap_gcp.sh"),
        "--stage",
        "activate",
        "--project",
        "test-project",
        "--runtime-service-account",
        TEST_JOB_SERVICE_ACCOUNT,
        "--scheduler-invoker-service-account",
        TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "--image",
        image,
        "--analytics-start-at",
        "2026-03-16T00:00:00Z",
        "--deploy-receipt-output",
        str(tmp_path / "job-deploy.json"),
        "--credential-file",
        str(credential),
        "--apply",
    ]

    rejected = subprocess.run(
        base_arguments,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert not mutation_marker.exists()
    assert "--confirm-activate must equal" in rejected.stderr

    confirmation = (
        "projects/test-project/locations/us-central1/jobs/"
        f"oura-navi-monitor-refresh:deploy:{image}:"
        f"{TEST_JOB_SERVICE_ACCOUNT}:{TEST_NEW_SCHEDULER_SERVICE_ACCOUNT}"
    )
    applied = subprocess.run(
        [
            *base_arguments[:-1],
            "--confirm-activate",
            confirmation,
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert applied.returncode == 0, applied.stderr
    assert mutation_marker.exists()
    receipt = json.loads(
        (tmp_path / "job-deploy.json").read_text(encoding="utf-8")
    )
    assert receipt["receipt_type"] == "monitor_refresh_job_deploy_v1"
    assert receipt["image"] == image
    assert receipt["scheduler_readback"]["state"] == "PAUSED"


def test_legacy_dts_pause_defaults_to_a_read_only_plan() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "pause_legacy_bigquery_refresh.sh"),
            "--project",
            "test-project",
            "--transfer-config",
            "projects/test-project/locations/us/transferConfigs/example",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=plan" in result.stdout
    assert "schedule=5 */3 * * *" in result.stdout
    assert "pause automatic scheduling only" in result.stdout


def test_scheduler_cutover_defaults_to_a_read_only_plan() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "cutover_refresh_scheduler.sh"),
            "--project",
            "test-project",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=plan stage=freeze-old" in result.stdout
    assert "old_scheduler=oura-navi-monitor-refresh-quarter-hour" in result.stdout
    assert "new_scheduler=oura-navi-monitor-refresh-three-hour" in result.stdout
    assert "pause the old scheduler before deploying" in result.stdout


def test_recent_backfill_defaults_to_a_read_only_plan() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "backfill_recent_data.sh"),
            "--project",
            "test-project",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=plan" in result.stdout
    assert "old and new schedulers must both be PAUSED" in result.stdout
    assert "--until-current" in result.stdout


@pytest.mark.parametrize(
    ("reconciliation", "expected_success"),
    (
        (_successful_reconciliation(), True),
        (
            _successful_reconciliation(
                duplicate_rows=2,
                deduplicated_rows=1,
                conflicting_duplicate_rows=1,
            ),
            True,
        ),
        (
            _successful_reconciliation(
                duplicate_rows=2,
                deduplicated_rows=1,
                conflicting_duplicate_rows=0,
            ),
            False,
        ),
    ),
)
def test_recent_backfill_runs_only_the_expected_frozen_job(
    tmp_path: Path,
    reconciliation: list[dict[str, str]],
    expected_success: bool,
) -> None:
    fake_bin = tmp_path / "backfill-bin"
    fake_bin.mkdir()
    execution_marker = tmp_path / "job-executed"
    bq_call_marker = tmp_path / "bq-called"
    expected_image = (
        "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
        + "a" * 64
    )
    job_uri = "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
    freeze_snapshot = tmp_path / "scheduler-freeze.json"
    freeze_snapshot.write_text(
        json.dumps(
            {
                "project": "test-project",
                "region": "us-central1",
                "dataset": "oura_navi_monitor",
                "location": "US",
                "source_service": "lcs-rag-app",
                "expected_job_service_account": TEST_JOB_SERVICE_ACCOUNT,
                "expected_old_scheduler_service_account": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
                "expected_new_scheduler_service_account": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
                "old_scheduler": "oura-navi-monitor-refresh-quarter-hour",
                "new_scheduler": "oura-navi-monitor-refresh-three-hour",
                "freeze_started_at": "2026-08-28T00:00:00Z",
                "freeze_verified_at": "2026-08-28T00:01:00Z",
                "active_bigquery_writers_at_freeze": [],
            }
        ),
        encoding="utf-8",
    )
    credential = tmp_path / "approved-backfill-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    job_deploy_receipt = tmp_path / "job-deploy-receipt.json"
    job_deploy_receipt.write_text(
        json.dumps(
            {
                "receipt_type": "monitor_refresh_job_deploy_v1",
                "project": "test-project",
                "region": "us-central1",
                "dataset": "oura_navi_monitor",
                "location": "US",
                "source_service": "lcs-rag-app",
                "job": "oura-navi-monitor-refresh",
                "scheduler": "oura-navi-monitor-refresh-three-hour",
                "image": expected_image,
                "expected_job_service_account": TEST_JOB_SERVICE_ACCOUNT,
                "expected_scheduler_service_account": (
                    TEST_NEW_SCHEDULER_SERVICE_ACCOUNT
                ),
                "captured_at": "2026-08-28T00:02:00Z",
                "validated_job_contract": _validated_refresh_contract(
                    expected_image
                ),
                "scheduler_readback": {"state": "PAUSED"},
            }
        ),
        encoding="utf-8",
    )
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" scheduler jobs describe "* ]]; then
  if [[ " $* " == *" oura-navi-monitor-refresh-quarter-hour "* ]]; then
    schedule="*/15 * * * *"
    deadline="30s"
    service_account="${FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT}"
  else
    schedule="5 */3 * * *"
    deadline="60s"
    service_account="${FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT}"
  fi
  printf '{"state":"PAUSED","schedule":"%s","timeZone":"Asia/Tokyo","attemptDeadline":"%s","retryConfig":{"retryCount":0},"httpTarget":{"uri":"%s","oauthToken":{"serviceAccountEmail":"%s"}}}\n' "${schedule}" "${deadline}" "${FAKE_JOB_URI}" "${service_account}"
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\n' "${FAKE_JOB_JSON}"
elif [[ " $* " == *" run jobs execute "* && " $* " == *"--until-current"* && " $* " == *"--target-at"* ]]; then
  : > "${FAKE_EXECUTION_MARKER}"
  printf '%s\n' "${FAKE_EXECUTION_JSON}"
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    bq = fake_bin / "bq"
    bq.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " != *" query "* ]]; then exit 2; fi
if [[ " $* " == *"pipeline_run_event_manifest"* ]]; then
  printf '%s\n' "${FAKE_RECONCILIATION_JSON}"
elif [[ -e "${FAKE_BQ_CALL_MARKER}" ]]; then
  data_through="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '[{"source":"published","status":"succeeded","published_run_id":"run-after","data_through":"%s","lease_active":"false"}]\n' "${data_through}"
else
  : > "${FAKE_BQ_CALL_MARKER}"
  printf '%s\n' '[{"source":"published","status":"succeeded","published_run_id":"run-before","data_through":"2026-08-27T00:57:05Z","lease_active":"false"}]'
fi
""",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    bq.chmod(0o755)
    env = {
        **os.environ,
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_JOB_URI": job_uri,
        "FAKE_EXPECTED_IMAGE": expected_image,
        "FAKE_JOB_JSON": json.dumps(_refresh_job_json(expected_image)),
        "FAKE_EXECUTION_JSON": json.dumps(
            _refresh_execution_json(
                expected_image,
                name="fake-backfill-execution",
            )
        ),
        "FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_RECONCILIATION_JSON": json.dumps(reconciliation),
        "FAKE_EXECUTION_MARKER": str(execution_marker),
        "FAKE_BQ_CALL_MARKER": str(bq_call_marker),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    confirmation = (
        "projects/test-project/locations/us-central1/jobs/"
        "oura-navi-monitor-refresh:backfill-until-current:"
        f"{expected_image}:{TEST_JOB_SERVICE_ACCOUNT}"
    )
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "backfill_recent_data.sh"),
            "--project",
            "test-project",
            "--freeze-snapshot",
            str(freeze_snapshot),
            "--job-deploy-receipt",
            str(job_deploy_receipt),
            "--receipt-output",
            str(tmp_path / "backfill-receipt.json"),
            "--expected-image",
            expected_image,
            "--expected-job-service-account",
            TEST_JOB_SERVICE_ACCOUNT,
            "--expected-old-scheduler-service-account",
            TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
            "--expected-new-scheduler-service-account",
            TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
            "--credential-file",
            str(credential),
            "--confirm-backfill",
            confirmation,
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert execution_marker.exists()
    if expected_success:
        assert result.returncode == 0, result.stderr
        receipt = json.loads(
            (tmp_path / "backfill-receipt.json").read_text(encoding="utf-8")
        )
        assert receipt["validated_execution_provenance"]["image"] == expected_image
        assert len(receipt["job_deploy_receipt_sha256"]) == 64
        assert "backfill=complete" in result.stdout
    else:
        assert result.returncode != 0
        assert "duplicate rows do not reconcile" in result.stderr
        assert not (tmp_path / "backfill-receipt.json").exists()


def _fake_scheduler_cutover_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "cutover-bin"
    fake_bin.mkdir()
    old_paused = tmp_path / "old-paused"
    new_enabled = tmp_path / "new-enabled"
    operation_log = tmp_path / "scheduler-operations.log"
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" scheduler jobs describe "* ]]; then
  if [[ " $* " == *" oura-navi-monitor-refresh-quarter-hour "* ]]; then
    state="ENABLED"
    [[ ! -e "${FAKE_OLD_PAUSED}" ]] || state="PAUSED"
    schedule="*/15 * * * *"
    deadline="30s"
    service_account="${FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT}"
  else
    state="PAUSED"
    [[ ! -e "${FAKE_NEW_ENABLED}" ]] || state="ENABLED"
    schedule="5 */3 * * *"
    deadline="60s"
    service_account="${FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT}"
  fi
  printf '{"state":"%s","schedule":"%s","timeZone":"Asia/Tokyo","attemptDeadline":"%s","retryConfig":{"retryCount":0},"httpTarget":{"uri":"%s","oauthToken":{"serviceAccountEmail":"%s"}}}\n' "${state}" "${schedule}" "${deadline}" "${FAKE_JOB_URI}" "${service_account}"
elif [[ " $* " == *" scheduler jobs pause "* ]]; then
  : > "${FAKE_OLD_PAUSED}"
  printf '%s\n' pause-old >> "${FAKE_OPERATION_LOG}"
elif [[ " $* " == *" scheduler jobs resume "* ]]; then
  : > "${FAKE_NEW_ENABLED}"
  printf '%s\n' resume-new >> "${FAKE_OPERATION_LOG}"
elif [[ " $* " == *" run jobs executions list "* ]]; then
  printf '%s\n' "${FAKE_EXECUTIONS_JSON:-[]}"
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\n' "${FAKE_JOB_JSON}"
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    bq = fake_bin / "bq"
    bq.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " != *" query "* ]]; then exit 2; fi
if [[ " $* " == *"INFORMATION_SCHEMA.JOBS_BY_PROJECT"* ]]; then
  printf '%s\n' '[]'
elif [[ -e "${FAKE_OLD_PAUSED}" && -n "${FAKE_POST_PAUSE_GATE_JSON:-}" ]]; then
  printf '%s\n' "${FAKE_POST_PAUSE_GATE_JSON}"
else
  printf '%s\n' "${FAKE_GATE_JSON}"
fi
""",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    bq.chmod(0o755)
    return old_paused, new_enabled, operation_log


def _scheduler_cutover_environment(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    old_paused, new_enabled, operation_log = _fake_scheduler_cutover_tools(tmp_path)
    fake_modules = _install_refresh_lock_firestore(tmp_path)
    credential = tmp_path / "approved-cutover-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    gate = {
        "source": "published",
        "status": "succeeded",
        "data_through": "2026-08-28T09:00:00Z",
        "freshness_minutes": "30",
        "lease_active": "false",
    }
    job_uri = "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
    expected_image = (
        "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
        + "b" * 64
    )
    return {
        **os.environ,
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "FAKE_GATE_JSON": json.dumps([gate]),
        "FAKE_JOB_URI": job_uri,
        "FAKE_EXPECTED_IMAGE": expected_image,
        "FAKE_JOB_JSON": json.dumps(_refresh_job_json(expected_image)),
        "FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_OLD_PAUSED": str(old_paused),
        "FAKE_NEW_ENABLED": str(new_enabled),
        "FAKE_OPERATION_LOG": str(operation_log),
        "FAKE_REFRESH_LOCK_STATE": str(tmp_path / "refresh-lock.json"),
        "FAKE_REFRESH_LOCK_MUTEX": str(tmp_path / "refresh-lock.mutex"),
        "PYTHONPATH": f"{fake_modules}:{ROOT}",
        "PATH": f"{tmp_path / 'cutover-bin'}:{os.environ['PATH']}",
    }, old_paused, new_enabled, operation_log


def _run_scheduler_cutover_stage(
    tmp_path: Path,
    *,
    env: dict[str, str],
    stage: str,
    post_pause_gate: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = {**env}
    if post_pause_gate is not None:
        run_env["FAKE_POST_PAUSE_GATE_JSON"] = json.dumps([post_pause_gate])
    confirmations = {
        "freeze-old": "projects/test-project/locations/us-central1/jobs/oura-navi-monitor-refresh-quarter-hour:freeze-before-job-deploy",
        "freeze": "projects/test-project/locations/us-central1/jobs/oura-navi-monitor-refresh-quarter-hour:freeze-for-backfill",
        "activate": "projects/test-project/locations/us-central1/jobs/oura-navi-monitor-refresh-three-hour:activate-after-backfill",
    }
    arguments = [
            "bash",
            str(ROOT / "scripts" / "cutover_refresh_scheduler.sh"),
            "--project",
            "test-project",
            "--stage",
            stage,
            "--snapshot-output",
            str(tmp_path / "scheduler-cutover.json"),
            "--expected-job-service-account",
            TEST_JOB_SERVICE_ACCOUNT,
            "--expected-old-scheduler-service-account",
            TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
            "--expected-new-scheduler-service-account",
            TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
            "--credential-file",
            run_env["GOOGLE_APPLICATION_CREDENTIALS"],
            "--confirm-cutover",
            confirmations[stage],
            "--apply",
        ]
    if stage == "activate":
        snapshot_path = tmp_path / "scheduler-cutover.json"
        backfill_receipt = tmp_path / "backfill-receipt.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        backfill_receipt.write_text(
            json.dumps(
                {
                    "project": "test-project",
                    "region": "us-central1",
                    "dataset": "oura_navi_monitor",
                    "location": "US",
                    "source_service": "lcs-rag-app",
                    "expected_job_service_account": TEST_JOB_SERVICE_ACCOUNT,
                    "expected_old_scheduler_service_account": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
                    "expected_new_scheduler_service_account": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
                    "job": "oura-navi-monitor-refresh",
                    "expected_image": run_env["FAKE_EXPECTED_IMAGE"],
                    "freeze_snapshot": snapshot,
                    "validated_job_contract": _validated_refresh_contract(
                        run_env["FAKE_EXPECTED_IMAGE"]
                    ),
                    "validated_execution_provenance": {
                        "name": "execution-1",
                        "image": run_env["FAKE_EXPECTED_IMAGE"],
                        "serviceAccount": TEST_JOB_SERVICE_ACCOUNT,
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
                    "reconciliation": _successful_reconciliation(),
                }
            ),
            encoding="utf-8",
        )
        arguments[arguments.index("--confirm-cutover"):arguments.index("--confirm-cutover")] = [
            "--backfill-receipt",
            str(backfill_receipt),
            "--activation-receipt-output",
            str(tmp_path / "activation-receipt.json"),
        ]
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=run_env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_scheduler_cutover_freezes_backfill_then_activates_new(
    tmp_path: Path,
) -> None:
    env, old_paused, new_enabled, operation_log = _scheduler_cutover_environment(
        tmp_path
    )
    freeze_old = _run_scheduler_cutover_stage(tmp_path, env=env, stage="freeze-old")

    assert freeze_old.returncode == 0, freeze_old.stderr
    assert old_paused.exists()
    assert not new_enabled.exists()
    assert operation_log.read_text(encoding="utf-8").splitlines() == ["pause-old"]
    assert "legacy_scheduler_freeze=complete old=PAUSED" in freeze_old.stdout

    freeze = _run_scheduler_cutover_stage(tmp_path, env=env, stage="freeze")

    assert freeze.returncode == 0, freeze.stderr
    assert "scheduler_freeze=complete old=PAUSED new=PAUSED" in freeze.stdout

    activate = _run_scheduler_cutover_stage(tmp_path, env=env, stage="activate")

    assert activate.returncode == 0, activate.stderr
    assert new_enabled.exists()
    assert operation_log.read_text(encoding="utf-8").splitlines() == [
        "pause-old",
        "resume-new",
    ]
    assert "scheduler_activation=complete old=PAUSED new=ENABLED" in activate.stdout
    assert "canonical_start_at=" in activate.stdout
    assert (tmp_path / "activation-receipt.json").exists()


def test_scheduler_cutover_does_not_resume_new_when_a_racing_lease_exists(
    tmp_path: Path,
) -> None:
    env, old_paused, new_enabled, operation_log = _scheduler_cutover_environment(
        tmp_path
    )
    result = _run_scheduler_cutover_stage(
        tmp_path,
        env=env,
        stage="freeze-old",
        post_pause_gate={
            "source": "published",
            "status": "succeeded",
            "data_through": "2026-08-28T09:00:00Z",
            "freshness_minutes": "30",
            "lease_active": "true",
        },
    )

    assert result.returncode != 0
    assert old_paused.exists()
    assert not new_enabled.exists()
    assert operation_log.read_text(encoding="utf-8").splitlines() == ["pause-old"]
    assert "still owns the pipeline lease" in result.stderr


def test_scheduler_cutover_rejects_a_nonterminal_refresh_execution(
    tmp_path: Path,
) -> None:
    env, old_paused, new_enabled, operation_log = _scheduler_cutover_environment(
        tmp_path
    )
    env["FAKE_EXECUTIONS_JSON"] = json.dumps(
        [
            {
                "name": "oura-navi-monitor-refresh-still-running",
                "status": {
                    "conditions": [
                        {"type": "Completed", "status": "Unknown"}
                    ]
                },
            }
        ]
    )

    result = _run_scheduler_cutover_stage(
        tmp_path,
        env=env,
        stage="freeze-old",
    )

    assert result.returncode != 0
    assert old_paused.exists()
    assert not new_enabled.exists()
    assert operation_log.read_text(encoding="utf-8").splitlines() == ["pause-old"]
    assert "still has non-terminal executions" in result.stderr
    snapshot = json.loads(
        (tmp_path / "scheduler-cutover.json").read_text(encoding="utf-8")
    )
    assert "freeze_verified_at" not in snapshot
    assert "active_bigquery_writers_at_freeze" not in snapshot
    assert "legacy_scheduler_freeze=complete" not in result.stdout


def _canonical_gate_rows() -> list[dict[str, str]]:
    return [
        {
            "run_id": f"run-{index}",
            "execution_id": f"execution-{index}",
            "started_at": f"2026-08-28T{hour:02d}:06:00Z",
            "window_start": f"2026-08-28T{max(hour - 3, 0):02d}:00:00Z",
            "window_end": f"2026-08-28T{hour:02d}:00:00Z",
            "freshness_minutes": "30",
        }
        for index, hour in enumerate((0, 3, 6), start=1)
    ]


def _fake_legacy_pause_tools(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    update_marker = tmp_path / "bq-update-called"
    bq = fake_bin / "bq"
    bq.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" query "* && " $* " == *"pipeline_state"* ]]; then
  printf '%s\\n' "${FAKE_PIPELINE_JSON}"
elif [[ " $* " == *" query "* ]]; then
  printf '%s\\n' "${FAKE_GATE_JSON}"
elif [[ " $* " == *" ls "* && " $* " == *"--transfer_run"* ]]; then
  printf '%s\\n' '[]'
elif [[ " $* " == *" update "* ]]; then
  if [[ "${FAKE_UPDATE_APPLIES:-true}" == "true" ]]; then
    : > "${FAKE_UPDATE_MARKER}"
  fi
  exit "${FAKE_UPDATE_RETURN_CODE:-0}"
elif [[ " $* " == *" show "* && " $* " != *"--transfer_config"* ]]; then
  resource="${@: -1}"
  table="${resource##*.}"
  printf '{"tableReference":{"tableId":"%s"},"lastModifiedTime":"%s","numRows":"10","etag":"table-etag"}\\n' "${table}" "${FAKE_TABLE_LAST_MODIFIED:-1788000000000}"
elif [[ " $* " == *" show "* ]]; then
  if [[ -e "${FAKE_UPDATE_MARKER}" ]]; then
    if [[ "${FAKE_FAIL_POST_UPDATE_SHOW_ONCE:-false}" == "true" && ! -e "${FAKE_POST_UPDATE_FAILURE_MARKER}" ]]; then
      : > "${FAKE_POST_UPDATE_FAILURE_MARKER}"
      echo "synthetic post-update readback interruption" >&2
      exit 77
    fi
    printf '%s\\n' "${FAKE_TRANSFER_AFTER_JSON}"
  else
    printf '%s\\n' "${FAKE_TRANSFER_BEFORE_JSON}"
  fi
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" projects describe "* ]]; then
  printf '%s\\n' '123456'
elif [[ " $* " == *" scheduler jobs describe "* ]]; then
  printf '%s\\n' "${FAKE_SCHEDULER_JSON}"
elif [[ " $* " == *" run jobs executions list "* ]]; then
  printf '%s\\n' "${FAKE_EXECUTIONS_JSON}"
elif [[ " $* " == *" run jobs describe "* ]]; then
  printf '%s\\n' "${FAKE_JOB_JSON}"
elif [[ " $* " == *" logging read "* ]]; then
  if [[ " $* " == *"protoPayload.serviceName"* ]]; then
    printf '%s\\n' "${FAKE_RUN_JOB_AUDITS_JSON}"
  else
    printf '%s\\n' "${FAKE_ATTEMPTS_JSON}"
  fi
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    bq.chmod(0o755)
    gcloud.chmod(0o755)
    return update_marker


def _install_refresh_lock_firestore(tmp_path: Path) -> Path:
    modules = tmp_path / "fake-firestore-modules"
    package = modules / "google" / "cloud"
    package.mkdir(parents=True, exist_ok=True)
    oauth2 = modules / "google" / "oauth2"
    oauth2.mkdir(parents=True, exist_ok=True)
    (oauth2 / "__init__.py").write_text(
        "from . import service_account\n", encoding="utf-8"
    )
    (oauth2 / "service_account.py").write_text(
        "class Credentials:\n"
        "    @staticmethod\n"
        "    def from_service_account_file(*args, **kwargs): return object()\n",
        encoding="utf-8",
    )
    (package / "firestore.py").write_text(
        '''import fcntl
import json
import os
from pathlib import Path

SERVER_TIMESTAMP = "server-time"

class Snapshot:
    def __init__(self, value):
        self.exists = value is not None
        self.value = value
    def to_dict(self):
        return dict(self.value) if self.value is not None else None

class Document:
    def __init__(self, key): self.key = key

class Collection:
    def __init__(self, name): self.name = name
    def document(self, name): return Document(self.name + "/" + name)

class Transaction:
    def __init__(self):
        self.store = None
        self.deleted = False
    def get(self, document): return iter([Snapshot(self.store.get(document.key))])
    def create(self, document, value): self.store[document.key] = dict(value)
    def delete(self, document):
        self.store.pop(document.key, None)
        self.deleted = True

class Client:
    def __init__(self, *, project, database, credentials=None):
        if project != "test-project" or database != "lcs-user-data":
            raise RuntimeError("wrong Firestore scope")
    def collection(self, name): return Collection(name)
    def transaction(self): return Transaction()

def transactional(function):
    def run(transaction):
        state = Path(os.environ["FAKE_REFRESH_LOCK_STATE"])
        mutex = Path(os.environ["FAKE_REFRESH_LOCK_MUTEX"])
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
        '''import importlib.util
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
''',
        encoding="utf-8",
    )
    return modules


def _run_legacy_pause_apply(
    tmp_path: Path,
    *,
    gate: list[dict[str, str]],
    execution_image: str | None = None,
    attempt_status: str = "OK",
    execution_creator: str = TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
    fail_post_update_show_once: bool = False,
    update_applies: bool = True,
    update_return_code: int = 0,
    after_schedule: str | None = None,
    table_last_modified: str = "1788000000000",
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, str]]:
    update_marker = _fake_legacy_pause_tools(tmp_path)
    fake_modules = _install_refresh_lock_firestore(tmp_path)
    credential = tmp_path / "approved-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    transfer = "projects/test-project/locations/us/transferConfigs/example"
    canonical_start = "2026-08-28T00:00:00Z"
    legacy_query = """
CREATE OR REPLACE TABLE `test-project.oura_navi_monitor.monitor_answer_events` AS SELECT 1 AS value;
CREATE OR REPLACE TABLE `test-project.oura_navi_monitor.monitor_user_daily` AS SELECT 1 AS value;
CREATE OR REPLACE TABLE `test-project.oura_navi_monitor.monitor_system_hourly` AS SELECT 1 AS value;
CREATE OR REPLACE TABLE `test-project.oura_navi_monitor.monitor_dashboard_snapshots` AS SELECT 1 AS value;
""".strip()
    query_sha = hashlib.sha256(legacy_query.encode("utf-8")).hexdigest()
    expected_image = (
        "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
        + "c" * 64
    )
    job_uri = "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
    scheduler_readback = {
        "state": "ENABLED",
        "schedule": "5 */3 * * *",
        "timeZone": "Asia/Tokyo",
        "attemptDeadline": "60s",
        "retryConfig": {"retryCount": 0},
        "httpTarget": {
            "uri": job_uri,
            "oauthToken": {
                "serviceAccountEmail": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
            },
        },
    }
    transfer_before = {
        "name": transfer,
        "displayName": "oura_navi_monitor_aggregate_refresh",
        "dataSourceId": "scheduled_query",
        "disabled": False,
        "schedule": "every 15 minutes",
        "destinationDatasetId": "",
        "ownerInfo": {"email": TEST_DTS_SERVICE_ACCOUNT},
        "userId": "opaque-dts-owner-id",
        "params": {
            "query": legacy_query,
            "destination_table_name_template": "monitor_answer_events",
        },
    }
    transfer_after = {**transfer_before, "disabled": True}
    if after_schedule is not None:
        transfer_after["schedule"] = after_schedule
    dependency_receipt = tmp_path / "legacy-dependency-receipt.json"
    dependency_receipt.write_text(
        json.dumps(
            {
                "project": "test-project",
                "dataset": "oura_navi_monitor",
                "location": "US",
                "transferConfig": transfer,
                "querySha256": query_sha,
                "codeReferenceCount": 0,
                "bigQueryObjectReferenceCount": 0,
                "queryJobReferenceCount": 0,
                "nonQueryReadReferenceCount": 0,
                "unknownConsumerCount": 0,
                "dataAccessAuditCoverage": "verified",
                "externalOwnerConfirmation": True,
                "lookbackDays": 90,
                "capturedAt": "2026-08-28T09:30:00Z",
            }
        ),
        encoding="utf-8",
    )
    activation_receipt = tmp_path / "activation-receipt.json"
    activation_receipt.write_text(
        json.dumps(
            {
                "project": "test-project",
                "region": "us-central1",
                "dataset": "oura_navi_monitor",
                "location": "US",
                "source_service": "lcs-rag-app",
                "job": "oura-navi-monitor-refresh",
                "old_scheduler": "oura-navi-monitor-refresh-quarter-hour",
                "new_scheduler": "oura-navi-monitor-refresh-three-hour",
                "expected_job_service_account": TEST_JOB_SERVICE_ACCOUNT,
                "expected_old_scheduler_service_account": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
                "expected_new_scheduler_service_account": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
                "image": expected_image,
                "canonical_start_at": canonical_start,
                "captured_at": "2026-08-28T06:10:00Z",
                "freeze_snapshot_sha256": "d" * 64,
                "backfill_receipt_sha256": "e" * 64,
                "old_scheduler_readback": {"state": "PAUSED"},
                "new_scheduler_readback": scheduler_readback,
                "validated_job_contract": _validated_refresh_contract(
                    expected_image
                ),
            }
        ),
        encoding="utf-8",
    )
    executions = [
        _refresh_execution_json(
            execution_image or expected_image,
            name=(
                "projects/test-project/locations/us-central1/jobs/"
                "oura-navi-monitor-refresh/executions/"
                f"execution-{index}"
            ),
            create_time=f"2026-08-28T{hour:02d}:05:30Z",
            creator=execution_creator,
        )
        for index, hour in enumerate((0, 3, 6), start=1)
    ]
    scheduler_name = "oura-navi-monitor-refresh-three-hour"
    attempts = [
        {
            "insertId": f"attempt-{index}",
            "timestamp": f"2026-08-28T{hour:02d}:05:00Z",
            "severity": "INFO",
            "resource": {
                "type": "cloud_scheduler_job",
                "labels": {
                    "project_id": "test-project",
                    "location_id": "us-central1",
                    "job_id": scheduler_name,
                },
            },
            "jsonPayload": {
                "jobName": (
                    "projects/test-project/locations/us-central1/jobs/"
                    f"{scheduler_name}"
                ),
                "url": job_uri,
                "status": attempt_status,
            },
        }
        for index, hour in enumerate((0, 3, 6), start=1)
    ]
    audits = [
        {
            "protoPayload": {
                "serviceName": "run.googleapis.com",
                "methodName": "google.cloud.run.v2.Jobs.RunJob",
                "authenticationInfo": {
                    "principalEmail": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
                },
                "resourceName": (
                    "projects/test-project/locations/us-central1/jobs/"
                    "oura-navi-monitor-refresh"
                ),
                "response": {
                    "metadata": {
                        "name": (
                            "projects/test-project/locations/us-central1/jobs/"
                            "oura-navi-monitor-refresh/executions/"
                            f"execution-{index}"
                        )
                    }
                },
            }
        }
        for index in range(1, 4)
    ]
    env = {
        **os.environ,
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        "FAKE_GATE_JSON": json.dumps(gate),
        "FAKE_PIPELINE_JSON": json.dumps(
            [
                {
                    "source": "published",
                    "status": "succeeded",
                    "published_run_id": "run-current",
                    "data_through": "2026-08-29T00:00:00Z",
                    "freshness_minutes": "30",
                }
            ]
        ),
        "FAKE_UPDATE_MARKER": str(update_marker),
        "FAKE_POST_UPDATE_FAILURE_MARKER": str(
            tmp_path / "post-update-show-failed-once"
        ),
        "FAKE_FAIL_POST_UPDATE_SHOW_ONCE": str(
            fail_post_update_show_once
        ).lower(),
        "FAKE_UPDATE_APPLIES": str(update_applies).lower(),
        "FAKE_UPDATE_RETURN_CODE": str(update_return_code),
        "FAKE_TRANSFER_BEFORE_JSON": json.dumps(transfer_before),
        "FAKE_TRANSFER_AFTER_JSON": json.dumps(transfer_after),
        "FAKE_EXECUTIONS_JSON": json.dumps(executions),
        "FAKE_ATTEMPTS_JSON": json.dumps(attempts),
        "FAKE_RUN_JOB_AUDITS_JSON": json.dumps(audits),
        "FAKE_JOB_JSON": json.dumps(_refresh_job_json(expected_image)),
        "FAKE_JOB_URI": job_uri,
        "FAKE_SERVICE_ACCOUNT": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_SCHEDULER_JSON": json.dumps(scheduler_readback),
        "FAKE_TABLE_LAST_MODIFIED": table_last_modified,
        "FAKE_REFRESH_LOCK_STATE": str(tmp_path / "refresh-lock.json"),
        "FAKE_REFRESH_LOCK_MUTEX": str(tmp_path / "refresh-lock.mutex"),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "PYTHONPATH": f"{fake_modules}:{ROOT}",
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
    }
    common_arguments = [
        "bash",
        str(ROOT / "scripts" / "pause_legacy_bigquery_refresh.sh"),
        "--project",
        "test-project",
        "--transfer-config",
        transfer,
        "--canonical-start-at",
        canonical_start,
        "--expected-query-sha256",
        query_sha,
        "--expected-dts-service-account",
        TEST_DTS_SERVICE_ACCOUNT,
        "--expected-scheduler-service-account",
        TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "--dependency-receipt",
        str(dependency_receipt),
        "--activation-receipt",
        str(activation_receipt),
        "--credential-file",
        str(credential),
    ]
    preflight_receipt = tmp_path / "legacy-transfer-preflight.json"
    if not preflight_receipt.exists():
        preflight = subprocess.run(
            [
                *common_arguments,
                "--preflight-receipt-output",
                str(preflight_receipt),
                "--preflight",
            ],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if preflight.returncode != 0:
            return preflight, update_marker, env
        if update_marker.exists():
            raise AssertionError("read-only DTS pause preflight performed a mutation")
    result = subprocess.run(
        [
            *common_arguments,
            "--snapshot-output",
            str(tmp_path / "legacy-transfer.json"),
            "--preflight-receipt",
            str(preflight_receipt),
            "--confirm-pause",
            f"{transfer}:pause-after-canonical-3:{canonical_start}",
            "--apply",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, update_marker, env


def test_legacy_dts_pause_requires_three_distinct_spaced_fresh_executions(
    tmp_path: Path,
) -> None:
    result, update_marker, env = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
    )

    assert result.returncode == 0, result.stderr
    assert update_marker.exists()
    assert "canonical_dependency_gate=runs=3 executions=3 windows=3" in result.stdout
    assert "scheduler_proven=3" in result.stdout
    pause_script = (ROOT / "scripts" / "pause_legacy_bigquery_refresh.sh").read_text(
        encoding="utf-8"
    )
    assert "trigger_source = 'scheduler_three_hour'" in pause_script

    snapshot_path = tmp_path / "legacy-transfer.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    preflight_receipt = json.loads(
        (tmp_path / "legacy-transfer-preflight.json").read_text(encoding="utf-8")
    )
    assert preflight_receipt["receipt_type"] == "monitor_legacy_dts_pause_preflight_v1"
    assert preflight_receipt["validated_read_only_gates"][
        "canonical_three_run_provenance"
    ].startswith("runs=3 executions=3 windows=3")
    assert snapshot["preflight_receipt"] == preflight_receipt
    assert len(snapshot["preflight_receipt_sha256"]) == 64
    snapshot["paused_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=60)
    ).isoformat().replace("+00:00", "Z")
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    verify = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "verify_legacy_bigquery_pause.sh"),
            "--project",
            "test-project",
            "--pause-snapshot",
            str(snapshot_path),
            "--receipt-output",
            str(tmp_path / "dts-verification.json"),
            "--min-observation-minutes",
            "45",
            "--credential-file",
            env["GOOGLE_APPLICATION_CREDENTIALS"],
            "--verify",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert verify.returncode == 0, verify.stderr
    assert (tmp_path / "dts-verification.json").exists()
    assert "legacy_dts_pause_verification=complete" in verify.stdout


def test_legacy_dts_pause_recovers_exact_intent_after_disable_readback_interruption(
    tmp_path: Path,
) -> None:
    interrupted, update_marker, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        fail_post_update_show_once=True,
    )

    assert interrupted.returncode != 0
    assert "synthetic post-update readback interruption" in interrupted.stderr
    assert update_marker.exists()
    snapshot_path = tmp_path / "legacy-transfer.json"
    intent = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert intent["receipt_type"] == "monitor_legacy_dts_pause_intent_v1"
    assert intent["state"] == "intent"

    recovered, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        fail_post_update_show_once=True,
    )

    assert recovered.returncode == 0, recovered.stderr
    final_bytes = snapshot_path.read_bytes()
    final = json.loads(final_bytes)
    assert final["receipt_type"] == "monitor_legacy_dts_pause_v2"
    assert final["state"] == "complete"
    assert final["transfer_config_after"]["disabled"] is True
    assert final["update_command_return_code"] == -1

    rejected_stale_proof, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=[],
        execution_image=(
            "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
            + "f" * 64
        ),
        attempt_status="PERMISSION_DENIED",
    )
    assert rejected_stale_proof.returncode != 0
    assert "three successful canonical runs are required" in rejected_stale_proof.stderr
    assert snapshot_path.read_bytes() == final_bytes

    repeated, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
    )
    assert repeated.returncode == 0, repeated.stderr
    assert "exact receipt verified" in repeated.stdout
    assert snapshot_path.read_bytes() == final_bytes

    update_marker.unlink()
    conflicted, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
    )
    assert conflicted.returncode != 0
    assert "completed DTS pause receipt is not exact or live-disabled" in conflicted.stderr
    assert snapshot_path.read_bytes() == final_bytes


def test_completed_dts_receipt_rechecks_live_table_inventory_under_global_lock(
    tmp_path: Path,
) -> None:
    completed, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
    )
    assert completed.returncode == 0, completed.stderr
    snapshot_path = tmp_path / "legacy-transfer.json"
    final_bytes = snapshot_path.read_bytes()

    drifted, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        table_last_modified="1788000000001",
    )

    assert drifted.returncode != 0
    assert "live legacy table inventory drifted" in drifted.stderr
    assert snapshot_path.read_bytes() == final_bytes


def test_legacy_dts_pause_finalizes_when_update_command_failed_after_applying(
    tmp_path: Path,
) -> None:
    result, update_marker, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        update_applies=True,
        update_return_code=23,
    )

    assert result.returncode == 0, result.stderr
    assert update_marker.exists()
    snapshot = json.loads(
        (tmp_path / "legacy-transfer.json").read_text(encoding="utf-8")
    )
    assert snapshot["receipt_type"] == "monitor_legacy_dts_pause_v2"
    assert snapshot["update_command_return_code"] == 23


def test_legacy_dts_pause_recovery_stops_on_transfer_contract_drift(
    tmp_path: Path,
) -> None:
    interrupted, update_marker, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        fail_post_update_show_once=True,
    )
    assert interrupted.returncode != 0
    assert update_marker.exists()

    drifted, _, _ = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        after_schedule="every 30 minutes",
    )

    assert drifted.returncode != 0
    assert "legacy 15-minute schedule" in drifted.stderr
    snapshot = json.loads(
        (tmp_path / "legacy-transfer.json").read_text(encoding="utf-8")
    )
    assert snapshot["receipt_type"] == "monitor_legacy_dts_pause_intent_v1"


def test_legacy_dts_pause_never_mutates_when_dependency_gate_is_short(
    tmp_path: Path,
) -> None:
    result, update_marker, _env = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows()[:2],
    )

    assert result.returncode != 0
    assert not update_marker.exists()
    assert "three successful canonical runs are required" in result.stderr


def test_legacy_dts_pause_rejects_scheduler_execution_from_another_digest(
    tmp_path: Path,
) -> None:
    result, update_marker, _env = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        execution_image=(
            "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
            + "f" * 64
        ),
    )

    assert result.returncode != 0
    assert not update_marker.exists()
    assert "execution image does not match" in result.stderr


def test_legacy_dts_pause_rejects_failed_scheduler_attempts(tmp_path: Path) -> None:
    result, update_marker, _env = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        attempt_status="PERMISSION_DENIED",
    )

    assert result.returncode != 0
    assert not update_marker.exists()
    assert "three unique successful exact Scheduler attempts" in result.stderr


def test_legacy_dts_pause_rejects_nearby_manual_execution(tmp_path: Path) -> None:
    result, update_marker, _env = _run_legacy_pause_apply(
        tmp_path,
        gate=_canonical_gate_rows(),
        # Attempts are deliberately at the same nearby timestamps. Exact
        # Execution.creator must still reject a manual operator execution.
        execution_creator="manual-operator@example.com",
    )

    assert result.returncode != 0
    assert not update_marker.exists()
    assert "creator is not the Scheduler OAuth identity" in result.stderr
