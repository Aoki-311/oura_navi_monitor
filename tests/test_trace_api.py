from fastapi.testclient import TestClient

from app.dependencies import (
    get_conversation_history_repository,
    get_user_directory_repository,
)
from app.main import app
from app.settings import get_settings


class _Directory:
    def __init__(
        self,
        department: str,
        *,
        is_active: object = True,
        email: str = "user@example.com",
    ) -> None:
        self.department = department
        self.is_active = is_active
        self.email = email

    def get_user(self, _roster_id: str):
        return {
            "roster_id": "roster_1",
            "email": self.email,
            "role": "本社スタッフ",
            "department": self.department,
            "is_active": self.is_active,
            "chat_user_id": "chat_1",
        }

    def list_users(self, *, include_inactive: bool = True):
        assert include_inactive is True
        return [self.get_user("roster_1")]


class _CollectionDirectory:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    def list_users(self, *, include_inactive: bool = True):
        assert include_inactive is True
        return list(self.users)


class _Conversations:
    def list_messages(self, **_kwargs):
        return {
            "messages": [{
                "messageId": "m1",
                "timestampJst": "2026-08-24 10:00:00",
                "role": "user",
                "roleLabel": "ユーザー",
                "content": "質問",
                "mode": "internal",
                "feedback": "none",
                "status": "done",
            }],
            "page": {"nextCursor": ""},
        }


def _local_settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def test_trace_scope_is_derived_from_governed_user_map_scope_not_stored_flags() -> None:
    app.dependency_overrides[get_settings] = _local_settings
    app.dependency_overrides[get_user_directory_repository] = lambda: _Directory("DM本社")
    app.dependency_overrides[get_conversation_history_repository] = _Conversations
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        response = client.get(
            "/api/trace/messages?roster_id=roster_1&conversation_id=conv_1",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["messages"][0]["messageId"] == "m1"

        app.dependency_overrides[get_user_directory_repository] = lambda: _Directory("管理者")
        assert client.get(
            "/api/trace/messages?roster_id=roster_1&conversation_id=conv_1",
            headers=headers,
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_inactive_user_cannot_open_conversations_or_messages_by_direct_url() -> None:
    app.dependency_overrides[get_settings] = _local_settings
    app.dependency_overrides[get_user_directory_repository] = lambda: _Directory(
        "DM専任", is_active=False
    )
    app.dependency_overrides[get_conversation_history_repository] = _Conversations
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        assert client.get(
            "/api/trace/conversations?roster_id=roster_1",
            headers=headers,
        ).status_code == 404
        assert client.get(
            "/api/trace/messages?roster_id=roster_1&conversation_id=conv_1",
            headers=headers,
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_trace_fails_closed_for_non_boolean_activity_and_identity_blockers() -> None:
    app.dependency_overrides[get_settings] = _local_settings
    app.dependency_overrides[get_conversation_history_repository] = _Conversations
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        for directory in (
            _Directory("DM専任", is_active="false"),
            _Directory("DM専任", is_active=1),
            _Directory("DM専任", email="invalid-email"),
        ):
            app.dependency_overrides[get_user_directory_repository] = lambda directory=directory: directory
            response = client.get(
                "/api/trace/messages?roster_id=roster_1&conversation_id=conv_1",
                headers=headers,
            )
            assert response.status_code == 404

        app.dependency_overrides[get_user_directory_repository] = lambda: _Directory(
            "DM専任",
            is_active=True,
        )
        assert client.get(
            "/api/trace/messages?roster_id=roster_1&conversation_id=conv_1",
            headers=headers,
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_trace_fails_closed_for_duplicate_identity_but_keeps_other_users() -> None:
    duplicate = {
        "name": "重複利用者",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社スタッフ",
        "department": "DM専任",
        "label_ids": [],
        "is_active": True,
        "chat_user_id": "duplicate-chat",
        "user_id": "duplicate-subject",
    }
    safe = {
        **duplicate,
        "roster_id": "safe",
        "email": "safe@example.com",
        "chat_user_id": "safe-chat",
        "user_id": "safe-subject",
    }
    directory = _CollectionDirectory([
        {
            **duplicate,
            "roster_id": "duplicate-a",
            "email": " Shared@Example.com ",
        },
        {
            **duplicate,
            "roster_id": "duplicate-b",
            "email": "shared@example.COM",
        },
        safe,
    ])
    app.dependency_overrides[get_settings] = _local_settings
    app.dependency_overrides[get_user_directory_repository] = lambda: directory
    app.dependency_overrides[get_conversation_history_repository] = _Conversations
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        for roster_id in ("duplicate-a", "duplicate-b"):
            response = client.get(
                f"/api/trace/messages?roster_id={roster_id}&conversation_id=conv_1",
                headers=headers,
            )
            assert response.status_code == 404

        response = client.get(
            "/api/trace/messages?roster_id=safe&conversation_id=conv_1",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["messages"][0]["messageId"] == "m1"
    finally:
        app.dependency_overrides.clear()
