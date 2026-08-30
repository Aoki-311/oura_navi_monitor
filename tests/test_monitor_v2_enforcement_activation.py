from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.activate_monitor_v2_enforcement as activation


PROJECT = "test-project"
REGION = "us-central1"
SERVICE = "lcs-rag-app"
DATASET = "monitor"
TARGET = "lcs-rag-app-00002-new"
OLD = "lcs-rag-app-00001-old"
ROOT = Path(__file__).resolve().parents[1]


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt(*, now: datetime, drained: bool = True) -> dict:
    timeout = 300
    readback = now - timedelta(minutes=10) if drained else now
    before_traffic = [
        {"revisionName": OLD, "percent": 100},
        {"revisionName": TARGET, "percent": 0, "tag": "candidate"},
    ]
    after_traffic = [
        {"revisionName": TARGET, "percent": 100},
        {"revisionName": TARGET, "percent": 0, "tag": "candidate"},
    ]
    return {
        "receiptType": "lcs_candidate_promotion_v2",
        "project": PROJECT,
        "region": REGION,
        "service": SERVICE,
        "targetRevision": TARGET,
        "serviceBefore": {
            "metadata": {"name": SERVICE, "generation": 8},
            "spec": {"traffic": [dict(row) for row in before_traffic]},
            "status": {
                "observedGeneration": 8,
                "latestReadyRevisionName": TARGET,
                "traffic": [dict(row) for row in before_traffic],
            },
        },
        "serviceAfter": {
            "metadata": {"name": SERVICE, "generation": 9},
            "spec": {"traffic": [dict(row) for row in after_traffic]},
            "status": {
                "observedGeneration": 9,
                "latestReadyRevisionName": TARGET,
                "traffic": [dict(row) for row in after_traffic],
            },
        },
        "trafficReadbackAt": _utc(readback),
        "oldPositiveRevisions": [
            {"revisionName": OLD, "percent": 100, "timeoutSeconds": timeout}
        ],
        "maxRequestTimeoutSeconds": timeout,
        "drainUntil": _utc(readback + timedelta(seconds=timeout)),
    }


def _raw(receipt: dict) -> bytes:
    return (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _live_service(*, target: str = TARGET, percent: int = 100) -> dict:
    traffic = [
        {"revisionName": target, "percent": percent},
        {"revisionName": target, "percent": 0, "tag": "candidate"},
    ]
    return {
        "metadata": {"name": SERVICE, "generation": 10},
        "spec": {"traffic": [dict(row) for row in traffic]},
        "status": {
            "observedGeneration": 10,
            "latestReadyRevisionName": target,
            "traffic": [dict(row) for row in traffic],
        },
    }


def test_promotion_v2_receipt_binds_exact_drained_traffic_and_old_timeouts() -> None:
    now = datetime.now(timezone.utc)
    raw = _raw(_receipt(now=now))

    proof = activation.validate_promotion_receipt(
        raw_bytes=raw,
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        now=now,
    )

    assert proof["target_revision"] == TARGET
    assert proof["receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert proof["max_request_timeout_seconds"] == 300
    assert proof["old_positive_revisions"] == [
        {"revisionName": OLD, "percent": 100, "timeoutSeconds": 300}
    ]


def test_activation_rejects_before_drain_or_with_inconsistent_timeout() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="has not elapsed"):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(_receipt(now=now, drained=False)),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )

    bad = _receipt(now=now)
    bad["maxRequestTimeoutSeconds"] = 60
    with pytest.raises(ValueError, match="does not match oldPositiveRevisions"):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(bad),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )


def test_activation_rejects_empty_prior_traffic_contract() -> None:
    now = datetime.now(timezone.utc)
    empty = _receipt(now=now)
    empty_traffic = [
        {"revisionName": TARGET, "percent": 0, "tag": "candidate"}
    ]
    empty["serviceBefore"]["spec"]["traffic"] = [dict(row) for row in empty_traffic]
    empty["serviceBefore"]["status"]["traffic"] = [dict(row) for row in empty_traffic]
    empty["oldPositiveRevisions"] = []
    empty["maxRequestTimeoutSeconds"] = 0
    empty["drainUntil"] = empty["trafficReadbackAt"]
    with pytest.raises(ValueError, match="must contain the prior positive revision"):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(empty),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )


