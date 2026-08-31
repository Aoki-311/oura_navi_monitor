from __future__ import annotations

from datetime import datetime, timezone

import pytest
from google.api_core.exceptions import BadRequest, NotFound, ServiceUnavailable

from app.repositories.pipeline_repository import PipelineRepository
from app.settings import Settings


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class _Client:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def query(self, sql, *, job_config, location):
        self.calls.append((sql, job_config, location))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _QueryResult(response)


def test_publication_snapshot_reads_watermark_and_quality_from_one_published_run() -> None:
    watermark = datetime(2026, 8, 29, 3, 5)
    client = _Client(
        [
            {
                "data_through": watermark,
                "published_run_id": "run-1",
                "updated_at": watermark,
            }
        ],
        [
            {
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
    assert snapshot["publication_state_available"] is True
    assert snapshot["quality_diagnostics_available"] is True
    assert len(client.calls) == 2
    publication_sql = " ".join(client.calls[0][0].lower().split())
    diagnostics_sql = " ".join(client.calls[1][0].lower().split())
    assert "pipeline_state" in publication_sql
    assert "to_json_string(publication)" in publication_sql
    assert "'$.scope_policy_version'" in publication_sql
    assert "pipeline_runs" not in publication_sql
    assert "pipeline_event_issues" not in publication_sql
    assert "pipeline_quality_events" not in publication_sql
    assert "pipeline_state" not in diagnostics_sql
    assert "pipeline_runs" in diagnostics_sql
    assert "pipeline_event_issues" in diagnostics_sql
    assert "pipeline_quality_events" in diagnostics_sql
    assert "issue.last_run_id = @published_run_id" in diagnostics_sql
    assert "quality.run_id = latest_run.run_id" in diagnostics_sql


def test_missing_diagnostics_schema_preserves_the_stable_published_watermark() -> None:
    watermark = datetime(2026, 8, 27, 3, 5)
    client = _Client(
        [
            {
                "data_through": watermark,
                "published_run_id": "last-known-good-run",
                "updated_at": watermark,
            }
        ],
        NotFound("pipeline_event_issues was not found"),
        [
            {
                "data_through": watermark,
                "published_run_id": "last-known-good-run",
                "updated_at": watermark,
            }
        ],
        NotFound("pipeline_event_issues was not found"),
    )
    repository = PipelineRepository(Settings(), client=client)

    snapshot = repository.publication_snapshot()

    assert snapshot["data_through"] == watermark.replace(tzinfo=timezone.utc)
    assert snapshot["published_run_id"] == "last-known-good-run"
    assert snapshot["publication_state_available"] is True
    assert snapshot["quality_diagnostics_available"] is False
    assert snapshot["quality_diagnostics_error_code"] == "schema_unavailable"
    assert repository.data_through() == watermark.replace(tzinfo=timezone.utc)


def test_empty_publication_has_no_forged_watermark() -> None:
    repository = PipelineRepository(Settings(), client=_Client([], [], [], []))

    snapshot = repository.publication_snapshot()

    assert snapshot["publication_state_available"] is True
    assert snapshot["quality_diagnostics_available"] is True
    assert "data_through" not in snapshot
    assert repository.data_through() is None


def test_publication_provider_failure_is_explicit_without_forging_a_watermark() -> None:
    repository = PipelineRepository(
        Settings(),
        client=_Client(NotFound("pipeline_state was not found")),
    )

    assert repository.publication_snapshot() == {
        "publication_state_available": False,
        "publication_state_error_code": "schema_unavailable",
        "quality_diagnostics_available": False,
        "quality_diagnostics_error_code": "publication_state_unavailable",
    }


def test_programming_errors_are_not_relabelled_as_provider_metadata_failures() -> None:
    repository = PipelineRepository(Settings(), client=_Client(ValueError("bad fake")))

    with pytest.raises(ValueError, match="bad fake"):
        repository.publication_snapshot()


def test_invalid_publication_metadata_query_is_explicit_without_erasing_facts() -> None:
    repository = PipelineRepository(
        Settings(),
        client=_Client(BadRequest("Syntax error: unexpected keyword")),
    )

    snapshot = repository.publication_snapshot()

    assert snapshot == {
        "publication_state_available": False,
        "publication_state_error_code": "query_invalid",
        "quality_diagnostics_available": False,
        "quality_diagnostics_error_code": "publication_state_unavailable",
    }


def test_missing_additive_column_is_recoverable_but_transient_outage_is_explicit() -> None:
    watermark = datetime(2026, 8, 27, 3, 5)
    missing_column = PipelineRepository(
        Settings(),
        client=_Client(
            [
                {
                    "data_through": watermark,
                    "published_run_id": "last-known-good-run",
                    "updated_at": watermark,
                }
            ],
            BadRequest("Unrecognized name: diagnostics_status"),
        ),
    )
    transient = PipelineRepository(
        Settings(),
        client=_Client(ServiceUnavailable("backend unavailable")),
    )

    missing_snapshot = missing_column.publication_snapshot()
    transient_snapshot = transient.publication_snapshot()

    assert missing_snapshot["data_through"] == watermark.replace(tzinfo=timezone.utc)
    assert missing_snapshot["quality_diagnostics_error_code"] == "schema_unavailable"
    assert transient_snapshot["publication_state_available"] is False
    assert transient_snapshot["publication_state_error_code"] == "provider_unavailable"


def test_optional_diagnostics_quota_failure_preserves_published_body() -> None:
    watermark = datetime(2026, 8, 28, 3, 0)
    client = _Client(
        [
            {
                "data_through": watermark,
                "published_run_id": "run-1",
                "updated_at": watermark,
            }
        ],
        BadRequest(
            "Quota exceeded during query execution",
            errors=[{"reason": "quotaExceeded", "message": "quota exceeded"}],
        ),
    )
    repository = PipelineRepository(Settings(), client=client)

    snapshot = repository.publication_snapshot()

    assert snapshot["data_through"] == watermark.replace(tzinfo=timezone.utc)
    assert snapshot["published_run_id"] == "run-1"
    assert snapshot["quality_diagnostics_available"] is False
    assert snapshot["quality_diagnostics_error_code"] == "provider_unavailable"


def test_optional_diagnostics_query_defect_preserves_published_body() -> None:
    watermark = datetime(2026, 8, 28, 3, 0)
    repository = PipelineRepository(
        Settings(),
        client=_Client(
            [
                {
                    "data_through": watermark,
                    "published_run_id": "run-1",
                    "updated_at": watermark,
                }
            ],
            BadRequest("Syntax error: unexpected keyword"),
        ),
    )

    snapshot = repository.publication_snapshot()

    assert snapshot["data_through"] == watermark.replace(tzinfo=timezone.utc)
    assert snapshot["published_run_id"] == "run-1"
    assert snapshot["quality_diagnostics_available"] is False
    assert snapshot["quality_diagnostics_error_code"] == "query_invalid"
