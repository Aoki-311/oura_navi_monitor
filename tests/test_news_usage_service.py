from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain.analytics_snapshot import roster_fingerprint
from app.repositories.news_usage_repository import NewsUsageConfiguration
from app.services.news_usage_service import (
    NewsUsageQuery,
    NewsUsageService,
    NewsUsageSnapshotConflictError,
)
from app.settings import Settings
from app.time_window import MetricsTimeWindow


def _window() -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
        end_utc=datetime(2026, 9, 4, 15, tzinfo=timezone.utc),
        timezone="Asia/Tokyo",
        source="custom",
        preset="",
        requested_days=3,
        bucket_minutes=1440,
    )


def _roster() -> list[dict]:
    return [
        {
            "snapshot_run_id": "roster-new",
            "snapshot_created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "roster_id": "roster-1",
            "user_id": "user-stable-1",
            "name": "利用者 一郎",
            "email": "user1@example.com",
            "area": "関西",
            "area_key": "関西",
            "workplace": "大阪",
            "role": "本社MR",
            "department": "DM専任",
            "mr_experience": "10年",
            "label_ids_json": "[]",
            "is_active": True,
            "global_scope_enabled": True,
            "user_map_scope_enabled": True,
            "is_admin": False,
            "updated_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
            "roster_isolated_count": 0,
            "roster_issue_counts_json": "{}",
            "roster_diagnostic_fingerprint": "diag-1",
        }
    ]


def _publication() -> dict:
    roster = [{**_roster()[0], "label_ids": []}]
    return {
        "source": "news_usage",
        "status": "succeeded",
        "measurement_start_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "data_through": datetime(2026, 9, 6, tzinfo=timezone.utc),
        "published_run_id": "usage-run-2",
        "roster_snapshot_run_id": "roster-new",
        "source_service": "oura-navi-test",
        "scope_policy_version": "summary_role_v1",
        "global_roster_fingerprint": roster_fingerprint(
            roster, diagnostic_fingerprint="diag-1"
        ),
        "global_content_fingerprint": "content-global-2",
        "user_map_roster_fingerprint": "roster-user-map-2",
        "user_map_content_fingerprint": "content-user-map-2",
        "updated_at": datetime(2026, 9, 6, tzinfo=timezone.utc),
    }


def _event(
    name: str,
    minute: int,
    *,
    operation_id: str = "",
    result: str = "",
    trigger: str = "",
    content: bool = False,
    filter_snapshot: bool = False,
) -> dict:
    occurred_at = datetime(2026, 9, 2, 1, minute, tzinfo=timezone.utc)
    return {
        "event_id": f"event-{name}-{minute}",
        "usage_event_id": f"usage-{name}-{minute}",
        "page_view_id": "page-1",
        "event_name": name,
        "channel": "society",
        "occurred_at": occurred_at,
        "usage_date_jst": occurred_at.astimezone(ZoneInfo("Asia/Tokyo")).date(),
        "user_id": "user-stable-1",
        "roster_id": "roster-1",
        "roster_snapshot_run_id": "roster-new",
        # The immutable ingestion audit may reference an older roster. It is
        # deliberately not the current reporting join key.
        "ingested_roster_id": "roster-old-id",
        "ingested_roster_snapshot_run_id": "roster-old",
        "content_event_id": "article-1" if content else None,
        "content_event_version": "version-1" if content else None,
        "content_domain_key": "diabetes" if content else None,
        "content_geography_scope": "domestic" if content else None,
        "content_source_id": "jds" if content else None,
        "content_category_key": "conference" if content else None,
        "source_catalog_version": "catalog-1" if content else None,
        "filter_snapshot_present": filter_snapshot,
        "filter_domain_keys": ["diabetes"] if filter_snapshot else [],
        "filter_source_ids": ["jds"] if filter_snapshot else [],
        "filter_category_keys": ["conference"] if filter_snapshot else [],
        "filter_event_types": ["meeting"] if filter_snapshot else [],
        "filter_news_geography_scope": "domestic" if filter_snapshot else None,
        "filter_start_date": None,
        "filter_end_date": None,
        "filter_has_query": True if name == "filter_change" else None,
        "changed_fields": ["query", "source_ids"] if name == "filter_change" else [],
        "surface": "detail" if content else "list",
        "trigger": trigger,
        "link_kind": "registration" if name == "outbound_click" else None,
        "operation_id": operation_id or None,
        "result": result or None,
        "error_code": None,
        "summary_date_jst": None,
        "source_service": "oura-navi-test",
        "publication_run_id": "usage-run-2",
        "publication_data_through": datetime(2026, 9, 6, tzinfo=timezone.utc),
    }


