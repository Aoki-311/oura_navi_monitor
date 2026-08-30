import re
import subprocess
from pathlib import Path

import pytest

from scripts.render_runtime_env import render_refresh_env


def test_release_configuration_removes_the_legacy_identity_secret_binding() -> None:
    root = Path(__file__).resolve().parents[1]
    cloudbuild = (root / "cloudbuild.yaml").read_text(encoding="utf-8")
    runtime_files = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in ("deploy/cloudrun.env.yaml", "scripts/bootstrap_gcp.sh")
    )
    assert "MONITOR_IDENTITY_HMAC_KEY" not in runtime_files
    assert "oura-navi-monitor-identity-hmac" not in cloudbuild
    assert "--set-secrets" not in cloudbuild
    assert "--remove-secrets=MONITOR_IDENTITY_HMAC_KEY" in cloudbuild
    policy = (root / "app" / "refresh_policy.py").read_text(encoding="utf-8")
    assert 'scheduler_cron: str = "5 */3 * * *"' in policy
    assert "max_window_hours: int = 24" in policy
    assert "MONITOR_REFRESH_MAX_WINDOW_HOURS" not in runtime_files


def test_cloud_build_gates_the_candidate_with_browser_contract_and_immutable_digest() -> None:
    root = Path(__file__).resolve().parents[1]
    cloudbuild = (root / "cloudbuild.yaml").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    gcloudignore = (root / ".gcloudignore").read_text(encoding="utf-8")

    assert "candidate-browser-contract" in cloudbuild
    assert "mcr.microsoft.com/playwright:v1.58.2-noble" in cloudbuild
    assert "npm ci --no-audit --no-fund && npm test" in cloudbuild
    assert "scripts/extract_docker_push_digest.sh" in cloudbuild
    assert "image_summary.digest" not in cloudbuild
    assert "gcloud artifacts docker images describe" not in cloudbuild
    assert "@$$\u007bdigest}" in cloudbuild
    assert "--no-traffic" in cloudbuild
    assert "--no-allow-unauthenticated" not in cloudbuild
    assert "scripts/verify_candidate_readback.py" in cloudbuild
    assert "scripts/verify_service_access_contract.py" not in cloudbuild
    assert "--revision-suffix" in cloudbuild
    assert "monitor-candidate-revision.txt" in cloudbuild
    assert "monitor-predeploy-runtime-service-account.txt" in cloudbuild
    assert "current Monitor runtime service account does not match" in cloudbuild
    assert "scripts/verify_candidate_service.py" not in cloudbuild
    assert "--candidate-tag candidate" in cloudbuild
    assert '--expected-project-id "${PROJECT_ID}"' in cloudbuild
    assert '--expected-project-number "${PROJECT_NUMBER}"' in cloudbuild
    assert '--expected-region "${_REGION}"' in cloudbuild
    assert "--max-attempts 30" in cloudbuild
    assert "--poll-seconds 2" in cloudbuild
    assert 'build_id="${BUILD_ID}"' in cloudbuild
    assert cloudbuild.count('--project "${PROJECT_ID}"') == cloudbuild.count(
        "gcloud run"
    )
    assert cloudbuild.count('--region "${_REGION}"') == cloudbuild.count("gcloud run")
    assert "latestCreatedRevisionName" not in cloudbuild
    assert dockerignore.startswith("*\n")
    for allowed in ("!Dockerfile", "!requirements.txt", "!app/**", "!frontend/**", "!sql/**"):
        assert allowed in dockerignore
    for build_only in ("!cloudbuild.yaml", "!requirements-dev.txt", "!deploy/**", "!scripts/**", "!tests/**", "!e2e/**"):
        assert build_only not in dockerignore
    assert gcloudignore.startswith("*\n")
    for required_upload in (
        "!cloudbuild.yaml",
        "!requirements-dev.txt",
        "!deploy/**",
        "!scripts/**",
        "!tests/**",
        "!e2e/**",
        "!.dockerignore",
    ):
        assert required_upload in gcloudignore
    assert "**/credentials/**" in gcloudignore
    assert "**/*service-account*.json" in gcloudignore
    assert "e2e/node_modules/**" in gcloudignore
    credential_wrapper = root / "scripts" / "credential_shell.sh"
    readback_owner = (root / "scripts" / "verify_candidate_readback.py").read_text(
        encoding="utf-8"
    )
    assert credential_wrapper.is_file()
    assert "!scripts/**" in gcloudignore
    assert not credential_wrapper.name.endswith(".json")
    assert re.search(r'"services",\s*"describe"', readback_owner)
    assert re.search(r'"revisions",\s*"describe"', readback_owner)
    assert '"get-iam-policy"' in readback_owner
    assert "verify_access(" in readback_owner
    assert "verify_candidate(" in readback_owner
    assert "ReconciliationPending" in readback_owner


