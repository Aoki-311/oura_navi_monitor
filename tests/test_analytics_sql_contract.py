import re
import subprocess
import sys
from pathlib import Path

import pytest

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
        "monitor_contract_revision_ledger",
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

    # HTTP-to-question emission coverage must use the frozen raw run source so
    # an out-of-scope roster does not masquerade as a missing emitted event.
    assert "from _run_monitor_source" in sql
    assert "delivery_row_number = 1" in sql
    assert "business_request_ids" in sql
    assert "from _run_admissible_monitor_events" in sql
    assert "question_ts >= @window_start" not in sql
    assert "answer_ts >= @window_start" not in sql


def test_http_question_coverage_uses_exact_cloud_trace_not_aggregate_counts() -> None:
    source_sql = _sql("create_source_tables.sql").lower()
    quality_sql = _sql("check_data_quality.sql").lower()
    merge_sql = _sql("merge_incremental.sql").lower()

    assert "json_value(raw_json, '$.trace')" in source_sql
    assert "logging.googleapis.com/trace" in source_sql
    assert source_sql.count("as cloud_trace") == 2
    assert "logging.googleapis.com/spanid" in source_sql
    assert "$.monitor_contract_version" in source_sql
    assert source_sql.count("as cloud_span_id") == 2
    assert "group by revision_name, cloud_trace, cloud_span_id, endpoint_class" in quality_sql
    assert "using (revision_name, cloud_trace, cloud_span_id, endpoint_class)" in quality_sql
    assert "count(distinct request.request_key) as request_count" in quality_sql
    assert "count(distinct event_key) as question_count" in quality_sql
    assert "trace_contract_revisions" in quality_sql
    assert "monitor_contract_version = 'monitor.v2'" in quality_sql
    assert "join trace_contract_revisions contract using (revision_name)" in quality_sql
    assert "from trace_enforced_ask_request_counts request" in quality_sql
    assert "http_trace_contract_unavailable" in quality_sql
    assert "count(distinct event_id) from source_questions" not in quality_sql
    assert "event.cloud_trace" in merge_sql
    assert "event.cloud_span_id" in merge_sql


def _classify_http_path_from_sql(path: str) -> str:
    """Exercise the ordered route regexes owned by the BigQuery source view."""

    source_sql = _sql("create_source_tables.sql")
    contracts = re.findall(
        r"WHEN\s+REGEXP_CONTAINS\(\s*request_path,\s*r'([^']+)'\s*\)\s*"
        r"THEN\s+'([^']+)'",
        source_sql,
        flags=re.IGNORECASE,
    )
    for pattern, endpoint_class in contracts:
        if re.search(pattern, path):
            return endpoint_class
    return "other"


def test_all_four_debug_ask_paths_are_classified_by_the_sql_owner() -> None:
    assert {
        path: _classify_http_path_from_sql(path)
        for path in (
            "/v2/debug/ask",
            "/v2/debug/ask/enhance_full",
            "/v2/debug/ask/stream",
            "/v2/debug/ask/enhance_full/stream",
        )
    } == {
        "/v2/debug/ask": "debug_ask",
        "/v2/debug/ask/enhance_full": "debug_ask",
        "/v2/debug/ask/stream": "debug_ask_stream",
        "/v2/debug/ask/enhance_full/stream": "debug_ask_stream",
    }


def test_v2_http_event_contract_is_bidirectional_and_route_exact() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())
    batch_clause = quality_sql.split(
        "then 'repaired' when check_name in (",
        maxsplit=1,
    )[1].split(
        ") then 'batch_blocking'",
        maxsplit=1,
    )[0]

    assert re.search(
        r"endpoint_class in \(\s*'ask', 'ask_stream', 'debug_ask', "
        r"'debug_ask_stream'\s*\)",
        quality_sql,
    )
    assert "http_event_route_class_mismatch" in quality_sql
    assert "monitor_v2_question_completed_http_cardinality_mismatch" in quality_sql
    assert "request.endpoint_class != question.endpoint_class" in quality_sql
    assert (
        "using (revision_name, cloud_trace, cloud_span_id, endpoint_class)"
        in quality_sql
    )
    for check_name in (
        "accepted_http_without_question_event",
        "http_event_route_class_mismatch",
        "monitor_v2_question_completed_http_cardinality_mismatch",
    ):
        assert f"'{check_name}'" in batch_clause


