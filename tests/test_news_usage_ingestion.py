from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.jobs.news_usage_ingestion import (
    NEWS_USAGE_STATE_SOURCE,
    NewsUsageRefreshJob,
    news_usage_configuration_status,
    render_news_usage_sql,
    run_configured_news_usage,
)
from app.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def _settings(**updates: object) -> Settings:
    values = {
        "monitor_project_id": "test-project",
        "monitor_bq_dataset": "monitor",
        "monitor_bq_location": "US",
        "monitor_analytics_start_at": "2026-08-01T00:00:00Z",
        "monitor_news_usage_source_service": "oura-navi-test",
        "monitor_news_usage_start_at": "2026-09-06T00:00:00Z",
    }
    values.update(updates)
    return Settings(**values)


def test_news_usage_configuration_is_additive_and_never_breaks_settings_startup() -> None:
    disabled = Settings(monitor_analytics_start_at="2026-08-01T00:00:00Z")
    missing_start = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_source_service="oura-navi-test",
    )
    missing_service = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_start_at="2026-09-06T00:00:00Z",
    )

    assert news_usage_configuration_status(disabled) == "disabled"
    assert news_usage_configuration_status(missing_start) == "invalid"
    assert news_usage_configuration_status(missing_service) == "invalid"
    assert news_usage_configuration_status(_settings()) == "enabled"


@pytest.mark.parametrize(
    ("updates", "expected_status"),
    [
        ({"monitor_news_usage_source_service": "Bad_Service"}, "invalid"),
        ({"monitor_news_usage_start_at": "not-a-date"}, "invalid"),
        ({"monitor_news_usage_start_at": "2026-09-06T00:00:00"}, "invalid"),
    ],
)
def test_news_usage_configuration_rejects_invalid_values_without_startup_failure(
    updates: dict[str, str], expected_status: str
) -> None:
    settings = _settings(**updates)

    assert settings.news_usage_configuration_status == expected_status
    with pytest.raises(ValueError, match="news usage configuration is incomplete"):
        NewsUsageRefreshJob(settings, client=object())


def test_source_view_is_bound_to_the_explicit_news_service_and_contract() -> None:
    sql = render_news_usage_sql("create_news_usage_source.sql", _settings())
    lowered = sql.lower()

    assert "resource.labels.service_name')\n    = 'oura-navi-test'" in lowered
    assert "event_family') = 'news_usage'" in lowered
    assert "monitor_event') = 'true'" in lowered
    assert "$.actor_email_hash" in lowered
    assert "metadata_issues_json" in lowered
    assert "${" not in sql
    assert "lcs-rag-app" not in sql


def test_schema_has_one_fact_one_private_diagnostic_and_success_pointer_views() -> None:
    sql = render_news_usage_sql("create_news_usage_tables.sql", _settings()).lower()

    assert "create table if not exists `test-project.monitor.news_usage_events`" in sql
    assert "create table if not exists `test-project.monitor.news_usage_event_issues`" in sql
    assert "create or replace view `test-project.monitor.news_usage_publication_state`" in sql
    assert "create or replace view `test-project.monitor.news_usage_published_events`" in sql
    assert "where source = 'news_usage'" in sql
    assert "state.status = 'succeeded'" in sql
    assert "state.published_run_id is not null" in sql
    for field in (
        "roster_snapshot_run_id",
        "ingested_roster_id",
        "ingested_roster_snapshot_run_id",
        "actor_email_hash",
        "measurement_start_at",
        "source_service",
        "filter_snapshot_present",
        "filter_domain_keys",
        "filter_source_ids",
        "filter_category_keys",
        "filter_event_types",
        "filter_news_geography_scope",
    ):
        assert field in sql

    published_view = sql.split(
        "create or replace view `test-project.monitor.news_usage_published_events`",
        1,
    )[1]
    assert "roster.snapshot_run_id = state.roster_snapshot_run_id" in published_view
    assert "roster.roster_id = events.ingested_roster_id" in published_view
    assert "events.* except (actor_email_hash)" in published_view
    assert "events.user_id" not in published_view
    assert "events.actor_email_hash" not in published_view
    assert "events.ingested_roster_snapshot_run_id" not in published_view
    roster_projection = published_view.split(")\nselect", 1)[0]
    assert "roster.user_map_scope_enabled = true" in roster_projection

    issue_block = sql.split(
        "create table if not exists `test-project.monitor.news_usage_event_issues`",
        1,
    )[1].split(";", 1)[0]
    for forbidden in ("user_id", "payload_json", "insert_id", "usage_event_id"):
        assert forbidden not in issue_block

    fact_block = sql.split(
        "create table if not exists `test-project.monitor.news_usage_events`",
        1,
    )[1].split(";", 1)[0]
    for forbidden in (
        "email string",
        "title string",
        "body string",
        "url string",
        "query_text",
        "payload_json",
        "received_at",
    ):
        assert forbidden not in fact_block


