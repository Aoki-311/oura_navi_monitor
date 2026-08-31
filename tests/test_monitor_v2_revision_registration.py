import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.register_monitor_v2_revision as registration
from scripts.register_monitor_v2_revision import (
    _proof_hash,
    _required_confirmation,
    _validate_contract,
    render_registration_sql,
)


PROJECT = "test-project"
DATASET = "monitor"
REVISION = "lcs-rag-app-00001-abc"
TRACE = f"projects/{PROJECT}/traces/{'a' * 32}"
SPAN = "000000000000002a"
ROOT = Path(__file__).resolve().parents[1]


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 8, 30, 1, tzinfo=timezone.utc)
    return start, start + timedelta(minutes=30)


def test_candidate_registration_proof_is_exact_and_source_window_bounded() -> None:
    sql = " ".join(
        render_registration_sql(
            project=PROJECT,
            dataset=DATASET,
            apply=False,
        ).lower().split()
    )

    assert "`test-project.monitor.http_request_source`" in sql
    assert "`test-project.monitor.monitor_event_source`" in sql
    assert "run_googleapis_com_requests" not in sql
    assert "run_googleapis_com_stdout" not in sql
    assert sql.count("source_ts >= @window_start and source_ts < @window_end") == 5
    assert "revision_name = @revision" in sql
    assert "cloud_trace = @cloud_trace" in sql
    assert "cloud_span_id = @cloud_span_id" in sql
    assert "endpoint_class = @endpoint_class" in sql
    assert "status between 200 and 299" in sql
    assert "monitor_contract_version = 'monitor.v2'" in sql
    assert "event_family = 'question_received'" in sql
    assert "assert exact_http_count = 1" in sql
    assert "assert exact_question_count = 1" in sql
    assert "assert tuple_http_count = 1" in sql
    assert "assert tuple_question_count = 1" in sql
    assert "merge `test-project.monitor.monitor_contract_revision_ledger`" not in sql


def test_apply_registers_only_the_proved_revision_and_rejects_conflicts() -> None:
    sql = " ".join(
        render_registration_sql(
            project=PROJECT,
            dataset=DATASET,
            apply=True,
        ).lower().split()
    )

    assert "candidate_v2_exact_http_question_sample" in sql
    assert "begin transaction" in sql
    assert "assert not exists" in sql
    assert "sample_correlation_hash != proof_correlation_hash" in sql
    assert "sample_cloud_trace != @cloud_trace" in sql
    assert "sample_cloud_span_id != @cloud_span_id" in sql
    assert "merge `test-project.monitor.monitor_contract_revision_ledger`" in sql
    assert "when not matched then insert" in sql
    assert "when matched" not in sql
    assert sql.index("assert exact_http_count = 1") < sql.index("merge `")
    assert sql.index("assert exact_question_count = 1") < sql.index("merge `")
    assert sql.index("merge `") < sql.index("commit transaction")
    assert sql.index("commit transaction") < sql.rindex(
        "from `test-project.monitor.monitor_contract_revision_ledger` target"
    )
    assert "target.sample_correlation_hash = proof_correlation_hash" in sql
    assert "target.sample_cloud_trace = @cloud_trace" in sql
    assert "target.sample_cloud_span_id = @cloud_span_id" in sql


def test_registration_contract_rejects_debug_or_unbounded_samples() -> None:
    start, end = _window()

    with pytest.raises(ValueError, match="requires ask or ask_stream"):
        _validate_contract(
            project=PROJECT,
            dataset=DATASET,
            revision=REVISION,
            trace=TRACE,
            span=SPAN,
            endpoint_class="debug_ask",
            window_start=start,
            window_end=end,
        )
    with pytest.raises(ValueError, match="must not exceed two hours"):
        _validate_contract(
            project=PROJECT,
            dataset=DATASET,
            revision=REVISION,
            trace=TRACE,
            span=SPAN,
            endpoint_class="ask",
            window_start=start,
            window_end=start + timedelta(hours=2, seconds=1),
        )


