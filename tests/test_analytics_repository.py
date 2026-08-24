from datetime import datetime, timezone

from app.repositories.analytics_repository import AnalyticsRepository
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class _Job:
    def result(self):
        return []


class _Client:
    def __init__(self) -> None:
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        return _Job()


def _window() -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
        timezone="Asia/Tokyo",
        source="custom",
        preset="",
        requested_days=2,
        bucket_minutes=1440,
    )


def test_partitioned_dashboard_queries_always_bind_partition_dates() -> None:
    client = _Client()
    repository = AnalyticsRepository(Settings(), client=client)

    repository.overview_events(window=_window())
    repository.user_detail_events(roster_id="roster_1", window=_window())

    assert len(client.calls) == 2
    for sql, config, location in client.calls:
        assert "question_date BETWEEN @start_date AND @end_date" in sql
        parameters = {item.name: item.value for item in config.query_parameters}
        assert str(parameters["start_date"]) == "2026-08-23"
        assert str(parameters["end_date"]) == "2026-08-24"
        assert location == "US"
