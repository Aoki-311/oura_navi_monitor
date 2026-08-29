from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _successful_reconciliation() -> list[dict[str, str]]:
    return [
        {
            "successful_run_count": "1",
            "input_row_count": "3",
            "merged_row_count": "3",
            "duplicate_row_count": "0",
            "quarantined_manifest_count": "0",
            "deduplicated_manifest_count": "0",
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
    assert settings.monitor_refresh_cadence_minutes == 180
    assert settings.monitor_refresh_delay_minutes == 5
    assert settings.monitor_event_future_tolerance_minutes == 10
    assert settings.monitor_refresh_overlap_minutes == 240
    assert settings.monitor_refresh_max_window_hours == 24
    assert settings.monitor_data_freshness_minutes == 240
    assert settings.monitor_refresh_lease_ttl_minutes == 45


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


def test_recent_backfill_runs_only_the_expected_frozen_job(tmp_path: Path) -> None:
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
  printf '%s\n' '{"name":"fake-backfill-execution","succeededCount":1}'
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
        "FAKE_OLD_SCHEDULER_SERVICE_ACCOUNT": TEST_OLD_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_NEW_SCHEDULER_SERVICE_ACCOUNT": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_RECONCILIATION_JSON": json.dumps(_successful_reconciliation()),
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

    assert result.returncode == 0, result.stderr
    assert execution_marker.exists()
    assert (tmp_path / "backfill-receipt.json").exists()
    assert "backfill=complete" in result.stdout


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
    credential = tmp_path / "approved-cutover-credential.json"
    credential.write_text("{}", encoding="utf-8")
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
    fake_bin.mkdir()
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
  : > "${FAKE_UPDATE_MARKER}"
elif [[ " $* " == *" show "* && " $* " != *"--transfer_config"* ]]; then
  resource="${@: -1}"
  table="${resource##*.}"
  printf '{"tableReference":{"tableId":"%s"},"lastModifiedTime":"1788000000000","numRows":"10","etag":"table-etag"}\\n' "${table}"
elif [[ " $* " == *" show "* ]]; then
  if [[ -e "${FAKE_UPDATE_MARKER}" ]]; then
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
  printf '%s\\n' "${FAKE_ATTEMPTS_JSON}"
else
  exit 2
fi
""",
        encoding="utf-8",
    )
    bq.chmod(0o755)
    gcloud.chmod(0o755)
    return update_marker


def _run_legacy_pause_apply(
    tmp_path: Path,
    *,
    gate: list[dict[str, str]],
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, str]]:
    update_marker = _fake_legacy_pause_tools(tmp_path)
    credential = tmp_path / "approved-credential.json"
    credential.write_text("{}", encoding="utf-8")
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
        {
            "name": (
                "projects/test-project/locations/us-central1/jobs/"
                "oura-navi-monitor-refresh/executions/"
                f"execution-{index}"
            ),
            "createTime": f"2026-08-28T{hour:02d}:05:30Z",
        }
        for index, hour in enumerate((0, 3, 6), start=1)
    ]
    scheduler_name = "oura-navi-monitor-refresh-three-hour"
    attempts = [
        {
            "timestamp": f"2026-08-28T{hour:02d}:05:00Z",
            "jsonPayload": {
                "jobName": (
                    "projects/test-project/locations/us-central1/jobs/"
                    f"{scheduler_name}"
                ),
                "url": job_uri,
            },
        }
        for hour in (0, 3, 6)
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
        "FAKE_TRANSFER_BEFORE_JSON": json.dumps(transfer_before),
        "FAKE_TRANSFER_AFTER_JSON": json.dumps(transfer_after),
        "FAKE_EXECUTIONS_JSON": json.dumps(executions),
        "FAKE_ATTEMPTS_JSON": json.dumps(attempts),
        "FAKE_JOB_JSON": json.dumps(_refresh_job_json(expected_image)),
        "FAKE_JOB_URI": job_uri,
        "FAKE_SERVICE_ACCOUNT": TEST_NEW_SCHEDULER_SERVICE_ACCOUNT,
        "FAKE_SCHEDULER_JSON": json.dumps(scheduler_readback),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential),
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "pause_legacy_bigquery_refresh.sh"),
            "--project",
            "test-project",
            "--transfer-config",
            transfer,
            "--snapshot-output",
            str(tmp_path / "legacy-transfer.json"),
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
