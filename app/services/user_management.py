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
from app.domain.analysis_scopes import Department
from app.domain.user_identity import roster_id_for_email


class UserDirectory(Protocol):
    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...
    def get_user(self, roster_id: str) -> dict[str, Any] | None: ...
    def find_user_by_email(self, email: str) -> dict[str, Any] | None: ...
    def put_user(self, user: dict[str, Any]) -> dict[str, Any]: ...
    def put_user_and_change(self, user: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]: ...
    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...
    def get_label(self, label_id: str) -> dict[str, Any] | None: ...
    def put_label(self, label: dict[str, Any]) -> dict[str, Any]: ...
    def put_label_and_change(self, label: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]: ...
    def delete_label(self, label_id: str) -> None: ...
    def delete_label_and_change(self, label_id: str, change: dict[str, Any]) -> None: ...
    def label_usage_count(self, label_id: str) -> int: ...


def normalize_roster_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def area_key_for(*, area: str, workplace: str) -> str:
    normalized_area = normalize_roster_text(area)
    normalized_workplace = normalize_roster_text(workplace)
    if normalized_area == "本社" and normalized_workplace == "虎ノ門":
        return "本社・虎ノ門"
    if not normalized_area:
        raise ValueError("area is required")
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

    def _change(self, *, action: str, target_type: str, target_id: str, actor: str) -> dict[str, Any]:
        changed_at = datetime.now(timezone.utc)
        return {
            "change_id": f"change_{uuid4().hex}",
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "updated_at": changed_at,
            "expires_at": changed_at + timedelta(days=self._audit_retention_days),
            "updated_by": normalize_email(actor),
        }

    def _validate_labels(self, label_ids: list[str]) -> list[str]:
        unique = list(dict.fromkeys(str(value or "").strip() for value in label_ids if str(value or "").strip()))
        missing = [
            label_id
            for label_id in unique
            if not (self._directory.get_label(label_id) or {}).get("is_active", False)
        ]
        if missing:
            raise ValueError(f"unknown or inactive labels: {', '.join(missing)}")
        return unique

    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self._directory.list_users(include_inactive=include_inactive)

    def create_user(self, payload: UserCreate, *, actor: str) -> dict[str, Any]:
        email = normalize_email(payload.email)
        if self._directory.find_user_by_email(email) is not None:
            raise ValueError("email already exists")
        label_ids = self._validate_labels(payload.label_ids)
        department = Department(payload.department)
        area = normalize_roster_text(payload.area)
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
            "mr_experience": normalize_roster_text(payload.mr_experience) or "-",
            "label_ids": label_ids,
            "chat_user_id": "",
            "is_active": bool(payload.is_active),
            "created_at": now,
            "updated_at": now,
            "updated_by": normalize_email(actor),
        }
        stored = self._directory.put_user_and_change(
            user,
            self._change(action="create", target_type="user", target_id=user["roster_id"], actor=actor),
        )
        return stored

    def update_user(self, roster_id: str, payload: UserPatch, *, actor: str) -> dict[str, Any]:
        current = self._directory.get_user(roster_id)
        if current is None:
            raise KeyError("user not found")
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        now = datetime.now(timezone.utc)
        if "email" in changes:
            email = normalize_email(str(changes["email"]))
            existing = self._directory.find_user_by_email(email)
            if existing is not None and existing.get("roster_id") != roster_id:
                raise ValueError("email already exists")
            changes["email"] = email
        for key in ("name", "area", "workplace", "role", "mr_experience"):
            if key in changes:
                changes[key] = normalize_roster_text(str(changes[key]))
        if "department" in changes:
            changes["department"] = Department(changes["department"]).value
        if "label_ids" in changes:
            changes["label_ids"] = self._validate_labels(list(changes["label_ids"] or []))
        updated = {**current, **changes}
        updated["area_key"] = area_key_for(area=updated["area"], workplace=updated["workplace"])
        updated.update({"updated_at": now, "updated_by": normalize_email(actor)})
        stored = self._directory.put_user_and_change(
            updated,
            self._change(action="update", target_type="user", target_id=roster_id, actor=actor),
        )
        return stored

    def bind_chat_identity(
        self,
        roster_id: str,
        *,
        chat_user_id: str,
        user_id: str,
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
        for item in self._directory.list_users(include_inactive=True):
            if item.get("roster_id") == roster_id:
                continue
            if str(item.get("chat_user_id") or "") == resolved_chat_user_id:
                raise ValueError("chat identity already bound")
            if str(item.get("user_id") or "") == resolved_user_id:
                raise ValueError("user identity already bound")
        updated = {
            **current,
            "chat_user_id": resolved_chat_user_id,
            "user_id": resolved_user_id,
            "identity_bound_at": datetime.now(timezone.utc),
        }
        return self._directory.put_user(updated)

    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self._directory.list_labels(include_inactive=include_inactive)

    def create_label(self, payload: LabelCreate, *, actor: str) -> dict[str, Any]:
        name = normalize_roster_text(payload.name)
        if any(normalize_roster_text(item["name"]).casefold() == name.casefold() for item in self._directory.list_labels(include_inactive=True)):
            raise ValueError("label name already exists")
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
            self._change(action="create", target_type="label", target_id=label["label_id"], actor=actor),
        )
        return stored

    def update_label(self, label_id: str, payload: LabelPatch, *, actor: str) -> dict[str, Any]:
        current = self._directory.get_label(label_id)
        if current is None:
            raise KeyError("label not found")
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        if "name" in changes:
            changes["name"] = normalize_roster_text(str(changes["name"]))
        if "name" in changes and any(
            normalize_roster_text(item["name"]).casefold() == changes["name"].casefold()
            and item["label_id"] != label_id
            for item in self._directory.list_labels(include_inactive=True)
        ):
            raise ValueError("label name already exists")
        updated = {
            **current,
            **changes,
            "usage_count": self._directory.label_usage_count(label_id),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": normalize_email(actor),
        }
        stored = self._directory.put_label_and_change(
            updated,
            self._change(action="update", target_type="label", target_id=label_id, actor=actor),
        )
        return stored

    def delete_label(self, label_id: str, *, actor: str) -> None:
        if self._directory.get_label(label_id) is None:
            raise KeyError("label not found")
        if self._directory.label_usage_count(label_id) > 0:
            raise ValueError("label is in use")
        self._directory.delete_label_and_change(
            label_id,
            self._change(action="delete", target_type="label", target_id=label_id, actor=actor),
        )