def _events() -> list[dict]:
    return [
        _event("tab_view", 0),
        _event("filter_change", 1, filter_snapshot=True),
        _event("detail_view", 2, content=True),
        _event("outbound_click", 3, content=True),
        _event("export_started", 4, operation_id="operation-1", filter_snapshot=True),
        _event(
            "export_finished",
            5,
            operation_id="operation-1",
            result="download_handed_off",
        ),
        _event("summary_view", 6, trigger="manual"),
        _event("summary_view", 7, trigger="auto"),
    ]


class _Repository:
    def __init__(
        self,
        *,
        publications: list[dict] | None = None,
        events=None,
        roster=None,
    ):
        self.publications = list(publications or [_publication(), _publication()])
        self.events = list(_events() if events is None else events)
        self.roster = list(_roster() if roster is None else roster)
        self.calls: list[str] = []
        self.diagnostic_kwargs: dict = {}

    def configuration(self):
        self.calls.append("configuration")
        return NewsUsageConfiguration(
            state="enabled",
            source_service="oura-navi-test",
            measurement_start_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    def publication_snapshot(self, **_kwargs):
        self.calls.append("publication")
        return self.publications.pop(0)

    def published_roster_snapshot(self, **_kwargs):
        self.calls.append("roster")
        return self.roster

    def published_events(self, **_kwargs):
        self.calls.append("events")
        return self.events

    def unmatched_event_diagnostics(self, **kwargs):
        self.calls.append("diagnostics")
        self.diagnostic_kwargs = kwargs
        return {"state": "available", "unmatched_event_count": 2, "error_code": ""}


def _service(repository) -> NewsUsageService:
    return NewsUsageService(repository=repository, settings=Settings())


def test_all_six_behavior_groups_and_current_roster_are_reported() -> None:
    repository = _Repository()

    report = _service(repository).report(
        window=_window(),
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )

    assert report["state"]["availability"] == "available"
    assert report["state"]["usage"] == "has_usage"
    assert report["kpis"] == {
        "scopeUsers": 1,
        "activeUsers": 1,
        "adoptionRate": 1.0,
        "totalActions": 6,
        "tabViews": 1,
        "filterChanges": 1,
        "detailViews": 1,
        "outboundClicks": 1,
        "exportStarts": 1,
        "manualSummaryViews": 1,
    }
    assert report["tabBehavior"]["views"] == 1
    assert report["filterBehavior"]["searchChanges"] == 1
    assert report["detailBehavior"]["popularArticles"][0]["contentEventId"] == "article-1"
    assert report["outboundBehavior"]["byLinkKind"][0]["key"] == "registration"
    assert report["exportBehavior"]["downloadHandoffRate"] == 1.0
    assert report["summaryBehavior"] == {
        "manualViews": 1,
        "manualUsers": 1,
        "automaticViews": 1,
        "automaticUsers": 1,
    }
    assert report["organizations"]["users"][0]["rosterId"] == "roster-1"
    assert report["diagnostics"]["unmatchedEventCount"] == 2
    assert repository.diagnostic_kwargs["publication_data_through"] == datetime(
        2026, 9, 6, tzinfo=timezone.utc
    )
    assert "published_run_id" not in repository.diagnostic_kwargs
    assert repository.calls == [
        "configuration",
        "publication",
        "roster",
        "events",
        "publication",
        "diagnostics",
    ]


def test_geography_filter_attributes_minimal_export_terminal_to_start_snapshot() -> None:
    report = _service(_Repository()).report(
        window=_window(),
        query=NewsUsageQuery(geography="domestic"),
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )

    assert report["exportBehavior"]["started"] == 1
    assert report["exportBehavior"]["finished"] == 1
    assert report["exportBehavior"]["pending"] == 0
    assert report["exportBehavior"]["orphanFinished"] == 0
    assert report["exportBehavior"]["results"] == [
        {"result": "download_handed_off", "attempts": 1}
    ]


def test_same_operation_id_for_different_users_does_not_cross_match() -> None:
    second_roster = {
        **_roster()[0],
        "roster_id": "roster-2",
        "user_id": "user-stable-2",
        "name": "利用者 二郎",
        "email": "user2@example.com",
    }
    roster = [*_roster(), second_roster]
    publication = {
        **_publication(),
        "global_roster_fingerprint": roster_fingerprint(
            [{**row, "label_ids": []} for row in roster],
            diagnostic_fingerprint="diag-1",
        ),
    }
    start = _event(
        "export_started", 4, operation_id="shared-operation", filter_snapshot=True
    )
    terminal = _event(
        "export_finished",
        5,
        operation_id="shared-operation",
        result="download_handed_off",
    )
    terminal.update(
        {
            "event_id": "event-export-finished-user-2",
            "usage_event_id": "usage-export-finished-user-2",
            "user_id": "user-stable-2",
            "roster_id": "roster-2",
        }
    )
    repository = _Repository(
        publications=[publication, publication],
        events=[start, terminal],
        roster=roster,
    )

    report = _service(repository).report(
        window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc)
    )

    assert report["exportBehavior"]["started"] == 1
    assert report["exportBehavior"]["finished"] == 1
    assert report["exportBehavior"]["pending"] == 1
    assert report["exportBehavior"]["orphanFinished"] == 1
    assert report["exportBehavior"]["downloadHandoffRate"] == 0.0


