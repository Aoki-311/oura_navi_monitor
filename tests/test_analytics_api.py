from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_user_management_service
from app.main import app
from app.settings import get_settings


def _freshness():
    return {
        "state": "fresh",
        "dataThrough": "2026-08-23T01:00:00Z",
    }


def _content_diagnostics():
    return {
        "state": "complete",
        "labelCatalogStatus": "available",
        "rosterStatus": "available",
        "rosterIsolatedCount": 0,
        "rosterIssueCounts": {},
        "issues": [],
    }


def _scope(scope: str) -> dict:
    return {
        "scope": scope,
        "scopePolicyVersion": "summary_role_v1",
        "rosterFingerprint": f"roster-{scope}",
        "contentFingerprint": f"content-{scope}",
        "publishedRunId": "run-1",
        "windowStart": "2026-08-17T00:00:00Z",
        "windowEnd": "2026-08-24T00:00:00Z",
        "windowTimezone": "Asia/Tokyo",
    }


def _coverage(measured: int, total: int, reason: str | None = None) -> dict:
    state = "no_usage" if total == 0 else "not_measured" if measured == 0 else "partial" if measured < total else "measured"
    return {
        "measuredCount": measured,
        "totalCount": total,
        "measurementState": state,
        "measurementReason": reason or ("no_usage" if state == "no_usage" else "complete" if state == "measured" else "historical_unavailable"),
    }


def _analytics_quality(total: int = 0):
    axis = {**_coverage(0, total), "isolatedCount": total}
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
            **_scope("global"),
            "contentDiagnostics": {
                "state": "complete",
                "labelCatalogStatus": "not_applicable",
                "rosterStatus": "available",
                "rosterIsolatedCount": 0,
                "rosterIssueCounts": {},
                "issues": [],
            },
            "scopeUserCount": 69,
            "freshness": _freshness(),
            "analyticsQuality": _analytics_quality(64),
            "kpis": {
                "activeUsers": 20,
                "adoptionRate": 20 / 69,
                "returnRate": 0.5,
                "questionsPerActiveUser": 3.2,
                "completeDelivery": {"value": 0.91, **_coverage(20, 20)},
                "p95Latency": {"valueMs": 72000, **_coverage(20, 20)},
            },
            "hourlyQuestions": [],
            "deviceDistribution": [],
            "deviceMeasurement": _coverage(0, 64),
            "modeDistribution": [],
            "modeMeasurement": _coverage(0, 64),
            "usageTrend": [],
            "requestTasks": [],
            "taskMeasurement": _coverage(0, 64),
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
                **_coverage(0, 64),
            },
        }

    def regions(self, **_kwargs):
        return {
            **_scope("global"),
            "contentDiagnostics": {
                "state": "complete",
                "labelCatalogStatus": "not_applicable",
                "rosterStatus": "available",
                "rosterIsolatedCount": 0,
                "rosterIssueCounts": {},
                "issues": [],
            },
            "scopeUserCount": 69,
            "freshness": _freshness(),
            "regions": [],
        }

    def users(self, **_kwargs):
        return {
            **_scope("user_map"),
            "contentDiagnostics": _content_diagnostics(),
            "scopeUserCount": 80,
            "freshness": _freshness(),
            "users": [],
        }

    def user_detail(self, roster_id: str, **_kwargs):
        return {
            **_scope("user_map"),
            "contentDiagnostics": _content_diagnostics(),
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
                "completeDelivery": {"value": None, **_coverage(0, 0)},
                "p95Latency": {"valueMs": None, **_coverage(0, 0)},
            },
            "comparisons": {
                "area": {
                    "label": "関西",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, **_coverage(0, 1, "population_without_usage")},
                },
                "role": {
                    "label": "本社MR",
                    "peerCount": 1,
                    "averageQuestions": 0.0,
                    "averageActiveDays": 0.0,
                    "averageCompleteDelivery": {"value": None, **_coverage(0, 1, "population_without_usage")},
                },
            },
            "trend": [],
            "products": [],
            "productResolution": {
                "candidateCount": 0,
                "resolvedCount": 0,
                "unresolvedQuestions": 0,
                "resolutionRate": None,
                **_coverage(0, 0),
            },
            "tasks": [],
            "taskMeasurement": _coverage(0, 0),
            "questionCategories": [],
            "questionCategoryMeasurement": _coverage(0, 0),
            "modes": [],
            "modeMeasurement": _coverage(0, 0),
            "devices": [],
            "deviceMeasurement": _coverage(0, 0),
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


