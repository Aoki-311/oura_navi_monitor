from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.analytics_service import AnalyticsService, _activity_level
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class _Pipeline:
    @staticmethod
    def data_through():
        return datetime.now(timezone.utc)


class _StalePipeline:
    @staticmethod
    def data_through():
        return datetime.now(timezone.utc) - timedelta(days=2)


class _Directory:
    def __init__(self) -> None:
        self.users = [
            {
                "roster_id": "field_1",
                "name": "利用者",
                "email": "field@example.com",
                "area": "関西",
                "area_key": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "mr_experience": "10年",
                "label_ids": [],
                "is_active": True,
            },
            {
                "roster_id": "hq_1",
                "name": "本社利用者",
                "email": "hq@example.com",
                "area": "本社",
                "area_key": "本社・虎ノ門",
                "workplace": "虎ノ門",
                "role": "本部メンバー",
                "department": "DM本社",
                "mr_experience": "-",
                "label_ids": [],
                "is_active": True,
            },
        ]

    def list_users(self, *, include_inactive: bool = True):
        return list(self.users)

    def get_user(self, roster_id: str):
        return next((item for item in self.users if item["roster_id"] == roster_id), None)

    @staticmethod
    def list_labels(*, include_inactive: bool = True):
        return []


class _Analytics:
    def __init__(self, rows, metrics=None):
        self.rows = rows
        self.metrics = list(metrics or [])

    def overview_events(self, **_kwargs):
        return list(self.rows)

    def activity_events(self, **_kwargs):
        return list(self.rows)

    def user_metrics(self):
        return list(self.metrics)

    def user_detail_events(self, **_kwargs):
        return list(self.rows)


def _window(now: datetime) -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=now - timedelta(days=7),
        end_utc=now,
        timezone="Asia/Tokyo",
        source="days",
        preset="",
        requested_days=7,
        bucket_minutes=1440,
    )


def _single_day_window() -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc),
        timezone="Asia/Tokyo",
        source="preset",
        preset="today",
        requested_days=1,
        bucket_minutes=1440,
    )


def test_overview_publishes_representative_partial_measurement_with_coverage() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=2),
            "question_date": (now - timedelta(hours=2)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": True,
            "answer_measurement_profile": "runtime_truth_full",
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "product_information",
        },
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": False,
            "total_latency_ms": 2000,
            "mode": "websearch",
            "device_class": "mobile",
            "primary_question_category": "institution_gpo_market",
        },
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["completeDelivery"] == {
        "value": 1.0,
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
    }
    assert payload["kpis"]["p95Latency"] == {
        "valueMs": 2000,
        "measuredCount": 2,
        "totalCount": 2,
        "measurementState": "measured",
    }


def test_failure_only_historical_outcomes_do_not_publish_a_biased_success_rate() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": False,
            "answer_measurement_profile": "terminal_outcome",
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "classification_status": "not_measured",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    measurement = service.overview(window=_window(now))["kpis"]["completeDelivery"]
    assert measurement == {
        "value": None,
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
    }


def test_measurement_state_distinguishes_no_usage_from_unmeasured_history() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    no_usage = service.overview(window=_window(now))
    assert no_usage["kpis"]["completeDelivery"]["measurementState"] == "no_usage"

    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": None,
            "total_latency_ms": None,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "unclassified",
            "classification_status": "not_measured",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )
    historical = service.overview(window=_window(now))
    assert historical["kpis"]["completeDelivery"]["measurementState"] == "not_measured"
    assert historical["kpis"]["p95Latency"]["measurementState"] == "not_measured"


def test_stale_pipeline_is_metadata_and_does_not_disable_available_analytics() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_StalePipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert "status" not in payload
    assert payload["freshness"]["state"] == "stale"
    assert payload["kpis"]["activeUsers"] == 0


def test_missing_request_tasks_are_explicitly_unmeasured_without_hiding_other_metrics() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": None,
            "total_latency_ms": None,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "legacy_unknown",
            "classification_status": "unclassified",
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["activeUsers"] == 1
    assert payload["requestTasks"] == []
    assert payload["taskMeasurement"] == {
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
    }


def test_historical_question_category_never_claims_request_task_measurement() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "primary_question_category": "product_information",
            "classification_status": "classified",
            "analytics_tasks": ["unclassified"],
            "record_origin": "legacy_audit_history",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["requestTasks"] == []
    assert payload["taskMeasurement"] == {
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
    }


