from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routers.news_usage import get_news_usage_service
from app.services.news_usage_service import NewsUsageService
from app.settings import get_settings


def _settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def _payload() -> dict:
    return {
        "contractVersion": "news_usage_report_v1",
        "scope": "global",
        "scopePolicyVersion": "summary_role_v1",
        "rosterFingerprint": "roster-fingerprint-1",
        "contentFingerprint": "content-fingerprint-1",
        "publishedRunId": "usage-run-1",
        "rosterSnapshotRunId": "roster-run-1",
        "sourceService": "oura-navi-test",
        "windowStart": "2026-09-01T15:00:00Z",
        "windowEnd": "2026-09-04T15:00:00Z",
        "windowTimezone": "Asia/Tokyo",
        "state": {
            "availability": "available",
            "usage": "has_usage",
            "freshness": "fresh",
            "historyCoverage": "full",
            "publicationCoverage": "full",
            "reasonCode": "complete",
            "message": "選択条件の利用記録を表示しています。",
            "measurementStartAt": "2026-09-01T00:00:00Z",
            "dataThrough": "2026-09-05T00:00:00Z",
            "publishedAt": "2026-09-05T00:05:00Z",
        },
        "diagnostics": {
            "state": "available",
            "unmatchedEventCount": 0,
            "errorCode": "",
        },
        "selection": {
            "channel": "society",
            "environment": "",
            "businessUnit": "",
            "geography": "domestic",
            "category": "",
            "society": "jds",
            "query": "",
        },
        "filterOptions": {
            "channels": [
                {"value": "news", "label": "ニュース"},
                {"value": "society", "label": "学会"},
            ],
            "environments": [
                {"value": "oura-navi-test", "label": "oura-navi-test"}
            ],
            "businessUnits": [],
            "geographies": [{"value": "domestic", "label": "国内"}],
            "categories": [],
            "societies": [{"value": "jds", "label": "jds"}],
        },
        "kpis": {
            "scopeUsers": 1,
            "activeUsers": 1,
            "adoptionRate": 1.0,
            "totalActions": 2,
            "tabViews": 1,
            "filterChanges": 0,
            "detailViews": 1,
            "outboundClicks": 0,
            "exportStarts": 0,
            "manualSummaryViews": 0,
        },
        "trend": [
            {
                "date": "2026-09-02",
                "activeUsers": 1,
                "tabViews": 1,
                "filterChanges": 0,
                "detailViews": 1,
                "outboundClicks": 0,
                "exportStarts": 0,
                "manualSummaryViews": 0,
                "isPartial": False,
            }
        ],
        "tabBehavior": {
            "views": 1,
            "activeUsers": 1,
            "byChannel": [
                {"key": "society", "label": "学会", "actions": 1, "activeUsers": 1}
            ],
        },
        "filterBehavior": {
            "changes": 0,
            "activeUsers": 0,
            "searchChanges": 0,
            "searchEnabledAfterChange": 0,
            "byChangedField": [],
        },
        "detailBehavior": {
            "views": 1,
            "activeUsers": 1,
            "totalArticles": 1,
            "isTruncated": False,
            "popularArticles": [
                {
                    "contentEventId": "article-1",
                    "contentEventVersion": "version-1",
                    "channel": "society",
                    "businessUnit": "diabetes",
                    "geography": "domestic",
                    "sourceId": "jds",
                    "category": "conference",
                    "detailViews": 1,
                    "outboundClicks": 0,
                    "activeUsers": 1,
                }
            ],
        },
        "outboundBehavior": {
            "clicks": 0,
            "activeUsers": 0,
            "totalArticles": 1,
            "isTruncated": False,
            "byLinkKind": [],
            "popularArticles": [
                {
                    "contentEventId": "article-1",
                    "contentEventVersion": "version-1",
                    "channel": "society",
                    "businessUnit": "diabetes",
                    "geography": "domestic",
                    "sourceId": "jds",
                    "category": "conference",
                    "detailViews": 1,
                    "outboundClicks": 0,
                    "activeUsers": 1,
                }
            ],
        },
        "exportBehavior": {
            "started": 0,
            "activeUsers": 0,
            "finished": 0,
            "pending": 0,
            "orphanFinished": 0,
            "downloadHandoffRate": None,
            "results": [],
        },
        "summaryBehavior": {
            "manualViews": 0,
            "manualUsers": 0,
            "automaticViews": 0,
            "automaticUsers": 0,
        },
        "organizations": {
            "users": [
                {
                    "rosterId": "roster-1",
                    "name": "利用者 一郎",
                    "area": "関西",
                    "areaKey": "関西",
                    "workplace": "大阪",
                    "role": "本社MR",
                    "department": "DM専任",
                    "actions": 2,
                    "activeDays": 1,
                    "lastActiveAt": "2026-09-02T01:00:00Z",
                }
            ],
            "departments": [
                {
                    "key": "DM専任",
                    "label": "DM専任",
                    "scopeUsers": 1,
                    "activeUsers": 1,
                    "actions": 2,
                    "adoptionRate": 1.0,
                }
            ],
            "regions": [
                {
                    "key": "関西",
                    "label": "関西",
                    "scopeUsers": 1,
                    "activeUsers": 1,
                    "actions": 2,
                    "adoptionRate": 1.0,
                }
            ],
        },
    }


class _Service:
    def __init__(self):
        self.calls = []
        self.payload = _payload()

    def report(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload

    def csv_bytes(self, report):
        return NewsUsageService.csv_bytes(report)


def test_news_usage_report_is_admin_only_and_binds_filters() -> None:
    service = _Service()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: service
    client = TestClient(app)
    try:
        assert client.get("/api/news-usage/report").status_code == 401
        response = client.get(
            "/api/news-usage/report",
            params={
                "days": 3,
                "channel": "society",
                "geography": "domestic",
                "society": "jds",
            },
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["contractVersion"] == "news_usage_report_v1"
    assert "actor_email_hash" not in response.text
    assert service.calls[0]["query"].channel == "society"
    assert service.calls[0]["query"].geography == "domestic"
    assert service.calls[0]["query"].society == "jds"


def test_news_usage_csv_requires_the_displayed_snapshot() -> None:
    service = _Service()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: service
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        conflict = client.get(
            "/api/news-usage/report.csv",
            params={
                "expected_published_run_id": "older-run",
                "expected_roster_fingerprint": "roster-fingerprint-1",
            },
            headers=headers,
        )
        response = client.get(
            "/api/news-usage/report.csv",
            params={
                "expected_published_run_id": "usage-run-1",
                "expected_roster_fingerprint": "roster-fingerprint-1",
            },
            headers=headers,
        )
    finally:
        app.dependency_overrides.clear()

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "snapshot_changed"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith("\ufeffrecord_type".encode())
    assert b"user" in response.content
    assert b"actor_email_hash" not in response.content


def test_news_usage_filter_values_are_closed_or_bounded() -> None:
    service = _Service()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_news_usage_service] = lambda: service
    client = TestClient(app)
    try:
        invalid_channel = client.get(
            "/api/news-usage/report?channel=chat",
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
        long_query = client.get(
            f"/api/news-usage/report?q={'x' * 121}",
            headers={"x-monitor-admin-email": "admin@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert invalid_channel.status_code == 422
    assert long_query.status_code == 422
