from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.user_directory import UserDirectoryRepository
from app.domain.management_errors import ManagementError, revision_text
from app.settings import Settings


class _Snapshot:
    def __init__(self, reference: "_DocumentReference") -> None:
        self._reference = reference

    @property
    def exists(self) -> bool:
        return self._reference.document_id in self._reference.store

    def to_dict(self) -> dict[str, Any] | None:
        value = self._reference.store.get(self._reference.document_id)
        return dict(value) if value is not None else None


class _DocumentReference:
    def __init__(self, client: "_Client", collection: str, document_id: str) -> None:
        self._client = client
        self.collection = collection
        self.document_id = document_id

    @property
    def store(self) -> dict[str, dict[str, Any]]:
        return self._client.data.setdefault(self.collection, {})

    def get(self, *, transaction: Any | None = None) -> _Snapshot:
        del transaction
        return _Snapshot(self)

    def delete(self) -> None:
        self.store.pop(self.document_id, None)


class _Query:
    def __init__(
        self,
        client: "_Client",
        collection: str,
        *,
        filters: list[Any] | None = None,
        limit_count: int | None = None,
    ) -> None:
        self._client = client
        self._collection = collection
        self._filters = list(filters or [])
        self._limit_count = limit_count

    def where(self, *, filter: Any) -> "_Query":
        return _Query(
            self._client,
            self._collection,
            filters=[*self._filters, filter],
            limit_count=self._limit_count,
        )

    def limit(self, count: int) -> "_Query":
        return _Query(
            self._client,
            self._collection,
            filters=self._filters,
            limit_count=count,
        )

    def stream(self) -> list[_Snapshot]:
        snapshots: list[_Snapshot] = []
        for document_id, payload in self._client.data.get(self._collection, {}).items():
            matched = True
            for condition in self._filters:
                actual = payload.get(condition.field_path)
                if condition.op_string == "==":
                    matched = actual == condition.value
                elif condition.op_string == "array_contains":
                    matched = condition.value in list(actual or [])
                else:  # pragma: no cover - repository uses only these operators
                    raise AssertionError(f"unsupported fake query operator: {condition.op_string}")
                if not matched:
                    break
            if matched:
                snapshots.append(
                    _Snapshot(_DocumentReference(self._client, self._collection, document_id))
                )
            if self._limit_count is not None and len(snapshots) >= self._limit_count:
                break
        return snapshots


class _Collection(_Query):
    def __init__(self, client: "_Client", collection: str) -> None:
        super().__init__(client, collection)

    def document(self, document_id: str) -> _DocumentReference:
        return _DocumentReference(self._client, self._collection, document_id)


class _Transaction:
    def set(self, reference: _DocumentReference, payload: dict[str, Any]) -> None:
        reference.store[reference.document_id] = dict(payload)

    def delete(self, reference: _DocumentReference) -> None:
        reference.store.pop(reference.document_id, None)

    def get(self, query: _Query) -> list[_Snapshot]:
        return query.stream()


class _Client:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self, name)

    @staticmethod
    def transaction() -> _Transaction:
        return _Transaction()


@pytest.fixture(autouse=True)
def _execute_transaction_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.repositories.user_directory.firestore.transactional",
        lambda function: function,
    )


def _repository() -> tuple[UserDirectoryRepository, _Client]:
    client = _Client()
    settings = Settings(
        monitor_project_id="test-project",
    )
    return UserDirectoryRepository(settings, client=client), client


