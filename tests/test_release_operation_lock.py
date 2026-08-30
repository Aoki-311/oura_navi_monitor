from __future__ import annotations

import threading
from typing import Any, Callable

import pytest

from scripts import release_operation_lock as lock


def _identity(*, kind: str, intent: str) -> dict[str, str]:
    return {
        "project": "test-project",
        "region": "us-central1",
        "resourceKey": "refresh-chain:job:old:new",
        "operationKind": kind,
        "targetKey": "exact-target",
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
        self.values[document.key] = dict(value)

    def delete(self, document: _Document) -> None:
        self.values.pop(document.key, None)


class _Client:
    def __init__(self):
        self.values: dict[str, dict[str, Any]] = {}
        self.guard = threading.Lock()

    def collection(self, name: str) -> _Collection:
        return _Collection(name)


@pytest.fixture(autouse=True)
def _atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(client: _Client, callback: Callable[[Any], str]) -> str:
        with client.guard:
            return callback(_Transaction(client.values))

    monkeypatch.setattr(lock, "_transaction", run)


def test_scheduler_activation_and_dts_pause_share_one_global_cas_domain() -> None:
    client = _Client()
    scheduler = _identity(kind="scheduler-activation", intent="a" * 64)
    dts = _identity(kind="legacy-dts-pause", intent="b" * 64)

    assert lock.acquire_operation_lock(client, scheduler) == "acquired"
    with pytest.raises(lock.OperationLockConflict):
        lock.acquire_operation_lock(client, dts)

    assert lock.release_operation_lock(client, scheduler) == "released"
    assert lock.acquire_operation_lock(client, dts) == "acquired"


def test_different_local_receipt_intent_cannot_steal_or_release_lock() -> None:
    client = _Client()
    owner = _identity(kind="legacy-dts-pause", intent="a" * 64)
    other_path = _identity(kind="legacy-dts-pause", intent="b" * 64)

    assert lock.acquire_operation_lock(client, owner) == "acquired"
    assert lock.acquire_operation_lock(client, owner) == "recovered"
    with pytest.raises(lock.OperationLockConflict):
        lock.acquire_operation_lock(client, other_path)
    with pytest.raises(lock.OperationLockConflict):
        lock.release_operation_lock(client, other_path)


def test_lock_never_auto_expires_and_unknown_fields_fail_closed() -> None:
    client = _Client()
    owner = _identity(kind="scheduler-activation", intent="a" * 64)
    assert lock.acquire_operation_lock(client, owner) == "acquired"
    document = next(iter(client.values.values()))
    document["acquiredAt"] = "2000-01-01T00:00:00Z"
    assert lock.acquire_operation_lock(client, owner) == "recovered"
    document["leaseExpiresAt"] = "soon"
    with pytest.raises(lock.OperationLockConflict):
        lock.acquire_operation_lock(client, owner)


def test_only_governed_named_database_and_collection_are_accepted() -> None:
    client = _Client()
    identity = _identity(kind="scheduler-activation", intent="a" * 64)
    identity["firestoreDatabase"] = "(default)"
    with pytest.raises(ValueError, match="named database"):
        lock.acquire_operation_lock(client, identity)
