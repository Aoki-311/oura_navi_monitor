from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.verify_github_trigger_contract import verify_trigger_contract


ROOT_DIR = Path(__file__).resolve().parents[1]

INCLUDED_FILES = (
    "app/**,frontend/**,deploy/**,scripts/**,sql/**,tests/**,e2e/**,Dockerfile,"
    "requirements.txt,requirements-dev.txt,cloudbuild.yaml,.env.example"
)
IGNORED_FILES = "**/.venv/**,**/__pycache__/**,**/*.pyc,**/.DS_Store,docs/**,**/*.md"


def _valid_trigger() -> dict[str, object]:
    return {
        "name": "oura-navi-monitor",
        "description": "CI creates a no-traffic OurA Navi Monitor candidate",
        "filename": "cloudbuild.yaml",
        "serviceAccount": (
            "projects/example-project/serviceAccounts/"
            "builder@example-project.iam.gserviceaccount.com"
        ),
        "github": {
            "owner": "Aoki-311",
            "name": "oura_navi_monitor",
            "push": {"branch": "^main$"},
        },
        "approvalConfig": {"approvalRequired": True},
        "includeBuildLogs": "INCLUDE_BUILD_LOGS_WITH_STATUS",
        "includedFiles": INCLUDED_FILES.split(","),
        "ignoredFiles": IGNORED_FILES.split(","),
        "substitutions": {
            "_IAP_AUDIENCE": "/projects/123/global/backendServices/456"
        },
    }


def _verify(payload: dict[str, object]) -> None:
    verify_trigger_contract(
        payload,
        project_id="example-project",
        trigger_name="oura-navi-monitor",
        repo_owner="Aoki-311",
        repo_name="oura_navi_monitor",
        branch_pattern="^main$",
        service_account="builder@example-project.iam.gserviceaccount.com",
        iap_audience="/projects/123/global/backendServices/456",
        included_files=INCLUDED_FILES,
        ignored_files=IGNORED_FILES,
    )


def test_cloudbuild_has_no_fake_iap_audience_default() -> None:
    text = (ROOT_DIR / "cloudbuild.yaml").read_text(encoding="utf-8")
    assert '--iap-audience "${_IAP_AUDIENCE}"' in text
    substitutions = text.split("substitutions:\n", 1)[1].split("\ntimeout:", 1)[0]
    assert "_IAP_AUDIENCE" not in substitutions
    assert "REQUIRED" not in text


def test_trigger_contract_accepts_the_complete_expected_configuration() -> None:
    _verify(_valid_trigger())


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("substitutions", "_IAP_AUDIENCE"), "REQUIRED", "_IAP_AUDIENCE"),
        (("approvalConfig", "approvalRequired"), False, "manual approval"),
        (("github", "push", "branch"), ".*", "branch pattern"),
        (("filename",), "other.yaml", "build config"),
    ],
)
def test_trigger_contract_rejects_release_critical_drift(
    path: tuple[str, ...], value: object, message: str
) -> None:
    payload = _valid_trigger()
    cursor: dict[str, object] = payload
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[assignment]
    cursor[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        _verify(payload)


def test_trigger_plan_rejects_placeholder_without_calling_gcloud(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_gcloud.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "scripts" / "create_github_trigger.sh"),
            "--project",
            "example-project",
            "--service-account",
            "builder@example-project.iam.gserviceaccount.com",
            "--iap-audience",
            "REQUIRED",
        ],
        cwd=ROOT_DIR,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "exact IAP signed-header audience" in result.stderr


def test_trigger_plan_is_explicitly_unverified() -> None:
    result = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "scripts" / "create_github_trigger.sh"),
            "--project",
            "example-project",
            "--service-account",
            "builder@example-project.iam.gserviceaccount.com",
            "--iap-audience",
            "/projects/123/global/backendServices/456",
        ],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=plan" in result.stdout
    assert "trigger_contract_verified=false" in result.stdout
    assert "next_push_build_ready=false" in result.stdout


def test_trigger_verify_reads_back_the_deployed_contract(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trigger_json = tmp_path / "trigger.json"
    trigger_json.write_text(json.dumps(_valid_trigger()), encoding="utf-8")
    credential = tmp_path / "credential.json"
    credential.write_text("{}", encoding="utf-8")
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" builds triggers list "* ]]; then
  echo trigger-id
elif [[ " $* " == *" builds triggers describe "* ]]; then
  cat "${FAKE_TRIGGER_JSON}"
else
  exit 99
fi
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_TRIGGER_JSON": str(trigger_json),
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(credential),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "scripts" / "create_github_trigger.sh"),
            "--project",
            "example-project",
            "--service-account",
            "builder@example-project.iam.gserviceaccount.com",
            "--iap-audience",
            "/projects/123/global/backendServices/456",
            "--verify",
        ],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mode=verify" in result.stdout
    assert "trigger_contract_verified=true" in result.stdout
    assert "next_push_build_ready=true" in result.stdout
