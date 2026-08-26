from fastapi.testclient import TestClient

from app.dependencies import (
    get_conversation_history_repository,
    get_user_directory_repository,
)
from app.main import app
from app.settings import get_settings


class _Directory:
    def __init__(self, department: str, *, is_active: bool = True) -> None:
        self.department = department
        self.is_active = is_active

    def get_user(self, _roster_id: str):
        return {
            "roster_id": "roster_1",
            "department": self.department,
            "is_active": self.is_active,
            "chat_user_id": "chat_1",
        }


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


def test_trace_scope_is_derived_from_department_not_stored_flags() -> None:
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
