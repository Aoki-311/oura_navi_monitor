import subprocess
import sys
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
        "retire_legacy_api_objects.sql",
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
        "pipeline_event_issues",
        "pipeline_run_event_manifest",
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
    assert "user_map_scope_enabled = true" in lowered
    assert "scope.is_active = true" not in lowered
    assert "concat('question:', event.request_id) as question_event_id" in lowered
    assert "target.request_date between" in lowered
    for partition_column in ("question_date", "answer_date", "action_date"):
        assert (
            f"target.{partition_column} between event_partition_start "
            "and event_partition_end"
        ) in " ".join(lowered.split())
    assert "safe_cast(latency_text as float64)" in lowered
    assert "regexp_extract(latency_text" in lowered


def test_late_events_use_effective_event_partitions_not_ingestion_window_partitions() -> None:
    sql = _sql("merge_incremental.sql").lower()

    assert "create temp table _run_monitor_source" in sql
    assert "create temp table _run_admissible_monitor_events" in sql
    assert "set event_partition_start" in sql
    assert "set event_partition_end" in sql
    assert "set persistence_partition_start" in sql
    assert "set persistence_partition_end" in sql
    for partition_column in ("question_date", "answer_date", "action_date"):
        assert (
            f"target.{partition_column} between event_partition_start "
            "and event_partition_end"
        ) in " ".join(sql.split())
    assert (
        "answer.answer_date between affected_answer_partition_start "
        "and affected_answer_partition_end"
    ) in " ".join(sql.split())


def test_bad_source_rows_have_a_hashed_per_event_ledger_and_are_not_merged() -> None:
    facts = _sql("create_fact_tables.sql").lower()
    sql = _sql("merge_incremental.sql").lower()

    assert "create table if not exists `${project_id}.${dataset_id}.pipeline_event_issues`" in facts
    ledger_schema = facts.split("pipeline_event_issues", maxsplit=1)[1].split(");", maxsplit=1)[0]
    assert "source_event_hash string not null" in ledger_schema
    assert "event_id string" not in ledger_schema
    assert "user_id string" not in ledger_schema
    assert "to_hex(sha256(" in sql
    assert "create temp table _run_event_issues" in sql
    assert "source_event_without_roster" in sql
    assert "conflicting_duplicate_event_id" in sql
    assert "duplicate_delivery_deduplicated" in sql
    assert "merge `${project_id}.${dataset_id}.pipeline_event_issues`" in sql
    assert "from _run_admissible_monitor_events" in sql


def test_each_run_has_a_privacy_safe_source_to_fact_accounting_manifest() -> None:
    facts = _sql("create_fact_tables.sql").lower()
    merge = _sql("merge_incremental.sql").lower()
    quality = _sql("check_data_quality.sql").lower()

    assert "pipeline_run_event_manifest" in facts
    manifest_schema = facts.split("pipeline_run_event_manifest", maxsplit=1)[1].split(
        ");", maxsplit=1
    )[0]
    assert "source_event_hash string not null" in manifest_schema
    assert "event_key_hash string" in manifest_schema
    assert "event_id string" not in manifest_schema
    assert "user_id string" not in manifest_schema
    assert "insert into `${project_id}.${dataset_id}.pipeline_run_event_manifest`" in merge
    assert "to_hex(sha256(source.event_id))" in merge
    assert "run_manifest_accounting_mismatch" in quality


def test_replayed_event_ids_repair_only_their_existing_duplicate_fact_rows() -> None:
    sql = _sql("merge_incremental.sql").lower()
    quality = _sql("check_data_quality.sql").lower()

    assert "create temp table _run_duplicate_fact_keys" in sql
    assert "having count(*) > 1" in sql
    assert "delete from `${project_id}.${dataset_id}.question_events` target" in sql
    assert "delete from `${project_id}.${dataset_id}.answer_events` target" in sql
    assert "target.question_date between event_partition_start and event_partition_end" in sql
    assert "target.answer_date between event_partition_start and event_partition_end" in sql
    assert "repaired_duplicate_question_event_id" in quality
    assert "repaired_duplicate_answer_event_id" in quality
    assert "then 'repaired'" in quality


def test_quality_checks_the_requests_affected_by_this_run_instead_of_event_time_window() -> None:
    sql = _sql("check_data_quality.sql").lower()

    assert "_run_admissible_monitor_events" in sql
    assert "_run_affected_request_ids" in sql
    assert "question_ts >= @window_start" not in sql
    assert "answer_ts >= @window_start" not in sql


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
    assert "dashboard_user_list`" in view_sql
    assert "drop " not in view_sql
    retirement = _sql("retire_legacy_api_objects.sql").lower()
    assert "drop table if exists `${project_id}.${dataset_id}.user_daily`" in retirement
    assert "drop table function" not in retirement


