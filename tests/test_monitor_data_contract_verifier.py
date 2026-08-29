from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.verify_monitor_data_contract import (
    API_READ_MAXIMUM_BYTES,
    REQUIRED_API_OUTPUT_COLUMNS,
    verify_data_contract,
)


class _Row(dict):
    def items(self):
        return super().items()


class _Query:
    def __init__(self, rows, columns=()):
        self._rows = rows
        self.schema = [SimpleNamespace(name=name) for name in sorted(columns)]

    def result(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _Client:
    def __init__(
        self,
        *,
        missing_column: str = "",
        missing_api_output_column: str = "",
    ) -> None:
        self.missing_column = missing_column
        self.missing_api_output_column = missing_api_output_column
        self.queries: list[str] = []

    @staticmethod
    def get_dataset(_reference):
        return SimpleNamespace(location="US")

    def get_table(self, reference):
        name = str(reference).rsplit(".", 1)[-1]
        source_views = {"monitor_event_source", "http_request_source"}
        columns = {
            "pipeline_state": {
                "source", "data_through", "published_run_id", "status",
                "lease_run_id", "lease_acquired_at", "lease_expires_at", "updated_at",
            },
            "pipeline_runs": {
                "run_id", "execution_id", "trigger_source", "started_at", "finished_at",
                "window_start", "window_end", "source", "status", "error_code",
            },
            "pipeline_quality_events": {
                "run_id", "check_name", "disposition", "failure_count", "passed", "observed_at",
            },
            "pipeline_event_issues": {
                "source_event_hash", "issue_code", "disposition", "last_run_id",
                "resolution_status", "last_observed_at",
            },
            "pipeline_run_event_manifest": {
                "run_id", "source_event_hash", "event_key_hash", "event_family",
                "disposition", "observed_at",
            },
            "question_events": {
                "event_id", "analytics_contract_version", "classification_reason_codes",
                "product_resolution_status", "product_resolution_reason_codes",
                "record_origin", "measurement_profile",
            },
            "answer_events": {
                "event_id", "analytics_contract_version", "classification_reason_codes",
                "product_resolution_status", "product_resolution_reason_codes",
                "record_origin", "measurement_profile", "measurement_available",
                "complete_delivery",
            },
            "user_scope": {"roster_id", "global_scope_enabled", "user_map_scope_enabled"},
        }
        selected = set(columns.get(name, set()))
        selected.discard(self.missing_column)
        return SimpleNamespace(
            table_type="VIEW" if name in source_views else "TABLE",
            schema=[SimpleNamespace(name=value) for value in sorted(selected)],
        )

    @staticmethod
    def get_routine(_reference):
        return SimpleNamespace(type_="TABLE_VALUED_FUNCTION")

    def query(self, sql, *, job_config, location):
        assert location == "US"
        assert job_config.use_query_cache is False
        self.queries.append(sql)
        if "pipeline_state" in sql:
            assert job_config.maximum_bytes_billed == 10_485_760
            return _Query(
                [
                    _Row(
                        source="published",
                        status="succeeded",
                        published_run_id="run-1",
                        data_through="2026-08-29T00:00:00Z",
                        lease_run_id=None,
                        lease_expires_at=None,
                    )
                ]
            )
        for routine_name, columns in REQUIRED_API_OUTPUT_COLUMNS.items():
            if routine_name in sql:
                assert job_config.maximum_bytes_billed == API_READ_MAXIMUM_BYTES
                selected = set(columns)
                selected.discard(self.missing_api_output_column)
                return _Query([_Row()], selected)
        raise AssertionError(f"unexpected query: {sql}")


def test_data_contract_receipt_requires_tables_views_routines_and_publication() -> None:
    client = _Client()
    receipt = verify_data_contract(
        client,
        project="test-project",
        dataset="oura_navi_monitor",
        location="US",
        git_sha="a" * 40,
        image=(
            "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
            + "b" * 64
        ),
    )

    assert receipt["schemaReady"] is True
    assert receipt["sourceViewsReady"] is True
    assert receipt["apiRoutinesReady"] is True
    assert receipt["apiRoutinesReadable"] is True
    assert receipt["publishedStateReadable"] is True
    assert receipt["publishedRunId"] == "run-1"
    assert receipt["dataThrough"] == "2026-08-29T00:00:00Z"
    assert receipt["apiReadMaximumBytes"] == API_READ_MAXIMUM_BYTES
    assert receipt["apiRoutineReads"]["dashboard_events"]["readable"] is True
    assert receipt["apiRoutineReads"]["dashboard_user_list"]["readable"] is True
    assert any("dashboard_events" in sql for sql in client.queries)
    assert any("dashboard_user_list" in sql for sql in client.queries)


def test_data_contract_receipt_stops_on_a_missing_migration_column() -> None:
    with pytest.raises(ValueError, match="pipeline_state.*lease_run_id"):
        verify_data_contract(
            _Client(missing_column="lease_run_id"),
            project="test-project",
            dataset="oura_navi_monitor",
            location="US",
            git_sha="a" * 40,
            image=(
                "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
                + "b" * 64
            ),
        )


def test_data_contract_receipt_stops_when_a_real_api_read_has_wrong_output() -> None:
    with pytest.raises(ValueError, match="dashboard_events.*complete_delivery"):
        verify_data_contract(
            _Client(missing_api_output_column="complete_delivery"),
            project="test-project",
            dataset="oura_navi_monitor",
            location="US",
            git_sha="a" * 40,
            image=(
                "us-central1-docker.pkg.dev/test-project/repository/monitor@sha256:"
                + "b" * 64
            ),
        )
