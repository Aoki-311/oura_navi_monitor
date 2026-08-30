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

    @property
    def id(self) -> str:
        return self._reference.document_id

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

    def update(self, reference: _DocumentReference, payload: dict[str, Any]) -> None:
        if reference.document_id not in reference.store:
            raise ValueError("document not found")
        reference.store[reference.document_id].update(dict(payload))

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
        "updated_by": "system@example.com",
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


def test_repository_read_preserves_firestore_document_id_for_roster_repair() -> None:
    repository, client = _repository()
    client.data.setdefault("monitor_users", {})["firestore_doc"] = {
        "name": "repair me",
        "email": "repair@example.com",
        "is_active": True,
    }

    payload = repository.get_user("firestore_doc")

    assert payload is not None
    assert payload["_document_id"] == "firestore_doc"
    assert "roster_id" not in payload


def test_find_user_by_email_normalizes_the_collection_and_rejects_ambiguity() -> None:
    repository, client = _repository()
    client.data.setdefault("monitor_users", {}).update(
        {
            "roster_a": _user("roster_a", " Same@Example.com "),
            "roster_b": _user("roster_b", "same@example.COM"),
        }
    )

    with pytest.raises(ManagementError) as captured:
        repository.find_user_by_email("same@example.com")

    assert captured.value.code == "duplicate_email"
    del client.data["monitor_users"]["roster_b"]
    match = repository.find_user_by_email(" SAME@example.com ")
    assert match is not None
    assert match["_document_id"] == "roster_a"


def test_label_read_preserves_document_id_without_persisting_internal_fields() -> None:
    repository, client = _repository()
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    repository.put_label(
        {
            "label_id": "label_a",
            "name": "重点",
            "color": "#23d28f",
            "is_active": True,
            "usage_count": 99,
            "updated_at": now,
        }
    )

    payload = repository.get_label("label_a")

    assert payload is not None
    assert payload["_document_id"] == "label_a"
    stored = client.data["monitor_labels"]["label_a"]
    assert "_document_id" not in stored
    assert "usage_count" not in stored


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


def test_identity_binding_patches_only_identity_fields_and_preserves_a_racing_admin_edit() -> None:
    repository, client = _repository()
    original = _user("roster_a", "a@example.com")
    original["role"] = "本社MR"
    repository.put_user(original)

    racing_revision = datetime(2026, 8, 25, tzinfo=timezone.utc)
    client.data["monitor_users"]["roster_a"].update({
        "role": "コントラクトMR",
        "label_ids": ["label_new"],
        "updated_at": racing_revision,
        "updated_by": "admin@example.com",
    })
    bound_at = datetime(2026, 8, 25, 1, tzinfo=timezone.utc)
    result = repository.bind_user_identity(
        "roster_a",
        chat_user_id="chat_a",
        user_id="subject_a",
        bound_at=bound_at,
        change=_change("identity_change", "identity_bind", "roster_a"),
    )

    assert result["role"] == "コントラクトMR"
    assert result["label_ids"] == ["label_new"]
    stored = client.data["monitor_users"]["roster_a"]
    assert stored["role"] == "コントラクトMR"
    assert stored["label_ids"] == ["label_new"]
    assert stored["updated_at"] == bound_at
    assert stored["updated_by"] == "system@example.com"
    assert stored["chat_user_id"] == "chat_a"
    assert stored["user_id"] == "subject_a"
    assert client.data["monitor_admin_changes"]["identity_change"]["action"] == "identity_bind"


def test_stale_admin_update_after_identity_bind_conflicts_without_losing_identity_claims() -> None:
    repository, client = _repository()
    original = _user("roster_a", "a@example.com")
    repository.put_user(original)
    stale_admin_payload = {
        **original,
        "name": "stale admin edit",
        "updated_at": datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
        "updated_by": "admin@example.com",
    }
    bound_at = datetime(2026, 8, 25, 1, tzinfo=timezone.utc)

    repository.bind_user_identity(
        "roster_a",
        chat_user_id="chat_a",
        user_id="subject_a",
        bound_at=bound_at,
        change=_change("identity_first", "identity_bind", "roster_a"),
    )

    with pytest.raises(ManagementError) as captured:
        repository.put_user_and_change(
            stale_admin_payload,
            _change("stale_admin", "update", "roster_a"),
            expected_updated_at=revision_text(original["updated_at"]),
        )

    assert captured.value.code == "update_conflict"
    stored = client.data["monitor_users"]["roster_a"]
    assert stored["name"] == "roster_a"
    assert stored["chat_user_id"] == "chat_a"
    assert stored["user_id"] == "subject_a"
    assert stored["updated_at"] == bound_at
    identity_claims = {
        (value["claim_type"], value["target_id"])
        for value in client.data["monitor_unique_claims"].values()
        if value["claim_type"] in {"chat_user_id", "user_id"}
    }
    assert identity_claims == {
        ("chat_user_id", "roster_a"),
        ("user_id", "roster_a"),
    }
    assert "stale_admin" not in client.data.get("monitor_admin_changes", {})


def test_admin_transaction_preserves_current_identity_and_rechecks_bound_email() -> None:
    repository, client = _repository()
    original = _user("roster_a", "a@example.com")
    repository.put_user(original)
    bound_at = datetime(2026, 8, 25, 1, tzinfo=timezone.utc)
    repository.bind_user_identity(
        "roster_a",
        chat_user_id="chat_a",
        user_id="subject_a",
        bound_at=bound_at,
        change=_change("identity_owner", "identity_bind", "roster_a"),
    )

    stale_identity_payload = {
        **original,
        "name": "safe admin edit",
        "updated_at": datetime(2026, 8, 25, 2, tzinfo=timezone.utc),
        "updated_by": "admin@example.com",
    }
    result = repository.put_user_and_change(
        stale_identity_payload,
        _change("safe_admin", "update", "roster_a"),
        expected_updated_at=revision_text(bound_at),
    )
    assert result["chat_user_id"] == "chat_a"
    assert result["user_id"] == "subject_a"

    bound_revision = result["updated_at"]
    with pytest.raises(ManagementError) as captured:
        repository.put_user_and_change(
            {
                **result,
                "email": "changed@example.com",
                "updated_at": datetime(2026, 8, 25, 3, tzinfo=timezone.utc),
            },
            _change("bound_email", "update", "roster_a"),
            expected_updated_at=revision_text(bound_revision),
        )

    assert captured.value.code == "bound_email"
    stored = client.data["monitor_users"]["roster_a"]
    assert stored["email"] == "a@example.com"
    assert stored["chat_user_id"] == "chat_a"
    assert stored["user_id"] == "subject_a"
    assert "bound_email" not in client.data.get("monitor_admin_changes", {})


def test_identity_binding_rejects_a_legacy_duplicate_even_without_a_claim_document() -> None:
    repository, client = _repository()
    repository.put_user(_user("roster_a", "a@example.com"))
    legacy = _user("roster_legacy", "legacy@example.com", "subject_legacy")
    legacy["chat_user_id"] = "chat_legacy"
    client.data.setdefault("monitor_users", {})["roster_legacy"] = legacy

    with pytest.raises(ManagementError) as captured:
        repository.bind_user_identity(
            "roster_a",
            chat_user_id="chat_legacy",
            user_id="subject_new",
            bound_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            change=_change("identity_duplicate", "identity_bind", "roster_a"),
        )

    assert captured.value.code == "duplicate_identity"
    assert client.data["monitor_users"]["roster_a"]["chat_user_id"] == ""
    assert "identity_duplicate" not in client.data.get("monitor_admin_changes", {})


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