def test_current_multi_task_event_drives_distribution_and_product_matrix() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "analytics_tasks": ["fact_lookup", "comparison_selection"],
            "record_origin": "canonical_event",
            "primary_product_name": "テルフュージョン",
            "product_candidate_count": 1,
            "product_resolved_count": 1,
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["taskMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 1,
        "measurementState": "measured",
    }
    assert {row["key"] for row in payload["requestTasks"]} == {
        "fact_lookup",
        "comparison_selection",
    }
    assert {
        (row["product"], row["task"], row["count"])
        for row in payload["productTaskMatrix"]
    } == {
        ("テルフュージョン", "fact_lookup", 1),
        ("テルフュージョン", "comparison_selection", 1),
    }


def test_environment_dimensions_distinguish_partial_measurement_from_unknown_history() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=2),
            "question_date": (now - timedelta(hours=2)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "mode": "internal",
            "device_class": "desktop",
        },
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "mode": "unknown",
            "device_class": "unknown",
            "record_origin": "firestore_history",
        },
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["modeDistribution"] == [
        {"key": "internal", "label": "社内モード", "count": 1, "rate": 1.0}
    ]
    assert payload["deviceDistribution"] == [
        {"key": "desktop", "label": "PC", "count": 1, "rate": 1.0}
    ]
    assert payload["modeMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
    }
    assert payload["deviceMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
    }


def test_single_day_overview_does_not_define_return_rate() -> None:
    window = _single_day_window()
    question_ts = datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": question_ts,
            "question_date": "2026-08-24",
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    assert service.overview(window=window)["kpis"]["returnRate"] is None


def test_regions_display_toranomon_separately_without_a_location_dictionary() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.regions(window=_window(now))

    headquarters = next(row for row in payload["regions"] if row["areaKey"] == "本社・虎ノ門")
    assert headquarters["area"] == "本社・虎ノ門"


def test_product_ranking_discloses_unresolved_governed_product_candidates() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": True,
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "product_information",
            "primary_product_name": "テルフュージョン",
            "product_candidate_count": 2,
            "product_resolved_count": 1,
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["topProducts"] == [
        {"label": "テルフュージョン", "count": 1}
    ]
    assert payload["productResolution"] == {
        "candidateCount": 2,
        "resolvedCount": 1,
        "unresolvedQuestions": 1,
        "resolutionRate": 0.5,
        "measuredCount": 1,
        "totalCount": 1,
        "measurementState": "measured",
    }


def test_user_detail_uses_actual_last_seen_even_when_outside_selected_window() -> None:
    now = datetime.now(timezone.utc)
    last_seen = now - timedelta(days=30, hours=3)
    service = AnalyticsService(
        analytics=_Analytics([], metrics=[{"roster_id": "field_1", "last_active_at": last_seen}]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.user_detail("field_1", window=_window(now))

    assert payload["summary"]["lastActiveAt"] == last_seen.isoformat()
    assert payload["summary"]["questions"] == 0


def test_inactive_user_cannot_be_opened_through_direct_detail_route() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["is_active"] = False
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=directory,
        settings=Settings(),
    )

    with pytest.raises(KeyError, match="user not found"):
        service.user_detail("field_1", window=_window(now))


def test_peer_comparison_excludes_the_selected_user_and_discloses_coverage() -> None:
    roster = [
        {"roster_id": "one", "area": "関西"},
        {"roster_id": "two", "area": "関西"},
    ]
    comparison = AnalyticsService._peer_comparison(
        user=roster[0],
        roster=roster,
        field="area",
        events={
            "one": [{"question_date": "2026-08-23", "measurement_available": True, "complete_delivery": True}],
            "two": [{"question_date": "2026-08-23", "measurement_available": False, "complete_delivery": False}],
        },
    )

    assert comparison["peerCount"] == 1
    assert comparison["averageQuestions"] == 1.0
    assert comparison["averageCompleteDelivery"] == {
        "value": None,
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
    }


def test_activity_level_uses_distinct_japan_active_days_not_question_volume() -> None:
    period_end = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    same_day_questions = [
        datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 3, 0, tzinfo=timezone.utc),
    ]

    assert _activity_level(
        same_day_questions,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == "low"

    six_active_days = [
        period_end - timedelta(days=offset, hours=1)
        for offset in range(6)
    ]
    assert _activity_level(
        six_active_days,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == "high"


@pytest.mark.parametrize(
    ("active_day_offsets", "expected"),
    [
        ([], "dormant"),
        ([0], "low"),
        ([0, 1], "low"),
        ([0, 1, 2], "middle"),
        ([0, 1, 2, 3, 4], "middle"),
        ([0, 1, 2, 3, 4, 5], "high"),
        ([13], "low"),
        ([14], "dormant"),
    ],
)
def test_activity_level_covers_all_four_segments_and_the_fourteen_day_boundary(
    active_day_offsets: list[int],
    expected: str,
) -> None:
    period_end = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    questions = [
        period_end - timedelta(days=offset, hours=1)
        for offset in active_day_offsets
    ]

    assert _activity_level(
        questions,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == expected
