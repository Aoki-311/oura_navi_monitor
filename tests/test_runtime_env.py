from pathlib import Path

import pytest

from scripts.render_runtime_env import render_refresh_env


def test_release_configuration_has_no_identity_secret_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "cloudbuild.yaml",
            "deploy/cloudrun.env.yaml",
            "scripts/bootstrap_gcp.sh",
        )
    )
    assert "MONITOR_IDENTITY_HMAC_KEY" not in combined
    assert "oura-navi-monitor-identity-hmac" not in combined


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
