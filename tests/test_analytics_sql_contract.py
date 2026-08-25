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
        "merge_history.sql",
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
        "pipeline_runs",
        "pipeline_state",
        "dashboard_events",
        "dashboard_user_list",
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
    assert "json_value(event_payload, '$.user_id')" in source_sql
    assert "$.user_key" not in source_sql
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
    sql = (_sql("merge_incremental.sql") + _sql("create_api_views.sql")).lower()
    assert "department in" not in sql


def test_activity_level_has_one_python_owner_not_a_second_sql_case() -> None:
    sql = _sql("create_api_views.sql").lower()
    assert "activity_level" not in sql
    service = (ROOT_DIR / "app" / "services" / "analytics_service.py").read_text(
        encoding="utf-8"
    )
    assert service.count("def _activity_level(") == 1


def test_user_list_preserves_the_actual_last_question_timestamp() -> None:
    view_sql = _sql("create_api_views.sql").lower()
    assert "max(question_ts) as last_active_at" in view_sql
    assert "timestamp(last_seen.last_active_date" not in view_sql
    assert "drop table if exists `${project_id}.${dataset_id}.user_daily`" in view_sql


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


def test_historical_unmeasured_classification_is_explicit_and_cannot_be_claimed_by_producers() -> None:
    history_sql = _sql("merge_history.sql").lower()
    quality_sql = _sql("check_data_quality.sql").lower()
    assert "'not_measured'" in history_sql
    normalized = " ".join(quality_sql.split())
    assert "record_origin in ('firestore_history', 'legacy_audit_history')" in normalized
    assert "and classification_status = 'not_measured'" in normalized


def test_history_merge_has_explicit_target_partition_filter_for_every_fact() -> None:
    history_sql = _sql("merge_history.sql").lower()
    assert history_sql.count("target.question_date = @history_partition_date") == 1
    assert history_sql.count("target.answer_date = @history_partition_date") == 2
    assert history_sql.count("target.updated_date = @history_partition_date") == 1


def test_incremental_join_has_an_explicit_event_identity_owner() -> None:
    incremental_sql = _sql("merge_incremental.sql").lower()
    assert incremental_sql.count("event.user_id") == 8


def test_canonical_merges_never_depend_on_physical_column_order() -> None:
    for name in (
        "merge_history.sql",
        "merge_incremental.sql",
        "merge_firestore_projection.sql",
    ):
        assert "insert row" not in _sql(name).lower()


def test_persistence_enrichment_has_an_explicit_target_partition_window() -> None:
    normalized = " ".join(_sql("merge_incremental.sql").lower().split())
    assert (
        "answer.answer_date between date_sub(date(@window_start, "
        "'${monitor_timezone}'), interval 1 day) and date_add(date(@window_end, "
        "'${monitor_timezone}'), interval 1 day)"
    ) in normalized


def test_semantic_sql_does_not_duplicate_completion_or_activity_formulas() -> None:
    aggregate_sql = _sql("create_aggregates.sql").lower()
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
        assert duplicate not in sql


def test_cutover_publishes_page_semantics_only_after_history_and_incremental_data() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "bootstrap_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    rebuild = (root / "scripts" / "rebuild_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    assert "create_api_views.sql" not in bootstrap
    history_position = rebuild.index("app.jobs.rebuild_history --apply")
    refresh_position = rebuild.index("run_monitor_refresh.sh")
    semantic_position = rebuild.index("render_sql('create_api_views.sql'")
    assert history_position < refresh_position < semantic_position
    assert "PREFLIGHT_CONFIRM" in rebuild


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
    assert "user_daily" not in sql
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
    assert "jsonPayload.monitor_event=true" in script
    assert "request_user_metric_json|stream_terminal_json" in script
    assert "tmcs_stage_latency_json[ =]" in script
    assert "run.googleapis.com%2Fstderr" in script


def test_one_rebuild_script_compiles_history_before_bounded_incremental_catchup() -> None:
    script = (ROOT_DIR / "scripts" / "rebuild_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    history = script.index("app.jobs.rebuild_history --apply")
    catchup = script.index("--until-current")
    assert history < catchup
    assert "--history-confirm" in script
    assert "shadow" in script
    assert "backup" in script


def test_sql_validation_script_is_read_only_and_uses_dry_run() -> None:
    script = (ROOT_DIR / "scripts" / "dry_run_monitor_sql.py").read_text(
        encoding="utf-8"
    )
    assert "dry_run=True" in script
    assert "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" in script
    assert "GOOGLE_APPLICATION_CREDENTIALS" in script
    assert "--apply" not in script


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
    assert len(obsolete_objects) == 17
    assert "run_googleapis_com_stderr" not in obsolete_objects
    assert "run_googleapis_com_varlog_system" in obsolete_objects
    assert "historical raw sources retained without row deletion" in script
    assert "delete from" not in script.lower()
    assert "--policy-id" in script
    assert "--history-confirm" in script
    assert "source = 'history_rebuild'" in script
    assert '"${HISTORY_ISSUES}" == "0"' in script
