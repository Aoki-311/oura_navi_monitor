import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def plan(*args):
    env = dict(os.environ)
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    return subprocess.run([
        "bash", str(ROOT / "scripts/bootstrap_gcp.sh"), "--project", "fixture-project",
        "--python", sys.executable, *args,
    ], cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)


def test_optional_usage_sink_branch_keeps_existing_chat_filter_exact():
    baseline = plan()
    extended = plan("--news-usage-source-service", "oura-navi-test")
    assert baseline.returncode == extended.returncode == 0
    original_filter = next(line.split("=", 1)[1] for line in baseline.stdout.splitlines()
                           if line.startswith("logging_filter="))
    combined = next(line.split("=", 1)[1] for line in extended.stdout.splitlines()
                    if line.startswith("logging_filter="))
    assert combined.startswith(f"({original_filter}) OR (")
    added = combined[len(original_filter) + 7:]
    assert 'service_name="oura-navi-test"' in added
    assert 'event_family="news_usage"' in added
    assert 'jsonPayload.monitor_event=true' in added
    assert "run.googleapis.com%2Frequests" not in added
    assert "run.googleapis.com%2Fstderr" not in added
    assert "mode=plan" in extended.stdout


def test_news_sink_binding_rejects_filter_injection_without_any_cloud_action():
    result = plan("--news-usage-source-service", 'x" OR true')
    assert result.returncode == 2
    assert "must be one Cloud Run service name" in result.stderr