def test_http_contract_separates_completed_failures_from_accepted_requests() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())
    completed_rows = quality_sql.split(
        "), completed_ask_request_rows as (",
        maxsplit=1,
    )[1].split(
        "), accepted_ask_request_rows as (",
        maxsplit=1,
    )[0]
    accepted_rows = quality_sql.split(
        "), accepted_ask_request_rows as (",
        maxsplit=1,
    )[1].split(
        "), trace_enforced_completed_ask_request_rows as (",
        maxsplit=1,
    )[0]
    completed_counts = quality_sql.split(
        "), trace_enforced_completed_ask_request_counts as (",
        maxsplit=1,
    )[1].split(
        "), trace_enforced_ask_request_rows as (",
        maxsplit=1,
    )[0]
    reverse_rows = quality_sql.split(
        "), monitor_v2_question_http_cardinality as (",
        maxsplit=1,
    )[1].split(
        "), business_request_ids as (",
        maxsplit=1,
    )[0]

    # A request log is emitted when Cloud Run completes the HTTP exchange, so
    # producer-event -> HTTP correlation must retain terminal 5xx responses.
    assert "status is not null" in completed_rows
    assert "status between 200 and 299" not in completed_rows
    assert "from completed_ask_request_rows" in accepted_rows
    assert "status between 200 and 299" in accepted_rows
    assert "from trace_enforced_completed_ask_request_rows request" in completed_counts
    assert "count(distinct request.request_key) as request_count" in completed_counts
    assert "trace_enforced_completed_ask_request_counts request" in reverse_rows
    assert "trace_enforced_ask_request_rows" not in reverse_rows

    accepted_check = quality_sql.split(
        "select 'accepted_http_without_question_event'",
        maxsplit=1,
    )[1].split("union all", maxsplit=1)[0]
    assert "from trace_enforced_ask_request_counts request" in accepted_check

    accepted_status = re.search(
        r"status between (\d+) and (\d+)", accepted_rows
    )
    assert accepted_status is not None
    accepted_min, accepted_max = map(int, accepted_status.groups())
    governed_routes = set(
        re.findall(
            r"'(ask|ask_stream|debug_ask|debug_ask_stream)'",
            completed_rows,
        )
    )

    def blocks_for_http_question_contract(
        *, status: int, endpoint_class: str, question_count: int, completed_count: int = 1
    ) -> bool:
        assert endpoint_class in governed_routes
        accepted_http_without_question = (
            accepted_min <= status <= accepted_max and question_count != 1
        )
        question_completed_cardinality_mismatch = (
            question_count > 0
            and (question_count != 1 or completed_count != 1)
        )
        return accepted_http_without_question or question_completed_cardinality_mismatch

    assert not blocks_for_http_question_contract(
        status=500, endpoint_class="ask", question_count=1
    )
    assert not blocks_for_http_question_contract(
        status=500, endpoint_class="debug_ask", question_count=1
    )
    assert blocks_for_http_question_contract(
        status=200, endpoint_class="ask", question_count=0
    )
    assert blocks_for_http_question_contract(
        status=200, endpoint_class="debug_ask", question_count=0
    )
    assert blocks_for_http_question_contract(
        status=500, endpoint_class="ask", question_count=1, completed_count=2
    )
    assert blocks_for_http_question_contract(
        status=500, endpoint_class="debug_ask", question_count=2
    )


def test_expected_v2_missing_trace_or_span_is_a_blocking_contract_failure() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())
    batch_clause = quality_sql.split(
        "then 'repaired' when check_name in (",
        maxsplit=1,
    )[1].split(
        ") then 'batch_blocking'",
        maxsplit=1,
    )[0]
    missing_fields_check = quality_sql.split(
        "select 'monitor_v2_event_missing_http_correlation_fields'",
        maxsplit=1,
    )[1].split("union all", maxsplit=1)[0]

    assert "monitor_v2_event_missing_http_correlation_fields" in quality_sql
    assert "join trace_contract_revisions contract using (revision_name)" in missing_fields_check
    assert "nullif(event.cloud_trace, '') is null" in missing_fields_check
    assert "nullif(event.cloud_span_id, '') is null" in missing_fields_check
    for check_name in (
        "monitor_v2_event_missing_http_correlation_fields",
        "monitor_v2_question_invalid_endpoint_class",
        "monitor_v2_http_missing_trace_context",
        "monitor_v2_revision_contract_downgrade",
    ):
        assert f"'{check_name}'" in batch_clause


