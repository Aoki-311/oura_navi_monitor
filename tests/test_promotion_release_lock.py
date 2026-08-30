from __future__ import annotations

import threading
from typing import Any, Callable

import pytest

from scripts import promotion_release_lock as release_lock


def _identity(*, target: str = "monitor-revision-a", intent: str = "a" * 64) -> dict[str, str]:
    return {
        "project": "test-project",
        "region": "us-central1",
        "service": "oura-navi-monitor",
        "targetRevision": target,
        "intentPayloadSha256": intent,
        "staticContractSha256": "c" * 64,
        "firestoreDatabase": "lcs-user-data",
        "releaseLockCollection": "monitor_release_locks",
    }


class _Snapshot:
    def __init__(self, value: dict[str, Any] | None):
        self.exists = value is not None
        self._value = value

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._value) if self._value is not None else None


class _Document:
    def __init__(self, key: str):
        self.key = key


class _Collection:
    def __init__(self, name: str):
        self.name = name

    def document(self, name: str) -> _Document:
        return _Document(self.name + "/" + name)


class _Transaction:
    def __init__(self, values: dict[str, dict[str, Any]]):
        self.values = values

    def get(self, document: _Document):
        return iter([_Snapshot(self.values.get(document.key))])

    def create(self, document: _Document, value: dict[str, Any]) -> None:
        if document.key in self.values:
            raise RuntimeError("create collision")
        self.values[document.key] = dict(value)

    def delete(self, document: _Document) -> None:
        self.values.pop(document.key, None)

    def set(self, document: _Document, value: dict[str, Any]) -> None:
        self.values[document.key] = dict(value)


class _Client:
    def __init__(self):
        self.values: dict[str, dict[str, Any]] = {}
        self.guard = threading.Lock()

    def collection(self, name: str) -> _Collection:
        return _Collection(name)


class _GeneratorShapeTransaction:
    def __init__(self, snapshots: list[object]):
        self.snapshots = snapshots
        self.requested_document: object | None = None

    def get(self, document: object):
        self.requested_document = document
        yield from self.snapshots


def _atomic_runner(client: _Client, callback: Callable[[Any], str]) -> str:
    with client.guard:
        return callback(_Transaction(client.values))


@pytest.fixture(autouse=True)
def _fake_transaction_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_lock, "_run_transaction", _atomic_runner)


def test_transaction_snapshot_consumes_the_firestore_generator_shape() -> None:
    document = object()
    expected = _Snapshot(None)
    transaction = _GeneratorShapeTransaction([expected])

    observed = release_lock._transaction_snapshot(transaction, document)

    assert observed is expected
    assert transaction.requested_document is document


@pytest.mark.parametrize("snapshots", [[], [_Snapshot(None), _Snapshot(None)]])
def test_transaction_snapshot_fails_closed_on_non_unique_generator_results(
    snapshots: list[object],
) -> None:
    transaction = _GeneratorShapeTransaction(snapshots)

    with pytest.raises(RuntimeError, match="no document|duplicate document"):
        release_lock._transaction_snapshot(transaction, object())


