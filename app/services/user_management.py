from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.contracts.admin import (
    LabelCreate,
    LabelPatch,
    UserCreate,
    UserPatch,
    normalize_email,
)
from app.domain.analysis_scopes import (
    Department,
    SCOPE_POLICY_VERSION,
    SUMMARY_ROLES,
    membership_for,
)
from app.domain.management_errors import ManagementError, revision_text
from app.domain.label_records import (
    normalize_label_name_claim,
    read_canonical_label_collection,
)
from app.domain.roster_records import read_canonical_roster
from app.domain.roster_values import (
    CANONICAL_AREAS,
    HEADQUARTERS_AREA,
    HEADQUARTERS_AREA_KEY,
    HEADQUARTERS_WORKPLACE,
)
from app.domain.user_identity import roster_id_for_email


class UserDirectory(Protocol):
    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...
    def get_user(self, roster_id: str) -> dict[str, Any] | None: ...
    def find_user_by_email(self, email: str) -> dict[str, Any] | None: ...
    def put_user(self, user: dict[str, Any]) -> dict[str, Any]: ...
    def put_user_and_change(
        self,
        user: dict[str, Any],
        change: dict[str, Any],
        *,
        expected_updated_at: str = "",
    ) -> dict[str, Any]: ...
    def bind_user_identity(
        self,
        roster_id: str,
        *,
        chat_user_id: str,
        user_id: str,
        bound_at: Any,
        change: dict[str, Any],
    ) -> dict[str, Any]: ...
    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...
    def get_label(self, label_id: str) -> dict[str, Any] | None: ...
    def put_label(self, label: dict[str, Any]) -> dict[str, Any]: ...
    def put_label_and_change(
        self,
        label: dict[str, Any],
        change: dict[str, Any],
        *,
        expected_updated_at: str = "",
    ) -> dict[str, Any]: ...
    def delete_label(self, label_id: str) -> None: ...
    def delete_label_and_change(
        self,
        label_id: str,
        change: dict[str, Any],
        *,
        expected_updated_at: str,
    ) -> None: ...
    def label_usage_count(self, label_id: str) -> int: ...


def normalize_roster_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def canonical_area(value: str) -> str:
    area = normalize_roster_text(value)
    if area not in CANONICAL_AREAS:
        raise ManagementError("invalid_roster_value", "unsupported roster area")
    return area


def area_key_for(*, area: str, workplace: str) -> str:
    normalized_area = canonical_area(area)
    normalized_workplace = normalize_roster_text(workplace)
    if normalized_area == HEADQUARTERS_AREA:
        if normalized_workplace != HEADQUARTERS_WORKPLACE:
            raise ManagementError(
                "invalid_roster_value",
                "headquarters workplace must be Toranomon",
            )
        return HEADQUARTERS_AREA_KEY
    return normalized_area


