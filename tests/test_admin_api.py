from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_user_management_service
from app.main import app
from app.settings import get_settings


class _AdminService:
    def __init__(self) -> None:
        self.deleted = []

    @staticmethod
    def _user():
        return {
            "roster_id": "roster_1",
            "name": "利用者",
            "email": "user@example.com",
            "area": "関西",
            "area_key": "関西",
            "workplace": "大阪",
            "role": "本社MR",
            "department": "DM専任",
            "mr_experience": "8年",
            "label_ids": [],
            "is_active": True,
            "updated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
            "updated_by": "admin@example.com",
        }

    def list_users(self, **_kwargs):
        return [self._user()]

    def create_user(self, _payload, *, actor):
        assert actor == "admin@example.com"
        return self._user()

    def list_labels(self, **_kwargs):
        return []

    def delete_label(self, label_id, *, actor):
        assert actor == "admin@example.com"
        if label_id == "in_use":
            raise ValueError("label is in use")
        self.deleted.append(label_id)


def _settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def test_admin_contract_rejects_scope_flags_and_translates_label_conflict() -> None:
    service = _AdminService()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_user_management_service] = lambda: service
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    user_payload = {
        "name": "利用者",
        "email": "user@example.com",
        "area": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
    }
    try:
        response = client.get("/api/admin/users", headers=headers)
        assert response.status_code == 200
        assert "scope" not in response.json()["users"][0]

        rejected = client.post(
            "/api/admin/users",
            json={**user_payload, "global_scope_enabled": True},
            headers=headers,
        )
        assert rejected.status_code == 422

        conflict = client.delete("/api/admin/labels/in_use", headers=headers)
        assert conflict.status_code == 409
    finally:
        app.dependency_overrides.clear()