def test_receipt_rejects_wrong_service_and_non_100_before_traffic() -> None:
    now = datetime.now(timezone.utc)
    wrong_service = _receipt(now=now)
    wrong_service["serviceAfter"]["metadata"]["name"] = "another-service"
    with pytest.raises(ValueError, match="another service"):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(wrong_service),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )

    incomplete_before = _receipt(now=now)
    incomplete_before["serviceBefore"]["spec"]["traffic"][0]["percent"] = 90
    incomplete_before["serviceBefore"]["status"]["traffic"][0]["percent"] = 90
    incomplete_before["oldPositiveRevisions"][0]["percent"] = 90
    with pytest.raises(ValueError, match="does not total 100"):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(incomplete_before),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )


def test_service_identity_rejects_conflicting_metadata_and_top_level_names() -> None:
    conflicted = _live_service()
    conflicted["name"] = "another-service"
    with pytest.raises(ValueError, match="another service"):
        activation._require_exact_target_traffic(
            conflicted,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            target_revision=TARGET,
        )


@pytest.mark.parametrize("invalid_percent", [-1, 101])
def test_traffic_validator_rejects_out_of_range_percent(invalid_percent: int) -> None:
    malformed = _live_service(percent=invalid_percent)
    with pytest.raises(ValueError, match=r"outside 0\.\.100"):
        activation._require_exact_target_traffic(
            malformed,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            target_revision=TARGET,
        )


def test_receipt_rejects_stale_desired_traffic_hidden_by_observed_status() -> None:
    now = datetime.now(timezone.utc)
    stale_desired = _receipt(now=now)
    stale_desired["serviceAfter"]["spec"]["traffic"] = [
        {"revisionName": OLD, "percent": 100}
    ]

    with pytest.raises(
        ValueError,
        match="desired and observed positive traffic disagree",
    ):
        activation.validate_promotion_receipt(
            raw_bytes=_raw(stale_desired),
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            now=now,
        )


def test_live_service_rejects_unobserved_generation() -> None:
    unobserved = _live_service()
    unobserved["metadata"]["generation"] = 11

    with pytest.raises(ValueError, match="generation has not been fully observed"):
        activation._require_exact_target_traffic(
            unobserved,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            target_revision=TARGET,
        )


def test_live_service_rejects_missing_candidate_tag() -> None:
    missing_tag = _live_service()
    missing_tag["spec"]["traffic"] = missing_tag["spec"]["traffic"][:1]
    missing_tag["status"]["traffic"] = missing_tag["status"]["traffic"][:1]

    with pytest.raises(ValueError, match="traffic tag must resolve exactly once"):
        activation._require_exact_target_traffic(
            missing_tag,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            target_revision=TARGET,
        )


def test_live_gcloud_readback_must_still_be_exact_target_100(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"${FAKE_SERVICE_JSON}\"\n",
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_SERVICE_JSON": json.dumps(_live_service()),
    }

    exact = activation.describe_live_service(
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        env=env,
    )
    activation._require_exact_target_traffic(
        exact,
        project=PROJECT,
        region=REGION,
        service_name=SERVICE,
        target_revision=TARGET,
    )

    drifted_payload = _live_service()
    drifted_traffic = [
        {"revisionName": TARGET, "percent": 99},
        {"revisionName": OLD, "percent": 1},
        {"revisionName": TARGET, "percent": 0, "tag": "candidate"},
    ]
    drifted_payload["spec"]["traffic"] = [dict(row) for row in drifted_traffic]
    drifted_payload["status"]["traffic"] = [dict(row) for row in drifted_traffic]
    env["FAKE_SERVICE_JSON"] = json.dumps(drifted_payload)
    drifted = activation.describe_live_service(
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        env=env,
    )
    with pytest.raises(ValueError, match="not exactly target revision"):
        activation._require_exact_target_traffic(
            drifted,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            target_revision=TARGET,
        )