def test_different_candidates_racing_for_one_service_have_one_winner() -> None:
    client = _Client()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt(identity: dict[str, str]) -> None:
        barrier.wait()
        try:
            results.append(release_lock.acquire_release_lock(client, identity))
        except release_lock.ReleaseLockConflict:
            results.append("conflict")

    threads = [
        threading.Thread(target=attempt, args=(_identity(),)),
        threading.Thread(
            target=attempt,
            args=(_identity(target="monitor-revision-b", intent="b" * 64),),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["acquired", "conflict"]
    assert len(client.values) == 1


def test_same_intent_conflicts_until_a_final_only_recovery() -> None:
    client = _Client()
    identity = _identity()

    assert release_lock.acquire_release_lock(client, identity) == "acquired"
    with pytest.raises(release_lock.ReleaseLockConflict, match="already active"):
        release_lock.acquire_release_lock(client, identity)
    assert (
        release_lock.acquire_release_lock(
            client,
            identity,
            allow_final_recovery=True,
        )
        == "recovered"
    )
    assert release_lock.release_release_lock(client, identity) == "released"
    assert release_lock.release_release_lock(client, identity) == "already_released"


def test_aborted_pre_tombstone_rejects_same_intent_but_allows_a_new_intent() -> None:
    client = _Client()
    retired = _identity()
    replacement = _identity(target="monitor-revision-b", intent="b" * 64)

    assert release_lock.acquire_release_lock(client, retired) == "acquired"
    assert release_lock.retire_release_lock(
        client,
        retired,
        disposition="aborted_pre",
    ) == "retired_aborted_pre"
    with pytest.raises(release_lock.ReleaseLockConflict, match="retired"):
        release_lock.acquire_release_lock(client, retired)
    assert release_lock.acquire_release_lock(client, replacement) == (
        "acquired_after_retired_intent"
    )


def test_authorized_post_tombstone_is_consumable_once_by_the_exact_intent() -> None:
    client = _Client()
    identity = _identity()

    assert release_lock.acquire_release_lock(client, identity) == "acquired"
    assert release_lock.retire_release_lock(
        client,
        identity,
        disposition="authorized_post_recovery",
    ) == "retired_authorized_post_recovery"
    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.acquire_release_lock(client, identity)
    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.acquire_release_lock(
            client,
            _identity(target="monitor-revision-b", intent="b" * 64),
            allow_post_recovery=True,
        )
    assert release_lock.acquire_release_lock(
        client,
        identity,
        allow_post_recovery=True,
    ) == "recovered_post_intent"
    with pytest.raises(release_lock.ReleaseLockConflict, match="already active"):
        release_lock.acquire_release_lock(
            client,
            identity,
            allow_post_recovery=True,
        )


def test_post_recovery_cannot_start_without_an_authorized_tombstone() -> None:
    client = _Client()

    with pytest.raises(release_lock.ReleaseLockConflict, match="authorized intent tombstone"):
        release_lock.acquire_release_lock(
            client,
            _identity(),
            allow_post_recovery=True,
        )
    assert client.values == {}


def test_two_same_intent_pre_holders_have_one_winner_and_cannot_clear_it() -> None:
    client = _Client()
    identity = _identity()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt() -> None:
        barrier.wait()
        try:
            results.append(release_lock.acquire_release_lock(client, identity))
        except release_lock.ReleaseLockConflict:
            results.append("conflict")

    threads = [threading.Thread(target=attempt), threading.Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["acquired", "conflict"]
    assert len(client.values) == 1


def test_different_target_cannot_release_or_replace_an_existing_lock() -> None:
    client = _Client()
    owner = _identity()
    contender = _identity(target="monitor-revision-b", intent="b" * 64)
    assert release_lock.acquire_release_lock(client, owner) == "acquired"

    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.acquire_release_lock(client, contender)
    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.release_release_lock(client, contender)

    assert release_lock.release_release_lock(client, owner) == "released"


def test_old_lock_is_never_auto_expired_or_stolen() -> None:
    client = _Client()
    owner = _identity()
    assert release_lock.acquire_release_lock(client, owner) == "acquired"
    document = next(iter(client.values.values()))
    document["acquiredAt"] = "2000-01-01T00:00:00Z"

    with pytest.raises(release_lock.ReleaseLockConflict, match="already active"):
        release_lock.acquire_release_lock(client, owner)
    assert (
        release_lock.acquire_release_lock(
            client,
            owner,
            allow_final_recovery=True,
        )
        == "recovered"
    )
    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.acquire_release_lock(
            client,
            _identity(target="monitor-revision-b", intent="b" * 64),
        )


def test_unrecognized_lock_fields_fail_closed() -> None:
    client = _Client()
    owner = _identity()
    assert release_lock.acquire_release_lock(client, owner) == "acquired"
    next(iter(client.values.values()))["leaseExpiresAt"] = "soon"

    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.acquire_release_lock(client, owner)
    with pytest.raises(release_lock.ReleaseLockConflict):
        release_lock.release_release_lock(client, owner)
