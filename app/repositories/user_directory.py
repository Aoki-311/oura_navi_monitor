from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.domain.management_errors import ManagementError, revision_text
from app.settings import Settings


_USER_DOCUMENT_FIELDS = frozenset(
    {
        "roster_id",
        "user_id",
        "chat_user_id",
        "identity_bound_at",
        "name",
        "email",
        "area",
        "workplace",
        "area_key",
        "role",
        "department",
        "mr_experience",
        "label_ids",
        "is_active",
        "created_at",
        "updated_at",
        "updated_by",
    }
)


class UserDirectoryRepository:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        database = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self._client = client or firestore.Client(project=settings.monitor_project_id, database=database)
        self._users = settings.monitor_firestore_user_collection
        self._labels = settings.monitor_firestore_label_collection
        self._changes = settings.monitor_firestore_admin_change_collection
        self._claims = settings.monitor_firestore_unique_claim_collection

    def _user_collection(self):
        return self._client.collection(self._users)

    def _label_collection(self):
        return self._client.collection(self._labels)

    def _claim_collection(self):
        return self._client.collection(self._claims)

    @staticmethod
    def _claim_document_id(kind: str, value: str) -> str:
        normalized = str(value or "").strip()
        digest = hashlib.sha256(
            f"{kind}|{normalized}".encode("utf-8")
        ).hexdigest()
        return f"{kind}_{digest}"

    @staticmethod
    def _user_claim_values(payload: dict[str, Any]) -> set[tuple[str, str]]:
        values: set[tuple[str, str]] = set()
        email = str(payload.get("email") or "").strip().lower()
        if email:
            values.add(("email", email))
        for kind, field in (
            ("chat_user_id", "chat_user_id"),
            ("user_id", "user_id"),
        ):
            value = str(payload.get(field) or "").strip()
            if value:
                values.add((kind, value))
        return values

    @staticmethod
    def _label_claim_value(payload: dict[str, Any]) -> str:
        return unicodedata.normalize(
            "NFKC", str(payload.get("name") or "")
        ).strip().casefold()

    @staticmethod
    def _payload(document: Any) -> dict[str, Any]:
        payload = dict(document.to_dict() or {})
        return payload

    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        query = self._user_collection()
        if not include_inactive:
            query = query.where(filter=FieldFilter("is_active", "==", True))
        users = [self._payload(document) for document in query.stream()]
        return sorted(users, key=lambda item: (str(item.get("area") or ""), str(item.get("name") or "")))

    def get_user(self, roster_id: str) -> dict[str, Any] | None:
        document = self._user_collection().document(str(roster_id)).get()
        return self._payload(document) if document.exists else None

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        query = self._user_collection().where(filter=FieldFilter("email", "==", str(email))).limit(1)
        document = next(iter(query.stream()), None)
        return self._payload(document) if document is not None else None

    def put_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return self._put_user_transaction(user, change=None)

    def _put_user_transaction(
        self,
        user: dict[str, Any],
        *,
        change: dict[str, Any] | None,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        roster_id = str(user.get("roster_id") or "").strip()
        if not roster_id:
            raise ValueError("roster_id is required")
        payload = {
            key: value
            for key, value in dict(user).items()
            if key in _USER_DOCUMENT_FIELDS
        }
        user_ref = self._user_collection().document(roster_id)
        change_id = str((change or {}).get("change_id") or "").strip()
        action = str((change or {}).get("action") or "").strip()
        if change is not None and not change_id:
            raise ValueError("change_id is required")
        new_claim_refs = {
            (kind, value): self._claim_collection().document(
                self._claim_document_id(kind, value)
            )
            for kind, value in self._user_claim_values(payload)
        }

        @firestore.transactional
        def commit(transaction: Any) -> None:
            current = user_ref.get(transaction=transaction)
            current_payload = dict(current.to_dict() or {}) if current.exists else {}
            old_claim_refs = {
                (kind, value): self._claim_collection().document(
                    self._claim_document_id(kind, value)
                )
                for kind, value in self._user_claim_values(current_payload)
            }
            all_claim_refs = {**old_claim_refs, **new_claim_refs}
            claim_snapshots = {
                key: ref.get(transaction=transaction)
                for key, ref in all_claim_refs.items()
            }
            label_snapshots = {
                label_id: self._label_collection().document(label_id).get(
                    transaction=transaction
                )
                for label_id in list(payload.get("label_ids") or [])
            }
            if action == "create" and current.exists:
                raise ValueError("user already exists")
            if action == "update" and not current.exists:
                raise ValueError("user not found")
            if action == "update" and revision_text(current_payload.get("updated_at")) != str(
                expected_updated_at or ""
            ).strip():
                raise ManagementError("update_conflict", "user update conflict")
            existing_label_ids = set(current_payload.get("label_ids") or [])
            for snapshot in label_snapshots.values():
                label = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
                label_id = str(label.get("label_id") or "")
                if not snapshot.exists or (
                    label.get("is_active") is not True and label_id not in existing_label_ids
                ):
                    raise ManagementError("invalid_roster_value", "unknown or inactive labels")
            for (kind, value), snapshot in claim_snapshots.items():
                if (kind, value) not in new_claim_refs:
                    continue
                claim = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
                if snapshot.exists and str(claim.get("target_id") or "") != roster_id:
                    code = "duplicate_email" if kind == "email" else "duplicate_identity"
                    raise ManagementError(code, f"{kind} already exists")
            for claim_key, ref in old_claim_refs.items():
                if claim_key in new_claim_refs:
                    continue
                snapshot = claim_snapshots[claim_key]
                claim = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
                if snapshot.exists and str(claim.get("target_id") or "") == roster_id:
                    transaction.delete(ref)
            for (kind, _value), ref in new_claim_refs.items():
                transaction.set(
                    ref,
                    {
                        "claim_type": kind,
                        "target_id": roster_id,
                        "updated_at": payload.get("updated_at"),
                    },
                )
            transaction.set(user_ref, payload)
            if change is not None:
                transaction.set(
                    self._client.collection(self._changes).document(change_id),
                    dict(change),
                )

        commit(self._client.transaction())
        return payload

    def put_user_and_change(
        self,
        user: dict[str, Any],
        change: dict[str, Any],
        *,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        return self._put_user_transaction(
            user,
            change=change,
            expected_updated_at=expected_updated_at,
        )

    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        query = self._label_collection()
        if not include_inactive:
            query = query.where(filter=FieldFilter("is_active", "==", True))
        labels = [self._payload(document) for document in query.stream()]
        for label in labels:
            label["usage_count"] = self.label_usage_count(str(label.get("label_id") or ""))
        return sorted(labels, key=lambda item: str(item.get("name") or ""))

    def get_label(self, label_id: str) -> dict[str, Any] | None:
        document = self._label_collection().document(str(label_id)).get()
        return self._payload(document) if document.exists else None

    def put_label(self, label: dict[str, Any]) -> dict[str, Any]:
        return self._put_label_transaction(label, change=None)

    def _put_label_transaction(
        self,
        label: dict[str, Any],
        *,
        change: dict[str, Any] | None,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        label_id = str(label.get("label_id") or "").strip()
        if not label_id:
            raise ValueError("label_id is required")
        payload = dict(label)
        name_claim = self._label_claim_value(payload)
        if not name_claim:
            raise ValueError("label name is required")
        label_ref = self._label_collection().document(label_id)
        new_claim_ref = self._claim_collection().document(
            self._claim_document_id("label_name", name_claim)
        )
        change_id = str((change or {}).get("change_id") or "").strip()
        action = str((change or {}).get("action") or "").strip()
        if change is not None and not change_id:
            raise ValueError("change_id is required")

        @firestore.transactional
        def commit(transaction: Any) -> None:
            current = label_ref.get(transaction=transaction)
            new_claim = new_claim_ref.get(transaction=transaction)
            current_payload = dict(current.to_dict() or {}) if current.exists else {}
            old_name_claim = self._label_claim_value(current_payload)
            old_claim_ref = (
                self._claim_collection().document(
                    self._claim_document_id("label_name", old_name_claim)
                )
                if old_name_claim and old_name_claim != name_claim
                else None
            )
            old_claim = (
                old_claim_ref.get(transaction=transaction)
                if old_claim_ref is not None
                else None
            )
            if action == "create" and current.exists:
                raise ValueError("label already exists")
            if action == "update" and not current.exists:
                raise ValueError("label not found")
            if action == "update" and revision_text(current_payload.get("updated_at")) != str(
                expected_updated_at or ""
            ).strip():
                raise ManagementError("update_conflict", "label update conflict")
            if new_claim.exists and str((new_claim.to_dict() or {}).get("target_id") or "") != label_id:
                raise ManagementError("duplicate_label", "label name already exists")
            if old_claim_ref is not None and old_claim is not None and old_claim.exists:
                if str((old_claim.to_dict() or {}).get("target_id") or "") == label_id:
                    transaction.delete(old_claim_ref)
            transaction.set(
                new_claim_ref,
                {
                    "claim_type": "label_name",
                    "target_id": label_id,
                    "updated_at": payload.get("updated_at"),
                },
            )
            transaction.set(label_ref, payload)
            if change is not None:
                transaction.set(
                    self._client.collection(self._changes).document(change_id),
                    dict(change),
                )

        commit(self._client.transaction())
        return payload

    def put_label_and_change(
        self,
        label: dict[str, Any],
        change: dict[str, Any],
        *,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        return self._put_label_transaction(
            label,
            change=change,
            expected_updated_at=expected_updated_at,
        )

    def delete_label(self, label_id: str) -> None:
        self._label_collection().document(str(label_id)).delete()

    def delete_label_and_change(
        self,
        label_id: str,
        change: dict[str, Any],
        *,
        expected_updated_at: str,
    ) -> None:
        resolved_label_id = str(label_id or "").strip()
        change_id = str(change.get("change_id") or "").strip()
        if not resolved_label_id or not change_id:
            raise ValueError("label_id and change_id are required")
        label_ref = self._label_collection().document(resolved_label_id)

        @firestore.transactional
        def commit(transaction: Any) -> None:
            current = label_ref.get(transaction=transaction)
            if not current.exists:
                raise ValueError("label not found")
            payload = dict(current.to_dict() or {})
            if revision_text(payload.get("updated_at")) != str(
                expected_updated_at or ""
            ).strip():
                raise ManagementError("update_conflict", "label delete conflict")
            name_claim = self._label_claim_value(payload)
            claim_ref = self._claim_collection().document(
                self._claim_document_id("label_name", name_claim)
            )
            claim = claim_ref.get(transaction=transaction)
            usage_query = self._user_collection().where(
                filter=FieldFilter(
                    "label_ids", "array_contains", resolved_label_id
                )
            ).limit(1)
            if any(True for _document in transaction.get(usage_query)):
                raise ManagementError("label_in_use", "label is in use")
            transaction.delete(label_ref)
            if claim.exists and str((claim.to_dict() or {}).get("target_id") or "") == resolved_label_id:
                transaction.delete(claim_ref)
            transaction.set(
                self._client.collection(self._changes).document(change_id),
                dict(change),
            )

        commit(self._client.transaction())

    def label_usage_count(self, label_id: str) -> int:
        if not str(label_id or "").strip():
            return 0
        query = self._user_collection().where(filter=FieldFilter("label_ids", "array_contains", str(label_id)))
        return sum(1 for _document in query.stream())
