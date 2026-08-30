from __future__ import annotations

import copy

import pytest

from scripts.scheduler_execution_provenance import validate_provenance


PROJECT = "test-project"
REGION = "us-central1"
JOB = "oura-navi-monitor-refresh"
SCHEDULER = "oura-navi-monitor-refresh-three-hour"
SCHEDULER_SA = "scheduler@test-project.iam.gserviceaccount.com"
JOB_SA = "writer@test-project.iam.gserviceaccount.com"
IMAGE = "us-central1-docker.pkg.dev/test-project/repo/monitor@sha256:" + "a" * 64
URI = (
    "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/"
    "namespaces/test-project/jobs/oura-navi-monitor-refresh:run"
)


def _inputs() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    runs = [{"execution_id": f"execution-{index}"} for index in range(1, 4)]
    executions = []
    audits = []
    for index in range(1, 4):
        name = (
            f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}/executions/"
            f"execution-{index}"
        )
        executions.append(
            {
                "name": name,
                "creator": SCHEDULER_SA,
                "template": {
                    "taskCount": 1,
                    "template": {
                        "serviceAccount": JOB_SA,
                        "containers": [{"image": IMAGE}],
                    },
                },
                "status": {
                    "succeededCount": 1,
                    "failedCount": 0,
                    "conditions": [{"type": "Completed", "status": "True"}],
                },
            }
        )
        audits.append(
            {
                "protoPayload": {
                    "serviceName": "run.googleapis.com",
                    "methodName": "google.cloud.run.v2.Jobs.RunJob",
                    "authenticationInfo": {"principalEmail": SCHEDULER_SA},
                    "resourceName": f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}",
                    "response": {"metadata": {"name": name}},
                },
                "insertId": f"audit-{index}",
                "timestamp": f"2026-08-30T0{index}:05:00Z",
            }
        )
    attempts = [
        {
            "insertId": f"attempt-{index}",
            "timestamp": f"2026-08-30T0{index}:05:00Z",
            "severity": "INFO",
            "resource": {
                "type": "cloud_scheduler_job",
                "labels": {
                    "project_id": PROJECT,
                    "location_id": REGION,
                    "job_id": SCHEDULER,
                },
            },
            "jsonPayload": {
                "jobName": f"projects/{PROJECT}/locations/{REGION}/jobs/{SCHEDULER}",
                "url": URI,
                "status": "OK",
            },
        }
        for index in range(1, 4)
    ]
    return runs, executions, attempts, audits


def _validate(values: tuple[list[dict], list[dict], list[dict], list[dict]]) -> int:
    runs, executions, attempts, audits = values
    return validate_provenance(
        runs=runs,
        executions=executions,
        attempts=attempts,
        audits=audits,
        project=PROJECT,
        region=REGION,
        job=JOB,
        scheduler=SCHEDULER,
        scheduler_service_account=SCHEDULER_SA,
        expected_job_uri=URI,
        expected_image=IMAGE,
        expected_job_service_account=JOB_SA,
    )


def test_exact_lro_execution_identity_and_creator_are_required() -> None:
    values = _inputs()
    assert _validate(values) == 3

    without_identity = copy.deepcopy(values)
    audit = without_identity[3][0]
    audit["protoPayload"].pop("response")
    audit["operation"] = {"id": "execution-1"}
    with pytest.raises(ValueError, match="exactly one consistent"):
        _validate(without_identity)


def test_run_job_audit_principal_must_equal_execution_creator() -> None:
    values = _inputs()
    values[3][0]["protoPayload"]["authenticationInfo"]["principalEmail"] = (
        "manual@example.com"
    )
    with pytest.raises(ValueError, match="no unique exact Scheduler RunJob audit"):
        _validate(values)


def test_one_conflicting_audit_cannot_be_reused_for_multiple_executions() -> None:
    values = _inputs()
    values[3][0]["protoPayload"]["response"]["response"] = {
        "name": (
            f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}/executions/"
            "execution-2"
        )
    }

    with pytest.raises(ValueError, match="exactly one consistent"):
        _validate(values)


def test_duplicate_scheduler_attempt_log_does_not_count_as_three_attempts() -> None:
    runs, executions, attempts, audits = _inputs()
    values = (runs, executions, [copy.deepcopy(attempts[0]) for _ in range(3)], audits)

    with pytest.raises(ValueError, match="three unique"):
        _validate(values)


def test_scheduler_attempt_requires_explicit_success_status() -> None:
    values = _inputs()
    values[2][0]["jsonPayload"].pop("status")

    with pytest.raises(ValueError, match="three unique"):
        _validate(values)


def test_run_job_audit_requires_exact_service_and_success() -> None:
    wrong_service = _inputs()
    wrong_service[3][0]["protoPayload"]["serviceName"] = "another.googleapis.com"
    with pytest.raises(ValueError, match="no unique exact"):
        _validate(wrong_service)

    wrong_method = _inputs()
    wrong_method[3][0]["protoPayload"]["methodName"] = "evil.Jobs.RunJob"
    with pytest.raises(ValueError, match="no unique exact"):
        _validate(wrong_method)

    failed = _inputs()
    failed[3][0]["protoPayload"]["status"] = {"code": 7}
    with pytest.raises(ValueError, match="no unique exact"):
        _validate(failed)