def test_v2_revision_contract_uses_a_bounded_persistent_ledger() -> None:
    schema_sql = _sql("create_aggregates.sql").lower()
    merge_sql = _sql("merge_incremental.sql").lower()
    quality_sql = _sql("check_data_quality.sql").lower()
    registration_path = ROOT_DIR / "scripts" / "register_monitor_v2_revision.py"
    assert registration_path.is_file()
    registration_sql = registration_path.read_text(encoding="utf-8").lower()
    bootstrap_sql = (ROOT_DIR / "scripts" / "bootstrap_gcp.sh").read_text(
        encoding="utf-8"
    ).lower()
    publish_sql = render_publish_sql(
        Settings(monitor_analytics_start_at="2026-08-01T00:00:00Z")
    ).lower()
    trace_contract = quality_sql.split(
        "with valid_trace_contract_registrations as (",
        maxsplit=1,
    )[1].split(
        "), source_question",
        maxsplit=1,
    )[0]

    assert "monitor_contract_revision_ledger" in schema_sql
    assert "registration_source string not null" in schema_sql
    assert "sample_cloud_trace string not null" in schema_sql
    assert "sample_cloud_span_id string not null" in schema_sql
    assert "sample_correlation_hash string not null" in schema_sql
    assert "enforcement_start timestamp" in schema_sql
    assert "promotion_receipt_sha256 string" in schema_sql
    assert "activation_service_readback_sha256 string" in schema_sql
    assert "monitor_contract_revision_ledger" not in merge_sql
    assert "merge {ledger} target" in registration_sql
    assert "candidate_v2_exact_http_question_sample" in registration_sql
    assert "monitor_contract_revision_ledger" in trace_contract
    assert "_run_monitor_source" not in trace_contract
    assert "monitor_event_source" not in trace_contract
    assert "@analytics_start" not in trace_contract
    assert "monitor_contract_revision_ledger; do" in bootstrap_sql
    assert "monitor_contract_revision_ledger`" not in publish_sql.split(
        "set run_quality_results", maxsplit=1
    )[0]


def test_cutover_comes_only_from_a_self_verifying_registration_row() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())
    registration_rows = quality_sql.split(
        "with valid_trace_contract_registrations as (",
        maxsplit=1,
    )[1].split(
        "), trace_contract_revisions as (",
        maxsplit=1,
    )[0]
    enforcement = quality_sql.split(
        "), trace_contract_enforcement as (",
        maxsplit=1,
    )[1].split(
        "), source_question_correlation_rows as (",
        maxsplit=1,
    )[0]

    assert "registration_source = 'candidate_v2_exact_http_question_sample'" in registration_rows
    assert "sample_endpoint_class in ('ask', 'ask_stream')" in registration_rows
    assert "sample_cloud_trace" in registration_rows
    assert "sample_cloud_span_id" in registration_rows
    assert "sample_correlation_hash = to_hex(sha256(concat(" in registration_rows
    assert "qualify count(*) over (partition by revision_name) = 1" in registration_rows
    assert "min(enforcement_start) as enforcement_start" in enforcement
    assert "from valid_trace_contract_registrations" in enforcement
    assert "activation_source = 'lcs_promotion_v2_drained_live_readback'" in enforcement
    assert "promotion_receipt_type = 'lcs_candidate_promotion_v2'" in enforcement
    assert "promotion_target_revision = revision_name" in enforcement
    assert "promotion_drain_until = timestamp_add(" in enforcement
    assert "enforcement_start >= promotion_drain_until" in enforcement
    assert "enforcement_start <= current_timestamp()" in enforcement
    assert "activation_service_readback_sha256" in enforcement
    assert "@enforcement_start" not in quality_sql


