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
    assert "image_summary.digest" in cloudbuild
    assert "@$$\u007bdigest}" in cloudbuild
    assert "--no-traffic" in cloudbuild
    assert "--revision-suffix" in cloudbuild
    assert dockerignore.startswith("*\n")
    for allowed in ("!Dockerfile", "!requirements.txt", "!app/**", "!frontend/**", "!sql/**"):
        assert allowed in dockerignore
    assert "**/credentials/**" in gcloudignore
    assert "**/*service-account*.json" in gcloudignore


def test_trigger_preserves_the_existing_monitor_runtime_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    cloudbuild = (root / "cloudbuild.yaml").read_text(encoding="utf-8")
    trigger = (root / "scripts" / "create_github_trigger.sh").read_text(
        encoding="utf-8"
    )

    assert 'BUILD_SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT##*/}"' in trigger
    assert '--service-account="${SERVICE_ACCOUNT}"' in trigger
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
