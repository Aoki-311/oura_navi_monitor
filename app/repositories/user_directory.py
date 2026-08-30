from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.domain.management_errors import ManagementError, revision_text
from app.domain.roster_records import normalize_roster_email
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

_IDENTITY_FIELDS = ("chat_user_id", "user_id", "identity_bound_at")
_DOCUMENT_ID_FIELD = "_document_id"
_LABEL_DOCUMENT_FIELDS = frozenset(
    {
        "label_id",
        "name",
        "color",
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
        try:
            email = normalize_roster_email(payload.get("email"))
        except ValueError:
            email = ""
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
    def _payload(
        document: Any,
        *,
        preserve_document_id: bool = False,
    ) -> dict[str, Any]:
        payload = dict(document.to_dict() or {})
        document_id = str(getattr(document, "id", "") or "").strip()
        if preserve_document_id and document_id:
            payload[_DOCUMENT_ID_FIELD] = document_id
        return payload

    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        query = self._user_collection()
        if not include_inactive:
            query = query.where(filter=FieldFilter("is_active", "==", True))
        users = [
            self._payload(document, preserve_document_id=True)
            for document in query.stream()
        ]
        return sorted(users, key=lambda item: (str(item.get("area") or ""), str(item.get("name") or "")))

    def get_user(self, roster_id: str) -> dict[str, Any] | None:
        document = self._user_collection().document(str(roster_id)).get()
        return (
            self._payload(document, preserve_document_id=True)
            if document.exists
            else None
        )

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        normalized = normalize_roster_email(email)
        matches = []
        for user in self.list_users(include_inactive=True):
            try:
                candidate = normalize_roster_email(user.get("email"))
            except ValueError:
                continue
            if candidate == normalized:
                matches.append(user)
        if len(matches) > 1:
            raise ManagementError("duplicate_email", "email identity is ambiguous")
        return matches[0] if matches else None

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

        @firestore.transactional
        def commit(transaction: Any) -> dict[str, Any]:
            current = user_ref.get(transaction=transaction)
            current_payload = dict(current.to_dict() or {}) if current.exists else {}
            if action == "create" and current.exists:
                raise ValueError("user already exists")
            if action == "update" and not current.exists:
                raise ValueError("user not found")
            if action == "update" and revision_text(current_payload.get("updated_at")) != str(
                expected_updated_at or ""
            ).strip():
                raise ManagementError("update_conflict", "user update conflict")

            effective_payload = dict(payload)
            if action == "update":
                current_email = normalize_roster_email(current_payload.get("email"))
                requested_email = normalize_roster_email(effective_payload.get("email"))
                identity_is_bound = any(
                    str(current_payload.get(field) or "").strip()
                    for field in ("chat_user_id", "user_id")
                )
                if identity_is_bound and requested_email != current_email:
                    raise ManagementError("bound_email", "bound email cannot be changed")
                # Identity binding has one writer.  An admin payload may have
                # been assembled before a concurrent bind, so never copy its
                # stale identity fields back over the transactional snapshot.
                for field in _IDENTITY_FIELDS:
                    if field in current_payload:
                        effective_payload[field] = current_payload[field]
                    else:
                        effective_payload.pop(field, None)

            new_claim_refs = {
                (kind, value): self._claim_collection().document(
                    self._claim_document_id(kind, value)
                )
                for kind, value in self._user_claim_values(effective_payload)
            }
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
                for label_id in list(effective_payload.get("label_ids") or [])
            }
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
                        "updated_at": effective_payload.get("updated_at"),
                    },
                )
            transaction.set(user_ref, effective_payload)
            if change is not None:
                transaction.set(
                    self._client.collection(self._changes).document(change_id),
                    dict(change),
                )
            return effective_payload

        return commit(self._client.transaction())

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

    def bind_user_identity(
        self,
        roster_id: str,
        *,
        chat_user_id: str,
        user_id: str,
        bound_at: Any,
        change: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically patch identity fields without replacing roster fields."""

        resolved_roster_id = str(roster_id or "").strip()
        resolved_chat_user_id = str(chat_user_id or "").strip()
        resolved_user_id = str(user_id or "").strip()
        change_id = str(change.get("change_id") or "").strip()
        if not all((resolved_roster_id, resolved_chat_user_id, resolved_user_id, change_id)):
            raise ValueError("roster identity and change_id are required")
        user_ref = self._user_collection().document(resolved_roster_id)
        identity_claims = {
            ("chat_user_id", resolved_chat_user_id): self._claim_collection().document(
                self._claim_document_id("chat_user_id", resolved_chat_user_id)
            ),
            ("user_id", resolved_user_id): self._claim_collection().document(
                self._claim_document_id("user_id", resolved_user_id)
            ),
        }

        @firestore.transactional
        def commit(transaction: Any) -> dict[str, Any]:
            current = user_ref.get(transaction=transaction)
            if not current.exists:
                raise ValueError("user not found")
            current_payload = dict(current.to_dict() or {})
            current_chat_user_id = str(current_payload.get("chat_user_id") or "").strip()
            current_user_id = str(current_payload.get("user_id") or "").strip()
            if current_chat_user_id and current_chat_user_id != resolved_chat_user_id:
                raise ValueError("chat identity cannot be rebound")
            if current_user_id and current_user_id != resolved_user_id:
                raise ValueError("user identity cannot be rebound")
            snapshots = {
                key: ref.get(transaction=transaction)
                for key, ref in identity_claims.items()
            }
            legacy_identity_snapshots = {
                (kind, value): transaction.get(
                    self._user_collection()
                    .where(filter=FieldFilter(kind, "==", value))
                    .limit(2)
                )
                for kind, value in identity_claims
            }
            for (kind, _value), snapshot in snapshots.items():
                claim = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
                if snapshot.exists and str(claim.get("target_id") or "") != resolved_roster_id:
                    raise ManagementError("duplicate_identity", f"{kind} already exists")
            for (kind, _value), matches in legacy_identity_snapshots.items():
                for match in matches:
                    payload = dict(match.to_dict() or {})
                    matched_roster_id = str(
                        payload.get("roster_id") or getattr(match, "id", "")
                    ).strip()
                    if matched_roster_id != resolved_roster_id:
                        raise ManagementError(
                            "duplicate_identity", f"{kind} already exists"
                        )
            patch = {
                "chat_user_id": resolved_chat_user_id,
                "user_id": resolved_user_id,
                "identity_bound_at": bound_at,
                "updated_at": bound_at,
                "updated_by": str(change.get("updated_by") or current_payload.get("updated_by") or ""),
            }
            transaction.update(user_ref, patch)
            for (kind, _value), ref in identity_claims.items():
                transaction.set(
                    ref,
                    {
                        "claim_type": kind,
                        "target_id": resolved_roster_id,
                        "updated_at": bound_at,
                    },
                )
            transaction.set(
                self._client.collection(self._changes).document(change_id),
                dict(change),
            )
            return {**current_payload, **patch}

        return commit(self._client.transaction())

    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        query = self._label_collection()
        if not include_inactive:
            query = query.where(filter=FieldFilter("is_active", "==", True))
        labels = [
            self._payload(document, preserve_document_id=True)
            for document in query.stream()
        ]
        for label in labels:
            label["usage_count"] = self.label_usage_count(str(label.get("label_id") or ""))
        return sorted(labels, key=lambda item: str(item.get("name") or ""))

    def get_label(self, label_id: str) -> dict[str, Any] | None:
        document = self._label_collection().document(str(label_id)).get()
        return (
            self._payload(document, preserve_document_id=True)
            if document.exists
            else None
        )

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
        payload = {
            key: value
            for key, value in dict(label).items()
            if key in _LABEL_DOCUMENT_FIELDS
        }
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
