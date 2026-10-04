"""Integration of department cohorts across the first summary's data owners."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_export_job_repository
from app.domain.analysis_scopes import SCOPE_POLICY_VERSION
from app.main import app
from app.settings import Settings, get_settings
from app.services.analytics_service import AnalyticsService as ProductionAnalyticsService, AnalyticsSnapshotConflictError
from app.services.news_usage_service import NewsUsageSnapshotConflictError
from test_analytics_service import AnalyticsService, _Analytics, _Directory, _Pipeline, _window
from test_export_api import _Analytics as ExportAnalytics, _Exports, _EXPORT_SNAPSHOT, _settings
from test_news_usage_dashboard import repository as news_repository, news_event, NOW
from test_news_usage_service import _roster, _service, _window as news_window


def chat_service():
    now = datetime.now(timezone.utc)
    directory = _Directory()
    template = directory.users[0]
    directory.users = [
        {**template, "roster_id": key, "email": f"{key}@example.com", "name": key,
         "department": department, "role": role, "area": area, "area_key": area, "is_active": active}
        for key, department, role, area, active in [
            ("dm", "DM専任", "本社MR", "関西", True),
            ("dm_idle", "MR(DM)", "コントラクトMR", "関西", True),
            ("hcs", "MR(HCS)", "社員MR", "関西", True),
            ("hcs_idle", "MR(HCS)", "社員MR", "関東A", True),
            ("hq", "DM本社", "社員MR", "関西", True),
            ("admin", "管理者", "社員MR", "関西", True),
            ("inactive", "MR(HCS)", "社員MR", "関西", False),
        ]
    ]
    rows = [
        {"roster_id": user["roster_id"], "question_ts": now - timedelta(hours=1),
         "question_date": now.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat(), "valid_question": True,
         "area_key": user["area_key"], "device_class": "desktop", "mode": "internal"}
        for user in directory.users if not user["roster_id"].endswith("idle")
        for _ in range(2 if user["roster_id"] == "hcs" else 1)
    ]
    return AnalyticsService(analytics=_Analytics(rows), pipeline=_Pipeline(), directory=directory, settings=Settings()), _window(now)


@pytest.mark.parametrize("cohort,population,questions,ids", [
    ("all", 4, 3, {"dm", "dm_idle", "hcs", "hcs_idle"}),
    ("dm", 2, 1, {"dm", "dm_idle"}),
    ("hcs", 2, 2, {"hcs", "hcs_idle"}),
])
def test_summary_cohort_is_shared_by_all_panels_and_zero_usage_denominators(cohort, population, questions, ids):
    service, window = chat_service()
    overview = service.overview(window=window, cohort=cohort)
    environment = service.environment(window=window, cohort=cohort)
    trend = service.trend(window=window, cohort=cohort)
    regions = service.regions(window=window, cohort=cohort)
    users = service.overview_users(window=window, cohort=cohort)
    for payload in (overview, environment, trend, regions, users):
        assert payload["cohort"] == cohort
        assert payload["scopeUserCount"] == population
        assert payload["rosterFingerprint"] == overview["rosterFingerprint"]
    assert overview["analyticsQuality"]["totalEventCount"] == questions
    assert overview["kpis"]["adoptionRate"] == 0.5
    assert sum(row["count"] for row in overview["activityDistribution"]) == population
    assert sum(row["rosterUsers"] for row in regions["regions"]) == population
    assert sum(row["questions"] for row in regions["regions"]) == questions
    assert sum(row["count"] for row in environment["deviceDistribution"]) == questions
    assert trend["usageTrend"] == overview["usageTrend"]
    assert {row["rosterId"] for row in users["users"]} == ids
    assert sum(row["userMessageCountInPeriod"] for row in users["users"]) == questions


def test_cohort_composes_with_area_and_preserves_publication_identity():
    service, window = chat_service()
    all_users = service.overview(window=window)
    hcs = service.overview(window=window, cohort="hcs", area_key="関東A")
    assert hcs["scopeUserCount"] == 1
    assert hcs["kpis"]["activeUsers"] == 0
    assert hcs["kpis"]["adoptionRate"] == 0
    assert hcs["rosterFingerprint"] == all_users["rosterFingerprint"]
    assert hcs["contentFingerprint"] == all_users["contentFingerprint"]
    users = service.overview_users(window=window, cohort="hcs", area_key="関東A")
    assert [row["rosterId"] for row in users["users"]] == ["hcs_idle"]
    # The second page continues to contain headquarters users.
    assert "hq" in {row["rosterId"] for row in service.users(window=window)["users"]}


def news_data():
    template = _roster()[0]
    roster = [template, {
        **template, "roster_id": "hcs", "user_id": "hcs", "email": "hcs@example.com",
        "department": "MR(HCS)", "role": "社員MR",
    }, {
        **template, "roster_id": "hcs_idle", "user_id": "hcs_idle", "email": "hcs-idle@example.com",
        "department": "MR(HCS)", "role": "社員MR", "area": "関東A", "area_key": "関東A",
    }]
    events = [news_event("detail_view", 1), news_event("detail_view", 2, roster_id="hcs", user_id="hcs"),
              news_event("tab_view", 3, roster_id="hcs", user_id="hcs")]
    return events, roster


@pytest.mark.parametrize("cohort,population,clicks,actions", [("all", 3, 2, 3), ("dm", 1, 1, 1), ("hcs", 2, 1, 2)])
def test_news_cohort_matches_overview_and_report_population(cohort, population, clicks, actions):
    events, roster = news_data()
    dashboard = _service(news_repository(events, roster)).dashboard(window=news_window(), now=NOW, cohort=cohort)
    report = _service(news_repository(events, roster)).report(window=news_window(), now=NOW, cohort=cohort)
    assert dashboard["cohort"] == report["cohort"] == cohort
    assert dashboard["totals"]["contentClicks"] == clicks
    assert sum(row["contentClicks"] for row in dashboard["trend"]) == clicks
    assert report["kpis"]["scopeUsers"] == population
    assert report["kpis"]["totalActions"] == actions
    assert report["kpis"]["adoptionRate"] == (1 / 2 if cohort == "hcs" else 1 if cohort == "dm" else 2 / 3)
    assert len(report["organizations"]["users"]) == population
    area = _service(news_repository(events, roster)).dashboard(window=news_window(), now=NOW, cohort=cohort, area_key="関東A")
    assert area["totals"]["contentClicks"] == 0


def test_legacy_policy_cannot_be_mislabeled_as_new_cohort():
    service, _ = chat_service()
    publication = {**service._publication_snapshot(), "scope_policy_version": "summary_role_v1"}
    with pytest.raises(AnalyticsSnapshotConflictError):
        ProductionAnalyticsService._run_versioned_snapshot_id(publication)
    events, roster = news_data()
    repository = news_repository(events, roster)
    repository.publications[0]["scope_policy_version"] = "summary_role_v1"
    with pytest.raises(NewsUsageSnapshotConflictError):
        _service(repository).dashboard(window=news_window(), now=NOW, cohort="hcs")


@pytest.mark.parametrize("path", ["overview", "environment", "trend", "regions", "overview/users"])
def test_analytics_routes_bind_and_validate_cohort(path):
    service, _ = chat_service()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = lambda: service
    try:
        client = TestClient(app)
        headers = {"x-monitor-admin-email": "admin@example.com"}
        response = client.get(f"/api/analytics/{path}", params={"cohort": "hcs"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["cohort"] == "hcs"
        assert response.json()["scopeUserCount"] == 2
        assert client.get(f"/api/analytics/{path}", params={"cohort": "unknown"}, headers=headers).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_export_binds_cohort_to_csv_and_idempotency():
    class CohortExport(ExportAnalytics):
        def overview_users(self, **kwargs):
            payload = super().overview_users(**kwargs)
            payload["cohort"] = kwargs["cohort"]
            payload["users"][0]["department"] = "MR(HCS)"
            return payload

    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = CohortExport
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    body = {
        "kind": "overview_users", "cohort": "hcs", "expectedPublishedRunId": "run-1",
        "expectedRosterFingerprint": "roster-fingerprint-1", "expectedScopePolicyVersion": SCOPE_POLICY_VERSION,
        **_EXPORT_SNAPSHOT, "idempotencyKey": "cohort-export-1",
    }
    try:
        client = TestClient(app)
        headers = {"x-monitor-admin-email": "admin@example.com"}
        response = client.post("/api/export/jobs", json=body, headers=headers)
        assert response.status_code == 201
        assert "summary_hcs_" in response.json()["filename"]
        download = client.get(response.json()["downloadUrl"], headers=headers)
        rows = list(csv.DictReader(io.StringIO(download.text.lstrip("\ufeff"))))
        assert rows[0]["対象MR"] == "hcs"
        job = exports.jobs[response.json()["jobId"]]
        assert job["snapshot_metadata"]["cohort"] == "hcs"
        conflict = client.post("/api/export/jobs", json={**body, "cohort": "dm"}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        app.dependency_overrides.clear()


def test_news_overview_route_binds_cohort_and_keeps_unavailable_states_scoped():
    from app.routers.news_usage import get_news_usage_service
    from test_news_usage_service import _DisabledRepository, NewsUsageConfiguration

    events, roster = news_data()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: _service(news_repository(events, roster))
    try:
        client = TestClient(app)
        headers = {"x-monitor-admin-email": "admin@example.com"}
        params = {"start": "2026-09-02", "end": "2026-09-04", "cohort": "hcs"}
        response = client.get("/api/news-usage/overview", params=params, headers=headers)
        assert response.status_code == 200
        assert response.json()["cohort"] == "hcs"
        assert response.json()["totals"]["contentClicks"] == 1
        assert client.get("/api/news-usage/overview", params={**params, "cohort": "unknown"}, headers=headers).status_code == 422
        app.dependency_overrides[get_news_usage_service] = lambda: _service(_DisabledRepository(NewsUsageConfiguration("disabled", "", None)))
        unavailable = client.get("/api/news-usage/overview", params=params, headers=headers)
        assert unavailable.status_code == 200
        assert unavailable.json()["cohort"] == "hcs"
        assert unavailable.json()["totals"] is None
    finally:
        app.dependency_overrides.clear()