def test_registration_confirmation_is_bound_to_the_exact_sample_tuple() -> None:
    proof = _proof_hash(
        revision=REVISION,
        trace=TRACE,
        span=SPAN,
        endpoint_class="ask",
    )
    confirmation = _required_confirmation(
        project=PROJECT,
        dataset=DATASET,
        revision=REVISION,
        proof_hash=proof,
    )

    assert proof != _proof_hash(
        revision=REVISION,
        trace=TRACE,
        span="000000000000002b",
        endpoint_class="ask",
    )
    assert confirmation == (
        "projects/test-project/datasets/monitor/monitor_contract_revision_ledger:"
        f"register-v2:{REVISION}:{proof}"
    )


def test_runbook_makes_candidate_proof_registration_a_pre_traffic_stop_gate() -> None:
    runbook = (ROOT / "docs" / "THREE_HOUR_RECOVERY_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(runbook.split())
    registration = normalized.index("scripts/register_monitor_v2_revision.py")
    traffic_stop = normalized.index("禁止给 LCS candidate 切生产流量")

    assert registration < traffic_stop
    assert "automatic HTTP request log" in runbook
    assert "monitor.v2 question_received" in runbook
    assert "定时刷新只读这个 ledger" in runbook
    assert "该证明用于发布验收" in runbook
    assert "不能让 recurring refresh 回滚整批" in runbook
    assert "不再充当全体用户数据的运行时开关" in runbook


class _QueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _RegistrationClient:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        return _QueryJob([self.row])


def test_apply_writes_a_readback_bound_receipt_without_overwriting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = tmp_path / "approved.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    proof_hash = _proof_hash(
        revision=REVISION,
        trace=TRACE,
        span=SPAN,
        endpoint_class="ask",
    )
    client = _RegistrationClient(
        {
            "revision_name": REVISION,
            "monitor_contract_version": "monitor.v2",
            "sample_endpoint_class": "ask",
            "sample_cloud_trace": TRACE,
            "sample_cloud_span_id": SPAN,
            "sample_source_ts": datetime(2026, 8, 30, 1, 5, tzinfo=timezone.utc),
            "sample_correlation_hash": proof_hash,
            "exact_http_count": 1,
            "exact_question_count": 1,
            "tuple_http_count": 1,
            "tuple_question_count": 1,
            "registered": True,
            "registration_source": "candidate_v2_exact_http_question_sample",
        }
    )
    receipt = tmp_path / "registration.json"
    confirmation = _required_confirmation(
        project=PROJECT,
        dataset=DATASET,
        revision=REVISION,
        proof_hash=proof_hash,
    )
    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", str(credential))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(credential))
    monkeypatch.setattr(
        registration.service_account.Credentials,
        "from_service_account_file",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        registration.bigquery,
        "Client",
        lambda project, credentials: client,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "register_monitor_v2_revision.py",
            "--project",
            PROJECT,
            "--dataset",
            DATASET,
            "--revision",
            REVISION,
            "--trace",
            TRACE,
            "--span",
            SPAN,
            "--endpoint-class",
            "ask",
            "--window-start",
            "2026-08-30T01:00:00Z",
            "--window-end",
            "2026-08-30T01:30:00Z",
            "--credential-file",
            str(credential),
            "--receipt-output",
            str(receipt),
            "--confirm-register",
            confirmation,
            "--apply",
        ],
    )

    assert registration.main() == 0
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["receipt_type"] == "monitor_v2_revision_registration_v1"
    assert payload["revision"] == REVISION
    assert payload["proof"]["sample_correlation_hash"] == proof_hash
    assert payload["proof"]["registered"] is True
    assert "merge `test-project.monitor.monitor_contract_revision_ledger`" in (
        client.calls[0][0].lower()
    )
    assert client.calls[0][2] == "US"

    with pytest.raises(SystemExit, match="receipt output already exists"):
        registration.main()
    assert len(client.calls) == 1