def test_gcloud_timeout_and_failure_use_stable_non_leaking_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args, **kwargs):
        raise activation.subprocess.TimeoutExpired(cmd=args[0], timeout=60)

    monkeypatch.setattr(activation.subprocess, "run", timeout)
    with pytest.raises(SystemExit, match="^live_service_describe_timeout$"):
        activation.describe_live_service(
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            env=dict(os.environ),
        )

    class Failure:
        returncode = 1
        stdout = ""
        stderr = "/secret/credential/path.json"

    monkeypatch.setattr(activation.subprocess, "run", lambda *args, **kwargs: Failure())
    with pytest.raises(SystemExit, match="^live_service_describe_failed$"):
        activation.describe_live_service(
            project=PROJECT,
            region=REGION,
            service=SERVICE,
            env=dict(os.environ),
        )


def test_activation_sql_is_write_once_and_requires_exact_registration() -> None:
    plan = " ".join(
        activation.render_activation_sql(
            project=PROJECT,
            dataset=DATASET,
            apply=False,
        ).lower().split()
    )
    apply = " ".join(
        activation.render_activation_sql(
            project=PROJECT,
            dataset=DATASET,
            apply=True,
        ).lower().split()
    )

    assert "update `test-project.monitor.monitor_contract_revision_ledger`" not in plan
    assert "registration_source = 'candidate_v2_exact_http_question_sample'" in plan
    assert "sample_correlation_hash = to_hex(sha256(concat(" in plan
    assert "assert current_timestamp() >= @promotion_drain_until" in apply
    assert apply.count("update `test-project.monitor.monitor_contract_revision_ledger`") == 1
    assert "enforcement_start = current_timestamp()" in apply
    assert "activation_source = 'lcs_promotion_v2_drained_live_readback'" in apply
    assert "set activation_write_performed = (@@row_count = 1)" in apply
    assert "activation_write_performed or exists" in apply
    assert "enforcement activation is neither newly written nor exactly recoverable" in apply
    assert "monitor.v2 enforcement conflicts with this promotion receipt" in apply
    assert "promotion_project is null" in apply
    assert "coalesce( promotion_receipt_sha256, @promotion_receipt_sha256 )" in plan
    assert "when matched" not in apply


def test_bad_activation_ledger_row_cannot_open_quality_cutover() -> None:
    quality = " ".join(
        (ROOT / "sql" / "check_data_quality.sql")
        .read_text(encoding="utf-8")
        .lower()
        .split()
    )
    enforcement = quality.split(
        "), trace_contract_enforcement as (",
        maxsplit=1,
    )[1].split(
        "), source_question_correlation_rows as (",
        maxsplit=1,
    )[0]

    for contract in (
        "activation_source = 'lcs_promotion_v2_drained_live_readback'",
        "promotion_receipt_type = 'lcs_candidate_promotion_v2'",
        "promotion_project = '${project_id}'",
        "promotion_service = '${source_service}'",
        "promotion_target_revision = revision_name",
        "promotion_drain_until = timestamp_add(",
        "promotion_max_request_timeout_seconds between 1 and 3600",
        "promotion_old_positive_revisions_json)), [] )) > 0",
        "enforcement_start >= promotion_drain_until",
        "enforcement_start <= current_timestamp()",
        "promotion_old_positive_revisions_json",
        "activation_service_readback_sha256",
    ):
        assert contract in enforcement


class _QueryJob:
    def __init__(self, row: dict):
        self.row = row

    def result(self):
        return [self.row]


class _Client:
    def __init__(self, rows: dict | list[dict]):
        self.rows = list(rows) if isinstance(rows, list) else [rows]
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        if not self.rows:
            raise AssertionError("unexpected extra activation query")
        return _QueryJob(self.rows.pop(0))


