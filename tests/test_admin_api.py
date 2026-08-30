from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_user_management_service
from app.domain.management_errors import ManagementError
from app.main import app
from app.routers.admin import _label_payload, _user_payload
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
        if _payload.expected_scope_policy_version != "summary_role_v1":
            raise ManagementError("scope_policy_conflict", "scope policy changed")
        return self._user()

    def update_user(self, _roster_id, payload, *, actor):
        assert actor == "admin@example.com"
        if payload.expected_scope_policy_version != "summary_role_v1":
            raise ManagementError("scope_policy_conflict", "scope policy changed")
        return self._user()

    def list_labels(self, **_kwargs):
        return []

    @staticmethod
    def metadata():
        return {
            "areas": ["関西"],
            "workplaces": ["大阪"],
            "roles": ["本社MR"],
            "summaryRoles": ["本社MR", "コントラクトMR"],
            "departments": ["DM専任", "ヘルスケア本社", "DM本社", "管理者"],
            "scopePolicyVersion": "summary_role_v1",
        }

    @staticmethod
    def scope_preview(**_kwargs):
        return {
            "globalScopeEnabled": True,
            "userMapScopeEnabled": True,
            "scopePolicyVersion": "summary_role_v1",
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
        "expected_scope_policy_version": "summary_role_v1",
    }
    try:
        response = client.get("/api/admin/users", headers=headers)
        assert response.status_code == 200
        assert "scope" not in response.json()["users"][0]
        assert response.json()["users"][0]["identityBound"] is False
        assert response.json()["users"][0]["globalScopeEnabled"] is True
        assert response.json()["users"][0]["userMapScopeEnabled"] is True

        metadata = client.get("/api/admin/metadata", headers=headers)
        assert metadata.status_code == 200
        assert metadata.json()["areas"] == ["関西"]
        assert metadata.json()["departments"] == ["DM専任", "ヘルスケア本社", "DM本社", "管理者"]
        assert metadata.json()["summaryRoles"] == ["本社MR", "コントラクトMR"]
        assert metadata.json()["scopePolicyVersion"] == "summary_role_v1"
        assert "departmentScopes" not in metadata.json()

        preview = client.post(
            "/api/admin/scope-preview",
            json={"role": "本社MR", "department": "DM専任", "is_active": True},
            headers=headers,
        )
        assert preview.status_code == 200
        assert preview.json()["globalScopeEnabled"] is True

        created = client.post("/api/admin/users", json=user_payload, headers=headers)
        assert created.status_code == 201
        assert created.json()["scopePolicyVersion"] == "summary_role_v1"

        updated = client.patch(
            "/api/admin/users/roster_1",
            json={
                "name": "更新後",
                "expected_updated_at": "2026-08-24T00:00:00+00:00",
                "expected_scope_policy_version": "summary_role_v1",
            },
            headers=headers,
        )
        assert updated.status_code == 200

        stale_policy = client.post(
            "/api/admin/users",
            json={**user_payload, "expected_scope_policy_version": "summary_role_stale"},
            headers=headers,
        )
        assert stale_policy.status_code == 409
        assert stale_policy.json()["detail"]["code"] == "scope_policy_conflict"

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


def test_management_payload_uses_document_id_to_expose_a_repairable_bad_roster() -> None:
    payload = _user_payload(
        {
            "_document_id": "firestore_doc",
            "roster_id": "",
            "name": "修復対象",
            "email": "repair@example.com",
            "area": "関西",
            "area_key": "関西",
            "workplace": "大阪",
            "role": "本社MR",
            "department": "DM専任",
            "label_ids": [],
            "is_active": True,
        }
    )

    assert payload["rosterId"] == "firestore_doc"
    assert payload["rosterIssues"] == ["missing_roster_id"]


