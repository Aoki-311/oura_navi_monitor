from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.contracts.admin import LabelCreate, LabelPatch, UserCreate, UserPatch
from app.domain.analysis_scopes import AnalysisScope, membership_for
from app.domain.management_errors import ManagementError, revision_text
from app.services.user_management import UserManagementService


class MemoryDirectory:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.labels: dict[str, dict] = {}
        self.audit: list[dict] = []

    def list_users(self, *, include_inactive: bool = True) -> list[dict]:
        values = list(self.users.values())
        return values if include_inactive else [item for item in values if item["is_active"]]

    def get_user(self, roster_id: str) -> dict | None:
        return self.users.get(roster_id)

    def find_user_by_email(self, email: str) -> dict | None:
        return next((item for item in self.users.values() if item["email"] == email), None)

    def put_user(self, user: dict) -> dict:
        self.users[user["roster_id"]] = dict(user)
        return dict(user)

    def put_user_and_change(
        self,
        user: dict,
        change: dict,
        *,
        expected_updated_at: str = "",
    ) -> dict:
        current = self.users.get(user["roster_id"])
        if change.get("action") == "update" and revision_text((current or {}).get("updated_at")) != expected_updated_at:
            raise ManagementError("update_conflict", "user update conflict")
        self.users[user["roster_id"]] = dict(user)
        self.audit.append(dict(change))
        return dict(user)

    def list_labels(self, *, include_inactive: bool = True) -> list[dict]:
        values = list(self.labels.values())
        return values if include_inactive else [item for item in values if item["is_active"]]

    def get_label(self, label_id: str) -> dict | None:
        return self.labels.get(label_id)

    def put_label(self, label: dict) -> dict:
        self.labels[label["label_id"]] = dict(label)
        return dict(label)

    def put_label_and_change(
        self,
        label: dict,
        change: dict,
        *,
        expected_updated_at: str = "",
    ) -> dict:
        if change.get("action") == "update":
            current = self.labels.get(label["label_id"])
            if revision_text((current or {}).get("updated_at")) != expected_updated_at:
                raise ManagementError("update_conflict", "label update conflict")
        self.labels[label["label_id"]] = dict(label)
        self.audit.append(dict(change))
        return dict(label)

    def delete_label(self, label_id: str) -> None:
        self.labels.pop(label_id, None)

    def delete_label_and_change(
        self,
        label_id: str,
        change: dict,
        *,
        expected_updated_at: str,
    ) -> None:
        if revision_text((self.labels.get(label_id) or {}).get("updated_at")) != expected_updated_at:
            raise ManagementError("update_conflict", "label delete conflict")
        self.labels.pop(label_id, None)
        self.audit.append(dict(change))

    def label_usage_count(self, label_id: str) -> int:
        return sum(label_id in item.get("label_ids", []) for item in self.users.values())

def service() -> tuple[UserManagementService, MemoryDirectory]:
    directory = MemoryDirectory()
    return UserManagementService(directory=directory), directory


def patch_for(user: dict, **changes) -> UserPatch:
    return UserPatch(expected_updated_at=revision_text(user.get("updated_at")), **changes)


def test_create_user_derives_scope_from_department_and_never_from_labels() -> None:
    manager, directory = service()
    created = manager.create_user(
        UserCreate(
            name="テスト 太郎",
            email="Taro@example.com",
            area="首都圏A",
            workplace="東　京",
            role="本社MR",
            department="DM専任",
            mr_experience="10年",
        ),
        actor="admin@example.com",
    )
    assert created["email"] == "taro@example.com"
    assert membership_for(created["department"], is_active=created["is_active"]).includes(AnalysisScope.GLOBAL)
    stored = directory.users[created["roster_id"]]
    assert "global_scope_enabled" not in stored
    assert "user_map_scope_enabled" not in stored
    assert "admin_scope_enabled" not in stored
    assert directory.audit[0]["expires_at"] > directory.audit[0]["updated_at"]

    label = manager.create_label(LabelCreate(name="重点", color="#23d28f"), actor="admin@example.com")
    updated = manager.update_user(
        created["roster_id"],
        patch_for(created, label_ids=[label["label_id"]]),
        actor="admin@example.com",
    )
    assert membership_for(updated["department"], is_active=updated["is_active"]) == membership_for(
        created["department"], is_active=created["is_active"]
    )


def test_duplicate_email_is_rejected_after_normalization() -> None:
    manager, _ = service()
    payload = UserCreate(
        name="一人目",
        email="same@example.com",
        area="関西",
        workplace="大阪",
        role="本社MR",
        department="DM専任",
        mr_experience="5年",
    )
    manager.create_user(payload, actor="admin@example.com")
    with pytest.raises(ValueError, match="email already exists"):
        manager.create_user(payload.model_copy(update={"name": "二人目", "email": " SAME@example.com "}), actor="admin@example.com")


def test_department_change_recomputes_scope_and_tag_cannot_grant_access() -> None:
    manager, _ = service()
    user = manager.create_user(
        UserCreate(
            name="管理者",
            email="admin-user@example.com",
            area="本社",
            workplace="虎ノ門",
            role="本部メンバー",
            department="管理者",
            mr_experience="-",
        ),
        actor="admin@example.com",
    )
    assert not membership_for(user["department"], is_active=user["is_active"]).includes(AnalysisScope.USER_MAP)
    updated = manager.update_user(
        user["roster_id"],
        patch_for(user, department="DM本社"),
        actor="admin@example.com",
    )
    assert membership_for(updated["department"], is_active=updated["is_active"]).includes(AnalysisScope.USER_MAP)
    assert "iap" not in " ".join(updated.keys()).lower()


