from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT = "lcs-developer-483404"
PROJECT_NUMBER = "643644246736"
REGION = "us-central1"
SERVICE = "oura-navi-monitor"
REVISION = "oura-navi-monitor-abcdef0-12345678"
IMAGE = "us-central1-docker.pkg.dev/project/repository/image@sha256:" + "a" * 64
SERVICE_ACCOUNT = "runtime@project.iam.gserviceaccount.com"
GIT_SHA = "b" * 40


def _service(*, candidate: bool, converged: bool = True) -> dict[str, Any]:
    generation = 8 if candidate else 7
    live = "oura-navi-monitor-live"
    desired = [{"revisionName": live, "percent": 100}]
    observed = [{"revisionName": live, "percent": 100}]
    if candidate:
        desired.append(
            {"revisionName": REVISION, "tag": "candidate", "percent": 0}
        )
        observed.append(
            {
                "revisionName": REVISION,
                "tag": "candidate",
                "percent": 0,
                "url": "https://candidate---oura-navi-monitor.example.run.app",
            }
        )
    return {
        "metadata": {
            "name": SERVICE,
            "namespace": PROJECT_NUMBER,
            "generation": str(generation),
            "labels": {"cloud.googleapis.com/location": REGION},
            "annotations": {
                "run.googleapis.com/ingress": "internal-and-cloud-load-balancing"
            },
        },
        "spec": {"traffic": desired, "defaultUriDisabled": False},
        "status": {
            "observedGeneration": generation if converged else generation - 1,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if converged else "Unknown",
                }
            ],
            "latestReadyRevisionName": REVISION if candidate else live,
            "traffic": observed,
        },
    }


def _revision(*, image: str = IMAGE) -> dict[str, Any]:
    return {
        "metadata": {
            "name": REVISION,
            "namespace": PROJECT_NUMBER,
            "generation": "1",
            "labels": {
                "cloud.googleapis.com/location": REGION,
                "git-sha": GIT_SHA,
            },
        },
        "spec": {
            "containers": [{"image": image}],
            "serviceAccountName": SERVICE_ACCOUNT,
        },
        "status": {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fake_gcloud(tmp_path: Path) -> tuple[Path, Path]:
    counter = tmp_path / "service-describe-count.txt"
    executable = tmp_path / "gcloud"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
if args[:3] == ["run", "services", "describe"]:
    counter = pathlib.Path(os.environ["FAKE_SERVICE_COUNTER"])
    count = int(counter.read_text() or "0") if counter.exists() else 0
    counter.write_text(str(count + 1))
    if count == 0 and os.environ.get("FAKE_SERVICE_ERROR_FIRST") == "1":
        sys.stderr.write("temporary service readback failure")
        raise SystemExit(1)
    key = "FAKE_STALE_SERVICE" if count == 0 and os.environ.get("FAKE_STALE_FIRST") == "1" else "FAKE_CANDIDATE_SERVICE"
elif args[:3] == ["run", "revisions", "describe"]:
    key = "FAKE_CANDIDATE_REVISION"
elif args[:3] == ["run", "services", "get-iam-policy"]:
    key = "FAKE_CANDIDATE_IAM"
else:
    raise SystemExit("unexpected fake gcloud command: " + repr(args))
sys.stdout.write(pathlib.Path(os.environ[key]).read_text())
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, counter


def _run_cli(
    tmp_path: Path,
    *,
    stale_first: bool,
    service_error_first: bool = False,
    revision_image: str = IMAGE,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    before_service = tmp_path / "before-service.json"
    stale_service = tmp_path / "stale-service.json"
    candidate_service = tmp_path / "candidate-service-source.json"
    candidate_revision = tmp_path / "candidate-revision-source.json"
    before_iam = tmp_path / "before-iam.json"
    candidate_iam = tmp_path / "candidate-iam-source.json"
    service_output = tmp_path / "service-output.json"
    revision_output = tmp_path / "revision-output.json"
    iam_output = tmp_path / "iam-output.json"
    iam = {
        "version": 1,
        "bindings": [
            {
                "role": "roles/run.invoker",
                "members": ["serviceAccount:caller@example.com"],
            }
        ],
    }
    for path, payload in (
        (before_service, _service(candidate=False)),
        (stale_service, _service(candidate=True, converged=False)),
        (candidate_service, _service(candidate=True)),
        (candidate_revision, _revision(image=revision_image)),
        (before_iam, iam),
        (candidate_iam, iam),
    ):
        _write_json(path, payload)

    _, counter = _fake_gcloud(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(tmp_path) + os.pathsep + env["PATH"],
            "FAKE_SERVICE_COUNTER": str(counter),
            "FAKE_STALE_FIRST": "1" if stale_first else "0",
            "FAKE_SERVICE_ERROR_FIRST": "1" if service_error_first else "0",
            "FAKE_STALE_SERVICE": str(stale_service),
            "FAKE_CANDIDATE_SERVICE": str(candidate_service),
            "FAKE_CANDIDATE_REVISION": str(candidate_revision),
            "FAKE_CANDIDATE_IAM": str(candidate_iam),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_candidate_readback.py",
            "--before-service",
            str(before_service),
            "--before-iam",
            str(before_iam),
            "--service-output",
            str(service_output),
            "--revision-output",
            str(revision_output),
            "--iam-output",
            str(iam_output),
            "--expected-project-id",
            PROJECT,
            "--expected-project-number",
            PROJECT_NUMBER,
            "--expected-region",
            REGION,
            "--expected-service",
            SERVICE,
            "--expected-revision",
            REVISION,
            "--expected-image",
            IMAGE,
            "--expected-service-account",
            SERVICE_ACCOUNT,
            "--expected-git-sha",
            GIT_SHA,
            "--candidate-tag",
            "candidate",
            "--max-attempts",
            "3",
            "--poll-seconds",
            "0",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, counter


def test_candidate_readback_cli_retries_only_until_resources_converge(
    tmp_path: Path,
) -> None:
    result, counter = _run_cli(tmp_path, stale_first=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["candidateRevision"] == REVISION
    assert counter.read_text(encoding="utf-8") == "2"
    assert json.loads((tmp_path / "service-output.json").read_text())["status"][
        "observedGeneration"
    ] == 8
    assert (tmp_path / "revision-output.json").is_file()
    assert (tmp_path / "iam-output.json").is_file()


def test_candidate_readback_cli_retries_a_transient_service_read_error(
    tmp_path: Path,
) -> None:
    result, counter = _run_cli(
        tmp_path,
        stale_first=False,
        service_error_first=True,
    )

    assert result.returncode == 0, result.stderr
    assert counter.read_text(encoding="utf-8") == "2"


def test_candidate_readback_cli_does_not_retry_permanent_contract_drift(
    tmp_path: Path,
) -> None:
    wrong_image = IMAGE.replace("a" * 64, "c" * 64)
    result, counter = _run_cli(
        tmp_path,
        stale_first=False,
        revision_image=wrong_image,
    )

    assert result.returncode != 0
    assert "image digest readback failed" in result.stderr
    assert counter.read_text(encoding="utf-8") == "1"
