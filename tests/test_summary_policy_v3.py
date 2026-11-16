"""New departments share one population across Chat, News and CSV consumers."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.domain.analysis_scopes import (
    AnalysisScope, Department, SCOPE_POLICY_VERSION, membership_for,
)
from app.domain.published_roster import validated_published_roster
from app.main import app
from app.routers.news_usage import get_news_usage_service
from app.settings import Settings, get_settings
from test_analytics_service import AnalyticsService, _Analytics, _Directory, _Pipeline, _window
from test_export_api import _settings
from test_news_usage_dashboard import NOW, news_event, repository
from test_news_usage_service import _roster, _service, _window as news_window
from test_summary_cohorts import legacy_rows


# Unequal denominators catch accidental summation/averaging of cohort rates.
POPULATION = [
    ("dm", "MR(DM)", True, 1), ("dm_idle", "MR(DM)", True, 0),
    ("hcs", "MR(HCS)", True, 2), ("hcs_idle", "MR(HCS)", True, 0),
    ("block", "MR(ブロック)", True, 3),
    ("block_second", "MR(ブロック)", True, 1),
    ("block_idle", "MR(ブロック)", True, 0),
    ("block_inactive", "MR(ブロック)", False, 5),
    ("misw", "MisW本社", True, 6),
    ("sales", "セールスマーケ本社", True, 7),
    ("hq", "DM本社", True, 8), ("admin", "管理者", True, 9),
]


def chat_service():
    now = datetime.now(timezone.utc)
    directory = _Directory()
    template = directory.users[0]
    directory.users = [{
        **template, "roster_id": key, "email": f"{key}@example.com", "name": key,
        "department": department, "role": "社員MR", "is_active": active,
    } for key, department, active, _ in POPULATION]
    events = [{
        "roster_id": key, "question_ts": now - timedelta(hours=1),
        "question_date": now.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        "valid_question": True, "area_key": "関西", "device_class": "desktop",
        "mode": "internal", "measurement_available": True,
        "answer_measurement_profile": "complete_delivery_full",
        "complete_delivery": key in {"dm", "block"},
    } for key, _, _, count in POPULATION for _ in range(count)]
    return AnalyticsService(
        analytics=_Analytics(events), pipeline=_Pipeline(),
        directory=directory, settings=Settings(),
    ), _window(now)


def news_repository():
    template = _roster()[0]
    rows, events = [], []
    for key, department, active, count in POPULATION:
        # Publication flags describe structural eligibility, with active state
        # applied separately when calculating the receipt population.
        structural = membership_for(role="社員MR", department=department, is_active=True)
        rows.append({
            **template, "roster_id": key, "user_id": key, "name": key,
            "email": f"{key}@example.com", "department": department,
            "role": "社員MR", "is_active": active,
            "global_scope_enabled": structural.global_enabled,
            "user_map_scope_enabled": structural.user_map_enabled,
            "is_admin": department == "管理者",
        })
        for _ in range(count):
            events.append(news_event(
                "detail_view", len(events) + 1, roster_id=key, user_id=key,
            ))
    result = repository(events, rows)
    for publication in result.publications:
        publication["scope_policy_version"] = SCOPE_POLICY_VERSION
    return result


@pytest.mark.parametrize("cohort,population,active,questions", [
    ("all", 7, 4, 7), ("dm", 2, 1, 1), ("hcs", 2, 1, 2), ("block", 3, 2, 4),
])
def test_chat_v3_cohort_is_consistent_across_every_summary_panel(cohort, population, active, questions):
    service, window = chat_service()
    overview = service.overview(window=window, cohort=cohort)
    assert overview["scopeUserCount"] == population
    assert overview["analyticsQuality"]["totalEventCount"] == questions
    assert overview["kpis"]["activeUsers"] == active
    assert overview["kpis"]["adoptionRate"] == pytest.approx(active / population, abs=0.0001)
    assert overview["kpis"]["questionsPerActiveUser"] == pytest.approx(questions / active, abs=0.0001)
    for method in ("environment", "trend", "regions", "overview_users"):
        payload = getattr(service, method)(window=window, cohort=cohort)
        assert payload["cohort"] == cohort
        assert payload["scopeUserCount"] == population
        assert payload["rosterFingerprint"] == overview["rosterFingerprint"]
    users = service.overview_users(window=window, cohort=cohort)["users"]
    assert len(users) == population
    assert sum(user["userMessageCountInPeriod"] for user in users) == questions


def test_chat_global_is_the_disjoint_union_and_misw_has_no_personal_statistics():
    service, window = chat_service()
    groups = [service.overview(window=window, cohort=cohort) for cohort in ("dm", "hcs", "block")]
    overall = service.overview(window=window)
    assert overall["scopeUserCount"] == sum(group["scopeUserCount"] for group in groups)
    assert overall["kpis"]["activeUsers"] == sum(group["kpis"]["activeUsers"] for group in groups)
    assert overall["kpis"]["adoptionRate"] != sum(group["kpis"]["adoptionRate"] for group in groups)
    delivery = overall["kpis"]["completeDelivery"]
    assert delivery["measuredCount"] == delivery["totalCount"] == 7
    assert delivery["value"] == 4 / 7  # DM 1/1 + HCS 0/2 + block 3/4.
    assert delivery["value"] != sum(group["kpis"]["completeDelivery"]["value"] for group in groups)
    mapped = {user["rosterId"] for user in service.users(window=window)["users"]}
    assert mapped == {key for key, _, active, _ in POPULATION if active} - {"misw", "admin"}
    with pytest.raises(KeyError):
        service.user_detail("misw", window=window)


@pytest.mark.parametrize("cohort,population,active,actions", [
    ("all", 7, 4, 7), ("dm", 2, 1, 1), ("hcs", 2, 1, 2), ("block", 3, 2, 4),
])
def test_news_v3_uses_the_same_population_and_weighted_rate(cohort, population, active, actions):
    report = _service(news_repository()).report(window=news_window(), now=NOW, cohort=cohort)
    dashboard = _service(news_repository()).dashboard(window=news_window(), now=NOW, cohort=cohort)
    assert report["kpis"]["scopeUsers"] == population
    assert report["kpis"]["activeUsers"] == active
    assert report["kpis"]["totalActions"] == actions
    assert report["kpis"]["adoptionRate"] == pytest.approx(active / population)
    assert dashboard["totals"]["contentClicks"] == actions
    assert len(report["organizations"]["users"]) == population


def test_news_misw_is_excluded_from_personal_reports_and_sales_hq_remains_available():
    with pytest.raises(KeyError):
        _service(news_repository()).dashboard(window=news_window(), now=NOW, roster_id="misw")
    personal = _service(news_repository()).dashboard(window=news_window(), now=NOW, roster_id="sales")
    assert personal["scope"] == AnalysisScope.USER_MAP.value
    assert personal["totals"]["contentClicks"] == 7


def v2_rows():
    rows = legacy_rows()
    for row in rows:
        if row["department"] == "DM専任":
            row["department"] = "MR(DM)"
        row["global_scope_enabled"] = row["department"] == "MR(DM)"
    return rows


def test_v2_publication_keeps_its_frozen_membership_contract():
    rows = v2_rows()
    validated = validated_published_roster(rows, policy_version="summary_department_v2")
    assert len(validated) == len(rows)
    altered = v2_rows()
    altered[2]["global_scope_enabled"] = True  # v1 permitted HQ; v2 never did.
    with pytest.raises(ValueError, match="scope flags"):
        validated_published_roster(altered, policy_version="summary_department_v2")


@pytest.mark.parametrize("department", [Department.BLOCK_FIELD, Department.MISW_HQ, Department.SALES_MARKETING_HQ])
@pytest.mark.parametrize("policy", ["summary_role_v1", "summary_department_v2"])
def test_pre_v3_receipts_cannot_introduce_new_departments(department, policy):
    rows = legacy_rows() if policy == "summary_role_v1" else v2_rows()
    rows[0]["department"] = department.value
    structural = membership_for(role=rows[0]["role"], department=department, is_active=True)
    rows[0]["global_scope_enabled"] = structural.global_enabled
    rows[0]["user_map_scope_enabled"] = structural.user_map_enabled
    with pytest.raises(ValueError, match="department"):
        validated_published_roster(rows, policy_version=policy)


def test_block_news_routes_and_csv_return_the_selected_population():
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: _service(news_repository())
    try:
        client = TestClient(app)
        headers = {"x-monitor-admin-email": "admin@example.com"}
        params = {"start": "2026-09-02", "end": "2026-09-04", "cohort": "block"}
        dashboard = client.get("/api/news-usage/overview", params=params, headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["totals"]["contentClicks"] == 4
        report = client.get("/api/news-usage/report", params=params, headers=headers)
        assert report.status_code == 200
        assert report.json()["kpis"]["scopeUsers"] == 3
        download = client.get("/api/news-usage/report.csv", params={
            **params, "expected_published_run_id": report.json()["publishedRunId"],
            "expected_roster_fingerprint": report.json()["rosterFingerprint"],
        }, headers=headers)
        assert download.status_code == 200
        assert "block" in download.text
        assert "misw@example.com" not in download.text
    finally:
        app.dependency_overrides.clear()