def test_label_in_use_cannot_be_deleted() -> None:
    manager, _ = service()
    user = manager.create_user(
        UserCreate(
            name="利用者",
            email="user@example.com",
            area="九州",
            workplace="福岡",
            role="本社MR",
            department="DM専任",
            mr_experience="8年",
        ),
        actor="admin@example.com",
    )
    label = manager.create_label(LabelCreate(name="研修対象", color="#386dff"), actor="admin@example.com")
    manager.update_user(
        user["roster_id"], patch_for(user, label_ids=[label["label_id"]]), actor="admin@example.com"
    )
    with pytest.raises(ValueError, match="label is in use"):
        manager.delete_label(
            label["label_id"],
            actor="admin@example.com",
            expected_updated_at=revision_text(label["updated_at"]),
        )


def test_bound_user_email_cannot_be_changed() -> None:
    manager, _ = service()
    created = manager.create_user(
        UserCreate(
            name="利用者",
            email="old@example.com",
            area="関西",
            workplace="大阪",
            role="本社MR",
            department="DM専任",
            mr_experience="8年",
        ),
        actor="admin@example.com",
    )
    bound = manager.bind_chat_identity(
        created["roster_id"], chat_user_id="chat-1", user_id="subject-1"
    )
    assert bound["user_id"] == "subject-1"
    with pytest.raises(ManagementError) as captured:
        manager.update_user(
            created["roster_id"],
            patch_for(bound, email="new@example.com"),
            actor="admin@example.com",
        )
    assert captured.value.code == "bound_email"


def test_inactive_assigned_label_is_preserved_and_cannot_be_newly_assigned() -> None:
    manager, directory = service()
    label = manager.create_label(
        LabelCreate(name="旧分類", color="#386dff"), actor="admin@example.com"
    )
    user = manager.create_user(
        UserCreate(
            name="利用者",
            email="inactive-label@example.com",
            area="関西",
            workplace="大阪",
            role="MR",
            department="DM専任",
            label_ids=[label["label_id"]],
        ),
        actor="admin@example.com",
    )
    directory.labels[label["label_id"]]["is_active"] = False

    updated = manager.update_user(
        user["roster_id"],
        patch_for(user, name="更新後"),
        actor="admin@example.com",
    )
    assert updated["label_ids"] == [label["label_id"]]

    other = manager.create_user(
        UserCreate(
            name="別利用者",
            email="other-inactive-label@example.com",
            area="九州",
            workplace="福岡",
            role="MR",
            department="DM専任",
        ),
        actor="admin@example.com",
    )
    with pytest.raises(ManagementError) as captured:
        manager.update_user(
            other["roster_id"],
            patch_for(other, label_ids=[label["label_id"]]),
            actor="admin@example.com",
        )
    assert captured.value.code == "invalid_roster_value"


def test_user_update_requires_current_revision_and_non_mr_experience_is_dash() -> None:
    manager, _ = service()
    user = manager.create_user(
        UserCreate(
            name="本社員",
            email="hq@example.com",
            area="本社",
            workplace="虎ノ門",
            role="本社",
            department="ヘルスケア本社",
            mr_experience="20年",
        ),
        actor="admin@example.com",
    )
    assert user["mr_experience"] == "-"
    with pytest.raises(ManagementError) as captured:
        manager.update_user(
            user["roster_id"],
            UserPatch(name="競合更新"),
            actor="admin@example.com",
        )
    assert captured.value.code == "update_conflict"


def test_label_update_and_delete_require_the_current_revision() -> None:
    manager, directory = service()
    label = manager.create_label(
        LabelCreate(name="重点", color="#386dff"), actor="admin@example.com"
    )

    with pytest.raises(ManagementError) as captured:
        manager.update_label(
            label["label_id"],
            LabelPatch(name="競合", expected_updated_at="stale"),
            actor="admin@example.com",
        )
    assert captured.value.code == "update_conflict"

    current_revision = revision_text(directory.labels[label["label_id"]]["updated_at"])
    updated = manager.update_label(
        label["label_id"],
        LabelPatch(name="更新済み", expected_updated_at=current_revision),
        actor="admin@example.com",
    )
    with pytest.raises(ManagementError) as captured:
        manager.delete_label(
            label["label_id"],
            actor="admin@example.com",
            expected_updated_at=current_revision,
        )
    assert captured.value.code == "update_conflict"

    manager.delete_label(
        label["label_id"],
        actor="admin@example.com",
        expected_updated_at=revision_text(updated["updated_at"]),
    )
    assert label["label_id"] not in directory.labels


def test_verified_chat_identity_is_unique_and_not_a_scope_owner() -> None:
    manager, _ = service()
    first = manager.create_user(
        UserCreate(name="一人目", email="one@example.com", area="関西", workplace="大阪", role="本社MR", department="DM専任"),
        actor="admin@example.com",
    )
    second = manager.create_user(
        UserCreate(name="二人目", email="two@example.com", area="九州", workplace="福岡", role="本社MR", department="DM専任"),
        actor="admin@example.com",
    )
    bound = manager.bind_chat_identity(first["roster_id"], chat_user_id="chat-1", user_id="subject-1")
    assert membership_for(bound["department"], is_active=bound["is_active"]) == membership_for(
        first["department"], is_active=first["is_active"]
    )
    with pytest.raises(ValueError, match="already bound"):
        manager.bind_chat_identity(second["roster_id"], chat_user_id="chat-1", user_id="subject-2")

    with pytest.raises(ValueError, match="already bound"):
        manager.bind_chat_identity(second["roster_id"], chat_user_id="chat-2", user_id="subject-1")

    with pytest.raises(ValueError, match="cannot be rebound"):
        manager.bind_chat_identity(first["roster_id"], chat_user_id="chat-1", user_id="subject-new")