def test_duplicate_export_delivery_is_scoped_to_its_operation() -> None:
    first = _event(
        "export_started", 4, operation_id="duplicated-operation", filter_snapshot=True
    )
    duplicate = {
        **first,
        "event_id": "event-export-started-duplicate",
        "usage_event_id": "usage-export-started-duplicate",
        "occurred_at": datetime(2026, 9, 2, 1, 5, tzinfo=timezone.utc),
    }
    terminal = _event(
        "export_finished",
        6,
        operation_id="duplicated-operation",
        result="download_handed_off",
    )

    report = _service(_Repository(events=[first, duplicate, terminal])).report(
        window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc)
    )

    assert report["exportBehavior"]["started"] == 1
    assert report["exportBehavior"]["finished"] == 1
    assert report["exportBehavior"]["pending"] == 0
    assert report["exportBehavior"]["downloadHandoffRate"] == 1.0


def test_old_ingestion_roster_audit_does_not_remove_prior_usage() -> None:
    events = [_event("detail_view", 2, content=True)]
    events[0]["ingested_roster_snapshot_run_id"] = "roster-before-next-publication"

    report = _service(_Repository(events=events)).report(
        window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc)
    )

    assert report["publishedRunId"] == "usage-run-2"
    assert report["rosterSnapshotRunId"] == "roster-new"
    assert report["detailBehavior"]["views"] == 1


def test_resolved_roster_id_reports_usage_when_current_roster_has_no_user_id() -> None:
    roster = [{**_roster()[0], "user_id": None}]
    publication = {
        **_publication(),
        "global_roster_fingerprint": roster_fingerprint(
            [{**roster[0], "label_ids": []}],
            diagnostic_fingerprint="diag-1",
        ),
    }
    event = _event("detail_view", 2, content=True)
    event["user_id"] = None
    repository = _Repository(
        publications=[publication, publication],
        events=[event],
        roster=roster,
    )

    report = _service(repository).report(
        window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc)
    )

    assert report["detailBehavior"]["views"] == 1
    assert report["kpis"]["activeUsers"] == 1
    assert report["organizations"]["users"][0]["rosterId"] == "roster-1"
    assert "userId" not in report["organizations"]["users"][0]


