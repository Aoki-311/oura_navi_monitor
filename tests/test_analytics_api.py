from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_user_management_service
from app.main import app
from app.settings import get_settings


def _freshness():
    return {
        "state": "fresh",
        "dataThrough": "2026-08-23T01:00:00Z",
        "refreshCadenceMinutes": 180,
        "expectedDelayMinutes": 5,
        "staleAfterMinutes": 240,
        "nextPlannedRefreshAt": "2026-08-23T03:05:00Z",
    }


def _analytics_quality(total: int = 0):
    axis = {
        "measuredCount": 0,
        "totalCount": total,
        "measurementState": "no_usage" if total == 0 else "not_measured",
        "isolatedCount": total,
    }
    return {
        "contractVersion": "dashboard_events_v2",
        "isolatedEventCount": total,
        "totalEventCount": total,
        "classification": dict(axis),
        "task": dict(axis),
        "product": dict(axis),
        "sourcePipeline": {
            "publishedRunId": "run-1",
            "latestRunId": "run-1",
            "latestRunStatus": "succeeded",
            "latestRunErrorCode": "",
            "latestRunFinishedAt": "2026-08-23T01:00:00Z",
            "state": "clean",
            "quarantinedEventCount": 0,
            "deduplicatedDeliveryCount": 0,
            "repairedDuplicateFactCount": 0,
            "axisUnmeasuredFindingCount": 0,
            "batchBlockingFailureCount": 0,
        },
    }


class FakeAnalyticsService:
    def overview(self, **_kwargs):
        return {
            "scope": "global",
            "scopeUserCount": 69,
            "freshness": _freshness(),
            "analyticsQuality": _analytics_quality(64),
            "kpis": {
                "activeUsers": 20,
                "adoptionRate": 20 / 69,
                "returnRate": 0.5,
                "questionsPerActiveUser": 3.2,
                "completeDelivery": {"value": 0.91, "measuredCount": 20, "totalCount": 20, "measurementState": "measured"},
                "p95Latency": {"valueMs": 72000, "measuredCount": 20, "totalCount": 20, "measurementState": "measured"},
            },
            "hourlyQuestions": [],
            "deviceDistribution": [],
            "deviceMeasurement": {"measuredCount": 0, "totalCount": 64, "measurementState": "not_measured"},
            "modeDistribution": [],
            "modeMeasurement": {"measuredCount": 0, "totalCount": 64, "measurementState": "not_measured"},
            "usageTrend": [],
            "requestTasks": [],
            "taskMeasurement": {"measuredCount": 0, "totalCount": 64, "measurementState": "not_measured"},
            "activityDistribution": [],
            "activityByArea": [],
            "activityByRole": [],
            "topProducts": [],
            "productTaskMatrix": [],
            "productResolution": {
                "candidateCount": 0,
                "resolvedCount": 0,
                "unresolvedQuestions": 0,
                "resolutionRate": None,
                "measuredCount": 0,
                "totalCount": 64,
                "measurementState": "not_measured",
            },
        }

    def regions(self, **_kwargs):
        return {"scopeUserCount": 80, "freshness": _freshness(), "regions": []}

    def users(self, **_kwargs):
        return {"scopeUserCount": 80, "freshness": _freshness(), "users": []}

    def user_detail(self, roster_id: str, **_kwargs):
        return {
            "freshness": _freshness(),
            "analyticsQuality": _analytics_quality(),
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
                "completeDelivery": {"value": None, "measuredCount": 0, "totalCount": 0, "measurementState": "no_usage"},
            },
            "comparisons": {
                "area": {
                    "label": "関西",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, "measuredCount": 0, "totalCount": 1, "measurementState": "not_measured"},
                },
                "role": {
                    "label": "本社MR",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, "measuredCount": 0, "totalCount": 1, "measurementState": "not_measured"},
                },
            },
            "trend": [],
            "products": [],
            "productResolution": {
                "candidateCount": 0,
                "resolvedCount": 0,
                "unresolvedQuestions": 0,
                "resolutionRate": None,
                "measuredCount": 0,
                "totalCount": 0,
                "measurementState": "no_usage",
            },
            "tasks": [],
            "taskMeasurement": {"measuredCount": 0, "totalCount": 0, "measurementState": "no_usage"},
            "questionCategories": [],
            "questionCategoryMeasurement": {"measuredCount": 0, "totalCount": 0, "measurementState": "no_usage"},
            "modes": [],
            "modeMeasurement": {"measuredCount": 0, "totalCount": 0, "measurementState": "no_usage"},
            "devices": [],
            "deviceMeasurement": {"measuredCount": 0, "totalCount": 0, "measurementState": "no_usage"},
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
        assert response.headers["cache-control"] == (
            "no-cache, no-store, must-revalidate"
        )
        assert response.headers["pragma"] == "no-cache"
        assert response.json()["kpis"]["completeDelivery"]["value"] == 0.91
        assert response.json()["deviceMeasurement"]["measurementState"] == "not_measured"
        assert client.get("/api/metrics/dashboard", headers=headers).status_code == 404
        assert client.get("/ops", headers=headers).status_code == 404
        assert client.get("/ops-legacy", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_frontend_api_requests_always_bypass_browser_http_cache() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "api" / "client.js"
    ).read_text(encoding="utf-8")

    assert 'cache: "no-store"' in source


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
        assert response.json()["modeMeasurement"]["measurementState"] == "no_usage"
    finally:
        app.dependency_overrides.clear()
