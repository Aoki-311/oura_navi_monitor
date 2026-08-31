#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

from app.domain.analysis_scopes import SCOPE_POLICY_VERSION

try:
    from scripts.credential_preflight import approved_credential_path
except ModuleNotFoundError:
    from credential_preflight import approved_credential_path


REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "pipeline_state": {
        "source",
        "data_through",
        "published_run_id",
        "scope_policy_version",
        "global_roster_fingerprint",
        "global_content_fingerprint",
        "user_map_roster_fingerprint",
        "user_map_content_fingerprint",
        "status",
        "lease_run_id",
        "lease_acquired_at",
        "lease_expires_at",
        "updated_at",
    },
    "pipeline_runs": {
        "run_id",
        "execution_id",
        "trigger_source",
        "started_at",
        "finished_at",
        "window_start",
        "window_end",
        "source",
        "status",
        "error_code",
    },
    "pipeline_quality_events": {
        "run_id",
        "check_name",
        "disposition",
        "failure_count",
        "passed",
        "observed_at",
    },
    "pipeline_event_issues": {
        "source_event_hash",
        "issue_code",
        "disposition",
        "last_run_id",
        "resolution_status",
        "last_observed_at",
    },
    "pipeline_run_event_manifest": {
        "run_id",
        "source_event_hash",
        "event_key_hash",
        "event_family",
        "disposition",
        "observed_at",
    },
    "question_events": {
        "event_id",
        "analytics_contract_version",
        "classification_reason_codes",
        "product_resolution_status",
        "product_resolution_reason_codes",
        "record_origin",
        "measurement_profile",
    },
    "answer_events": {
        "event_id",
        "analytics_contract_version",
        "classification_reason_codes",
        "product_resolution_status",
        "product_resolution_reason_codes",
        "record_origin",
        "measurement_profile",
        "measurement_available",
        "complete_delivery",
    },
    "user_scope": {
        "snapshot_run_id",
        "snapshot_created_at",
        "roster_id",
        "name",
        "email",
        "area",
        "area_key",
        "workplace",
        "role",
        "department",
        "mr_experience",
        "label_ids_json",
        "labels_json",
        "global_scope_enabled",
        "user_map_scope_enabled",
        "roster_diagnostic_fingerprint",
        "global_label_catalog_status",
        "user_map_label_catalog_status",
    },
}
REQUIRED_SOURCE_VIEWS = {"monitor_event_source", "http_request_source"}
REQUIRED_API_ROUTINES = {
    "dashboard_events_v2",
    "dashboard_user_list_v2",
}
API_READ_MAXIMUM_BYTES = 67_108_864
REQUIRED_API_OUTPUT_COLUMNS: dict[str, set[str]] = {
    "dashboard_events_v2": {
        "question_event_id",
        "question_ts",
        "question_date",
        "roster_id",
        "request_id",
        "conversation_id",
        "turn_id",
        "area_key",
        "area",
        "role",
        "department",
        "valid_question",
        "mode",
        "device_class",
        "primary_question_category",
        "analytics_tasks",
        "task_measurement_state",
        "primary_product_name",
        "product_candidate_count",
        "product_resolved_count",
        "product_measurement_state",
        "classification_measurement_state",
        "measurement_available",
        "complete_delivery",
        "total_latency_ms",
        "answer_measurement_profile",
    },
    "dashboard_user_list_v2": {
        "roster_id",
        "area_key",
        "area",
        "role",
        "department",
        "last_active_at",
        "active_days_7",
        "user_message_count_7",
    },
}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def _row_values(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _routine_type(value: Any) -> str:
    resolved = getattr(value, "value", value)
    return str(resolved or "").rsplit(".", 1)[-1].upper()


def _as_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(
        tzinfo=timezone.utc
    )


def _read_api_routine(
    client: Any,
    *,
    sql: str,
    parameters: list[Any],
    required_columns: set[str],
    routine_name: str,
    location: str,
) -> dict[str, Any]:
    result = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=API_READ_MAXIMUM_BYTES,
            use_query_cache=False,
            query_parameters=parameters,
        ),
        location=location,
    ).result()
    rows = list(result)
    actual_columns = {
        str(getattr(field, "name", "") or "")
        for field in (getattr(result, "schema", None) or ())
    }
    missing = sorted(required_columns - actual_columns)
    if missing:
        raise ValueError(
            f"{routine_name} query output is missing required columns: "
            f"{','.join(missing)}"
        )
    return {
        "readable": True,
        "sampleRowCount": len(rows),
        "verifiedColumns": sorted(required_columns),
    }