def test_response_model_rejects_legacy_diagnostics_missing_roster_receipt() -> None:
    class LegacyDiagnosticsService(FakeAnalyticsService):
        def overview(self, **kwargs):
            payload = super().overview(**kwargs)
            diagnostics = payload["contentDiagnostics"]
            diagnostics.pop("rosterStatus")
            diagnostics.pop("rosterIsolatedCount")
            diagnostics.pop("rosterIssueCounts")
            return payload

    app.dependency_overrides[get_analytics_service] = (
        lambda: LegacyDiagnosticsService()
    )
    app.dependency_overrides[get_settings] = _settings
    client = TestClient(app)
    try:
        with pytest.raises(ResponseValidationError) as captured:
            client.get(
                "/api/analytics/overview",
                headers={"x-monitor-admin-email": "admin@example.com"},
            )
    finally:
        app.dependency_overrides.clear()

    missing_fields = {
        error["loc"][-1]
        for error in captured.value.errors()
        if error["type"] == "missing"
    }
    assert missing_fields == {
        "rosterStatus",
        "rosterIsolatedCount",
        "rosterIssueCounts",
    }


def test_frontend_api_requests_always_bypass_browser_http_cache() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "api" / "client.js"
    ).read_text(encoding="utf-8")

    assert 'cache: "no-store"' in source


def test_shared_as_of_query_anchors_all_summary_api_windows() -> None:
    class CapturingAnalyticsService(FakeAnalyticsService):
        def __init__(self) -> None:
            self.windows = []

        def overview(self, **kwargs):
            self.windows.append(kwargs["window"])
            return super().overview(**kwargs)

        def regions(self, **kwargs):
            self.windows.append(kwargs["window"])
            return super().regions(**kwargs)

        def overview_users(self, **kwargs):
            self.windows.append(kwargs["window"])
            payload = super().users(**kwargs)
            payload.update(_scope("global"))
            payload["scopeUserCount"] = 69
            return payload

        def user_detail(self, roster_id: str, **kwargs):
            self.windows.append(kwargs["window"])
            return super().user_detail(roster_id, **kwargs)

    service = CapturingAnalyticsService()
    app.dependency_overrides[get_analytics_service] = lambda: service
    app.dependency_overrides[get_settings] = _settings
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    expected_anchor = datetime.now(timezone.utc).replace(microsecond=123456)
    anchor = expected_anchor.isoformat().replace("+00:00", "Z")
    try:
        for path in ("/overview", "/regions", "/overview/users", "/users/roster_1"):
            response = client.get(
                f"/api/analytics{path}",
                params={"preset": "last_7d", "as_of": anchor},
                headers=headers,
            )
            assert response.status_code == 200

        assert len(service.windows) == 4
        assert {window.start_utc for window in service.windows} == {
            service.windows[0].start_utc
        }
        assert {window.end_utc for window in service.windows} == {
            expected_anchor
        }
    finally:
        app.dependency_overrides.clear()


def test_dashboard_document_and_static_assets_are_never_served_from_old_cache() -> None:
    app.dependency_overrides[get_settings] = _settings
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        for path in ("/dashboard", "/dashboard-assets/app.js"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200
            assert response.headers["cache-control"] == (
                "no-cache, no-store, must-revalidate"
            )
            assert response.headers["pragma"] == "no-cache"
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
        assert response.json()["contentDiagnostics"]["state"] == "complete"
        assert response.json()["modeMeasurement"]["measurementState"] == "no_usage"
    finally:
        app.dependency_overrides.clear()
