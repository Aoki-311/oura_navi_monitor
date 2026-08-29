from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.jobs.refresh_analytics import (
    AnalyticsRefreshJob,
    DataQualityGateError,
    PublicationOutcomeUnknownError,
    _failed_quality_checks,
    _render_begin_run_sql,
    render_publish_sql,
)
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
        monitor_refresh_overlap_minutes=240,
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

    assert start.isoformat() == "2026-08-02T08:00:00+00:00"
    assert end.isoformat() == "2026-08-03T08:00:00+00:00"


class _CatchupJob:
    _settings = _settings()

    def __init__(self):
        self.calls = 0

    def run(self, *, now, sequence):
        self.calls += 1
        assert sequence == self.calls - 1
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


def test_publish_uses_a_lease_owner_and_watermark_compare_and_set() -> None:
    sql = render_publish_sql(_settings()).lower()

    assert "lease_run_id = @lease_id" in sql
    assert "data_through = @expected_watermark" in sql
    assert "published watermark compare-and-set failed" in sql
    assert "delete from `lcs-developer-483404.oura_navi_monitor.pipeline_state`" not in sql
    assert "disposition = 'batch_blocking'" in sql
    assert "pipeline_quality_events" in sql


def test_blocking_quality_rolls_back_facts_but_retains_diagnostics() -> None:
    sql = render_publish_sql(_settings()).lower()

    assert "declare run_quality_results array<struct<" in sql
    assert "rollback transaction" in sql
    assert sql.index("rollback transaction") < sql.index(
        "error_code = 'dataqualitygateerror'"
    )
    assert "from unnest(run_quality_results)" in sql


def test_pipeline_run_records_a_closed_scheduler_or_backfill_trigger_source() -> None:
    schema = (Path(__file__).resolve().parents[1] / "sql" / "create_aggregates.sql").read_text(
        encoding="utf-8"
    ).lower()
    refresh = (Path(__file__).resolve().parents[1] / "app" / "jobs" / "refresh_analytics.py").read_text(
        encoding="utf-8"
    ).lower()

    assert "trigger_source string" in schema
    assert "add column if not exists trigger_source string" in schema
    assert "trigger_source = @trigger_source" in refresh
    assert "--trigger-source" in refresh
    assert "scheduler_three_hour" in refresh
    assert "manual_backfill" in refresh


def test_begin_run_uses_partition_bounded_update_then_insert() -> None:
    sql = _render_begin_run_sql("test-project.monitor").lower()

    assert "merge `test-project.monitor.pipeline_runs`" not in sql
    assert "update `test-project.monitor.pipeline_runs`" in sql
    assert "started_at >= @run_partition_start" in sql
    assert "started_at < @run_partition_end" in sql
    assert "set matched_run_count = @@row_count" in sql
    assert "assert matched_run_count <= 1" in sql
    assert "if matched_run_count = 0 then" in sql
    assert "insert into `test-project.monitor.pipeline_runs`" in sql
    assert "@run_started_at" in sql
    assert "commit transaction" in sql


class _BeginRunCaptureJob(AnalyticsRefreshJob):
    def __init__(self) -> None:
        super().__init__(
            _settings(),
            client=object(),
            projector=object(),
            execution_id="execution-partition-bound",
            trigger_source="manual_backfill",
        )
        self.parameters = {}

    def _query(self, sql, parameters):
        self.parameters = {
            parameter.name: parameter.value for parameter in parameters
        }
        return []


