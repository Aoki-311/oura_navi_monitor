from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/cloud_sdk_python.sh"


def environment(tmp_path: Path, python: str = sys.executable, *, gcloud_exit: int = 0) -> dict[str, str]:
    commands = tmp_path / "commands"
    commands.mkdir()
    gcloud = commands / "gcloud"
    gcloud.write_text(
        "#!/bin/bash\n"
        "[[ $# -eq 2 && $1 == info && $2 == '--format=value(basic.python_location)' ]] || exit 97\n"
        f"exit_code={gcloud_exit}\n"
        "[[ $exit_code -eq 0 ]] || exit \"$exit_code\"\n"
        "printf '%s\\n' \"$TEST_SDK_PYTHON\"\n",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    return {**os.environ, "PATH": str(commands), "TEST_SDK_PYTHON": python, "PYTHONDONTWRITEBYTECODE": "1"}


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/bash", str(WRAPPER), *args], cwd=ROOT, env=env,
                          text=True, capture_output=True, timeout=20)


def test_runs_reported_sdk_interpreter_without_system_python_and_preserves_arguments(tmp_path: Path) -> None:
    executable = tmp_path / "sdk python with spaces"
    executable.symlink_to(sys.executable)
    env = environment(tmp_path, str(executable))
    assert not (Path(env["PATH"]) / "python3").exists()
    result = run(env, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", "two words", "$(not-a-shell-command)")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["two words", "$(not-a-shell-command)"]


def test_real_candidate_verifier_help_runs_with_only_the_sdk_interpreter(tmp_path: Path) -> None:
    result = run(environment(tmp_path), "scripts/verify_candidate_readback.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "--expected-image" in result.stdout
    assert "--before-iam" in result.stdout


@pytest.mark.parametrize("kind", ["empty", "relative", "missing", "directory", "nonexecutable"])
def test_invalid_reported_interpreter_fails_before_running_python(tmp_path: Path, kind: str) -> None:
    if kind == "empty": reported = ""
    elif kind == "relative": reported = "relative/python3"
    elif kind == "missing": reported = str(tmp_path / "missing-python")
    elif kind == "directory": reported = str(tmp_path)
    else:
        path = tmp_path / "nonexecutable"
        path.write_text("#!/bin/bash\nexit 0\n")
        path.chmod(0o644)
        reported = str(path)
    result = run(environment(tmp_path, reported), "-c", "raise RuntimeError('must never execute')")
    assert result.returncode == 2
    assert "absolute executable Python path" in result.stderr
    assert "must never execute" not in result.stderr


@pytest.mark.parametrize("version, expected", [((3, 9), 2), ((3, 10), 0)])
def test_enforces_minimum_python_version_before_forwarding(tmp_path: Path, version: tuple[int, int], expected: int) -> None:
    interpreter = tmp_path / "versioned-python"
    version_check = "import sys; sys.version_info = " + repr(version) + "; exec(sys.argv[1])"
    interpreter.write_text(
        "#!/bin/bash\n"
        "if [[ $1 == -c ]]; then\n"
        f"  exec {shlex.quote(sys.executable)} -c {shlex.quote(version_check)} \"$2\"\n"
        "fi\n"
        f"exec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    result = run(environment(tmp_path, str(interpreter)), "-c", "print('forwarded')")
    assert result.returncode == expected
    if expected:
        assert "forwarded" not in result.stdout
        assert "3.10 or newer" in result.stderr
    else:
        assert result.stdout.strip() == "forwarded"


def test_gcloud_failure_stops_without_a_fallback_interpreter(tmp_path: Path) -> None:
    result = run(environment(tmp_path, gcloud_exit=42), "-c", "print('must not execute')")
    assert result.returncode == 2
    assert "Unable to resolve" in result.stderr
    assert not result.stdout


def test_python_script_failure_is_not_masked(tmp_path: Path) -> None:
    result = run(environment(tmp_path), "-c", "raise SystemExit(37)")
    assert result.returncode == 37


def test_no_arguments_cannot_start_an_interactive_interpreter(tmp_path: Path) -> None:
    result = run(environment(tmp_path))
    assert result.returncode == 2
    assert "script or argument is required" in result.stderr