class UserManagementService:
    def __init__(
        self,
        *,
        directory: UserDirectory,
        audit_retention_days: int = 180,
    ) -> None:
        self._directory = directory
        self._audit_retention_days = max(1, int(audit_retention_days))

    def _change(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        changed_at = datetime.now(timezone.utc)
        before = dict(before or {})
        after = dict(after or {})
        tracked_fields = (
            ("role", "department", "is_active", "label_ids", "area", "workplace", "mr_experience")
            if target_type == "user"
            else ("name", "color", "is_active")
        )
        before_values = {key: before.get(key) for key in tracked_fields if key in before}
        after_values = {key: after.get(key) for key in tracked_fields if key in after}
        changed_fields = [
            key
            for key in tracked_fields
            if before.get(key) != after.get(key)
            and (key in before or key in after)
        ]
        change = {
            "change_id": f"change_{uuid4().hex}",
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "updated_at": changed_at,
            "expires_at": changed_at + timedelta(days=self._audit_retention_days),
            "updated_by": normalize_email(actor),
            "changed_fields": changed_fields,
            "before": before_values,
            "after": after_values,
        }
        if target_type == "user":
            def scope(record: dict[str, Any]) -> dict[str, bool]:
                evaluation = read_canonical_roster(record).evaluation
                return {
                    "global_scope_enabled": evaluation.membership.global_enabled,
                    "user_map_scope_enabled": evaluation.membership.user_map_enabled,
                }

            change.update(
                {
                    "scope_policy_version": SCOPE_POLICY_VERSION,
                    "scope_before": scope(before),
                    "scope_after": scope(after),
                }
            )
        return change

    def _validate_labels(self, label_ids: list[str]) -> list[str]:
        unique = list(dict.fromkeys(str(value or "").strip() for value in label_ids if str(value or "").strip()))
        if not unique:
            return []
        records = read_canonical_label_collection(
            self._directory.list_labels(include_inactive=True)
        )
        if any(not record.catalog_eligible for record in records):
            raise ManagementError(
                "invalid_label_catalog",
                "label catalog must be repaired before editing relationships",
            )
        labels = {
            str(record.value.get("label_id") or ""): record.value
            for record in records
        }
        missing: list[str] = []
        for label_id in unique:
            label = labels.get(label_id)
            if label is None or label.get("is_active") is not True:
                missing.append(label_id)
        if missing:
            raise ManagementError("invalid_roster_value", "unknown or inactive labels")
        return unique

    def _editable_label_catalog(self) -> list[dict[str, Any]]:
        records = read_canonical_label_collection(
            self._directory.list_labels(include_inactive=True)
        )
        if any(not record.catalog_eligible for record in records):
            raise ManagementError(
                "invalid_label_catalog",
                "label catalog must be repaired before editing",
            )
        return [record.value for record in records]

    def _label_catalog_for_update(
        self,
        *,
        label_id: str,
        replacement: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Allow one-row repair only when the resulting full catalog is valid."""

        raw_rows = self._directory.list_labels(include_inactive=True)
        records = read_canonical_label_collection(raw_rows)
        if all(record.catalog_eligible for record in records):
            return [record.value for record in records]

        matching_indexes = [
            index
            for index, record in enumerate(records)
            if str(record.document_id or record.value.get("label_id") or "")
            == label_id
        ]
        if len(matching_indexes) != 1:
            raise ManagementError(
                "invalid_label_catalog",
                "label catalog must be repaired before editing",
            )
        candidate_rows = list(raw_rows)
        candidate_rows[matching_indexes[0]] = replacement
        candidate_records = read_canonical_label_collection(candidate_rows)
        if any(not record.catalog_eligible for record in candidate_records):
            raise ManagementError(
                "invalid_label_catalog",
                "label catalog must be repaired before editing",
            )
        return [record.value for record in candidate_records]

    @staticmethod
    def _require_scope_policy_version(expected_version: str) -> None:
        if str(expected_version or "").strip() != SCOPE_POLICY_VERSION:
            raise ManagementError(
                "scope_policy_conflict",
                "scope policy changed; reload management metadata and preview again",
            )

    def _labels_for_update(
        self,
        *,
        current_ids: list[str],
        requested_ids: list[str],
    ) -> list[str]:
        current = list(dict.fromkeys(str(value) for value in current_ids if str(value)))
        requested = list(dict.fromkeys(str(value) for value in requested_ids if str(value)))
        current_set = set(current)
        records = read_canonical_label_collection(
            self._directory.list_labels(include_inactive=True)
        )
        if any(not record.catalog_eligible for record in records):
            raise ManagementError(
                "invalid_label_catalog",
                "label catalog must be repaired before editing relationships",
            )
        labels = {
            str(record.value.get("label_id") or ""): record.value
            for record in records
        }
        preserved_inactive: list[str] = []
        for label_id in current:
            label = labels.get(label_id)
            if label is not None and label.get("is_active") is not True:
                preserved_inactive.append(label_id)
        for label_id in requested:
            label = labels.get(label_id)
            if label is None or (
                label.get("is_active") is not True
                and label_id not in current_set
            ):
                raise ManagementError("invalid_roster_value", "unknown or inactive labels")
        return list(dict.fromkeys([*requested, *preserved_inactive]))

    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self._directory.list_users(include_inactive=include_inactive)

    def metadata(self) -> dict[str, Any]:
        users = self._directory.list_users(include_inactive=True)
        observed_roles = sorted(
            {
                normalize_roster_text(item.get("role", ""))
                for item in users
                if normalize_roster_text(item.get("role", ""))
            }
        )
        return {
            "areas": list(CANONICAL_AREAS),
            "workplaces": sorted(
                {
                    normalize_roster_text(item.get("workplace", ""))
                    for item in users
                    if normalize_roster_text(item.get("workplace", ""))
                }
            ),
            "roles": list(dict.fromkeys([*SUMMARY_ROLES, *observed_roles])),
            "summaryRoles": list(SUMMARY_ROLES),
            "departments": [member.value for member in Department],
            "scopePolicyVersion": SCOPE_POLICY_VERSION,
        }

    @staticmethod
    def scope_preview(
        *,
        role: str,
        department: Department | str,
        is_active: bool,
    ) -> dict[str, Any]:
        membership = membership_for(
            role=role,
            department=department,
            is_active=is_active,
        )
        return {
            "globalScopeEnabled": membership.global_enabled,
            "userMapScopeEnabled": membership.user_map_enabled,
            "scopePolicyVersion": SCOPE_POLICY_VERSION,
        }

    def create_user(self, payload: UserCreate, *, actor: str) -> dict[str, Any]:
        self._require_scope_policy_version(payload.expected_scope_policy_version)
        email = normalize_email(payload.email)
        if self._directory.find_user_by_email(email) is not None:
            raise ManagementError("duplicate_email", "email already exists")
        label_ids = self._validate_labels(payload.label_ids)
        department = Department(payload.department)
        area = canonical_area(payload.area)
        workplace = normalize_roster_text(payload.workplace)
        now = datetime.now(timezone.utc)
        user = {
            "roster_id": roster_id_for_email(email),
            "user_id": "",
            "name": normalize_roster_text(payload.name),
            "email": email,
            "area": area,
            "workplace": workplace,
            "area_key": area_key_for(area=area, workplace=workplace),
            "role": normalize_roster_text(payload.role),
            "department": department.value,
            "mr_experience": (
                normalize_roster_text(payload.mr_experience) or "-"
                if department is Department.DM_FIELD
                else "-"
            ),
            "label_ids": label_ids,
            "chat_user_id": "",
            "is_active": bool(payload.is_active),
            "created_at": now,
            "updated_at": now,
            "updated_by": normalize_email(actor),
        }
        stored = self._directory.put_user_and_change(
            user,
            self._change(
                action="create",
                target_type="user",
                target_id=user["roster_id"],
                actor=actor,
                after=user,
            ),
        )
        return stored

    def update_user(self, roster_id: str, payload: UserPatch, *, actor: str) -> dict[str, Any]:
        current = self._directory.get_user(roster_id)
        if current is None:
            raise KeyError("user not found")
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        expected_scope_policy_version = str(
            changes.pop("expected_scope_policy_version", "") or ""
        ).strip()
        self._require_scope_policy_version(expected_scope_policy_version)
        expected_updated_at = str(changes.pop("expected_updated_at", "") or "").strip()
        current_revision = revision_text(current.get("updated_at"))
        if current_revision and expected_updated_at != current_revision:
            raise ManagementError("update_conflict", "user update conflict")
        now = datetime.now(timezone.utc)
        if "email" in changes:
            email = normalize_email(str(changes["email"]))
            if email != normalize_email(str(current.get("email") or "")) and (
                str(current.get("chat_user_id") or "").strip()
                or str(current.get("user_id") or "").strip()
            ):
                raise ManagementError("bound_email", "bound email cannot be changed")
            existing = self._directory.find_user_by_email(email)
            existing_document_id = str(
                (existing or {}).get("_document_id")
                or (existing or {}).get("roster_id")
                or ""
            )
            if existing is not None and existing_document_id != roster_id:
                raise ManagementError("duplicate_email", "email already exists")
            changes["email"] = email
        for key in ("name", "area", "workplace", "role", "mr_experience"):
            if key in changes:
                changes[key] = normalize_roster_text(str(changes[key]))
        if "department" in changes:
            changes["department"] = Department(changes["department"]).value
        if "area" in changes:
            changes["area"] = canonical_area(str(changes["area"]))
        if "label_ids" in changes:
            changes["label_ids"] = self._labels_for_update(
                current_ids=list(current.get("label_ids") or []),
                requested_ids=list(changes["label_ids"] or []),
            )
        updated = {**current, **changes}
        # The route id is the actual Firestore repair address.  Persisting it
        # repairs legacy documents whose internal roster_id is absent or stale.
        updated["roster_id"] = str(current.get("_document_id") or roster_id)
        updated["area_key"] = area_key_for(area=updated["area"], workplace=updated["workplace"])
        if Department(updated["department"]) is not Department.DM_FIELD:
            updated["mr_experience"] = "-"
        updated.update({"updated_at": now, "updated_by": normalize_email(actor)})
        stored = self._directory.put_user_and_change(
            updated,
            self._change(
                action="update",
                target_type="user",
                target_id=roster_id,
                actor=actor,
                before=current,
                after=updated,
            ),
            expected_updated_at=expected_updated_at,
        )
        return stored

    def bind_chat_identity(
        self,
        roster_id: str,
        *,
        chat_user_id: str,
        user_id: str,
        actor: str = "monitor-refresh@system.local",
    ) -> dict[str, Any]:
        """Persist the verified LCS root-document binding without exposing it as a label."""

        current = self._directory.get_user(roster_id)
        if current is None:
            raise KeyError("user not found")
        resolved_chat_user_id = str(chat_user_id or "").strip()
        resolved_user_id = str(user_id or "").strip()
        if not resolved_chat_user_id or not resolved_user_id:
            raise ValueError("verified chat identity is required")
        if "@" in resolved_user_id:
            raise ValueError("verified user identity must not be an email")
        current_chat_user_id = str(current.get("chat_user_id") or "").strip()
        current_user_id = str(current.get("user_id") or "").strip()
        if current_chat_user_id and current_chat_user_id != resolved_chat_user_id:
            raise ValueError("chat identity cannot be rebound")
        if current_user_id and current_user_id != resolved_user_id:
            raise ValueError("user identity cannot be rebound")
        bound_at = datetime.now(timezone.utc)
        audit_before = {**current, "identity_binding": bool(current_chat_user_id or current_user_id)}
        audit_after = {**current, "identity_binding": True}
        change = self._change(
            action="identity_bind",
            target_type="user",
            target_id=roster_id,
            actor=actor,
            before=audit_before,
            after=audit_after,
        )
        change["changed_fields"] = ["identity_binding"]
        change["before"] = {"identity_binding": audit_before["identity_binding"]}
        change["after"] = {"identity_binding": True}
        return self._directory.bind_user_identity(
            roster_id,
            chat_user_id=resolved_chat_user_id,
            user_id=resolved_user_id,
            bound_at=bound_at,
            change=change,
        )

    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self._directory.list_labels(include_inactive=include_inactive)

    def create_label(self, payload: LabelCreate, *, actor: str) -> dict[str, Any]:
        name = normalize_roster_text(payload.name)
        if any(
            normalize_label_name_claim(item["name"])
            == normalize_label_name_claim(name)
            for item in self._editable_label_catalog()
        ):
            raise ManagementError("duplicate_label", "label name already exists")
        now = datetime.now(timezone.utc)
        label = {
            "label_id": f"label_{uuid4().hex[:16]}",
            "name": name,
            "color": payload.color,
            "is_active": True,
            "usage_count": 0,
            "created_at": now,
            "updated_at": now,
            "updated_by": normalize_email(actor),
        }
        stored = self._directory.put_label_and_change(
            label,
            self._change(
                action="create",
                target_type="label",
                target_id=label["label_id"],
                actor=actor,
                after=label,
            ),
        )
        return stored

    def update_label(self, label_id: str, payload: LabelPatch, *, actor: str) -> dict[str, Any]:
        current = self._directory.get_label(label_id)
        if current is None:
            raise KeyError("label not found")
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        expected_updated_at = str(changes.pop("expected_updated_at", "") or "").strip()
        if revision_text(current.get("updated_at")) != expected_updated_at:
            raise ManagementError("update_conflict", "label update conflict")
        if "name" in changes:
            changes["name"] = normalize_roster_text(str(changes["name"]))
        candidate = {**current, **changes}
        candidate["label_id"] = str(current.get("_document_id") or label_id)
        catalog = self._label_catalog_for_update(
            label_id=label_id,
            replacement=candidate,
        )
        if "name" in changes and any(
            normalize_label_name_claim(item["name"])
            == normalize_label_name_claim(changes["name"])
            and item["label_id"] != label_id
            for item in catalog
        ):
            raise ManagementError("duplicate_label", "label name already exists")
        updated = {
            **current,
            **changes,
            "usage_count": self._directory.label_usage_count(label_id),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": normalize_email(actor),
        }
        updated["label_id"] = str(current.get("_document_id") or label_id)
        stored = self._directory.put_label_and_change(
            updated,
            self._change(
                action="update",
                target_type="label",
                target_id=label_id,
                actor=actor,
                before=current,
                after=updated,
            ),
            expected_updated_at=expected_updated_at,
        )
        return stored

    def delete_label(
        self,
        label_id: str,
        *,
        actor: str,
        expected_updated_at: str,
    ) -> None:
        self._editable_label_catalog()
        current = self._directory.get_label(label_id)
        if current is None:
            raise KeyError("label not found")
        expected = str(expected_updated_at or "").strip()
        if revision_text(current.get("updated_at")) != expected:
            raise ManagementError("update_conflict", "label delete conflict")
        if self._directory.label_usage_count(label_id) > 0:
            raise ManagementError("label_in_use", "label is in use")
        self._directory.delete_label_and_change(
            label_id,
            self._change(
                action="delete",
                target_type="label",
                target_id=label_id,
                actor=actor,
                before=current,
            ),
            expected_updated_at=expected,
        )