def _ledger_row(
    *,
    now: datetime,
    receipt_sha: str,
    live_sha: str,
    write_performed: bool,
) -> dict:
    promotion = _receipt(now=now)
    return {
        "revision_name": TARGET,
        "monitor_contract_version": "monitor.v2",
        "enforcement_start": now,
        "activation_source": "lcs_promotion_v2_drained_live_readback",
        "promotion_receipt_type": "lcs_candidate_promotion_v2",
        "promotion_receipt_sha256": receipt_sha,
        "promotion_project": PROJECT,
        "promotion_region": REGION,
        "promotion_service": SERVICE,
        "promotion_target_revision": TARGET,
        "promotion_traffic_readback_at": promotion["trafficReadbackAt"],
        "promotion_max_request_timeout_seconds": 300,
        "promotion_drain_until": promotion["drainUntil"],
        "promotion_old_positive_revisions_json": json.dumps(
            promotion["oldPositiveRevisions"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "activation_service_readback_sha256": live_sha,
        "activated": True,
        "activation_write_performed": write_performed,
    }


def test_apply_writes_readback_bound_non_overwriting_activation_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    promotion = tmp_path / "promotion.json"
    raw = _raw(_receipt(now=now))
    promotion.write_bytes(raw)
    receipt_sha = hashlib.sha256(raw).hexdigest()
    live = _live_service()
    live_sha = hashlib.sha256(
        activation._canonical_json(live).encode("utf-8")
    ).hexdigest()
    client = _Client(
        [
            _ledger_row(
                now=now,
                receipt_sha=receipt_sha,
                live_sha=live_sha,
                write_performed=True,
            ),
            _ledger_row(
                now=now,
                receipt_sha=receipt_sha,
                live_sha=live_sha,
                write_performed=False,
            ),
        ]
    )
    output = tmp_path / "activation.json"
    confirmation = activation._required_confirmation(
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        revision=TARGET,
        receipt_sha256=receipt_sha,
    )
    monkeypatch.setattr(
        activation,
        "_credential_environment",
        lambda _path: (dict(os.environ), object()),
    )
    monkeypatch.setattr(activation, "describe_live_service", lambda **_: live)
    monkeypatch.setattr(
        activation.bigquery, "Client", lambda project, credentials: client
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "activate_monitor_v2_enforcement.py",
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--service",
            SERVICE,
            "--dataset",
            DATASET,
            "--promotion-receipt",
            str(promotion),
            "--credential-file",
            "/test/approved.json",
            "--receipt-output",
            str(output),
            "--confirm-activate",
            confirmation,
            "--apply",
        ],
    )

    assert activation.main() == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["receiptType"] == "monitor_v2_enforcement_activation_v1"
    assert saved["targetRevision"] == TARGET
    assert saved["promotionReceiptSha256"] == receipt_sha
    assert saved["liveServiceReadbackSha256"] == live_sha
    assert saved["activationRecovered"] is False
    assert "enforcement_start = CURRENT_TIMESTAMP()" in client.calls[0][0]
    original_bytes = output.read_bytes()

    assert activation.main() == 0
    assert output.read_bytes() == original_bytes
    assert len(client.calls) == 2


def test_apply_recovers_receipt_after_database_commit_but_local_write_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    promotion = tmp_path / "promotion.json"
    raw = _raw(_receipt(now=now))
    promotion.write_bytes(raw)
    receipt_sha = hashlib.sha256(raw).hexdigest()
    first_live = _live_service()
    first_live_sha = hashlib.sha256(
        activation._canonical_json(first_live).encode("utf-8")
    ).hexdigest()
    recovered_live = _live_service()
    recovered_live["metadata"]["resourceVersion"] = "natural-readback-change"
    recovered_live_sha = hashlib.sha256(
        activation._canonical_json(recovered_live).encode("utf-8")
    ).hexdigest()
    live_readbacks = [first_live, recovered_live]
    client = _Client(
        [
            _ledger_row(
                now=now,
                receipt_sha=receipt_sha,
                live_sha=first_live_sha,
                write_performed=True,
            ),
            _ledger_row(
                now=now,
                receipt_sha=receipt_sha,
                live_sha=first_live_sha,
                write_performed=False,
            ),
        ]
    )
    output = tmp_path / "activation.json"
    confirmation = activation._required_confirmation(
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        revision=TARGET,
        receipt_sha256=receipt_sha,
    )
    monkeypatch.setattr(
        activation,
        "_credential_environment",
        lambda _path: (dict(os.environ), object()),
    )
    monkeypatch.setattr(
        activation,
        "describe_live_service",
        lambda **_: live_readbacks.pop(0),
    )
    monkeypatch.setattr(
        activation.bigquery, "Client", lambda project, credentials: client
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "activate_monitor_v2_enforcement.py",
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--service",
            SERVICE,
            "--dataset",
            DATASET,
            "--promotion-receipt",
            str(promotion),
            "--credential-file",
            "/test/approved.json",
            "--receipt-output",
            str(output),
            "--confirm-activate",
            confirmation,
            "--apply",
        ],
    )
    real_writer = activation._write_new_receipt
    monkeypatch.setattr(
        activation,
        "_write_new_receipt",
        lambda *_: (_ for _ in ()).throw(OSError("synthetic disk failure")),
    )

    with pytest.raises(OSError, match="synthetic disk failure"):
        activation.main()
    assert not output.exists()

    monkeypatch.setattr(activation, "_write_new_receipt", real_writer)
    assert activation.main() == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["activationRecovered"] is True
    assert saved["liveServiceReadbackSha256"] == first_live_sha
    assert saved["recoveryLiveServiceReadbackSha256"] == recovered_live_sha
    assert len(client.calls) == 2


