from __future__ import annotations

from datetime import datetime, timezone

from app.repositories import news_usage_repository as repository_module
from app.domain.analysis_scopes import AnalysisScope
from app.repositories.news_usage_repository import NewsUsageRepository
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class _Job:
    def __init__(self, rows):
        self.rows = rows

    def result(self):
        return self.rows


class _Client:
    def __init__(self, results=None):
        self.results = list(results or [[]])
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        return _Job(self.results.pop(0) if self.results else [])


def _settings(**updates) -> Settings:
    values = {
        "monitor_news_usage_source_service": "oura-navi-test",
        "monitor_news_usage_start_at": "2026-09-01T00:00:00Z",
    }
    values.update(updates)
    return Settings(**values)


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


def test_disabled_configuration_does_not_initialize_bigquery(monkeypatch) -> None:
    initialized = False

    def forbidden_client(**_kwargs):
        nonlocal initialized
        initialized = True
        raise AssertionError("BigQuery must stay lazy")

    monkeypatch.setattr(repository_module.bigquery, "Client", forbidden_client)
    repository = NewsUsageRepository(Settings())

    configuration = repository.configuration()

    assert configuration.state == "disabled"
    assert initialized is False


def test_incomplete_configuration_is_local_to_news_branch() -> None:
    repository = NewsUsageRepository(
        Settings(monitor_news_usage_source_service="oura-navi-test")
    )

    configuration = repository.configuration()

    assert configuration.state == "invalid"
    assert configuration.error_code == "invalid_config"


def test_invalid_news_usage_configuration_stays_local_to_news_reader() -> None:
    cases = (
        {
            "monitor_news_usage_source_service": "INVALID_SERVICE",
            "monitor_news_usage_start_at": "2026-09-01T00:00:00Z",
        },
        {
            "monitor_news_usage_source_service": "oura-navi-test",
            "monitor_news_usage_start_at": "not-an-iso-date",
        },
        {
            "monitor_news_usage_source_service": "oura-navi-test",
            "monitor_news_usage_start_at": "2026-09-01T00:00:00",
        },
    )

    for values in cases:
        configuration = NewsUsageRepository(Settings(**values)).configuration()
        assert configuration.state == "invalid"
        assert configuration.measurement_start_at is None
        assert configuration.error_code == "invalid_config"


def test_publication_reader_requires_success_and_explicit_source_service() -> None:
    client = _Client([[]])
    repository = NewsUsageRepository(_settings(), client=client)

    assert repository.publication_snapshot(source_service="oura-navi-test") == {}

    sql, config, location = client.calls[0]
    assert "news_usage_publication_state" in sql
    assert "source = 'news_usage'" in sql
    assert "status = 'succeeded'" in sql
    assert "source_service = @source_service" in sql
    assert {item.name: item.value for item in config.query_parameters} == {
        "source_service": "oura-navi-test"
    }
    assert location == "US"


def test_events_bind_pointer_echo_and_current_roster_projection() -> None:
    client = _Client([[]])
    repository = NewsUsageRepository(_settings(), client=client)

    repository.published_events(
        window=_window(),
        published_run_id="usage-run-2",
        roster_snapshot_run_id="roster-new",
        publication_data_through=datetime(2026, 9, 6, tzinfo=timezone.utc),
        source_service="oura-navi-test",
    )

    sql, config, _location = client.calls[0]
    assert "news_usage_published_events" in sql
    assert "event.publication_run_id = @published_run_id" in sql
    assert "event.publication_data_through = @publication_data_through" in sql
    assert "event.roster_snapshot_run_id = @roster_snapshot_run_id" in sql
    assert "roster.global_scope_enabled = TRUE" in sql
    assert "roster.is_active = TRUE" in sql
    assert "event.roster_id = roster.roster_id" in sql
    assert "event.user_id = roster.user_id" not in sql
    assert "actor_email_hash" not in sql
    assert "ingested_roster" not in sql
    assert "usage_date_jst BETWEEN @start_date AND @end_date" in sql
    assert "event.occurred_at < @publication_data_through" not in sql
    parameters = {item.name: item.value for item in config.query_parameters}
    assert parameters["published_run_id"] == "usage-run-2"
    assert parameters["roster_snapshot_run_id"] == "roster-new"
    assert str(parameters["start_date"]) == "2026-09-02"
    assert str(parameters["end_date"]) == "2026-09-04"


