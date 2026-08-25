from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_user_management_service
from app.main import app
from app.settings import get_settings


class FakeAnalyticsService:
    def overview(self, **_kwargs):
        return {
            "scope": "global",
            "scopeUserCount": 69,
            "freshness": {"state": "fresh", "dataThrough": "2026-08-23T01:00:00Z"},
            "kpis": {
                "activeUsers": 20,
                "adoptionRate": 20 / 69,
                "returnRate": 0.5,
                "questionsPerActiveUser": 3.2,
                "completeDelivery": {"value": 0.91, "measuredCount": 20, "totalCount": 20},
                "p95Latency": {"valueMs": 72000, "measuredCount": 20, "totalCount": 20},
            },
            "hourlyQuestions": [],
            "deviceDistribution": [],
            "modeDistribution": [],
            "usageTrend": [],
            "questionCategories": [],
            "activityDistribution": [],
            "activityByArea": [],
            "activityByRole": [],
            "topProducts": [],
            "productQuestionMatrix": [],
            "productResolution": {
                "candidateCount": 0,
                "resolvedCount": 0,
                "unresolvedQuestions": 0,
                "resolutionRate": None,
            },
        }

    def regions(self, **_kwargs):
        return {"scopeUserCount": 80, "freshness": {"state": "fresh", "dataThrough": "2026-08-23T01:00:00Z"}, "regions": []}

    def users(self, **_kwargs):
        return {"scopeUserCount": 80, "freshness": {"state": "fresh", "dataThrough": "2026-08-23T01:00:00Z"}, "users": []}

    def user_detail(self, roster_id: str, **_kwargs):
        return {
            "freshness": {"state": "fresh", "dataThrough": "2026-08-23T01:00:00Z"},
            "profile": {
                "rosterId": roster_id,
                "name": "利用者",
                "email": "user@example.com",
                "area": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "mrExperience": "8年",
                "labels": [],
            },
            "summary": {
                "lastActiveAt": "",
                "activeDays": 0,
                "questions": 0,
                "questionsPerActiveDay": None,
                "completeDelivery": {"value": None, "measuredCount": 0, "totalCount": 0},
            },
            "comparisons": {
                "area": {
                    "label": "関西",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, "measuredCount": 0, "totalCount": 1},
                },
                "role": {
                    "label": "本社MR",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, "measuredCount": 0, "totalCount": 1},
                },
            },
            "trend": [],
            "products": [],
            "productResolution": {
                "candidateCount": 0,
                "resolvedCount": 0,
                "unresolvedQuestions": 0,
                "resolutionRate": None,
            },
            "tasks": [],
            "questionCategories": [],
            "modes": [],
            "devices": [],
        }


def _settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def test_only_unversioned_analytics_contract_is_exposed() -> None:
    app.dependency_overrides[get_analytics_service] = lambda: FakeAnalyticsService()
    app.dependency_overrides[get_settings] = _settings
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        response = client.get("/api/analytics/overview", headers=headers)
        assert response.status_code == 200
        assert response.json()["kpis"]["completeDelivery"]["value"] == 0.91
        assert client.get("/api/metrics/dashboard", headers=headers).status_code == 404
        assert client.get("/ops", headers=headers).status_code == 404
        assert client.get("/ops-legacy", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_user_url_uses_roster_id_and_never_email() -> None:
    app.dependency_overrides[get_analytics_service] = lambda: FakeAnalyticsService()
    app.dependency_overrides[get_settings] = _settings
    client = TestClient(app)
    try:
        response = client.get(
            "/api/analytics/users/roster_123",
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
        assert response.status_code == 200
        assert response.json()["profile"]["rosterId"] == "roster_123"
    finally:
        app.dependency_overrides.clear()