def test_begin_run_binds_the_same_started_at_inside_a_three_day_scan() -> None:
    job = _BeginRunCaptureJob()

    job._begin_run(
        run_id="run-1",
        window_start=datetime(2026, 8, 26, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    assert job.parameters["execution_id"] == "execution-partition-bound"
    assert job.parameters["trigger_source"] == "manual_backfill"
    assert job.parameters["run_partition_start"] <= job.parameters["run_started_at"]
    assert job.parameters["run_started_at"] < job.parameters["run_partition_end"]
    assert (
        job.parameters["run_partition_end"]
        - job.parameters["run_partition_start"]
    ).days == 3


def test_only_nonzero_quality_findings_emit_bounded_operator_events() -> None:
    events = _failed_quality_checks(
        {
            "runId": "run-1",
            "dataQualityChecks": [
                {
                    "check_name": "source_event_missing_identity",
                    "disposition": "row_quarantined",
                    "severity": "warning",
                    "failure_count": 2,
                },
                {
                    "check_name": "unknown_analytics_task",
                    "disposition": "axis_unmeasured",
                    "severity": "producer_error",
                    "failure_count": 0,
                },
            ],
        }
    )

    assert events == [
        {
            "monitor_pipeline_quality_event": True,
            "check_name": "source_event_missing_identity",
            "disposition": "row_quarantined",
            "severity": "warning",
            "failure_count": 2,
            "run_id": "run-1",
        }
    ]


def test_run_identity_changes_when_a_retried_catchup_has_a_new_watermark() -> None:
    job = AnalyticsRefreshJob(
        _settings(),
        client=_WatermarkClient(None),
        projector=object(),
        execution_id="execution-1",
    )
    first_end = datetime(2026, 8, 2, tzinfo=timezone.utc)
    second_end = datetime(2026, 8, 3, tzinfo=timezone.utc)

    first = job._run_id(
        sequence=0,
        expected_watermark=None,
        window_end=first_end,
    )
    retried_after_progress = job._run_id(
        sequence=0,
        expected_watermark=first_end,
        window_end=second_end,
    )

    assert first != retried_after_progress
    assert job._lease_id() == job._lease_id()


def test_publication_receipt_requires_the_same_successful_run_and_watermark() -> None:
    expected_end = datetime(2026, 8, 3, tzinfo=timezone.utc)

    assert AnalyticsRefreshJob._publication_is_committed(
        {
            "data_through": expected_end,
            "published_run_id": "run-1",
            "state_status": "succeeded",
            "run_status": "succeeded",
        },
        run_id="run-1",
        expected_window_end=expected_end,
    )
    assert not AnalyticsRefreshJob._publication_is_committed(
        {
            "data_through": expected_end,
            "published_run_id": "older-run",
            "state_status": "succeeded",
            "run_status": "succeeded",
        },
        run_id="run-1",
        expected_window_end=expected_end,
    )


class _MinimalProjector:
    def resolve_chat_identities(self):
        return 1

    def user_scope_rows(self):
        return [
            {
                "roster_id": "roster-1",
                "user_id": "subject-1",
                "area": "area",
                "area_key": "area-key",
                "workplace": "workplace",
                "role": "role",
                "department": "department",
                "mr_experience": "experience",
                "is_active": True,
                "global_scope_enabled": True,
                "user_map_scope_enabled": True,
                "is_admin": False,
                "updated_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
            }
        ]

    def changed_conversation_rows(self, *, window_start, window_end):
        return [], [], {}


class _AmbiguousCommitJob(AnalyticsRefreshJob):
    def __init__(self, *, receipt_available: bool):
        super().__init__(
            _settings(),
            client=object(),
            projector=_MinimalProjector(),
            execution_id="execution-ambiguous",
        )
        self.receipt_available = receipt_available
        self.failed_marked = False
        self.lease_released = False

    def _acquire_lease(self, lease_id):
        return {"data_through": datetime(2026, 8, 2, tzinfo=timezone.utc)}

    def _begin_run(self, **kwargs):
        return None

    def _renew_lease(self, lease_id):
        return None

    def _mark_failed(self, run_id, exc):
        self.failed_marked = True

    def _release_lease(self, lease_id):
        self.lease_released = True

    def _query(self, sql, parameters):
        if sql.lstrip().startswith("DECLARE event_partition_start"):
            raise TimeoutError("result stream ended after commit")
        if "AS state_status" in sql and "AS run_status" in sql:
            if not self.receipt_available:
                raise ConnectionError("receipt unavailable")
            values = {parameter.name: parameter.value for parameter in parameters}
            return [
                {
                    "data_through": values["expected_window_end"],
                    "published_run_id": values["run_id"],
                    "state_status": "succeeded",
                    "run_status": "succeeded",
                }
            ]
        raise AssertionError("unexpected query")


def test_commit_transport_error_is_reconciled_from_the_atomic_publication_receipt() -> None:
    job = _AmbiguousCommitJob(receipt_available=True)

    result = job.run(now=datetime(2026, 8, 2, 12, tzinfo=timezone.utc))

    assert result["status"] == "succeeded"
    assert result["publicationOutcome"] == "reconciled_after_transport_error"
    assert job.failed_marked is False
    assert job.lease_released is False


def test_unknown_commit_outcome_preserves_the_lease_and_does_not_forge_failure() -> None:
    job = _AmbiguousCommitJob(receipt_available=False)

    with pytest.raises(PublicationOutcomeUnknownError):
        job.run(now=datetime(2026, 8, 2, 12, tzinfo=timezone.utc))

    assert job.failed_marked is False
    assert job.lease_released is False


class _WindowFailureJob(AnalyticsRefreshJob):
    def __init__(self):
        super().__init__(
            _settings(),
            client=object(),
            projector=_MinimalProjector(),
            execution_id="execution-window-failure",
        )
        self.lease_released = False

    def _acquire_lease(self, lease_id):
        return {"data_through": None}

    def window(self, **kwargs):
        raise ValueError("refresh window is empty")

    def _release_lease(self, lease_id):
        self.lease_released = True


def test_failure_before_run_row_creation_releases_the_acquired_lease() -> None:
    job = _WindowFailureJob()

    with pytest.raises(ValueError, match="window is empty"):
        job.run(now=datetime(2026, 8, 2, 12, tzinfo=timezone.utc))

    assert job.lease_released is True


class _QualityBlockedJob(AnalyticsRefreshJob):
    def __init__(self):
        super().__init__(
            _settings(),
            client=object(),
            projector=_MinimalProjector(),
            execution_id="execution-quality-blocked",
        )
        self.failed_marked = False
        self.lease_released = False

    def _acquire_lease(self, lease_id):
        return {"data_through": datetime(2026, 8, 2, tzinfo=timezone.utc)}

    def _begin_run(self, **kwargs):
        return None

    def _renew_lease(self, lease_id):
        return None

    def _mark_failed(self, run_id, exc):
        self.failed_marked = True

    def _release_lease(self, lease_id):
        self.lease_released = True

    def _query(self, sql, parameters):
        if sql.lstrip().startswith("DECLARE event_partition_start"):
            return [
                {
                    "check_name": "duplicate_question_event_id",
                    "disposition": "batch_blocking",
                    "severity": "critical",
                    "failure_count": 1,
                    "passed": False,
                }
            ]
        raise AssertionError("unexpected query")


def test_blocking_quality_is_a_typed_failure_and_releases_the_lease() -> None:
    job = _QualityBlockedJob()

    with pytest.raises(DataQualityGateError) as raised:
        job.run(now=datetime(2026, 8, 2, 12, tzinfo=timezone.utc))

    assert raised.value.run_id.startswith("refresh_")
    assert raised.value.checks[0]["check_name"] == "duplicate_question_event_id"
    assert job.failed_marked is True
    assert job.lease_released is True


def test_failed_marking_only_transitions_a_still_running_run() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "jobs" / "refresh_analytics.py").read_text(
        encoding="utf-8"
    )

    assert "AND status = 'running'" in source
    assert "--target-at" in source
    assert '--target-at requires --until-current and manual_backfill' in source