def test_passive_events_alone_are_zero_usage_not_active_usage() -> None:
    report = _service(
        _Repository(
            events=[
                _event(
                    "export_finished",
                    5,
                    operation_id="orphan",
                    result="failed",
                ),
                _event("summary_view", 7, trigger="auto"),
            ]
        )
    ).report(window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert report["state"]["usage"] == "no_usage"
    assert report["kpis"]["activeUsers"] == 0
    assert report["exportBehavior"]["orphanFinished"] == 1
    assert report["summaryBehavior"]["automaticViews"] == 1


def test_changed_pointer_never_becomes_a_false_zero_report() -> None:
    changed = {**_publication(), "published_run_id": "usage-run-3"}
    repository = _Repository(publications=[_publication(), changed], events=[])

    with pytest.raises(NewsUsageSnapshotConflictError) as captured:
        _service(repository).report(window=_window())

    assert captured.value.code == "snapshot_changed"


class _DisabledRepository:
    def __init__(self, configuration: NewsUsageConfiguration):
        self.value = configuration
        self.queried = False

    def configuration(self):
        return self.value

    def publication_snapshot(self, **_kwargs):
        self.queried = True
        raise AssertionError("disabled News usage must not initialize a BQ read")


@pytest.mark.parametrize(
    ("configuration", "availability", "reason"),
    [
        (
            NewsUsageConfiguration("disabled", "", None),
            "not_enabled",
            "not_enabled",
        ),
        (
            NewsUsageConfiguration("invalid", "oura-navi-test", None, "invalid_config"),
            "unavailable",
            "invalid_config",
        ),
    ],
)
def test_disabled_or_incomplete_branch_does_not_read_bigquery(
    configuration, availability, reason
) -> None:
    repository = _DisabledRepository(configuration)

    report = _service(repository).report(window=_window())

    assert report["state"]["availability"] == availability
    assert report["state"]["reasonCode"] == reason
    assert repository.queried is False


def test_before_measurement_is_not_reported_as_zero_usage() -> None:
    repository = _Repository()
    configuration = NewsUsageConfiguration(
        "enabled",
        "oura-navi-test",
        datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    repository.configuration = lambda: configuration

    report = _service(repository).report(window=_window())

    assert report["state"]["availability"] == "before_measurement"
    assert report["state"]["usage"] == "not_measured"
    assert "publication" not in repository.calls


def test_csv_protects_formula_cells_and_unions_both_visible_article_lists() -> None:
    report = _service(_Repository()).report(
        window=_window(), now=datetime(2026, 9, 6, tzinfo=timezone.utc)
    )
    report["organizations"]["users"][0]["name"] = "=HYPERLINK(\"bad\")"
    report["organizations"]["users"][0]["department"] = "+SUM(1,1)"
    detail_article = {
        **report["detailBehavior"]["popularArticles"][0],
        "contentEventId": "detail-only",
    }
    outbound_article = {
        **report["outboundBehavior"]["popularArticles"][0],
        "contentEventId": "outbound-only",
    }
    report["detailBehavior"]["popularArticles"] = [detail_article]
    report["outboundBehavior"]["popularArticles"] = [
        detail_article,
        outbound_article,
    ]

    rows = list(
        csv.DictReader(
            io.StringIO(
                NewsUsageService.csv_bytes(report).decode("utf-8-sig")
            )
        )
    )

    user = next(row for row in rows if row["record_type"] == "user")
    assert user["label"] == "'=HYPERLINK(\"bad\")"
    assert user["department"] == "'+SUM(1,1)"
    articles = [row for row in rows if row["record_type"] == "article"]
    assert {row["key"] for row in articles} == {
        "detail-only/version-1",
        "outbound-only/version-1",
    }
    assert len(articles) == 2
    assert all(row["business_unit"] == "diabetes" for row in articles)