def verify_data_contract(
    client: Any,
    *,
    project: str,
    dataset: str,
    location: str,
    git_sha: str,
    image: str,
) -> dict[str, Any]:
    dataset_ref = f"{project}.{dataset}"
    dataset_object = client.get_dataset(dataset_ref)
    actual_location = str(getattr(dataset_object, "location", "") or "").upper()
    if actual_location != location.upper():
        raise ValueError(
            f"dataset location mismatch: expected {location}, got {actual_location}"
        )

    verified_tables: dict[str, list[str]] = {}
    for table_name, required_columns in sorted(REQUIRED_TABLE_COLUMNS.items()):
        table = client.get_table(f"{dataset_ref}.{table_name}")
        if str(getattr(table, "table_type", "") or "").upper() != "TABLE":
            raise ValueError(f"{table_name} is not a base table")
        actual_columns = {
            str(getattr(field, "name", "") or "") for field in table.schema
        }
        missing = sorted(required_columns - actual_columns)
        if missing:
            raise ValueError(
                f"{table_name} is missing required columns: {','.join(missing)}"
            )
        verified_tables[table_name] = sorted(required_columns)

    verified_views: list[str] = []
    for view_name in sorted(REQUIRED_SOURCE_VIEWS):
        view = client.get_table(f"{dataset_ref}.{view_name}")
        if str(getattr(view, "table_type", "") or "").upper() != "VIEW":
            raise ValueError(f"{view_name} is not a view")
        verified_views.append(view_name)

    verified_routines: list[str] = []
    for routine_name in sorted(REQUIRED_API_ROUTINES):
        routine = client.get_routine(f"{dataset_ref}.{routine_name}")
        if _routine_type(getattr(routine, "type_", "")) != "TABLE_VALUED_FUNCTION":
            raise ValueError(f"{routine_name} is not a table-valued function")
        verified_routines.append(routine_name)

    rows = list(
        client.query(
            f"""
            SELECT source, status, published_run_id, data_through,
                   scope_policy_version,
                   global_roster_fingerprint, global_content_fingerprint,
                   user_map_roster_fingerprint, user_map_content_fingerprint,
                   lease_run_id, lease_expires_at
            FROM `{dataset_ref}.pipeline_state`
            WHERE source = 'published'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=10_485_760,
                use_query_cache=False,
            ),
            location=location,
        ).result()
    )
    if len(rows) != 1:
        raise ValueError("pipeline_state has no single readable published row")
    published = _row_values(rows[0])
    if published.get("source") != "published" or published.get("status") != "succeeded":
        raise ValueError("pipeline_state published row is not succeeded")
    if not str(published.get("published_run_id") or ""):
        raise ValueError("pipeline_state published row has no atomic run id")
    if str(published.get("scope_policy_version") or "") != SCOPE_POLICY_VERSION:
        raise ValueError("pipeline_state published row has no current scope policy")
    for field in (
        "global_roster_fingerprint",
        "global_content_fingerprint",
        "user_map_roster_fingerprint",
        "user_map_content_fingerprint",
    ):
        if not str(published.get(field) or "").strip():
            raise ValueError(f"pipeline_state published row has no {field}")
    if not _iso(published.get("data_through")):
        raise ValueError("pipeline_state published row has no watermark")
    if published.get("lease_run_id") or published.get("lease_expires_at"):
        raise ValueError("pipeline_state still has an active or unreleased lease")

    data_through = _as_utc_datetime(published["data_through"])
    published_run_id = str(published["published_run_id"])
    projection_rows = list(
        client.query(
            f"""
            SELECT
              COUNT(*) AS scope_row_count,
              COUNTIF(snapshot_run_id != @published_run_id) AS wrong_run_count,
              COUNTIF(snapshot_created_at IS NULL) AS missing_created_at_count,
              COUNTIF(NULLIF(roster_diagnostic_fingerprint, '') IS NULL)
                AS missing_diagnostic_count
            FROM `{dataset_ref}.user_scope`
            WHERE snapshot_run_id = @published_run_id
            """,
            job_config=bigquery.QueryJobConfig(
                maximum_bytes_billed=10_485_760,
                use_query_cache=False,
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "published_run_id", "STRING", published_run_id
                    )
                ],
            ),
            location=location,
        ).result()
    )
    projection = _row_values(projection_rows[0]) if len(projection_rows) == 1 else {}
    if int(projection.get("scope_row_count") or 0) <= 0:
        raise ValueError("published user_scope projection is empty")
    if any(
        int(projection.get(field) or 0) != 0
        for field in (
            "wrong_run_count",
            "missing_created_at_count",
            "missing_diagnostic_count",
        )
    ):
        raise ValueError("published user_scope projection is internally inconsistent")
    end_date = data_through.date()
    start_date = end_date - timedelta(days=1)
    api_reads = {
        "dashboard_events_v2": _read_api_routine(
            client,
            sql=f"""
                SELECT *
                FROM `{dataset_ref}.dashboard_events_v2`(
                  @start_date, @end_date, @published_run_id
                )
                WHERE question_date BETWEEN @start_date AND @end_date
                ORDER BY question_ts DESC
                LIMIT 1
            """,
            parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
                bigquery.ScalarQueryParameter(
                    "published_run_id", "STRING", published_run_id
                ),
            ],
            required_columns=REQUIRED_API_OUTPUT_COLUMNS["dashboard_events_v2"],
            routine_name="dashboard_events_v2",
            location=location,
        ),
        "dashboard_user_list_v2": _read_api_routine(
            client,
            sql=f"""
                SELECT *
                FROM `{dataset_ref}.dashboard_user_list_v2`(
                  @history_start_date, @as_of, @published_run_id
                )
                ORDER BY last_active_at DESC
                LIMIT 1
            """,
            parameters=[
                bigquery.ScalarQueryParameter(
                    "history_start_date", "DATE", start_date
                ),
                bigquery.ScalarQueryParameter("as_of", "TIMESTAMP", data_through),
                bigquery.ScalarQueryParameter(
                    "published_run_id", "STRING", published_run_id
                ),
            ],
            required_columns=REQUIRED_API_OUTPUT_COLUMNS["dashboard_user_list_v2"],
            routine_name="dashboard_user_list_v2",
            location=location,
        ),
    }

    return {
        "receiptType": "monitor_data_contract_v1",
        "project": project,
        "dataset": dataset,
        "location": location,
        "gitSha": git_sha,
        "image": image,
        "schemaReady": True,
        "sourceViewsReady": True,
        "apiRoutinesReady": True,
        "apiRoutinesReadable": True,
        "publishedStateReadable": True,
        "publishedRunId": str(published["published_run_id"]),
        "scopePolicyVersion": str(published["scope_policy_version"]),
        "scopeProjectionRowCount": int(projection["scope_row_count"]),
        "dataThrough": _iso(published["data_through"]),
        "tables": verified_tables,
        "sourceViews": verified_views,
        "apiRoutines": verified_routines,
        "apiRoutineReads": api_reads,
        "apiReadMaximumBytes": API_READ_MAXIMUM_BYTES,
        "capturedAt": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read back the additive Monitor BigQuery data contract"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="oura_navi_monitor")
    parser.add_argument("--location", default="US")
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--receipt-output", required=True)
    parser.add_argument("--credential-file")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_git_sha):
        raise SystemExit("--expected-git-sha must be a full Git SHA")
    image_pattern = re.compile(
        rf"^[a-z0-9-]+-docker\.pkg\.dev/{re.escape(args.project)}/"
        r"[^/@]+/[^/@]+@sha256:[0-9a-f]{64}$"
    )
    if not image_pattern.fullmatch(args.expected_image):
        raise SystemExit("--expected-image must be an immutable image in the project")
    output = Path(args.receipt_output).expanduser().resolve()
    if output.exists() or not output.parent.is_dir():
        raise SystemExit("--receipt-output must be a new file in an existing directory")

    print(f"mode={'verify' if args.verify else 'plan'}")
    print(f"dataset={args.project}.{args.dataset} location={args.location}")
    print(
        "checks=required-tables-and-columns,source-views,api-table-functions-and-reads,"
        "released-published-watermark"
    )
    if not args.verify:
        return 0

    credentials = service_account.Credentials.from_service_account_file(
        str(approved_credential_path(args.credential_file or ""))
    )

    receipt = verify_data_contract(
        bigquery.Client(project=args.project, credentials=credentials),
        project=args.project,
        dataset=args.dataset,
        location=args.location,
        git_sha=args.expected_git_sha,
        image=args.expected_image,
    )
    with output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"data_contract=verified receipt={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
