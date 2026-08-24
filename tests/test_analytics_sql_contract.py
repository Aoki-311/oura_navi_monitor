from pathlib import Path

from app.jobs.refresh_analytics import render_publish_sql
from app.settings import Settings


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
ROOT_DIR = SQL_DIR.parent


def _sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def test_canonical_sql_files_and_objects_exist_once() -> None:
    required = {
        "create_dataset.sql",
        "create_source_tables.sql",
        "merge_firestore_projection.sql",
        "create_fact_tables.sql",
        "create_aggregates.sql",
        "merge_incremental.sql",
        "refresh_daily.sql",
        "create_api_views.sql",
        "check_data_quality.sql",
    }
    assert required <= {path.name for path in SQL_DIR.glob("*.sql")}
    combined = "\n".join(_sql(name) for name in sorted(required))
    for object_name in (
        "question_events",
        "answer_events",
        "answer_action_events",
        "demand_events",
        "citation_events",
        "conversation_events",
        "user_scope",
        "user_daily",
        "pipeline_runs",
        "pipeline_state",
        "dashboard_overview",
        "dashboard_user_list",
        "dashboard_user_detail",
    ):
        assert object_name in combined


def test_incremental_sql_uses_window_parameters_merge_and_no_plaintext_content_columns() -> None:
    sql = _sql("merge_incremental.sql")
    assert "@window_start" in sql
    assert "@window_end" in sql
    assert "MERGE" in sql
    assert "event_id" in sql
    lowered = sql.lower()
    assert "user_email" not in lowered
    assert "user_key" not in lowered
    assert "valid_from" not in lowered
    assert "valid_to" not in lowered
    assert "raw_query" not in lowered
    assert "answer_text" not in lowered
    assert "topic_ideation" not in lowered
    assert "left join `${project_id}.${dataset_id}.user_scope` scope" not in lowered
    assert "scope.user_map_scope_enabled = true" in lowered
    assert "scope.is_active = true" not in lowered
    assert "concat('question:', event.request_id) as question_event_id" in lowered
    for partition_column in ("request_date", "question_date", "answer_date", "action_date"):
        assert f"target.{partition_column} between" in lowered
    assert "safe_cast(latency_text as float64)" in lowered
    assert "regexp_extract(latency_text" in lowered


def test_verified_user_id_is_the_only_fact_identity_contract() -> None:
    source_sql = _sql("create_source_tables.sql").lower()
    fact_sql = _sql("create_fact_tables.sql").lower()
    projection_sql = _sql("merge_firestore_projection.sql").lower()
    assert "jsonpayload.user_id" in source_sql
    assert "jsonpayload.user_key" not in source_sql
    assert "user_id string not null" in fact_sql
    assert "roster_id string not null,\n  user_id string," in fact_sql
    assert "user_key" not in fact_sql
    assert "valid_from" not in projection_sql
    assert "valid_to" not in projection_sql


def test_firestore_projection_is_part_of_the_single_partition_bounded_publish() -> None:
    sql = _sql("merge_firestore_projection.sql").lower()
    assert "delete from `${project_id}.${dataset_id}.user_scope` where true" in sql
    assert "unnest(@user_scope_rows)" in sql
    assert "target.updated_date between @conversation_partition_start and @conversation_partition_end" in sql
    assert "target.answer_date between @citation_partition_start and @citation_partition_end" in sql


def test_scope_flags_are_not_reimplemented_as_department_literals() -> None:
    sql = _sql("refresh_daily.sql").lower()
    assert "department in" not in sql


def test_activity_level_has_one_python_owner_not_a_second_sql_case() -> None:
    sql = _sql("create_api_views.sql").lower()
    assert "activity_level" not in sql
    service = (ROOT_DIR / "app" / "services" / "analytics_service.py").read_text(
        encoding="utf-8"
    )
    assert service.count("def _activity_level(") == 1


def test_user_list_preserves_the_actual_last_question_timestamp() -> None:
    aggregate_sql = _sql("create_aggregates.sql").lower()
    refresh_sql = _sql("refresh_daily.sql").lower()
    view_sql = _sql("create_api_views.sql").lower()
    assert "last_active_at timestamp" in aggregate_sql
    assert "max(qa.question_ts) as last_active_at" in refresh_sql
    assert "max(last_active_at) as last_active_at" in view_sql
    assert "timestamp(last_seen.last_active_date" not in view_sql


def test_api_views_do_not_read_logging_raw_tables() -> None:
    sql = _sql("create_api_views.sql")
    assert "run_googleapis_com_stdout" not in sql
    assert "run_googleapis_com_requests" not in sql


def test_missing_persistence_is_visible_coverage_not_a_pipeline_wide_blocker() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "terminal_without_persistence_measurement" in sql
    assert "'coverage'" in sql
    assert "'critical'" in sql


