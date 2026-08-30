from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.credential_preflight import approved_credential_path


def test_release_shells_share_metadata_preflight_and_never_globally_export() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = (
        "approve_pending_build.sh",
        "backfill_recent_data.sh",
        "bootstrap_gcp.sh",
        "bootstrap_monitor_data.sh",
        "create_github_trigger.sh",
        "cutover_refresh_scheduler.sh",
        "pause_legacy_bigquery_refresh.sh",
        "promote_candidate.sh",
        "publish_monitor_source_views.sh",
        "publish_monitor_views.sh",
        "rebuild_monitor_data.sh",
        "run_monitor_refresh.sh",
        "setup_alerts.sh",
        "verify_legacy_bigquery_pause.sh",
    )
    for name in scripts:
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        assert "--credential-file" in text, name
        assert "credential_preflight.py" in text, name
        assert "export GOOGLE_APPLICATION_CREDENTIALS" not in text, name
        assert "export CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" not in text, name

    google_cli_shells = (
        "backfill_recent_data.sh",
        "bootstrap_gcp.sh",
        "bootstrap_monitor_data.sh",
        "create_github_trigger.sh",
        "cutover_refresh_scheduler.sh",
        "pause_legacy_bigquery_refresh.sh",
        "promote_candidate.sh",
        "publish_monitor_source_views.sh",
        "publish_monitor_views.sh",
        "setup_alerts.sh",
        "verify_legacy_bigquery_pause.sh",
    )
    for name in google_cli_shells:
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        assert 'source "${ROOT_DIR}/scripts/credential_shell.sh"' in text, name
        assert "monitor_install_google_credential_wrappers" in text, name


def test_google_cli_wrappers_scope_the_credential_to_each_child_process(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    credential = tmp_path / "approved.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for name in ("gcloud", "bq"):
        executable = binary_dir / name
        executable.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s|%s|%s\\n' \"$CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE\" "
            "\"$GOOGLE_APPLICATION_CREDENTIALS\" \"$1\"\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    command = f"""
      source {root / 'scripts' / 'credential_shell.sh'}
      monitor_install_google_credential_wrappers {credential}
      gcloud alpha
      bq beta
      printf 'parent=%s|%s\\n' "${{CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE-}}" "${{GOOGLE_APPLICATION_CREDENTIALS-}}"
    """
    env = {**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"}
    env.pop("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", None)
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = result.stdout.splitlines()
    expected_prefix = f"{credential}|{credential}|"
    assert lines == [
        expected_prefix + "alpha",
        expected_prefix + "beta",
        "parent=|",
    ]


def _configure(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", str(path))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))


def test_credential_preflight_accepts_owned_0600_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "approved.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)
    _configure(monkeypatch, path)

    assert approved_credential_path(path) == path


def test_credential_preflight_rejects_symlink_and_group_readable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "approved.json"
    link.symlink_to(target)
    _configure(monkeypatch, link)
    with pytest.raises(ValueError, match="non-symlink"):
        approved_credential_path(link)

    target.chmod(0o640)
    _configure(monkeypatch, target)
    with pytest.raises(ValueError, match="exactly 0600"):
        approved_credential_path(target)

    for unsafe_mode in (0o700, 0o400, 0o200):
        target.chmod(unsafe_mode)
        with pytest.raises(ValueError, match="exactly 0600"):
            approved_credential_path(target)


def test_credential_preflight_rejects_mismatched_sdk_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for path in (first, second):
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)
    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", str(first))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(second))

    with pytest.raises(ValueError, match="does not match"):
        approved_credential_path(first)


def test_credential_preflight_requires_the_explicit_path_to_match_any_ambient_sdk_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = tmp_path / "approved.json"
    other = tmp_path / "other.json"
    for path in (approved, other):
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)

    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", str(other))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ValueError, match="does not match --credential-file"):
        approved_credential_path(approved)


def test_credential_preflight_rejects_relative_or_non_normalized_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = tmp_path / "approved.json"
    approved.write_text("{}", encoding="utf-8")
    approved.chmod(0o600)
    monkeypatch.delenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    with pytest.raises(ValueError, match="normalized absolute"):
        approved_credential_path(Path("approved.json"))
    with pytest.raises(ValueError, match="normalized absolute"):
        approved_credential_path(tmp_path / "nested" / ".." / "approved.json")