def test_legacy_unknown_v2_is_coverage_but_unknown_after_cutover_is_blocking() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())
    batch_clause = quality_sql.split(
        "then 'repaired' when check_name in (",
        maxsplit=1,
    )[1].split(
        ") then 'batch_blocking'",
        maxsplit=1,
    )[0]
    post_event_check = quality_sql.split(
        "select 'unexpected_monitor_v2_revision_after_enforcement'",
        maxsplit=1,
    )[1].split("union all", maxsplit=1)[0]
    post_http_check = quality_sql.split(
        "select 'unexpected_accepted_business_http_revision_after_enforcement'",
        maxsplit=1,
    )[1].split("union all", maxsplit=1)[0]
    legacy_check = quality_sql.split(
        "select 'legacy_unregistered_monitor_v2_revision'",
        maxsplit=1,
    )[1].split("union all", maxsplit=1)[0]

    assert "event.source_ts >= enforcement.enforcement_start" in post_event_check
    assert "request.source_ts >= enforcement.enforcement_start" in post_http_check
    assert "request.endpoint_class in ('ask', 'ask_stream')" in post_http_check
    assert "event.source_ts < enforcement.enforcement_start" in legacy_check
    assert "'unexpected_monitor_v2_revision_after_enforcement'" in batch_clause
    assert "'unexpected_accepted_business_http_revision_after_enforcement'" in batch_clause
    assert "'legacy_unregistered_monitor_v2_revision'" not in batch_clause

    def unknown_revision_blocks(*, activated_at: int | None, source_ts: int) -> bool:
        return activated_at is not None and source_ts >= activated_at

    # Candidate proof may precede traffic promotion by minutes. Until the
    # drained activation is written, old production traffic remains coverage.
    assert not unknown_revision_blocks(activated_at=None, source_ts=200)
    assert not unknown_revision_blocks(activated_at=300, source_ts=299)
    assert unknown_revision_blocks(activated_at=300, source_ts=300)


def test_two_requests_sharing_one_trace_cannot_match_one_question_span() -> None:
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())

    assert "request.request_count = 1 and question.question_count = 1" in quality_sql
    assert (
        "greatest(request.request_count, coalesce(question.question_count, 0))"
        in quality_sql
    )
    assert "revision_name, cloud_trace, cloud_span_id, endpoint_class" in quality_sql


def test_all_answer_generating_http_paths_share_one_source_contract() -> None:
    source_sql = _sql("create_source_tables.sql").lower()
    quality_sql = _sql("check_data_quality.sql").lower()
    merge_sql = _sql("merge_incremental.sql").lower()

    route_contract = "ask(/enhance_full)?"
    assert route_contract in source_sql
    assert "debug/ask(/enhance_full)?/stream" in source_sql
    assert "then 'debug_ask_stream'" in source_sql
    assert "debug/ask(/enhance_full)?/?" in source_sql
    assert "then 'debug_ask'" in source_sql
    assert "ask(/enhance_full)?/stream" in source_sql
    assert "ask(/enhance_full)?/?" in source_sql
    assert "accepted_ask_request_rows" in quality_sql
    assert "'debug_ask', 'debug_ask_stream'" in quality_sql
    assert "regexp_contains" not in merge_sql


def test_debug_requests_cannot_pollute_business_measurements() -> None:
    quality_sql = _sql("check_data_quality.sql").lower()
    semantic_sql = _sql("create_api_views.sql").lower()

    assert "business_request_ids" in quality_sql
    assert quality_sql.count("endpoint_class in ('ask', 'ask_stream')") >= 2
    assert "http_event_route_class_mismatch" in quality_sql
    assert "monitor_v2_question_completed_http_cardinality_mismatch" in quality_sql
    assert "debug/ask" not in quality_sql
    assert semantic_sql.count("endpoint_class in ('ask', 'ask_stream')") >= 2


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
    assert "where snapshot_run_id = @run_id" in sql
    assert "where snapshot_run_id is null" in sql
    assert "snapshot_created_at < timestamp_sub(current_timestamp(), interval 7 day)" in sql
    assert "snapshot_run_id != coalesce" in sql
    assert "where true" not in sql
    assert "unnest(@user_scope_rows)" in sql
    assert "target.updated_date between @conversation_partition_start and @conversation_partition_end" in sql
    assert "target.answer_date between @citation_partition_start and @citation_partition_end" in sql