def test_personal_query_binds_user_map_scope_and_exact_roster_parameter():
    client = _Client([[]])
    repository = NewsUsageRepository(_settings(), client=client)
    roster_id = "employee-'parameter"
    repository.published_events(
        window=_window(), published_run_id="usage-run-2",
        roster_snapshot_run_id="roster-new",
        publication_data_through=datetime(2026, 9, 6, tzinfo=timezone.utc),
        source_service="oura-navi-test", scope=AnalysisScope.USER_MAP, roster_id=roster_id,
    )
    sql, config, _ = client.calls[0]
    assert "roster.user_map_scope_enabled = TRUE" in sql
    assert "roster.global_scope_enabled = TRUE" not in sql
    assert "roster.is_active = TRUE" in sql
    assert roster_id not in sql
    assert {item.name: item.value for item in config.query_parameters}["roster_id"] == roster_id


def test_overview_area_is_bound_to_the_published_roster_as_a_query_parameter():
    client = _Client([[]])
    repository = NewsUsageRepository(_settings(), client=client)
    area_key = "関西-'parameter"
    repository.published_events(
        window=_window(), published_run_id="usage-run-2",
        roster_snapshot_run_id="roster-new",
        publication_data_through=datetime(2026, 9, 6, tzinfo=timezone.utc),
        source_service="oura-navi-test", area_key=area_key,
    )
    sql, config, _ = client.calls[0]
    assert "roster.global_scope_enabled = TRUE" in sql
    assert "roster.is_active = TRUE" in sql
    assert "(@area_key = '' OR roster.area_key = @area_key)" in sql
    assert area_key not in sql
    assert {item.name: item.value for item in config.query_parameters}["area_key"] == area_key


def test_only_immutable_roster_is_cached_and_pointer_is_read_fresh():
    client = _Client([
        [{"roster_id": "one"}], [{"published_run_id": "old"}],
        [{"published_run_id": "new"}], [{"roster_id": "two"}],
    ])
    repository = NewsUsageRepository(_settings(), client=client)
    roster = repository.published_roster_snapshot(roster_snapshot_run_id="snapshot-1")
    roster[0]["roster_id"] = "caller-mutation"
    assert repository.published_roster_snapshot(roster_snapshot_run_id="snapshot-1")[0]["roster_id"] == "one"
    assert repository.publication_snapshot(source_service="oura-navi-test")["published_run_id"] == "old"
    assert repository.publication_snapshot(source_service="oura-navi-test")["published_run_id"] == "new"
    assert repository.published_roster_snapshot(roster_snapshot_run_id="snapshot-2")[0]["roster_id"] == "two"
    assert len(client.calls) == 4


def test_unmatched_diagnostic_reads_only_a_count_in_the_selected_window() -> None:
    client = _Client([[{"unmatched_event_count": 3}]])
    repository = NewsUsageRepository(_settings(), client=client)

    result = repository.unmatched_event_diagnostics(
        window=_window(),
        publication_data_through=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )

    assert result == {
        "state": "available",
        "unmatched_event_count": 3,
        "error_code": "",
    }
    sql, config, _location = client.calls[0]
    assert "COUNT(DISTINCT source_event_hash)" in sql
    assert "source_event_without_roster" in sql
    assert "resolution_status = 'open'" in sql
    assert "source_ts < @publication_data_through" in sql
    assert "last_run_id" not in sql
    assert "SELECT source_event_hash" not in sql
    assert {item.name for item in config.query_parameters} == {
        "publication_data_through",
        "start_ts",
        "end_ts",
    }