def test_docker_push_receipt_extracts_one_exact_commit_digest(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "extract_docker_push_digest.sh"
    commit_sha = "a" * 40
    digest = "sha256:" + "b" * 64
    push_log = tmp_path / "push.log"
    push_log.write_text(
        f"layer: pushed\n{commit_sha}: digest: {digest} size: 2414\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script), commit_sha, str(push_log)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == digest


def test_docker_push_receipt_rejects_missing_or_duplicate_digest(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "extract_docker_push_digest.sh"
    commit_sha = "a" * 40
    digest = "sha256:" + "b" * 64
    push_log = tmp_path / "push.log"

    for text in (
        "push completed without a digest\n",
        f"{commit_sha}: digest: {digest} size: 1\n"
        f"{commit_sha}: digest: {digest} size: 1\n",
    ):
        push_log.write_text(text, encoding="utf-8")
        result = subprocess.run(
            ["bash", str(script), commit_sha, str(push_log)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2


def test_trigger_preserves_the_existing_monitor_runtime_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    cloudbuild = (root / "cloudbuild.yaml").read_text(encoding="utf-8")
    trigger = (root / "scripts" / "create_github_trigger.sh").read_text(
        encoding="utf-8"
    )

    assert 'BUILD_SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT##*/}"' in trigger
    assert '--service-account="${SERVICE_ACCOUNT}"' in trigger
    assert 'BRANCH_PATTERN="^main$"' in trigger
    assert '--build-config="cloudbuild.yaml"' in trigger
    assert "--require-approval" in trigger
    assert "cloudbuild.app.yaml" not in trigger
    assert "_WEB_RUNTIME_SERVICE_ACCOUNT" not in trigger
    assert "required-web-reader@invalid.invalid" not in cloudbuild
    assert (
        "_RUNTIME_SERVICE_ACCOUNT: "
        "lcs-agent@lcs-developer-483404.iam.gserviceaccount.com"
    ) in cloudbuild


def test_refresh_activation_separates_writer_from_scheduler_invoker() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "bootstrap_gcp.sh").read_text(encoding="utf-8")

    assert "--web-runtime-service-account" not in bootstrap
    assert "refresh writer and scheduler invoker identities must be distinct" in bootstrap


def test_refresh_env_replaces_the_single_analytics_start_owner(tmp_path: Path) -> None:
    source = tmp_path / "base.yaml"
    output = tmp_path / "refresh.yaml"
    source.write_text(
        'MONITOR_PROJECT_ID: "project"\n'
        'MONITOR_BQ_DATASET: "dataset"\n'
        'MONITOR_BQ_LOCATION: "US"\n'
        'MONITOR_SOURCE_SERVICE: "service"\n'
        'MONITOR_ANALYTICS_START_AT: ""\n',
        encoding="utf-8",
    )

    render_refresh_env(
        source=source,
        output=output,
        analytics_start_at="2026-08-24T00:00:00Z",
        project_id="runtime-project",
        dataset_id="runtime_dataset",
        location="EU",
        source_service="runtime-service",
    )

    text = output.read_text(encoding="utf-8")
    assert text.count("MONITOR_ANALYTICS_START_AT:") == 1
    assert 'MONITOR_ANALYTICS_START_AT: "2026-08-24T00:00:00Z"' in text
    assert 'MONITOR_PROJECT_ID: "runtime-project"' in text


@pytest.mark.parametrize("value", ["", "2026-08-24", "2026-08-24T00:00:00+00:00"])
def test_refresh_env_rejects_an_unfrozen_or_inexact_start(
    tmp_path: Path,
    value: str,
) -> None:
    source = tmp_path / "base.yaml"
    source.write_text(
        'MONITOR_PROJECT_ID: "project"\n'
        'MONITOR_BQ_DATASET: "dataset"\n'
        'MONITOR_BQ_LOCATION: "US"\n'
        'MONITOR_SOURCE_SERVICE: "service"\n'
        'MONITOR_ANALYTICS_START_AT: ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exact UTC analytics start"):
        render_refresh_env(
            source=source,
            output=tmp_path / "refresh.yaml",
            analytics_start_at=value,
            project_id="project",
            dataset_id="dataset",
            location="US",
            source_service="service",
        )