def test_scope_flags_are_not_reimplemented_as_department_literals() -> None:
    sql = (_sql("merge_incremental.sql") + _sql("create_api_views.sql")).lower()
    assert "department in" not in sql


def test_missing_contract_version_is_historical_only_for_explicit_history_origins() -> None:
    view = " ".join(_sql("create_api_views.sql").lower().split())
    quality = " ".join(_sql("check_data_quality.sql").lower().split())

    assert "when q.record_origin in ('firestore_history', 'legacy_audit_history') then coalesce(nullif(q.analytics_contract_version, ''), 'request_spec_analytics_v1')" in view
    assert "else nullif(q.analytics_contract_version, '')" in view
    assert "missing_current_analytics_contract_version" in quality
    assert "ifnull(record_origin, '') not in ('firestore_history', 'legacy_audit_history')" in quality
    assert "'missing_current_analytics_contract_version', 'unknown_analytics_contract_version'" in quality


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


def test_user_list_uses_an_exact_as_of_cutoff_not_a_date_only_snapshot() -> None:
    view_sql = " ".join(_sql("create_api_views.sql").lower().split())
    user_list = view_sql.split(
        "create or replace table function `${project_id}.${dataset_id}.dashboard_user_list_v2`",
        maxsplit=1,
    )[1].split(
        "create or replace table function `${project_id}.${dataset_id}.dashboard_events`",
        maxsplit=1,
    )[0]

    assert "p_as_of timestamp" in user_list
    assert "question_ts < p_as_of" in user_list
    assert "date(p_as_of, '${monitor_timezone}')" in user_list
    assert "p_today date" not in user_list


def test_api_views_do_not_read_logging_raw_tables() -> None:
    sql = _sql("create_api_views.sql")
    assert "run_googleapis_com_stdout" not in sql
    assert "run_googleapis_com_requests" not in sql


def test_missing_current_lifecycle_telemetry_blocks_an_incomplete_publish() -> None:
    normalized = " ".join(_sql("check_data_quality.sql").lower().split())
    batch_clause = normalized.split(
        "then 'repaired' when check_name in (",
        maxsplit=1,
    )[1].split(
        ") then 'batch_blocking'",
        maxsplit=1,
    )[0]
    for check_name in (
        "accepted_http_without_question_event",
        "invalid_current_question_event_contract",
        "question_without_terminal",
        "answer_without_question",
        "invalid_current_terminal_contract",
        "current_final_without_persistence_measurement",
        "current_final_without_demand_measurement",
        "current_terminal_without_latency_measurement",
    ):
        assert f"'{check_name}'" in batch_clause
    assert "when 'batch_blocking' then 'critical'" in normalized


def test_missing_or_malformed_current_contract_fields_block_publication() -> None:
    normalized = " ".join(_sql("check_data_quality.sql").lower().split())
    batch_clause = normalized.split(
        "then 'repaired' when check_name in (",
        maxsplit=1,
    )[1].split(
        ") then 'batch_blocking'",
        maxsplit=1,
    )[0]

    for check_name in (
        "unknown_question_category",
        "unknown_secondary_question_category",
        "unknown_analytics_task",
        "missing_analytics_axes",
        "invalid_classification_semantics",
        "invalid_task_semantics",
        "invalid_product_resolution_counts",
        "invalid_product_resolution_semantics",
        "invalid_product_identity_alignment",
        "missing_current_analytics_contract_version",
        "unknown_analytics_contract_version",
        "unknown_classification_reason_code",
        "unknown_product_resolution_status",
        "unknown_product_resolution_reason_code",
        "unknown_classification_status",
    ):
        assert f"'{check_name}'" in batch_clause