def test_schema_can_be_rendered_before_the_optional_runtime_source_is_enabled() -> None:
    settings = Settings(monitor_analytics_start_at="2026-08-01T00:00:00Z")

    sql = render_news_usage_sql("create_news_usage_tables.sql", settings)

    assert "news_usage_events" in sql
    assert "${" not in sql


def test_published_history_uses_fixed_roster_identity_after_subject_or_email_changes() -> None:
    sql = render_news_usage_sql("create_news_usage_tables.sql", _settings()).lower()
    published_view = sql.split(
        "create or replace view `test-project.monitor.news_usage_published_events`",
        1,
    )[1]

    assert "roster.roster_id = events.ingested_roster_id" in published_view
    assert "events.user_id" not in published_view
    assert "events.actor_email_hash" not in published_view
    assert "events.ingested_roster_snapshot_run_id" not in published_view


def test_publish_sql_owns_separate_state_and_all_seven_events() -> None:
    sql = render_news_usage_sql("publish_news_usage.sql", _settings()).lower()

    assert "source = 'news_usage'" in sql
    assert "source = 'published'" in sql  # read-only roster pointer assertion
    assert "merge `test-project.monitor.news_usage_events`" in sql
    assert "merge `test-project.monitor.news_usage_event_issues`" in sql
    assert "update `test-project.monitor.pipeline_state`" in sql
    assert "roster_snapshot_run_id = @roster_snapshot_run_id" in sql
    assert "begin transaction" in sql and "commit transaction" in sql
    for event_name in (
        "tab_view",
        "filter_change",
        "detail_view",
        "outbound_click",
        "export_started",
        "export_finished",
        "summary_view",
    ):
        assert f"'{event_name}'" in sql
    for forbidden in (
        "merge `test-project.monitor.question_events`",
        "merge `test-project.monitor.answer_events`",
        "merge `test-project.monitor.conversation_events`",
        "monitor_event_source",
    ):
        assert forbidden not in sql
    export_terminal_guard = sql.split(
        "'export_finished_context_missing'", 1
    )[1].split("union all", 1)[0]
    assert "operation_id is null or result is null" in export_terminal_guard
    assert "error_code" not in export_terminal_guard
    assert "dense_rank() over (" in sql
    assert (
        "partition by user_id, payload_event_name, payload_channel, operation_id"
        in sql
    )
    assert "existing.channel = source.payload_channel" in sql


def test_news_identity_prefers_subject_and_safely_falls_back_to_verified_email_hash() -> None:
    sql = render_news_usage_sql("publish_news_usage.sql", _settings()).lower()

    assert "_news_usage_scope_by_subject" in sql
    assert "_news_usage_scope_by_email_hash" in sql
    assert "subject.user_id = extracted.user_id" in sql
    assert "email.actor_email_hash = extracted.actor_email_hash" in sql
    assert "normalize_and_casefold(trim(email), nfkc)" in sql
    assert "subject.subject_match_count, 0) = 1" in sql
    assert "subject.subject_match_count, 0) = 0" in sql
    assert "email.email_match_count, 0) = 1" in sql
    assert "then subject.subject_roster_id" in sql
    assert "then email.email_roster_id" in sql
    assert "source_event_identity_conflict" in sql
    assert "subject_roster_id != email_roster_id" in sql
    assert "source_event_ambiguous_roster" in sql
    assert "subject_match_count > 1 or email_match_count > 1" in sql


def test_email_hash_is_optional_private_identity_evidence_not_semantic_event_content() -> None:
    sql = render_news_usage_sql("publish_news_usage.sql", _settings()).lower()
    semantic_hash = sql.split(") as event_content_hash", 1)[0].rsplit(
        "to_hex(sha256(to_json_string(struct(", 1
    )[1]
    invalid_hash_issue = sql.split("'source_actor_email_hash_invalid'", 1)[1].split(
        "union all", 1
    )[0]

    assert "actor_email_hash" not in semantic_hash
    assert "actor_email_hash is not null" in invalid_hash_issue
    assert "normalized_actor_email_hash is null" in invalid_hash_issue
    assert "'axis_omitted'" in invalid_hash_issue
    assert "'row_quarantined'" not in invalid_hash_issue
    assert "target.actor_email_hash, source.normalized_actor_email_hash" in sql


