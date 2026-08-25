from datetime import datetime, timezone

from app.jobs.refresh_analytics import AnalyticsRefreshJob
from app.settings import Settings


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _WatermarkClient:
    def __init__(self, watermark):
        self.watermark = watermark
        self.sql = ""

    def query(self, sql, *, job_config, location):
        self.sql = sql
        assert job_config.maximum_bytes_billed > 0
        assert location == "US"
        return _Result([{"data_through": self.watermark}])


def _settings(**updates):
    return Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_refresh_delay_minutes=5,
        monitor_refresh_overlap_minutes=120,
        monitor_refresh_max_window_hours=24,
        **updates,
    )


def test_first_refresh_is_partition_bounded_instead_of_scanning_all_history() -> None:
    client = _WatermarkClient(None)
    job = AnalyticsRefreshJob(
        _settings(),
        client=client,
        projector=object(),
    )

    start, end = job.window(now=datetime(2026, 8, 5, tzinfo=timezone.utc))

    assert start.isoformat() == "2026-08-01T00:00:00+00:00"
    assert end.isoformat() == "2026-08-02T00:00:00+00:00"
    assert "source = 'published'" in client.sql


def test_followup_refresh_replays_overlap_and_still_advances() -> None:
    job = AnalyticsRefreshJob(
        _settings(),
        client=_WatermarkClient(datetime(2026, 8, 2, 12, tzinfo=timezone.utc)),
        projector=object(),
    )

    start, end = job.window(now=datetime(2026, 8, 5, tzinfo=timezone.utc))

    assert start.isoformat() == "2026-08-02T10:00:00+00:00"
    assert end.isoformat() == "2026-08-03T10:00:00+00:00"


class _CatchupJob:
    _settings = _settings()

    def __init__(self):
        self.calls = 0

    def run(self, *, now):
        self.calls += 1
        end = "2026-08-02T00:00:00+00:00" if self.calls == 1 else "2026-08-04T23:55:00+00:00"
        return {
            "windowStart": "2026-08-01T00:00:00+00:00",
            "windowEnd": end,
            "status": "succeeded",
        }


def test_catchup_reuses_the_same_incremental_owner_until_frozen_target() -> None:
    job = _CatchupJob()

    result = AnalyticsRefreshJob.run_until_current(
        job,
        now=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert job.calls == 2
    assert result["runCount"] == 2
    assert result["windowEnd"] == "2026-08-04T23:55:00+00:00"