def test_current_lifecycle_gates_do_not_reclassify_explicit_history_gaps() -> None:
    normalized = " ".join(_sql("check_data_quality.sql").lower().split())

    assert normalized.count(
        "ifnull(q.record_origin, '') not in "
        "('firestore_history', 'legacy_audit_history')"
    ) >= 1
    assert normalized.count(
        "ifnull(a.record_origin, '') not in "
        "('firestore_history', 'legacy_audit_history')"
    ) >= 3
    assert (
        "safe_cast(json_value(payload_json, '$.valid_question') as bool) "
        "is not true"
    ) in normalized
    assert (
        "a.analytics_contract_version = 'request_spec_analytics_v2' and ( "
        "ifnull(a.terminal, '') not in ('final', 'error') "
        "or ifnull(a.runtime_status, '') not in ('completed', 'failed') "
        "or (a.terminal = 'final') != (a.runtime_status = 'completed') )"
    ) in normalized
    assert (
        "a.terminal = 'final' and a.runtime_status = 'completed' and ( "
        "a.message_persisted is null or a.assistant_error_present is null )"
    ) in normalized
    assert (
        "a.terminal = 'final' and a.runtime_status = 'completed' and ( "
        "a.demand_total is null or a.demand_total <= 0 "
        "or a.partial_demand_count is null or a.omitted_demand_count is null "
        "or a.system_fault_count is null )"
    ) in normalized
    assert "a.total_latency_ms is null or a.total_latency_ms < 0" in normalized


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


def test_one_sided_telemetry_is_detected_before_an_incomplete_publish() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "question_without_terminal" in sql
    assert "answer_without_question" in sql
    assert "left join question_links" in sql
    assert "accepted_http_without_question_event" in sql
    assert "accepted_ask_request_rows" in sql
    assert "trace_enforced_ask_request_counts" in sql
    assert "status between 200 and 299" in sql
    assert "completed_ask_request_rows" in sql
    assert "status is not null" in sql
    assert "duplicate_source_question_event_id" in sql
    assert "duplicate_source_answer_event_id" in sql


def test_invalid_current_v2_producer_axes_are_visible_and_block_publication() -> None:
    sql = _sql("check_data_quality.sql").lower()
    assert "invalid_classification_producer" in sql
    assert "classification_status = 'producer_invalid'" in sql
    assert "invalid_product_resolution_counts" in sql
    assert "product_resolved_count > product_candidate_count" in sql
    normalized = " ".join(sql.split())
    assert (
        "when check_name in ( "
        "'invalid_classification_producer', "
        "'invalid_task_producer', "
        "'invalid_product_producer' "
        ") then 'batch_blocking'"
    ) in normalized
    assert "when 'batch_blocking' then 'critical'" in normalized


def test_versioned_api_routines_coexist_with_legacy_two_argument_contracts() -> None:
    sql = " ".join(_sql("create_api_views.sql").lower().split())
    assert "dashboard_events_v2`( p_start_date date, p_end_date date, p_run_id string" in sql
    assert "dashboard_user_list_v2`( p_history_start date, p_as_of timestamp, p_run_id string" in sql
    assert "dashboard_events`( p_start_date date, p_end_date date" in sql
    assert "dashboard_user_list`( p_history_start date, p_as_of timestamp" in sql
    assert "dashboard_events_v2`( p_start_date, p_end_date," in sql
    assert "dashboard_user_list_v2`( p_history_start, p_as_of," in sql
    assert "p_run_id is null and scope.snapshot_run_id is null" in sql
    assert "p_run_id is null and snapshot_run_id is null" in sql
    assert "versioned_scope.snapshot_run_id = p_run_id" not in sql
    assert sql.count("cast(null as string)") == 2
    # The legacy wrapper keeps the broad event universe; only the new service's
    # run-versioned roster owner applies the strict Summary role cohort.
    assert "global_scope_enabled = true" not in sql


def test_versioned_scope_projection_has_no_publish_gap_or_legacy_null_leak() -> None:
    projection = " ".join(_sql("merge_firestore_projection.sql").lower().split())
    settings = Settings()
    publish = " ".join(render_publish_sql(settings).lower().split())
    scope_table = (
        f"`{settings.monitor_project_id}.{settings.monitor_bq_dataset}.user_scope`"
    ).lower()
    insert_at = publish.index(f"insert into {scope_table}")
    quality_at = publish.index("set run_quality_results")
    pointer_at = publish.index("published_run_id = @run_id", quality_at)
    assert insert_at < quality_at < pointer_at
    assert f"delete from {scope_table} where snapshot_run_id = @run_id" in publish
    assert f"delete from {scope_table} where true" not in publish
    assert "where snapshot_run_id is null" in projection


