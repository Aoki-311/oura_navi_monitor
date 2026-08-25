from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_export_job_repository
from app.main import app
from app.settings import get_settings


class _Analytics:
    def users(self, **_kwargs):
        return {
            "scopeUserCount": 80,
            "freshness": {"state": "fresh", "dataThrough": "2026-08-24T00:00:00Z"},
            "users": [
                {
                    "rosterId": "roster_1",
                    "name": "利用者",
                    "email": "user@example.com",
                    "area": "関西",
                    "areaKey": "関西",
                    "labels": [],
                    "lastActiveAt": "2026-08-24T00:00:00Z",
                    "activeDays7": 2,
                    "userMessageCount7": 4,
                    "completeDelivery": {"value": 0.75, "measuredCount": 3, "totalCount": 4},
                    "activity": "middle",
                    "activityLabel": "中アクティブ",
                }
            ]
        }


class _Exports:
    def __init__(self) -> None:
        self.jobs = {}

    def put(self, job):
        self.jobs[job["job_id"]] = dict(job)
        return dict(job)

    def cleanup_expired(self, *, limit=200):
        return 0

    def get(self, job_id):
        return self.jobs.get(job_id)

    @staticmethod
    def is_expired(_job):
        return False


def _settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def test_export_is_job_only_owner_scoped_and_not_legacy_get_csv() -> None:
    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = _Analytics
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        response = client.post(
            "/api/export/jobs",
            json={"kind": "users", "preset": "last_7d"},
            headers=headers,
        )
        assert response.status_code == 201
        job_id = response.json()["jobId"]
        download = client.get(f"/api/export/jobs/{job_id}/download", headers=headers)
        assert download.status_code == 200
        assert "利用者" in download.text
        assert client.get("/api/export/users.csv", headers=headers).status_code == 404

        exports.jobs[job_id]["created_by"] = "other@example.com"
        assert client.get(f"/api/export/jobs/{job_id}", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()