def _user(
    roster_id: str,
    email: str,
    user_id: str = "",
    *,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return {
        "roster_id": roster_id,
        "email": email,
        "name": roster_id,
        "user_id": user_id,
        "chat_user_id": "",
        "label_ids": list(label_ids or []),
        "is_active": True,
        "updated_at": now,
    }


def _change(change_id: str, action: str, target_id: str) -> dict[str, Any]:
    return {
        "change_id": change_id,
        "action": action,
        "target_type": "user",
        "target_id": target_id,
        "updated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }


def test_user_claims_and_audit_are_written_in_the_same_transaction() -> None:
    repository, client = _repository()
    payload = _user("roster_a", "a@example.com", "subject_a")

    repository.put_user_and_change(
        payload,
        _change("change_a", "create", "roster_a"),
    )

    assert client.data["monitor_users"]["roster_a"]["email"] == "a@example.com"
    assert client.data["monitor_admin_changes"]["change_a"]["target_id"] == "roster_a"
    claims = list(client.data["monitor_unique_claims"].values())
    assert {(item["claim_type"], item["target_id"]) for item in claims} == {
        ("email", "roster_a"),
        ("user_id", "roster_a"),
    }


def test_user_document_keeps_only_the_canonical_directory_contract() -> None:
    repository, client = _repository()
    payload = _user("roster_a", "a@example.com", "subject_a")
    payload.update(
        {
            "user_key": "obsolete-key",
            "identity_keys": [{"user_key": "obsolete-key"}],
            "login_subject": "obsolete-subject",
            "global_scope_enabled": True,
        }
    )

    repository.put_user(payload)

    stored = client.data["monitor_users"]["roster_a"]
    assert stored["user_id"] == "subject_a"
    assert "user_key" not in stored
    assert "identity_keys" not in stored
    assert "login_subject" not in stored
    assert "global_scope_enabled" not in stored


def test_user_claim_rejects_a_second_roster_even_if_service_precheck_races() -> None:
    repository, _client = _repository()
    repository.put_user(_user("roster_a", "same@example.com", "subject_same"))

    with pytest.raises(ValueError, match="already exists"):
        repository.put_user(
            _user("roster_b", "same@example.com", "subject_same")
        )


def test_verified_user_id_claim_stays_reserved_after_email_change() -> None:
    repository, client = _repository()
    repository.put_user(_user("roster_a", "old@example.com", "subject_a"))
    repository.put_user(
        _user("roster_a", "new@example.com", "subject_a")
    )

    with pytest.raises(ValueError, match="already exists"):
        repository.put_user(
            _user("roster_b", "other@example.com", "subject_a")
        )
    user_id_claims = [
        value
        for value in client.data["monitor_unique_claims"].values()
        if value["claim_type"] == "user_id"
    ]
    assert len(user_id_claims) == 1
    assert {value["target_id"] for value in user_id_claims} == {"roster_a"}
    email_claims = [
        value
        for value in client.data["monitor_unique_claims"].values()
        if value["claim_type"] == "email"
    ]
    assert len(email_claims) == 1


def test_user_update_revision_is_checked_inside_transaction() -> None:
    repository, client = _repository()
    current = _user("roster_a", "a@example.com")
    repository.put_user_and_change(
        current,
        _change("change_create", "create", "roster_a"),
    )
    changed = {**current, "name": "updated", "updated_at": datetime(2026, 8, 25, tzinfo=timezone.utc)}

    with pytest.raises(ManagementError) as captured:
        repository.put_user_and_change(
            changed,
            _change("change_stale", "update", "roster_a"),
            expected_updated_at="stale-revision",
        )
    assert captured.value.code == "update_conflict"
    assert client.data["monitor_users"]["roster_a"]["name"] == "roster_a"
    assert "change_stale" not in client.data.get("monitor_admin_changes", {})

    repository.put_user_and_change(
        changed,
        _change("change_current", "update", "roster_a"),
        expected_updated_at=revision_text(current["updated_at"]),
    )
    assert client.data["monitor_users"]["roster_a"]["name"] == "updated"


def test_label_name_claim_is_normalized_and_in_use_delete_is_atomic() -> None:
    repository, client = _repository()
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    repository.put_label(
        {
            "label_id": "label_a",
            "name": "ABC",
            "is_active": True,
            "updated_at": now,
        }
    )
    with pytest.raises(ValueError, match="label name already exists"):
        repository.put_label(
            {
                "label_id": "label_b",
                "name": "abc",
                "is_active": True,
                "updated_at": now,
            }
        )

    repository.put_user(
        _user("roster_a", "a@example.com", "subject_a", label_ids=["label_a"])
    )
    with pytest.raises(ValueError, match="label is in use"):
        repository.delete_label_and_change(
            "label_a",
            {
                "change_id": "change_delete",
                "action": "delete",
                "target_type": "label",
                "target_id": "label_a",
                "updated_at": now,
            },
            expected_updated_at=revision_text(now),
        )
    assert "label_a" in client.data["monitor_labels"]
    assert "change_delete" not in client.data.get("monitor_admin_changes", {})


def test_label_revision_is_rechecked_inside_update_and_delete_transactions() -> None:
    repository, client = _repository()
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    label = {
        "label_id": "label_a",
        "name": "初期",
        "is_active": True,
        "updated_at": now,
    }
    repository.put_label(label)
    changed = {**label, "name": "更新", "updated_at": datetime(2026, 8, 25, tzinfo=timezone.utc)}

    with pytest.raises(ManagementError) as captured:
        repository.put_label_and_change(
            changed,
            {**_change("label_change", "update", "label_a"), "target_type": "label"},
            expected_updated_at="stale",
        )
    assert captured.value.code == "update_conflict"
    assert client.data["monitor_labels"]["label_a"]["name"] == "初期"

    repository.put_label_and_change(
        changed,
        {**_change("label_change_current", "update", "label_a"), "target_type": "label"},
        expected_updated_at=revision_text(now),
    )
    with pytest.raises(ManagementError) as captured:
        repository.delete_label_and_change(
            "label_a",
            {**_change("label_delete_stale", "delete", "label_a"), "target_type": "label"},
            expected_updated_at=revision_text(now),
        )
    assert captured.value.code == "update_conflict"
    assert "label_a" in client.data["monitor_labels"]
