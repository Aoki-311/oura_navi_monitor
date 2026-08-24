from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.analytics_service import AnalyticsService, _activity_level
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class _Pipeline:
    @staticmethod
    def data_through():
        return datetime.now(timezone.utc)


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


class _Conversations:
    @staticmethod
    def list_conversations(*, chat_user_id: str):
        return []


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


def test_overview_does_not_publish_completion_rate_from_partial_measurement() -> None:
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
        conversations=_Conversations(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["completeDeliveryRate"] is None
    assert payload["kpis"]["p95LatencyMs"] == 2000


def test_regions_display_toranomon_separately_without_a_location_dictionary() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        conversations=_Conversations(),
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
        conversations=_Conversations(),
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
    }


def test_user_detail_uses_actual_last_seen_even_when_outside_selected_window() -> None:
    now = datetime.now(timezone.utc)
    last_seen = now - timedelta(days=30, hours=3)
    service = AnalyticsService(
        analytics=_Analytics([], metrics=[{"roster_id": "field_1", "last_active_at": last_seen}]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        conversations=_Conversations(),
        settings=Settings(),
    )

    payload = service.user_detail("field_1", window=_window(now))

    assert payload["summary"]["lastActiveAt"] == last_seen.isoformat()
    assert payload["summary"]["questions"] == 0


def test_peer_completion_average_is_not_published_when_any_active_peer_is_unmeasured() -> None:
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

    assert comparison["averageCompleteDeliveryRate"] is None


def test_activity_level_uses_one_japan_calendar_day_boundary() -> None:
    period_end = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    questions = [
        datetime(2026, 8, 22, 15, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 14, 59, tzinfo=timezone.utc),
    ]

    assert _activity_level(
        questions,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == "high"