def test_chat_roster_cleanup_retains_the_last_successful_news_snapshot_safely() -> None:
    sql = (ROOT / "sql" / "merge_firestore_projection.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "where source = 'news_usage' and status = 'succeeded'" in sql
    assert "to_json_string(state)" in sql
    assert "$.roster_snapshot_run_id" in sql
    # The old schema can compile this expression before the additive column exists.
    assert "select roster_snapshot_run_id" not in sql


class _WatermarkClient:
    def __init__(self, watermark: datetime | None):
        self.watermark = watermark
        self.sql = ""

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def result(self):
            return list(self._rows)

    def query(self, sql, *, job_config, location):
        self.sql = sql
        assert job_config.maximum_bytes_billed > 0
        assert location == "US"
        return self._Result([{"data_through": self.watermark}])


def test_news_window_reads_only_the_independent_news_usage_cursor() -> None:
    client = _WatermarkClient(datetime(2026, 9, 6, 12, tzinfo=timezone.utc))
    job = NewsUsageRefreshJob(_settings(), client=client, execution_id="execution-1")

    start, end = job.window(now=datetime(2026, 9, 7, 12, tzinfo=timezone.utc))

    assert NEWS_USAGE_STATE_SOURCE == "news_usage"
    assert "source = 'news_usage'" in client.sql
    assert "source = 'published'" not in client.sql
    assert start.isoformat() == "2026-09-06T08:00:00+00:00"
    assert end.isoformat() == "2026-09-07T08:00:00+00:00"


def test_news_job_rejects_half_configuration_only_when_news_branch_runs() -> None:
    settings = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_source_service="oura-navi-test",
    )

    with pytest.raises(ValueError, match="news usage configuration is incomplete"):
        NewsUsageRefreshJob(settings, client=object())


def test_disabled_or_incomplete_branch_never_initializes_a_bigquery_client(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.jobs.news_usage_ingestion.bigquery.Client",
        lambda **_: pytest.fail("BQ client must remain lazy"),
    )
    disabled = Settings(monitor_analytics_start_at="2026-08-01T00:00:00Z")
    incomplete = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_source_service="oura-navi-test",
    )

    assert run_configured_news_usage(
        disabled,
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
        until_current=False,
        trigger_source="manual",
    ) is None
    with pytest.raises(ValueError, match="configuration is incomplete"):
        run_configured_news_usage(
            incomplete,
            now=datetime(2026, 9, 6, tzinfo=timezone.utc),
            until_current=False,
            trigger_source="manual",
        )


class _SimulatedNewsJob(NewsUsageRefreshJob):
    def __init__(self, *, fail_publish: bool = False) -> None:
        super().__init__(
            _settings(),
            client=object(),
            execution_id="execution-simulated",
            trigger_source="scheduler_hourly",
        )
        self.fail_publish = fail_publish
        self.calls: list[str] = []
        self.parameters: dict[str, object] = {}

    def _acquire_lease(self, lease_id):
        self.calls.append("lease")
        return {"data_through": None}

    def _begin_run(self, **_):
        self.calls.append("begin")

    def _read_roster_pointer(self):
        self.calls.append("roster")
        return {
            "roster_snapshot_run_id": "chat-run-7",
            "scope_policy_version": "scope-v1",
            "global_roster_fingerprint": "global-roster",
            "global_content_fingerprint": "global-content",
            "user_map_roster_fingerprint": "map-roster",
            "user_map_content_fingerprint": "map-content",
        }

    def _renew_lease(self, lease_id):
        self.calls.append("renew")

    def _query(self, sql, parameters):
        assert "merge `test-project.monitor.news_usage_events`" in sql.lower()
        self.calls.append("publish")
        parameter_names = [parameter.name for parameter in parameters]
        assert len(parameter_names) == len(set(parameter_names))
        self.parameters = {parameter.name: parameter.value for parameter in parameters}
        if self.fail_publish:
            raise TimeoutError("simulated publish failure")
        return [{"input_rows": 8, "canonical_rows": 7, "quarantined_rows": 1}]

    def _read_publication_receipt(self, **_):
        self.calls.append("receipt")
        return {}

    def _mark_failed(self, run_id, exc):
        self.calls.append("failed")

    def _release_lease(self, lease_id):
        self.calls.append("released")


def test_simulated_success_binds_the_current_roster_and_advances_only_usage() -> None:
    job = _SimulatedNewsJob()

    result = job.run(now=datetime(2026, 9, 7, tzinfo=timezone.utc))

    assert job.calls == ["lease", "begin", "roster", "renew", "publish"]
    assert job.parameters["roster_snapshot_run_id"] == "chat-run-7"
    assert job.parameters["source_service"] == "oura-navi-test"
    assert result["status"] == "succeeded"
    assert result["inputRows"] == 8
    assert result["canonicalRows"] == 7
    assert result["quarantinedRows"] == 1


def test_simulated_uncommitted_usage_failure_marks_only_usage_and_releases_lease() -> None:
    job = _SimulatedNewsJob(fail_publish=True)

    with pytest.raises(TimeoutError, match="simulated publish failure"):
        job.run(now=datetime(2026, 9, 7, tzinfo=timezone.utc))

    assert job.calls == [
        "lease",
        "begin",
        "roster",
        "renew",
        "publish",
        "receipt",
        "failed",
        "released",
    ]