def test_api_views_do_not_read_logging_raw_tables() -> None:
    sql = _sql("create_api_views.sql")
    assert "run_googleapis_com_stdout" not in sql
    assert "run_googleapis_com_requests" not in sql


def test_incomplete_observability_is_visible_coverage_not_a_pipeline_wide_blocker() -> None:
    normalized = " ".join(_sql("check_data_quality.sql").lower().split())
    assert "else 'coverage'" in normalized
    for check_name in (
        "accepted_http_without_question_event",
        "question_without_terminal",
        "terminal_without_persistence_measurement",
    ):
        assert check_name in normalized
    assert "'coverage'" in normalized
    assert "when 'batch_blocking' then 'critical'" in normalized


def test_delayed_message_write_uses_answer_timestamp_for_a_bounded_partition_update() -> None:
    sql = _sql("merge_incremental.sql").lower()
    assert "json_value(payload_json, '$.answer_ts')" in sql
    assert "date(persistence.answer_ts" in sql
    assert "persistence_partition_start" in sql
    assert "persistence_partition_end" in sql


def test_unmatched_source_identity_is_detected_before_scope_filtering() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "source_question_without_roster" in sql
    assert "source_event_without_roster" in sql
    incremental = _sql("merge_incremental.sql").lower()
    assert "left join _run_scope_by_user scope" in incremental
    assert "create temp table _run_event_issues" in incremental


def test_one_sided_telemetry_is_measured_while_orphan_answers_still_block_publish() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "question_without_terminal" in sql
    assert "answer_without_question" in sql
    assert "left join question_links" in sql
    assert "accepted_http_without_question_event" in sql
    assert "accepted_ask_requests" in sql
    assert "status between 200 and 399" in sql
    assert "duplicate_source_question_event_id" in sql
    assert "duplicate_source_answer_event_id" in sql


def test_invalid_producer_axes_are_visible_but_do_not_block_the_batch() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "invalid_classification_producer" in sql
    assert "classification_status = 'producer_invalid'" in sql
    assert "invalid_product_resolution_counts" in sql
    assert "product_resolved_count > product_candidate_count" in sql
    normalized = " ".join(sql.split())
    assert "'invalid_classification_producer'," in normalized
    assert "then 'axis_unmeasured'" in normalized
    assert "when 'axis_unmeasured' then 'producer_error'" in normalized


def test_v2_product_measurement_requires_status_count_and_identity_consistency() -> None:
    quality_sql = _sql("check_data_quality.sql").lower()
    view_sql = " ".join(_sql("create_api_views.sql").lower().split())

    assert "invalid_product_resolution_semantics" in quality_sql
    assert "invalid_product_identity_alignment" in quality_sql
    assert "effective_product_resolution_status = 'not_applicable'" in view_sql
    assert "q.product_candidate_count = 0" in view_sql
    assert "effective_product_resolution_status = 'resolved'" in view_sql
    assert "q.product_resolved_count = q.product_candidate_count" in view_sql
    assert "effective_product_resolution_status = 'partially_resolved'" in view_sql
    assert "q.product_resolved_count < q.product_candidate_count" in view_sql
    assert "effective_product_resolution_status = 'unresolved'" in view_sql
    assert "array_length(ifnull(q.product_keys, []))" in view_sql
    assert "q.product_names[safe_offset(position)]" in view_sql


def test_analytics_v2_category_and_task_fail_independently() -> None:
    sql = _sql("create_api_views.sql").lower()
    classification_owner, axis_owner = sql.split(
        "), axis_owned_questions as (",
        maxsplit=1,
    )
    task_owner = axis_owner.split(
        "end as task_measurement_state",
        maxsplit=1,
    )[0]
    v2_task_owner = task_owner.split(
        "when q.effective_analytics_contract_version = 'request_spec_analytics_v2'",
        maxsplit=1,
    )[1]

    assert "invalid_question_category" in classification_owner
    assert "request_spec_unavailable" in classification_owner
    assert "invalid_analytics_task" not in classification_owner
    assert "invalid_analytics_task" in task_owner
    assert "request_spec_unavailable" in task_owner
    assert "invalid_question_category" not in task_owner
    assert "q.classification_measurement_state = 'measured'" not in v2_task_owner