def test_apply_stops_when_ledger_activation_conflicts_with_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    promotion = tmp_path / "promotion.json"
    raw = _raw(_receipt(now=now))
    promotion.write_bytes(raw)
    receipt_sha = hashlib.sha256(raw).hexdigest()
    output = tmp_path / "activation.json"
    confirmation = activation._required_confirmation(
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        revision=TARGET,
        receipt_sha256=receipt_sha,
    )

    class ConflictClient:
        def query(self, *args, **kwargs):
            raise RuntimeError(
                "monitor.v2 enforcement conflicts with this promotion receipt"
            )

    monkeypatch.setattr(
        activation,
        "_credential_environment",
        lambda _path: (dict(os.environ), object()),
    )
    monkeypatch.setattr(
        activation,
        "describe_live_service",
        lambda **_: _live_service(),
    )
    monkeypatch.setattr(
        activation.bigquery,
        "Client",
        lambda project, credentials: ConflictClient(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "activate_monitor_v2_enforcement.py",
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--service",
            SERVICE,
            "--dataset",
            DATASET,
            "--promotion-receipt",
            str(promotion),
            "--credential-file",
            "/test/approved.json",
            "--receipt-output",
            str(output),
            "--confirm-activate",
            confirmation,
            "--apply",
        ],
    )

    with pytest.raises(RuntimeError, match="conflicts with this promotion receipt"):
        activation.main()
    assert not output.exists()


def test_runbook_orders_registration_promotion_drain_activation_before_backfill() -> None:
    runbook = " ".join(
        (ROOT / "docs" / "THREE_HOUR_RECOVERY_RUNBOOK.md")
        .read_text(encoding="utf-8")
        .split()
    )
    registration = runbook.index("scripts/register_monitor_v2_revision.py")
    promotion = runbook.index("lcs_candidate_promotion_v2")
    drain = runbook.index("drainUntil")
    enforcement = runbook.index("scripts/activate_monitor_v2_enforcement.py")
    backfill = runbook.index("scripts/backfill_recent_data.sh")
    assert registration < promotion < drain < enforcement < backfill
