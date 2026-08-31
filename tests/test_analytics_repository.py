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
    repository = AnalyticsRepository(
        Settings(monitor_analytics_start_at="2026-03-16T00:00:00Z"),
        client=client,
    )

    repository.overview_events(window=_window(), published_run_id="run-1")
    repository.user_detail_events(
        roster_id="roster_1",
        window=_window(),
        published_run_id="run-1",
    )

    assert len(client.calls) == 2
    for sql, config, location in client.calls:
        assert "question_date BETWEEN @start_date AND @end_date" in sql
        assert "valid_question = TRUE" in sql
        assert "dashboard_events_v2" in sql
        parameters = {item.name: item.value for item in config.query_parameters}
        assert str(parameters["start_date"]) == "2026-08-23"
        assert str(parameters["end_date"]) == "2026-08-24"
        assert parameters["published_run_id"] == "run-1"
        assert location == "US"


def test_user_metrics_binds_history_and_exact_as_of_to_the_transaction_window() -> None:
    client = _Client()
    repository = AnalyticsRepository(
        Settings(monitor_analytics_start_at="2026-03-16T00:00:00Z"),
        client=client,
    )

    repository.user_metrics(window=_window(), published_run_id="run-1")

    sql, config, _location = client.calls[0]
    assert "dashboard_user_list_v2`(@history_start_date, @as_of, @published_run_id)" in sql
    parameters = {item.name: item for item in config.query_parameters}
    assert str(parameters["history_start_date"].value) == "2026-03-16"
    assert parameters["as_of"].type_ == "TIMESTAMP"
    assert parameters["as_of"].value == _window().end_utc
    assert parameters["published_run_id"].value == "run-1"
    assert "today" not in parameters


def test_published_roster_snapshot_is_read_only_from_the_captured_run() -> None:
    client = _Client()
    repository = AnalyticsRepository(Settings(), client=client)

    repository.published_roster_snapshot(published_run_id="run-42")

    sql, config, _location = client.calls[0]
    assert "FROM `" in sql and ".user_scope`" in sql
    assert "WHERE snapshot_run_id = @published_run_id" in sql
    assert "label_ids_json" in sql
    assert "labels_json" in sql
    parameters = {item.name: item.value for item in config.query_parameters}
    assert parameters == {"published_run_id": "run-42"}


def test_legacy_publication_uses_the_existing_stable_routines_as_one_contract() -> None:
    client = _Client()
    repository = AnalyticsRepository(
        Settings(monitor_analytics_start_at="2026-03-16T00:00:00Z"),
        client=client,
    )

    repository.overview_events(window=_window(), published_run_id=None)
    repository.activity_events(
        end=_window().end_utc,
        published_run_id=None,
    )
    repository.user_detail_events(
        roster_id="roster_1",
        window=_window(),
        published_run_id=None,
    )
    repository.user_metrics(window=_window(), published_run_id=None)

    assert len(client.calls) == 4
    for sql, config, _location in client.calls[:3]:
        assert ".dashboard_events`(@start_date, @end_date)" in sql
        assert "dashboard_events_v2" not in sql
        parameters = {item.name: item.value for item in config.query_parameters}
        assert "published_run_id" not in parameters
    metrics_sql, metrics_config, _location = client.calls[3]
    assert ".dashboard_user_list`(@history_start_date, @today)" in metrics_sql
    assert "dashboard_user_list_v2" not in metrics_sql
    metric_parameters = {
        item.name: item for item in metrics_config.query_parameters
    }
    assert metric_parameters["today"].type_ == "DATE"
    assert str(metric_parameters["today"].value) == "2026-08-24"
    assert "published_run_id" not in metric_parameters