def test_delayed_message_write_uses_answer_timestamp_for_a_bounded_partition_update() -> None:
    sql = _sql("merge_incremental.sql").lower()
    assert "json_value(payload_json, '$.answer_ts')" in sql
    assert "date(persistence.answer_ts" in sql
    assert "date_sub(date(persistence.answer_ts" in sql
    assert "date_add(date(persistence.answer_ts" in sql


def test_unmatched_source_identity_is_detected_before_scope_filtering() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "source_question_without_roster" in sql
    assert "from source_questions source" in sql
    assert "left join `${project_id}.${dataset_id}.user_scope` scope" in sql
    assert "countif(scope.user_id is null)" in sql


def test_one_sided_question_or_answer_telemetry_blocks_publish() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "question_without_terminal" in sql
    assert "answer_without_question" in sql
    assert "left join question_links" in sql
    assert "accepted_http_without_question_event" in sql
    assert "accepted_ask_requests" in sql
    assert "status between 200 and 399" in sql
    assert "duplicate_source_question_event_id" in sql
    assert "duplicate_source_answer_event_id" in sql


def test_invalid_classification_producer_is_a_visible_critical_failure() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "invalid_classification_producer" in sql
    assert "classification_status = 'producer_invalid'" in sql
    assert "invalid_product_resolution_counts" in sql
    assert "product_resolved_count > product_candidate_count" in sql


def test_user_daily_does_not_duplicate_completion_or_activity_formulas() -> None:
    aggregate_sql = _sql("create_aggregates.sql").lower()
    refresh_sql = _sql("refresh_daily.sql").lower()
    sql = _sql("create_api_views.sql").lower()
    for duplicate in (
        "complete_count",
        "measurable_count",
        "latency_values",
        "mode_counts",
        "device_counts",
        "activity_level",
    ):
        assert duplicate not in aggregate_sql
        assert duplicate not in refresh_sql
        assert duplicate not in sql


def test_complete_delivery_formula_and_failure_priority_have_one_sql_owner() -> None:
    sql = _sql("merge_incremental.sql").lower()
    required_fragments = [
        "terminal = 'final'",
        "runtime_status = 'completed'",
        "demand_total > 0",
        "partial_demand_count = 0",
        "omitted_demand_count = 0",
        "system_fault_count = 0",
        "message_persisted = true",
        "assistant_error_present = false",
        "coalesce(writer_error_code, '') = ''",
    ]
    for fragment in required_fragments:
        assert fragment in sql
    ordered_reasons = [
        "then 'stream_failed'",
        "then 'not_final'",
        "then 'not_persisted'",
        "then 'assistant_error'",
        "then 'writer_error'",
        "then 'system_fault'",
        "then 'demand_omitted'",
        "then 'demand_partial'",
        "then 'measurement_missing'",
    ]
    positions = [sql.rindex(reason) for reason in ordered_reasons]
    assert positions == sorted(positions)


def test_fact_daily_quality_and_watermark_publish_in_one_transaction() -> None:
    sql = render_publish_sql(Settings()).lower()
    assert sql.startswith("begin transaction;")
    assert "merge `lcs-developer-483404.oura_navi_monitor.question_events`" in sql
    assert "delete from `lcs-developer-483404.oura_navi_monitor.user_daily`" in sql
    assert "assert (" in sql
    assert "canonical monitor data quality gate failed" in sql
    assert "pipeline_state" in sql
    assert sql.index("assert (") < sql.index("commit transaction;")


def test_event_emission_failures_have_a_dedicated_alert_owner() -> None:
    script = (ROOT_DIR / "scripts" / "setup_alerts.sh").read_text(
        encoding="utf-8"
    )
    assert "lcs_rag_app_monitor_event_failed" in script
    assert r'textPayload:\"monitor_event_not_emitted\"' in script
    assert "analytics event emission failure" in script


def test_cloud_bootstrap_separates_sink_prepare_from_refresh_activation() -> None:
    script = (ROOT_DIR / "scripts" / "bootstrap_gcp.sh").read_text(
        encoding="utf-8"
    )
    assert "--stage" in script
    assert 'STAGE="prepare"' in script
    assert '[[ "${STAGE}" == "activate" ]]' in script
    assert "render_refresh_env" in (
        ROOT_DIR / "scripts" / "render_runtime_env.py"
    ).read_text(encoding="utf-8")


def test_obsolete_deletion_retains_canonical_raw_tables() -> None:
    script = (ROOT_DIR / "scripts" / "delete_obsolete_monitor_resources.sh").read_text(
        encoding="utf-8"
    )
    object_line = next(
        line for line in script.splitlines() if line.startswith("OBJECTS=(")
    )
    assert "run_googleapis_com_stdout" not in object_line
    assert "run_googleapis_com_requests" not in object_line
    obsolete_objects = object_line.removeprefix("OBJECTS=(").removesuffix(")").split()
    assert len(obsolete_objects) == 18
    assert "run_googleapis_com_stderr" in obsolete_objects
    assert "run_googleapis_com_varlog_system" in obsolete_objects
    assert "canonical raw tables retained" in script
    assert "--policy-id" in script