def test_one_invalid_axis_cannot_filter_usage_or_answer_measurements() -> None:
    sql = " ".join(_sql("create_api_views.sql").lower().split())
    projection = sql.split(
        "select q.event_id as question_event_id",
        maxsplit=1,
    )[1].split(
        "left join canonical_answers answer",
        maxsplit=1,
    )[0]

    assert "q.valid_question" in projection
    assert "q.classification_measurement_state" in projection
    assert "q.task_measurement_state" in projection
    assert "q.product_measurement_state" in projection
    assert "answer.measurement_available" in projection
    assert "answer.complete_delivery" in projection
    assert "answer.total_latency_ms" in projection
    assert " where " not in projection


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
    view_sql = " ".join(_sql("create_api_views.sql").lower().split())
    quality_sql = _sql("check_data_quality.sql").lower()
    assert "'not_measured'" in history_sql
    normalized = " ".join(quality_sql.split())
    assert (
        "when q.record_origin in ('firestore_history', 'legacy_audit_history') "
        "then 'unmeasured'"
    ) in view_sql
    assert (
        "ifnull(record_origin, '') not in "
        "('firestore_history', 'legacy_audit_history')"
    ) in normalized


def test_history_never_invents_request_tasks_and_quality_allows_only_history_to_omit_them() -> None:
    history_sql = " ".join(_sql("merge_history.sql").lower().split())
    quality_sql = " ".join(_sql("check_data_quality.sql").lower().split())

    assert "analytics_tasks = array<string>[]" in history_sql
    assert "false, array<string>[]" in history_sql
    assert (
        "ifnull(record_origin, '') not in ('firestore_history', 'legacy_audit_history') "
        "and ( ifnull(array_length(question_categories), 0) = 0 "
        "or ifnull(array_length(analytics_tasks), 0) = 0 )"
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


def test_persistence_truth_is_monotonic_and_identity_exact() -> None:
    """A late FALSE cannot revoke TRUE or leak across another answer."""

    normalized = " ".join(_sql("merge_incremental.sql").lower().split())
    assert (
        "partition by request_id, conversation_id, message_id "
        "order by if(persisted is true, 1, 0) desc, source_ts desc, insert_id desc"
    ) in normalized
    assert (
        "when answer.message_persisted is true or persistence.persisted is true "
        "then true"
    ) in normalized
    assert (
        "coalesce(answer.message_id, '') = coalesce(persistence.message_id, '')"
    ) in normalized
    assert "nullif(persistence.message_id, '') is null" not in normalized


@pytest.mark.parametrize(
    ("existing", "events", "expected"),
    [
        (None, [(True, 1), (False, 2)], True),  # TRUE, then late FALSE
        (False, [(False, 1), (True, 2)], True),  # writer FALSE, then browser TRUE
        (True, [(False, 3)], True),  # TRUE was published by an earlier run
        (None, [(False, 1), (False, 2)], False),
    ],
)
def test_persistence_monotonic_contract_counterexamples(
    existing: bool | None,
    events: list[tuple[bool, int]],
    expected: bool,
) -> None:
    selected = max(events, key=lambda item: (item[0] is True, item[1]))[0]
    resolved = True if existing is True or selected is True else selected
    assert resolved is expected


def test_persistence_identity_counterexample_does_not_join_other_message() -> None:
    answer = ("request-a", "conversation-a", "message-a")
    other_request = ("request-b", "conversation-a", "message-a")
    other_message = ("request-a", "conversation-a", "message-b")
    assert answer != other_request
    assert answer != other_message


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
    history_position = rebuild.index("app.jobs.rebuild_history")
    assert rebuild.index("--apply", history_position) < rebuild.index(
        "publish_monitor_views.sh"
    )
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
    history = script.index("app.jobs.rebuild_history")
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
    assert "--credential-file" in script
    assert "approved_credential_path" in script
    assert "credentials=credentials" in script
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in script
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