def test_v2_category_and_task_measurement_require_closed_consistent_values() -> None:
    quality_sql = _sql("check_data_quality.sql").lower()
    view_sql = " ".join(_sql("create_api_views.sql").lower().split())

    assert "invalid_classification_semantics" in quality_sql
    assert "invalid_task_semantics" in quality_sql
    assert "q.primary_question_category in (" in view_sql
    assert (
        "q.primary_question_category in unnest(ifnull(q.question_categories, []))"
        in view_sql
    )
    assert "q.is_multi_intent = (array_length(q.question_categories) > 1)" in view_sql
    assert "task not in (" in view_sql
    assert "invalid_analytics_task" in view_sql


def test_historical_unmeasured_classification_is_explicit_and_cannot_be_claimed_by_producers() -> None:
    history_sql = _sql("merge_history.sql").lower()
    quality_sql = _sql("check_data_quality.sql").lower()
    assert "'not_measured'" in history_sql
    normalized = " ".join(quality_sql.split())
    assert "record_origin in ('firestore_history', 'legacy_audit_history')" in normalized
    assert "and classification_status = 'not_measured'" in normalized


def test_history_never_invents_request_tasks_and_quality_allows_only_history_to_omit_them() -> None:
    history_sql = " ".join(_sql("merge_history.sql").lower().split())
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())

    assert "analytics_tasks = array<string>[]" in history_sql
    assert "false, array<string>[]" in history_sql
    assert (
        "ifnull(record_origin, '') not in ('firestore_history', 'legacy_audit_history') "
        "and ifnull(array_length(analytics_tasks), 0) = 0"
    ) in quality_sql


def test_history_merge_has_explicit_target_partition_filter_for_every_fact() -> None:
    history_sql = _sql("merge_history.sql").lower()
    assert history_sql.count("target.question_date = @history_partition_date") == 1
    assert history_sql.count("target.answer_date = @history_partition_date") == 2
    assert history_sql.count("target.updated_date = @history_partition_date") == 1


def test_incremental_join_has_an_explicit_event_identity_owner() -> None:
    incremental_sql = _sql("merge_incremental.sql").lower()
    assert incremental_sql.count("left join _run_scope_by_user scope") == 1
    assert "on event.user_id = scope.user_id" in incremental_sql
    assert incremental_sql.count("from _run_admissible_monitor_events") >= 5


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
        "answer.answer_date between persistence_partition_start and "
        "persistence_partition_end"
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


def test_cutover_publishes_additive_page_semantics_only_after_history() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "bootstrap_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    rebuild = (root / "scripts" / "rebuild_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    assert "create_api_views.sql" not in bootstrap
    history_position = rebuild.index("app.jobs.rebuild_history --apply")
    semantic_position = rebuild.index("publish_monitor_views.sh")
    assert history_position < semantic_position
    assert "run_monitor_refresh.sh" not in rebuild
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


def test_fact_quality_and_watermark_publish_atomically_with_durable_failure_diagnostics() -> None:
    sql = render_publish_sql(Settings()).lower()
    assert sql.startswith("declare event_partition_start")
    assert sql.index("begin transaction;") < sql.index(
        "merge `lcs-developer-483404.oura_navi_monitor.question_events`"
    )
    assert "merge `lcs-developer-483404.oura_navi_monitor.question_events`" in sql
    assert "user_daily" not in sql
    assert "if exists (" in sql
    assert "rollback transaction" in sql
    assert "error_code = 'dataqualitygateerror'" in sql
    assert "pipeline_state" in sql
    assert "pipeline_quality_events" in sql
    assert "lease_run_id = @lease_id" in sql
    assert "data_through = @expected_watermark" in sql
    assert "delete from `lcs-developer-483404.oura_navi_monitor.pipeline_state`" not in sql
    assert sql.index("if exists (") < sql.index("commit transaction;")


