from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "promotion_receipt_state.py"
SPEC = importlib.util.spec_from_file_location("promotion_receipt_state", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


PROJECT = "test-project"
REGION = "us-central1"
SERVICE = "oura-navi-monitor"
GIT_SHA = "a" * 40
BUILD_ID = "12345678-1234-1234-1234-123456789abc"
REVISION = f"{SERVICE}-{GIT_SHA[:7]}-{BUILD_ID[:8]}"
IMAGE = (
    f"{REGION}-docker.pkg.dev/{PROJECT}/repo/monitor@sha256:" + "b" * 64
)
SERVICE_ACCOUNT = f"monitor-web@{PROJECT}.iam.gserviceaccount.com"


def _revision() -> dict:
    return {
        "metadata": {
            "name": REVISION,
            "labels": {"git-sha": GIT_SHA},
        },
        "spec": {
            "serviceAccountName": SERVICE_ACCOUNT,
            "containers": [{"image": IMAGE}],
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _validate(revision: dict) -> None:
    state._validate_revision(
        revision,
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        target_revision=REVISION,
        image=IMAGE,
        git_sha=GIT_SHA,
        build_id=BUILD_ID,
        service_account=SERVICE_ACCOUNT,
    )


def test_revision_accepts_only_bare_or_exact_service_nested_full_name() -> None:
    bare = _revision()
    _validate(bare)

    full = _revision()
    exact = (
        f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/"
        f"revisions/{REVISION}"
    )
    full["metadata"]["name"] = exact
    full["name"] = REVISION
    _validate(full)

    legacy_wrong_shape = _revision()
    legacy_wrong_shape["metadata"]["name"] = (
        f"projects/{PROJECT}/locations/{REGION}/revisions/{REVISION}"
    )
    with pytest.raises(ValueError, match="revision readback identity mismatch"):
        _validate(legacy_wrong_shape)


def test_revision_rejects_conflicting_dual_identity_fields() -> None:
    revision = _revision()
    revision["name"] = "another-revision"

    with pytest.raises(ValueError, match="revision readback identity mismatch"):
        _validate(revision)


def test_container_image_cannot_be_hidden_by_a_correct_status_digest() -> None:
    revision = _revision()
    revision["spec"]["containers"][0]["image"] = IMAGE.replace("b" * 64, "c" * 64)
    revision["status"]["imageDigest"] = IMAGE

    with pytest.raises(ValueError, match="container image mismatch"):
        _validate(revision)


@pytest.mark.parametrize("observed", ["sha256:" + "c" * 64, "", None])
def test_status_digest_is_strict_when_the_field_is_present(observed: object) -> None:
    revision = _revision()
    revision["status"]["imageDigest"] = observed

    with pytest.raises(ValueError, match="status image digest mismatch"):
        _validate(revision)


def test_status_digest_accepts_exact_full_image_or_digest_component() -> None:
    for observed in (IMAGE, IMAGE.rsplit("@", 1)[1]):
        revision = deepcopy(_revision())
        revision["status"]["imageDigest"] = observed
        _validate(revision)


def test_release_lock_namespace_has_one_governed_owner() -> None:
    state._validate_lock_namespace("lcs-user-data", "monitor_release_locks")

    with pytest.raises(ValueError, match="governed owner"):
        state._validate_lock_namespace("another-database", "monitor_release_locks")
    with pytest.raises(ValueError, match="governed owner"):
        state._validate_lock_namespace("lcs-user-data", "another_collection")


def test_promotion_intent_publication_is_exclusive_and_never_partial(
    tmp_path: Path,
) -> None:
    path = tmp_path / "promotion.json"
    stale = tmp_path / ".promotion.json.intent-stale.tmp"
    stale.write_text("{partial", encoding="utf-8")
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def publish(marker: str) -> None:
        payload = {"receiptType": state.INTENT_TYPE, "state": "intent", "marker": marker}
        barrier.wait()
        results.append(state._write_new_intent(path, payload))

    threads = [
        threading.Thread(target=publish, args=("first",)),
        threading.Thread(target=publish, args=("second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published["marker"] in {"first", "second"}
    state._validate_intent_integrity(published)
    assert stale.read_text(encoding="utf-8") == "{partial"


def test_failed_intent_link_never_exposes_a_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "promotion.json"

    def fail_link(_source: object, _target: object) -> None:
        raise OSError("synthetic link interruption")

    monkeypatch.setattr(state.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic link interruption"):
        state._write_new_intent(path, {"state": "intent"})

    assert not path.exists()
    assert list(tmp_path.glob(".promotion.json.intent-*.tmp")) == []


def test_promotion_final_replace_is_followed_by_parent_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "promotion.json"
    path.write_text("intent", encoding="utf-8")
    expected = path.read_bytes()
    stale = tmp_path / ".promotion.json.final-stale.tmp"
    stale.write_text("{partial", encoding="utf-8")
    events: list[str] = []
    real_replace = state.os.replace

    def record_replace(source: object, target: object) -> None:
        real_replace(source, target)
        events.append("replace")

    monkeypatch.setattr(state.os, "replace", record_replace)
    monkeypatch.setattr(
        state,
        "_fsync_directory",
        lambda _path: events.append("parent-fsync"),
    )

    state._atomic_finalize(
        path,
        expected_raw=expected,
        payload={"receiptType": state.FINAL_TYPE, "state": "complete"},
    )

    assert events == ["replace", "parent-fsync"]
    assert json.loads(path.read_text())["state"] == "complete"
    assert stale.read_text(encoding="utf-8") == "{partial"


def _receipt_path_args(tmp_path: Path) -> SimpleNamespace:
    values: dict[str, str] = {}
    for key, argument, _label in state._RECEIPT_ARGUMENTS:
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps({"marker": key}), encoding="utf-8")
        values[argument] = str(path)
    return SimpleNamespace(**values)


def test_receipt_snapshot_payload_and_hash_come_from_one_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _receipt_path_args(tmp_path)
    receipt_paths = {
        Path(getattr(args, argument))
        for _key, argument, _label in state._RECEIPT_ARGUMENTS
    }
    reads: dict[Path, int] = {path: 0 for path in receipt_paths}
    real_read_bytes = Path.read_bytes

    def replace_after_read(path: Path) -> bytes:
        raw = real_read_bytes(path)
        if path in receipt_paths:
            reads[path] += 1
            path.write_text(json.dumps({"marker": "replacement"}), encoding="utf-8")
        return raw

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    snapshot = state._read_receipt_snapshot(args)

    assert set(reads.values()) == {1}
    for key, _argument, _label in state._RECEIPT_ARGUMENTS:
        expected = json.dumps({"marker": key}).encode("utf-8")
        assert snapshot.raw[key] == expected
        assert snapshot.payloads[key] == {"marker": key}
        assert snapshot.hashes[key] == state._sha_bytes(expected)


def test_static_contract_never_rereads_receipts_after_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path_args = _receipt_path_args(tmp_path)
    snapshot = state._read_receipt_snapshot(path_args)
    args = SimpleNamespace(
        **vars(path_args),
        project=PROJECT,
        region=REGION,
        service=SERVICE,
        revision=REVISION,
        image=IMAGE,
        git_sha=GIT_SHA,
        build_id=BUILD_ID,
        service_account=SERVICE_ACCOUNT,
        expected_job_service_account="refresh@test-project.iam.gserviceaccount.com",
        legacy_transfer_resource=(
            "projects/test-project/locations/us/transferConfigs/example"
        ),
        dataset="oura_navi_monitor",
        location="US",
        source_service="lcs-rag-app",
        job="oura-navi-monitor-refresh",
        scheduler="oura-navi-monitor-refresh-three-hour",
        job_timeout_minutes=30,
        firestore_database="lcs-user-data",
        release_lock_collection="monitor_release_locks",
    )
    monkeypatch.setenv("CURRENT_SCHEDULER_JSON", "{}")
    monkeypatch.setenv("CURRENT_JOB_JSON", "{}")
    monkeypatch.setenv("CURRENT_TRANSFER_JSON", "{}")
    monkeypatch.setattr(state, "_scheduler_governance", lambda _value: {})
    monkeypatch.setattr(state, "validate_refresh_job", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(state, "_legacy_transfer_governance", lambda _value: {})
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("receipt was reread")),
    )

    contract = state._static_contract(args, receipt_snapshot=snapshot)

    assert contract["schemaReceiptSha256"] == snapshot.hashes["schema"]
    assert contract["apiReceiptSha256"] == snapshot.hashes["api"]
    assert contract["dts72hReceiptSha256"] == snapshot.hashes["dts72"]
