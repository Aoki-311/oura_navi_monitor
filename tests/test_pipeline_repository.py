from __future__ import annotations

from datetime import datetime, timezone

from app.repositories.pipeline_repository import PipelineRepository
from app.settings import Settings


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _Client:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        return _QueryResult(self._rows)


def test_publication_snapshot_reads_watermark_and_quality_from_one_published_run() -> None:
    watermark = datetime(2026, 8, 29, 3, 5)
    client = _Client(
        [
            {
                "data_through": watermark,
                "published_run_id": "run-1",
                "updated_at": watermark,
                "latest_run_id": "run-1",
                "latest_run_status": "succeeded",
                "latest_run_error_code": None,
                "latest_run_finished_at": watermark,
                "quarantined_event_count": 2,
                "deduplicated_delivery_count": 3,
                "repaired_duplicate_fact_count": 1,
                "axis_unmeasured_finding_count": 4,
                "batch_blocking_failure_count": 0,
            }
        ]
    )
    repository = PipelineRepository(Settings(), client=client)

    snapshot = repository.publication_snapshot()

    assert snapshot["data_through"] == watermark.replace(tzinfo=timezone.utc)
    assert snapshot["published_run_id"] == "run-1"
    assert snapshot["quarantined_event_count"] == 2
    assert len(client.calls) == 1
    sql = " ".join(client.calls[0][0].lower().split())
    assert "pipeline_state" in sql
    assert "pipeline_runs" in sql
    assert "pipeline_event_issues" in sql
    assert "pipeline_quality_events" in sql
    assert "issue.last_run_id = published.published_run_id" in sql
    assert "quality.run_id = latest_run.run_id" in sql


def test_empty_publication_has_no_forged_watermark() -> None:
    repository = PipelineRepository(Settings(), client=_Client([]))

    assert repository.publication_snapshot() == {}
    assert repository.data_through() is None