def test_management_api_keeps_all_duplicate_identity_rows_visible_for_repair() -> None:
    service = _AdminService()
    duplicate_a = {
        **service._user(),
        "roster_id": "duplicate_a",
        "email": " Duplicate@Example.com ",
        "user_id": "shared-subject",
    }
    duplicate_b = {
        **service._user(),
        "roster_id": "duplicate_b",
        "email": "duplicate@example.COM",
        "user_id": "shared-subject",
    }
    safe = {
        **service._user(),
        "roster_id": "safe",
        "email": "safe@example.com",
        "user_id": "safe-subject",
    }
    service.list_users = lambda **_kwargs: [duplicate_a, duplicate_b, safe]
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_user_management_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.get(
            "/api/admin/users",
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    users = response.json()["users"]
    assert [row["rosterId"] for row in users] == [
        "duplicate_a",
        "duplicate_b",
        "safe",
    ]
    assert all(
        set(row["rosterIssues"]) == {"duplicate_email", "duplicate_identity"}
        for row in users[:2]
    )
    assert all(row["globalScopeEnabled"] is False for row in users[:2])
    assert all(row["userMapScopeEnabled"] is False for row in users[:2])
    assert users[2]["rosterIssues"] == []
    assert users[2]["globalScopeEnabled"] is True
    assert users[2]["userMapScopeEnabled"] is True


def test_management_label_payload_exposes_strict_catalog_issues() -> None:
    payload = _label_payload(
        {
            "_document_id": "firestore_label_doc",
            "label_id": "wrong_label_id",
            "name": "重点",
            "color": "#23d28f",
            "is_active": "false",
            "usage_count": 1,
        }
    )

    assert payload["labelId"] == "firestore_label_doc"
    assert payload["isActive"] is False
    assert payload["labelIssues"] == [
        "label_id_document_mismatch",
        "invalid_label_is_active",
    ]


def test_user_mutation_response_is_resolved_from_the_full_post_write_snapshot() -> None:
    service = _AdminService()
    target = service._user()
    duplicate = {
        **target,
        "roster_id": "roster_2",
        "email": " USER@example.COM ",
    }
    service.list_users = lambda **_kwargs: [target, duplicate]
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_user_management_service] = lambda: service
    client = TestClient(app)
    try:
        created = client.post(
            "/api/admin/users",
            json={
                "name": "利用者",
                "email": "user@example.com",
                "area": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "expected_scope_policy_version": "summary_role_v1",
            },
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
        updated = client.patch(
            "/api/admin/users/roster_1",
            json={
                "name": "更新後",
                "expected_updated_at": "2026-08-24T00:00:00+00:00",
                "expected_scope_policy_version": "summary_role_v1",
            },
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert updated.status_code == 200
    for response in (created, updated):
        assert response.json()["rosterIssues"] == ["duplicate_email"]
        assert response.json()["globalScopeEnabled"] is False
        assert response.json()["userMapScopeEnabled"] is False


def test_user_mutation_never_falls_back_when_post_write_target_is_missing() -> None:
    service = _AdminService()
    service.list_users = lambda **_kwargs: []
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_user_management_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.post(
            "/api/admin/users",
            json={
                "name": "利用者",
                "email": "user@example.com",
                "area": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "expected_scope_policy_version": "summary_role_v1",
            },
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "readback_conflict"


def test_management_lists_normalized_duplicate_label_names_for_repair() -> None:
    service = _AdminService()
    service.list_labels = lambda **_kwargs: [
        {
            "label_id": "label_a",
            "name": " ＴＥＳＴ ",
            "color": "#23d28f",
            "is_active": True,
            "usage_count": 0,
        },
        {
            "label_id": "label_b",
            "name": "test",
            "color": "#386dff",
            "is_active": True,
            "usage_count": 0,
        },
    ]
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_user_management_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.get(
            "/api/admin/labels",
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [label["labelId"] for label in response.json()["labels"]] == [
        "label_a",
        "label_b",
    ]
    assert all(
        label["labelIssues"] == ["duplicate_label_name"]
        for label in response.json()["labels"]
    )
