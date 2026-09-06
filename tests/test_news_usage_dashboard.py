from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.contracts.news_usage import NewsUsageDashboardResponse
from app.domain.analytics_snapshot import roster_fingerprint
from app.repositories.news_usage_repository import NewsUsageConfiguration
from app.services.news_usage_service import NewsUsageSnapshotConflictError
from test_news_usage_service import (
    _DisabledRepository, _Repository, _event, _publication, _roster, _service, _window,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def repository(events, roster=None):
    roster = _roster() if roster is None else roster
    publication = _publication()
    for scope in ("global", "user_map"):
        members = [
            {**row, "label_ids": []} for row in roster
            if row["is_active"] and row[f"{scope}_scope_enabled"]
        ]
        publication[f"{scope}_roster_fingerprint"] = roster_fingerprint(
            members, diagnostic_fingerprint="diag-1",
        )
    return _Repository(
        publications=[dict(publication), dict(publication)], events=events, roster=roster,
    )


def news_event(name, minute, *, link_kind="primary", **values):
    return {
        **_event(name, minute, content=name != "tab_view"),
        "channel": "news", "content_event_type": "regulatory_safety",
        "link_kind": link_kind, **values,
    }


def test_repeated_article_details_plus_primary_links_are_five_clicks():
    events = [
        news_event("detail_view", 1),
        news_event("detail_view", 2),
        news_event("outbound_click", 3),
        news_event("outbound_click", 4, content_geography_scope="overseas"),
        news_event("outbound_click", 5),
        news_event("outbound_click", 6, link_kind="evidence"),
        news_event("outbound_click", 7, link_kind="registration"),
        news_event("tab_view", 8),
        _event("tab_view", 9),
    ]
    events.append(dict(events[0]))  # Replay the same delivery, not another action.
    source = repository(events)
    result = _service(source).dashboard(window=_window(), now=NOW)
    NewsUsageDashboardResponse.model_validate(result)
    assert result["totals"] == {
        "tabViews": 2, "newsTabViews": 1, "societyTabViews": 1,
        "contentClicks": 5, "newsContentClicks": 5, "societyContentClicks": 0,
        "newsDomesticClicks": 4, "newsOverseasClicks": 1,
        "newsUnknownGeographyClicks": 0,
    }
    category = next(row for row in result["newsCategories"] if row["clicks"])
    assert category == {
        "key": "regulatory_safety", "label": "規制・安全", "clicks": 5,
        "domesticClicks": 4, "overseasClicks": 1, "unknownGeographyClicks": 0,
    }
    assert sum(row["contentClicks"] for row in result["trend"]) == 5
    assert sum(row["tabViews"] for row in result["trend"]) == 2
    assert "diagnostics" not in source.calls


def test_society_source_hover_counts_sum_to_actual_category_clicks():
    events = [
        {**_event("detail_view", 1, content=True), "content_category_key": "糖尿病関連"},
        {**_event("outbound_click", 2, content=True), "link_kind": "primary",
         "content_category_key": "糖尿病関連", "content_source_id": "jadec"},
        {**_event("outbound_click", 3, content=True), "link_kind": "evidence",
         "content_category_key": "糖尿病関連"},
    ]
    result = _service(repository(events)).dashboard(window=_window(), now=NOW)
    category = next(row for row in result["societyCategories"] if row["clicks"])
    assert category["clicks"] == 2
    assert sum(row["clicks"] for row in category["sources"]) == category["clicks"]
    assert {row["label"] for row in category["sources"]} == {
        "一般社団法人日本糖尿病学会", "公益社団法人日本糖尿病協会",
    }
    assert result["totals"]["societyContentClicks"] == 2


def test_missing_article_type_or_geography_never_uses_selected_filters():
    event = news_event(
        "detail_view", 1, content_event_type=None, content_geography_scope=None,
        filter_event_types=["regulatory_safety"], filter_news_geography_scope="domestic",
    )
    result = _service(repository([event])).dashboard(window=_window(), now=NOW)
    assert result["totals"]["newsContentClicks"] == 1
    assert result["totals"]["newsUnknownGeographyClicks"] == 1
    category = next(row for row in result["newsCategories"] if row["clicks"])
    assert category["key"] == "__unclassified__"
    assert category["unknownGeographyClicks"] == 1


def test_inactive_former_mr_cannot_inflate_dashboard_or_legacy_report():
    active = _roster()[0]
    inactive = {
        **active, "roster_id": "former-mr", "user_id": "former-subject",
        "email": "former@example.com", "is_active": False,
    }
    events = [
        news_event("tab_view", 1),
        news_event("tab_view", 2, roster_id="former-mr", user_id="former-subject"),
    ]
    result = _service(repository(events, [active, inactive])).dashboard(
        window=_window(), now=NOW,
    )
    assert result["totals"]["tabViews"] == 1
    legacy = _service(repository(events, [active, inactive])).report(
        window=_window(), now=NOW,
    )
    assert legacy["kpis"]["scopeUsers"] == legacy["kpis"]["activeUsers"] == 1
    assert legacy["kpis"]["adoptionRate"] == 1
    assert sum(row["actions"] for row in legacy["organizations"]["users"]) == 1


def test_personal_non_mr_without_chat_identity_uses_user_map_membership():
    user = {**_roster()[0], "role": "本社担当", "global_scope_enabled": False,
            "user_id": ""}
    event = news_event("detail_view", 1)
    source = repository([event], [user])
    result = _service(source).dashboard(window=_window(), roster_id=user["roster_id"], now=NOW)
    assert result["scope"] == "user_map"
    assert result["totals"]["newsContentClicks"] == 1
    global_result = _service(repository([event], [user])).dashboard(window=_window(), now=NOW)
    assert global_result["totals"]["contentClicks"] == 0
    with pytest.raises(KeyError):
        _service(repository([event], [user])).dashboard(
            window=_window(), roster_id="outside-roster", now=NOW,
        )


def test_overview_area_uses_active_global_roster_for_counts_hover_and_date_range():
    west = _roster()[0]
    east = {
        **west, "roster_id": "roster-2", "user_id": "user-2",
        "email": "user2@example.com", "area": "関東A", "area_key": "関東A",
    }
    inactive = {
        **west, "roster_id": "inactive", "email": "inactive@example.com",
        "user_id": "inactive-user", "is_active": False,
    }
    non_mr = {
        **west, "roster_id": "non-mr", "email": "non-mr@example.com",
        "user_id": "", "role": "本社担当", "global_scope_enabled": False,
    }
    events = [
        news_event("tab_view", 1), news_event("detail_view", 2),
        news_event("outbound_click", 3, content_geography_scope="overseas"),
        {**_event("detail_view", 4, content=True), "content_category_key": "糖尿病関連"},
    ]
    for minute, name, channel, source in (
        (5, "tab_view", "society", "jds"),
        (6, "detail_view", "news", "jds"),
        (7, "detail_view", "society", "jadec"),
        (8, "outbound_click", "society", "jds"),
    ):
        events.append(news_event(
            name, minute, channel=channel, roster_id=east["roster_id"],
            user_id=east["user_id"], content_source_id=source,
            content_category_key="糖尿病関連", content_geography_scope="overseas",
            occurred_at=datetime(2026, 9, 3, 1, minute, tzinfo=timezone.utc),
            usage_date_jst=datetime(2026, 9, 3).date(),
            area_key="関西",  # Event metadata cannot override roster ownership.
        ))
    for minute, user in ((9, inactive), (10, non_mr)):
        events.append(news_event("detail_view", minute, roster_id=user["roster_id"]))
    events.append(news_event(
        "detail_view", 11, occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        usage_date_jst=datetime(2026, 9, 5).date(),
    ))
    roster = [west, east, inactive, non_mr]
    results = {}
    for area, news_clicks, society_clicks in (("", 3, 3), ("関西", 2, 1), ("関東A", 1, 2)):
        result = _service(repository(events, roster)).dashboard(
            window=_window(), area_key=area, now=NOW,
        )
        results[area] = result
        assert result["totals"]["newsContentClicks"] == news_clicks
        assert result["totals"]["societyContentClicks"] == society_clicks
        assert sum(row["contentClicks"] for row in result["trend"]) == news_clicks + society_clicks
        assert sum(row["clicks"] for row in result["newsCategories"]) == news_clicks
        for row in result["newsCategories"]:
            assert row["clicks"] == row["domesticClicks"] + row["overseasClicks"] + row["unknownGeographyClicks"]
        assert sum(row["clicks"] for row in result["societyCategories"]) == society_clicks
        for row in result["societyCategories"]:
            assert row["clicks"] == sum(source["clicks"] for source in row["sources"])
        assert [row["date"] for row in result["trend"]] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert {result["rosterFingerprint"] for result in results.values()} == {
        results[""]["rosterFingerprint"],
    }
    assert results["関西"]["totals"]["tabViews"] == results["関東A"]["totals"]["tabViews"] == 1
    assert results["関西"]["totals"]["newsDomesticClicks"] == 1
    assert results["関東A"]["totals"]["newsDomesticClicks"] == 0
    day_three = replace(_window(), start_utc=datetime(2026, 9, 2, 15, tzinfo=timezone.utc))
    for area, count in (("関西", 0), ("関東A", 3), ("unknown-area", 0)):
        result = _service(repository(events, roster)).dashboard(
            window=day_three, area_key=area, now=NOW,
        )
        assert result["totals"]["contentClicks"] == count
        assert result["windowStart"] == "2026-09-02T15:00:00Z"
        assert result["trend"][0]["date"] == "2026-09-03"
    personal = _service(repository(events, roster)).dashboard(
        window=_window(), roster_id=east["roster_id"], area_key="関西", now=NOW,
    )
    assert personal["totals"]["contentClicks"] == 3


def test_unavailable_dashboard_totals_are_null_and_published_zero_is_measured():
    # Disabled configuration does not instantiate a BigQuery client.
    result = _service(_DisabledRepository(NewsUsageConfiguration("disabled", "", None))).dashboard(
        window=_window(), now=NOW,
    )
    assert result["state"]["availability"] == "not_enabled"
    assert result["totals"] is None
    NewsUsageDashboardResponse.model_validate(result)
    measured = _service(repository([])).dashboard(window=_window(), now=NOW)
    assert measured["state"]["availability"] == "available"
    assert measured["state"]["usage"] == "no_usage"
    assert measured["totals"]["contentClicks"] == 0


def test_dashboard_refuses_changed_publication_and_reports_jst_dates():
    event = news_event("detail_view", 1)
    event["occurred_at"] = datetime(2026, 9, 2, 15, 1, tzinfo=timezone.utc)
    event["usage_date_jst"] = datetime(2026, 9, 3).date()
    result = _service(repository([event])).dashboard(window=_window(), now=NOW)
    assert next(row for row in result["trend"] if row["contentClicks"])["date"] == "2026-09-03"
    source = repository([event])
    source.publications[1]["published_run_id"] = "changed"
    with pytest.raises(NewsUsageSnapshotConflictError):
        _service(source).dashboard(window=_window(), now=NOW)


def test_registered_dashboard_routes_require_admin_and_check_personal_membership():
    from fastapi.testclient import TestClient
    from test_news_usage_api import app, _settings, get_settings, get_news_usage_service

    user = {**_roster()[0], "role": "本社担当", "global_scope_enabled": False,
            "user_id": ""}
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: _service(
        repository([news_event("detail_view", 1)], [user])
    )
    headers = {"x-monitor-admin-email": "admin@example.com"}
    query = {"start": "2026-09-02", "end": "2026-09-04"}
    try:
        client = TestClient(app)
        for path in ("/api/news-usage/overview", "/api/news-usage/users/roster-1"):
            assert client.get(path, params=query).status_code == 401
        overview = client.get("/api/news-usage/overview", params=query, headers=headers)
        personal = client.get("/api/news-usage/users/roster-1", params=query, headers=headers)
        assert overview.status_code == personal.status_code == 200
        assert overview.json()["totals"]["contentClicks"] == 0
        assert personal.json()["scope"] == "user_map"
        assert personal.json()["rosterId"] == "roster-1"
        assert personal.json()["totals"]["newsContentClicks"] == 1
        assert "actor_email_hash" not in personal.text
        assert client.get(
            "/api/news-usage/users/unknown", params=query, headers=headers,
        ).status_code == 404
        assert client.get(
            "/api/news-usage/overview", params={"start": "bad"}, headers=headers,
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_overview_route_passes_area_selection_to_the_published_roster_owner():
    from fastapi.testclient import TestClient
    from test_news_usage_api import app, _settings, get_settings, get_news_usage_service

    west = _roster()[0]
    east = {
        **west, "roster_id": "roster-2", "user_id": "user-2",
        "email": "user2@example.com", "area": "関東A", "area_key": "関東A",
    }
    events = [news_event("detail_view", 1), news_event("detail_view", 2),
              news_event("detail_view", 3, roster_id="roster-2")]
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: _service(repository(events, [west, east]))
    headers = {"x-monitor-admin-email": "admin@example.com"}
    query = {"start": "2026-09-02", "end": "2026-09-04"}
    try:
        client = TestClient(app)
        for area, count in (("", 3), ("関西", 2), ("関東A", 1)):
            response = client.get(
                "/api/news-usage/overview", params={**query, "area_key": area}, headers=headers,
            )
            assert response.status_code == 200
            assert response.json()["totals"]["contentClicks"] == count
        assert client.get(
            "/api/news-usage/overview", params={**query, "area_key": "a" * 81}, headers=headers,
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()
