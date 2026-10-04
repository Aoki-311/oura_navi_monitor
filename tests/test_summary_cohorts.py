"""Integration of department cohorts across the first summary's data owners."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_export_job_repository
from app.domain.analysis_scopes import AnalysisScope, SCOPE_POLICY_VERSION
from app.main import app
from app.settings import Settings, get_settings
from app.services.analytics_service import AnalyticsService as ProductionAnalyticsService, AnalyticsSnapshotConflictError
from app.services.news_usage_service import NewsUsageSnapshotConflictError
from test_analytics_service import AnalyticsService, _Analytics, _Directory, _Pipeline, _window
from test_export_api import _Analytics as ExportAnalytics, _Exports, _EXPORT_SNAPSHOT, _settings
from test_news_usage_dashboard import repository as news_repository, news_event, NOW
from test_news_usage_service import _roster, _service, _window as news_window, _Repository, _publication


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


# Frozen with project_user_scope from fd2c0509e1bbd8bde9130a003f1adc982911246d.
# These literal receipts intentionally do not use today's projector/hash code.
LEGACY_RECEIPTS = {
    "global_roster_fingerprint": "1b8a9c1a39efb5b22b728e41dd168c9e434ff66cb773eea9edd4418b6a5c2812",
    "global_content_fingerprint": "354a20e880b5c24dd5de99ab4b69a935bd6cf0032d918ec1e4269f46e431cb7b",
    "user_map_roster_fingerprint": "0dbd062794277b468b2530aeae170f4e0d1fc454f828d85627da4cc09d59be13",
    "user_map_content_fingerprint": "a07ac0db4566af237dde3c6f7a7c31e2ed74a20d75099469ae1cb5e2fec984bf",
}


def legacy_rows():
    frozen = datetime(2026, 9, 30, tzinfo=timezone.utc)
    return [{
        "snapshot_run_id": "roster-new", "snapshot_created_at": frozen,
        "roster_id": key, "user_id": key, "name": key, "email": f"{key}@example.com",
        "area": "関西", "area_key": "関西", "workplace": "大阪", "mr_experience": "10年",
        "role": role, "department": department, "is_active": active,
        "global_scope_enabled": global_enabled, "user_map_scope_enabled": user_map_enabled,
        "is_admin": key == "admin", "updated_at": frozen,
        "label_ids_json": '["label-1"]' if key == "employee" else "[]",
        "labels_json": json.dumps([{
            "color": "#386dff", "is_active": True, "label_id": "label-1",
            "name": "参考", "updated_at": "2026-09-30 00:00:00+00:00", "usage_count": 0,
        }], ensure_ascii=False) if key == "employee" else "[]",
        "roster_isolated_count": 0, "roster_issue_counts_json": "{}",
        "roster_diagnostic_fingerprint": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        "global_label_catalog_status": "available", "global_label_catalog_issues_json": "[]",
        "user_map_label_catalog_status": "available", "user_map_label_catalog_issues_json": "[]",
    } for key, role, department, active, global_enabled, user_map_enabled in [
        ("dm", "本社MR", "DM専任", True, True, True),
        ("employee", "社員MR", "DM専任", True, False, True),
        ("hq", "本社MR", "DM本社", True, True, True),
        ("admin", "本社MR", "管理者", True, False, False),
        ("inactive", "本社MR", "DM専任", False, True, True),
    ]]


def legacy_chat_service():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = legacy_rows()
    events = [{
        "roster_id": row["roster_id"], "question_ts": now - timedelta(hours=1),
        "question_date": "2026-10-01", "area_key": "関西", "valid_question": True,
        "device_class": "desktop", "mode": "internal",
    } for row in rows]
    analytics = _Analytics(events)
    analytics.published_roster_rows = rows
    publication = {
        "publication_state_available": True, "published_run_id": "roster-new",
        "scope_policy_version": "summary_role_v1", "data_through": now,
        **LEGACY_RECEIPTS,
    }
    service = ProductionAnalyticsService(
        analytics=analytics,
        pipeline=SimpleNamespace(publication_snapshot=lambda: deepcopy(publication)),
        directory=None, settings=Settings(),
    )
    return service, _window(now), publication


@pytest.mark.parametrize("cohort,population", [("all", 2), ("dm", 2), ("hcs", 0)])
def test_frozen_v1_publication_serves_every_summary_panel_with_current_membership(cohort, population):
    service, window, publication = legacy_chat_service()
    payloads = [getattr(service, name)(window=window, cohort=cohort) for name in (
        "overview", "environment", "trend", "regions", "overview_users",
    )]
    for payload in payloads:
        assert payload["scopePolicyVersion"] == SCOPE_POLICY_VERSION
        assert payload["publishedRunId"] == publication["published_run_id"]
        assert payload["scopeUserCount"] == population
        assert payload["cohort"] == cohort
        assert payload["rosterFingerprint"] == payloads[0]["rosterFingerprint"]
        assert payload["contentFingerprint"] == payloads[0]["contentFingerprint"]
    assert payloads[0]["analyticsQuality"]["totalEventCount"] == population
    assert sum(row["count"] for row in payloads[1]["deviceDistribution"]) == population
    assert sum(row["questions"] for row in payloads[3]["regions"]) == population
    assert {row["rosterId"] for row in payloads[4]["users"]} == (
        {"dm", "employee"} if population else set()
    )
    assert {row["rosterId"] for row in service.users(window=window)["users"]} == {
        "dm", "employee", "hq",
    }
    # The verified raw publication remains immutable, including legacy flags.
    assert service._analytics.published_roster_rows == legacy_rows()
    assert service.overview_users(window=window)["users"][0]["department"] == "MR(DM)"


@pytest.mark.parametrize("corruption", [
    "unknown_policy", "missing_receipt", "name", "scope_flag", "department",
    "user_map_receipt", "content_receipt", "label", "run_id",
])
def test_frozen_v1_receipts_fail_closed_before_even_an_empty_cohort(corruption):
    service, window, publication = legacy_chat_service()
    rows = service._analytics.published_roster_rows
    if corruption == "unknown_policy":
        publication["scope_policy_version"] = "summary_role_v0"
    elif corruption == "missing_receipt":
        publication["global_roster_fingerprint"] = ""
    elif corruption == "name":
        rows[1]["name"] = "altered employee outside v1 global"
    elif corruption == "scope_flag":
        rows[1]["global_scope_enabled"] = True
    elif corruption == "department":
        rows[0]["department"] = "MR(HCS)"
    elif corruption == "user_map_receipt":
        publication["user_map_roster_fingerprint"] = "wrong"
    elif corruption == "content_receipt":
        publication["user_map_content_fingerprint"] = "wrong"
    elif corruption == "label":
        labels = json.loads(rows[1]["labels_json"])
        labels[0]["name"] = "altered label outside v1 global"
        rows[1]["labels_json"] = json.dumps(labels)
    else:
        rows[0]["snapshot_run_id"] = "different-run"
    with pytest.raises(AnalyticsSnapshotConflictError):
        service.overview(window=window, cohort="hcs")


def test_legacy_summary_preserves_label_diagnostics_for_newly_included_employees():
    service, window, publication = legacy_chat_service()
    for row in service._analytics.published_roster_rows:
        row["labels_json"] = "[]"
        row["user_map_label_catalog_status"] = "partial"
        row["user_map_label_catalog_issues_json"] = '["unknown_label_reference"]'
    # Also frozen using the predecessor's content_fingerprint implementation.
    publication["user_map_content_fingerprint"] = "b546f43f29ac8c5390b52f4b4ff67b4e414d476c1f4f97911c40a79724ec9861"
    payload = service.overview_users(window=window)
    assert {row["rosterId"] for row in payload["users"]} == {"dm", "employee"}
    assert payload["contentDiagnostics"]["state"] == "degraded"
    assert payload["contentDiagnostics"]["labelCatalogStatus"] == "partial"
    assert "unknown_label_reference" in payload["contentDiagnostics"]["issues"]


class LegacyNewsRepository(_Repository):
    def __init__(self):
        rows = legacy_rows()
        publication = {**_publication(), **LEGACY_RECEIPTS, "scope_policy_version": "summary_role_v1"}
        super().__init__(
            publications=[deepcopy(publication), deepcopy(publication)], roster=rows,
            events=[news_event("detail_view", index + 1, roster_id=row["roster_id"], user_id=row["user_id"])
                    for index, row in enumerate(rows)],
        )
        self.event_scopes = []

    def published_events(self, *, scope, **kwargs):
        self.event_scopes.append(scope)
        # Reproduce the SQL repository's stored-flag filter, so asking for the
        # legacy global scope would incorrectly hide the employee's activity.
        members = {row["roster_id"] for row in self.roster
                   if row["is_active"] and row[f"{scope.value}_scope_enabled"]}
        return [row for row in self.events if row["roster_id"] in members]


@pytest.mark.parametrize("cohort,population", [("all", 2), ("dm", 2), ("hcs", 0)])
def test_news_frozen_v1_receipts_keep_current_summary_and_user_map_population(cohort, population):
    repository = LegacyNewsRepository()
    dashboard = _service(repository).dashboard(window=news_window(), now=NOW, cohort=cohort)
    assert repository.event_scopes == [AnalysisScope.USER_MAP]
    assert dashboard["totals"]["contentClicks"] == population
    report = _service(LegacyNewsRepository()).report(window=news_window(), now=NOW, cohort=cohort)
    assert report["kpis"]["scopeUsers"] == report["kpis"]["activeUsers"] == population
    # News distinguishes the effective filter from its original publication.
    assert report["scopePolicyVersion"] == SCOPE_POLICY_VERSION
    assert report["publicationScopePolicyVersion"] == "summary_role_v1"
    assert report["rosterFingerprint"] == LEGACY_RECEIPTS["global_roster_fingerprint"]
    personal = _service(LegacyNewsRepository()).dashboard(window=news_window(), now=NOW, roster_id="hq")
    assert personal["scope"] == "user_map"
    assert personal["totals"]["contentClicks"] == 1


@pytest.mark.parametrize("corruption", ["unknown_policy", "name", "scope_flag", "user_map_receipt", "department"])
def test_news_legacy_receipts_still_reject_unknown_or_corrupt_snapshots(corruption):
    repository = LegacyNewsRepository()
    if corruption == "unknown_policy":
        repository.publications[0]["scope_policy_version"] = "unknown"
    elif corruption == "user_map_receipt":
        repository.publications[0]["user_map_roster_fingerprint"] = "wrong"
    elif corruption == "name":
        repository.roster[1]["name"] = "altered employee outside v1 global"
    elif corruption == "department":
        repository.roster[0]["department"] = "MR(HCS)"
    else:
        repository.roster[1]["global_scope_enabled"] = True
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
