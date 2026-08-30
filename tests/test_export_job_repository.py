from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.repositories.export_jobs import ExportJobRepository
from app.settings import Settings


class _Snapshot:
    def __init__(self, reference: "_DocumentReference") -> None:
        self.reference = reference

    @property
    def exists(self) -> bool:
        return self.reference.document_id in self.reference.store

    def to_dict(self) -> dict[str, Any] | None:
        value = self.reference.store.get(self.reference.document_id)
        return dict(value) if value is not None else None


class _DocumentReference:
    def __init__(self, store: dict[str, dict[str, Any]], document_id: str) -> None:
        self.store = store
        self.document_id = document_id

    def get(self, *, transaction: Any | None = None) -> _Snapshot:
        del transaction
        return _Snapshot(self)

    def set(self, payload: dict[str, Any]) -> None:
        self.store[self.document_id] = dict(payload)

    def delete(self) -> None:
        self.store.pop(self.document_id, None)


class _Collection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store

    def document(self, document_id: str) -> _DocumentReference:
        return _DocumentReference(self.store, document_id)


class _Transaction:
    @staticmethod
    def set(reference: _DocumentReference, payload: dict[str, Any]) -> None:
        reference.set(payload)


class _Client:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def collection(self, _name: str) -> _Collection:
        return _Collection(self.store)

    @staticmethod
    def transaction() -> _Transaction:
        return _Transaction()


@pytest.fixture(autouse=True)
def _execute_transaction_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.repositories.export_jobs.firestore.transactional",
        lambda function: function,
    )


def test_idempotent_export_keeps_live_job_and_only_replaces_expired_job() -> None:
    client = _Client()
    repository = ExportJobRepository(
        Settings(monitor_project_id="test-project"),
        client=client,
    )
    now = datetime.now(timezone.utc)
    first = {
        "job_id": "export_same",
        "filename": "first.csv",
        "expires_at": now + timedelta(hours=1),
    }
    competing = {
        "job_id": "export_same",
        "filename": "competing.csv",
        "expires_at": now + timedelta(hours=1),
    }

    assert repository.put_idempotent(first)["filename"] == "first.csv"
    assert repository.put_idempotent(competing)["filename"] == "first.csv"
    assert repository.get("export_same")["filename"] == "first.csv"

    client.store["export_same"]["expires_at"] = now - timedelta(seconds=1)
    assert repository.put_idempotent(competing)["filename"] == "competing.csv"
    assert repository.get("export_same")["filename"] == "competing.csv"

    repository.delete("export_same")
    assert repository.get("export_same") is None