def test_analytics_v2_fields_and_pipeline_lease_are_additive_schema_migrations() -> None:
    facts = _sql("create_fact_tables.sql").lower()
    aggregates = _sql("create_aggregates.sql").lower()
    for field in (
        "analytics_contract_version",
        "classification_reason_codes",
        "product_resolution_status",
        "product_resolution_reason_codes",
    ):
        assert field in facts
        assert f"add column if not exists {field}" in facts
    for field in ("lease_run_id", "lease_acquired_at", "lease_expires_at"):
        assert field in aggregates
        assert f"add column if not exists {field}" in aggregates
    assert "execution_id string" in aggregates
    assert "add column if not exists execution_id string" in aggregates
    assert "pipeline_quality_events" in aggregates

    refresh_job = (ROOT_DIR / "app" / "jobs" / "refresh_analytics.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "execution_id = @execution_id" in refresh_job
    assert "run_id, execution_id, trigger_source, started_at" in refresh_job


def test_event_emission_failures_have_a_dedicated_alert_owner() -> None:
    script = (ROOT_DIR / "scripts" / "setup_alerts.sh").read_text(
        encoding="utf-8"
    )
    assert "lcs_rag_app_monitor_event_failed" in script
    assert r'textPayload:\"monitor_event_not_emitted\"' in script
    assert "analytics event emission failure" in script
    assert "oura_navi_monitor_rows_quarantined" in script
    assert "oura_navi_monitor_axis_unmeasured" in script
    assert "monitor_pipeline_quality_event" in script


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


def test_one_rebuild_script_cannot_bypass_the_frozen_backfill_workflow() -> None:
    script = (ROOT_DIR / "scripts" / "rebuild_monitor_data.sh").read_text(
        encoding="utf-8"
    )
    history = script.index("app.jobs.rebuild_history --apply")
    semantics = script.index("publish_monitor_views.sh")
    assert history < semantics
    assert "--until-current" not in script
    assert "--history-confirm" in script
    assert "shadow" in script
    assert "backup" in script
    assert "one-time history rebuild entry is retired for production" in script
    assert "frozen incremental backfill workflow" in script


def test_sql_validation_script_is_read_only_and_uses_dry_run() -> None:
    script = (ROOT_DIR / "scripts" / "dry_run_monitor_sql.py").read_text(
        encoding="utf-8"
    )
    assert "dry_run=True" in script
    assert "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" in script
    assert "GOOGLE_APPLICATION_CREDENTIALS" in script
    assert "--apply" not in script


def test_sql_validation_script_runs_from_the_repository_without_pythonpath() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "dry_run_monitor_sql.py"), "--help"],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Read-only BigQuery validation" in result.stdout


def test_build_approval_shell_adapter_never_materializes_an_access_token() -> None:
    script = (ROOT_DIR / "scripts" / "approve_pending_build.sh").read_text(
        encoding="utf-8"
    )
    assert "cloud_build_approval.py" in script
    assert "print-access-token" not in script
    assert "Authorization: Bearer" not in script


def test_obsolete_deletion_is_a_read_only_inventory_and_hard_stops_apply() -> None:
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
    assert "destructive retirement is intentionally disabled" in script
    assert "separately reviewed tool" in script
    assert "bq --project_id" not in script
    assert "gcloud --project" not in script
    assert " rm -f " not in script


def test_additive_schema_and_semantic_publishers_never_run_legacy_retirement() -> None:
    additive_sql = [
        ROOT_DIR / "sql" / "create_dataset.sql",
        ROOT_DIR / "sql" / "create_fact_tables.sql",
        ROOT_DIR / "sql" / "create_aggregates.sql",
        ROOT_DIR / "sql" / "create_source_tables.sql",
        ROOT_DIR / "sql" / "create_api_views.sql",
    ]
    for path in additive_sql:
        source = path.read_text(encoding="utf-8").lower()
        assert "drop table" not in source, path.name
        assert "drop view" not in source, path.name
        assert "drop function" not in source, path.name

    for name in (
        "bootstrap_monitor_data.sh",
        "publish_monitor_source_views.sh",
        "publish_monitor_views.sh",
    ):
        source = (ROOT_DIR / "scripts" / name).read_text(encoding="utf-8")
        assert "retire_legacy_api_objects.sql" not in source

    retirement = (
        ROOT_DIR / "sql" / "retire_legacy_api_objects.sql"
    ).read_text(encoding="utf-8").lower()
    assert "drop" in retirement


def test_schema_migration_batches_new_columns_into_one_metadata_update_per_table() -> None:
    facts = (ROOT_DIR / "sql" / "create_fact_tables.sql").read_text(
        encoding="utf-8"
    )
    aggregates = (ROOT_DIR / "sql" / "create_aggregates.sql").read_text(
        encoding="utf-8"
    )
    for table in ("question_events", "answer_events", "demand_events"):
        assert facts.count(
            f"ALTER TABLE `${{PROJECT_ID}}.${{DATASET_ID}}.{table}`\nADD COLUMN"
        ) == 1
    for table in ("pipeline_runs", "pipeline_state"):
        assert aggregates.count(
            f"ALTER TABLE `${{PROJECT_ID}}.${{DATASET_ID}}.{table}`\nADD COLUMN"
        ) == 1
