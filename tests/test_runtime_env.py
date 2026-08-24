from pathlib import Path

import pytest

from scripts.render_runtime_env import (
    render_refresh_env,
    render_runtime_env,
    validate_iap_audience,
)


def test_runtime_env_has_one_exact_iap_audience_owner(tmp_path: Path) -> None:
    source = tmp_path / "base.yaml"
    output = tmp_path / "runtime.yaml"
    source.write_text('MONITOR_PROJECT_ID: "project"\n', encoding="utf-8")

    render_runtime_env(
        source=source,
        output=output,
        iap_audience="/projects/123/global/backendServices/456",
    )

    text = output.read_text(encoding="utf-8")
    assert text.count("MONITOR_IAP_AUDIENCE:") == 1
    assert 'MONITOR_IAP_AUDIENCE: "/projects/123/global/backendServices/456"' in text


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "REQUIRED",
        "MUST_BE_CONFIGURED",
        "audience.example",
        "/projects/project-id/global/backendServices/456",
        "/projects/123/regions/us-central1/backendServices/456",
        "/projects/123/global/backendServices/backend-name",
    ],
)
def test_runtime_env_rejects_missing_iap_audience(tmp_path: Path, value: str) -> None:
    source = tmp_path / "base.yaml"
    source.write_text('MONITOR_PROJECT_ID: "project"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="IAP signed-header audience"):
        render_runtime_env(
            source=source,
            output=tmp_path / "runtime.yaml",
            iap_audience=value,
        )


def test_iap_audience_validator_returns_the_exact_normalized_value() -> None:
    assert (
        validate_iap_audience(" /projects/123/global/backendServices/456 ")
        == "/projects/123/global/backendServices/456"
    )


def test_refresh_env_replaces_the_single_analytics_start_owner(tmp_path: Path) -> None:
    source = tmp_path / "base.yaml"
    output = tmp_path / "refresh.yaml"
    source.write_text(
        'MONITOR_PROJECT_ID: "project"\nMONITOR_ANALYTICS_START_AT: ""\n',
        encoding="utf-8",
    )

    render_refresh_env(
        source=source,
        output=output,
        analytics_start_at="2026-08-24T00:00:00Z",
    )

    text = output.read_text(encoding="utf-8")
    assert text.count("MONITOR_ANALYTICS_START_AT:") == 1
    assert 'MONITOR_ANALYTICS_START_AT: "2026-08-24T00:00:00Z"' in text


@pytest.mark.parametrize("value", ["", "2026-08-24", "2026-08-24T00:00:00+00:00"])
def test_refresh_env_rejects_an_unfrozen_or_inexact_start(
    tmp_path: Path,
    value: str,
) -> None:
    source = tmp_path / "base.yaml"
    source.write_text('MONITOR_ANALYTICS_START_AT: ""\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exact UTC analytics start"):
        render_refresh_env(
            source=source,
            output=tmp_path / "refresh.yaml",
            analytics_start_at=value,
        )
