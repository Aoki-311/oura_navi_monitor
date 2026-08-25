from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_user_management_service
from app.domain.management_errors import ManagementError
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

    @staticmethod
    def metadata():
        return {
            "areas": ["関西"],
            "workplaces": ["大阪"],
            "roles": ["本社MR"],
            "departments": ["DM専任", "ヘルスケア本社", "DM本社", "管理者"],
        }

    def delete_label(self, label_id, *, actor, expected_updated_at):
        assert actor == "admin@example.com"
        assert expected_updated_at == "2026-08-24T00:00:00+00:00"
        if label_id == "in_use":
            raise ManagementError("label_in_use", "label is in use")
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
        assert response.json()["users"][0]["identityBound"] is False

        metadata = client.get("/api/admin/metadata", headers=headers)
        assert metadata.status_code == 200
        assert metadata.json()["areas"] == ["関西"]
        assert metadata.json()["departments"] == ["DM専任", "ヘルスケア本社", "DM本社", "管理者"]

        rejected = client.post(
            "/api/admin/users",
            json={**user_payload, "global_scope_enabled": True},
            headers=headers,
        )
        assert rejected.status_code == 422

        conflict = client.request(
            "DELETE",
            "/api/admin/labels/in_use",
            json={"expected_updated_at": "2026-08-24T00:00:00+00:00"},
            headers=headers,
        )
        assert conflict.status_code == 409
    finally:
        app.dependency_overrides.clear()
