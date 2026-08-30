#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.jobs.project_firestore import (
    CITATION_SCHEMA,
    CONVERSATION_SCHEMA,
    USER_SCOPE_SCHEMA,
    struct_array_parameter,
)
from app.jobs.rebuild_history import ANSWER_SCHEMA, QUESTION_SCHEMA
from app.jobs.refresh_analytics import render_publish_sql, render_sql
from app.settings import get_settings
from app.refresh_policy import REFRESH_POLICY
from scripts.credential_preflight import approved_credential_path


SQL_FILES = (
    "create_dataset.sql",
    "create_source_tables.sql",
    "create_fact_tables.sql",
    "create_aggregates.sql",
    "create_api_views.sql",
    "merge_firestore_projection.sql",
    "merge_incremental.sql",
    "merge_history.sql",
    "check_data_quality.sql",
)


def _analytics_start(settings, fallback: datetime) -> datetime:
    text = str(settings.monitor_analytics_start_at or "").strip()
    if not text:
        return fallback
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _publish_parameters(settings) -> list[Any]:
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc)
    today = date.today()
    return [
        bigquery.ScalarQueryParameter("run_id", "STRING", "dry-run"),
        bigquery.ScalarQueryParameter("lease_id", "STRING", "dry-run-lease"),
        bigquery.ScalarQueryParameter("expected_watermark", "TIMESTAMP", None),
        bigquery.ScalarQueryParameter(
            "scope_policy_version", "STRING", "summary_role_v1"
        ),
        bigquery.ScalarQueryParameter(
            "global_roster_fingerprint", "STRING", "dry-run-global-roster"
        ),
        bigquery.ScalarQueryParameter(
            "global_content_fingerprint", "STRING", "dry-run-global-content"
        ),
        bigquery.ScalarQueryParameter(
            "user_map_roster_fingerprint", "STRING", "dry-run-user-map-roster"
        ),
        bigquery.ScalarQueryParameter(
            "user_map_content_fingerprint", "STRING", "dry-run-user-map-content"
        ),
        struct_array_parameter("user_scope_rows", USER_SCOPE_SCHEMA, []),
        struct_array_parameter("conversation_rows", CONVERSATION_SCHEMA, []),
        struct_array_parameter("citation_rows", CITATION_SCHEMA, []),
        bigquery.ScalarQueryParameter("conversation_partition_start", "DATE", today),
        bigquery.ScalarQueryParameter("conversation_partition_end", "DATE", today),
        bigquery.ScalarQueryParameter("citation_partition_start", "DATE", today),
        bigquery.ScalarQueryParameter("citation_partition_end", "DATE", today),
        bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
        bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
        bigquery.ScalarQueryParameter(
            "analytics_start",
            "TIMESTAMP",
            _analytics_start(settings, window_start),
        ),
        bigquery.ScalarQueryParameter(
            "event_future_tolerance_minutes",
            "INT64",
            REFRESH_POLICY.event_future_tolerance_minutes,
        ),
    ]


def _parameters(name: str, settings) -> list[Any]:
    today = date.today()
    if name == "merge_firestore_projection.sql":
        return [
            bigquery.ScalarQueryParameter("run_id", "STRING", "dry-run"),
            struct_array_parameter("user_scope_rows", USER_SCOPE_SCHEMA, []),
            struct_array_parameter("conversation_rows", CONVERSATION_SCHEMA, []),
            struct_array_parameter("citation_rows", CITATION_SCHEMA, []),
            bigquery.ScalarQueryParameter(
                "conversation_partition_start", "DATE", today
            ),
            bigquery.ScalarQueryParameter(
                "conversation_partition_end", "DATE", today
            ),
            bigquery.ScalarQueryParameter(
                "citation_partition_start", "DATE", today
            ),
            bigquery.ScalarQueryParameter(
                "citation_partition_end", "DATE", today
            ),
        ]
    if name == "merge_history.sql":
        return [
            bigquery.ScalarQueryParameter(
                "history_partition_date", "DATE", today
            ),
            struct_array_parameter("history_questions", QUESTION_SCHEMA, []),
            struct_array_parameter("history_answers", ANSWER_SCHEMA, []),
            struct_array_parameter(
                "history_conversations", CONVERSATION_SCHEMA, []
            ),
            struct_array_parameter("history_citations", CITATION_SCHEMA, []),
        ]
    return []


def _cutover_sequence(settings) -> tuple[str, list[Any]]:
    names = (
        "create_fact_tables.sql",
        "create_aggregates.sql",
        "create_source_tables.sql",
        "merge_history.sql",
    )
    today = date.today()
    parameters = [
        bigquery.ScalarQueryParameter(
            "history_partition_date", "DATE", today
        ),
        struct_array_parameter("history_questions", QUESTION_SCHEMA, []),
        struct_array_parameter("history_answers", ANSWER_SCHEMA, []),
        struct_array_parameter(
            "history_conversations", CONVERSATION_SCHEMA, []
        ),
        struct_array_parameter("history_citations", CITATION_SCHEMA, []),
    ]
    parameters.extend(_publish_parameters(settings))
    publish_sql = render_publish_sql(settings)
    publish_declarations, publish_body = publish_sql.split(
        "BEGIN TRANSACTION;", maxsplit=1
    )
    sql_parts = [publish_declarations]
    sql_parts.extend(render_sql(name, settings) for name in names)
    sql_parts.append("BEGIN TRANSACTION;" + publish_body)
    sql_parts.append(render_sql("create_api_views.sql", settings))
    return "\n".join(sql_parts), parameters


def _dry_run(client, settings, *, label: str, sql: str, parameters: list[Any]):
    config = bigquery.QueryJobConfig(
        dry_run=True,
        use_query_cache=False,
        query_parameters=parameters,
        maximum_bytes_billed=max(1, int(settings.monitor_query_maximum_bytes)),
    )
    try:
        job = client.query(
            sql,
            job_config=config,
            location=settings.monitor_bq_location,
        )
        return {
            "file": label,
            "status": "passed",
            "bytes": int(job.total_bytes_processed or 0),
        }
    except Exception as exc:
        return {
            "file": label,
            "status": "failed",
            "errorType": type(exc).__name__,
            "message": str(exc)[:1000],
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only BigQuery validation for the canonical Monitor SQL"
    )
    parser.add_argument("--file", action="append", choices=SQL_FILES)
    parser.add_argument("--credential-file", required=True)
    args = parser.parse_args()
    credentials = service_account.Credentials.from_service_account_file(
        str(approved_credential_path(args.credential_file))
    )
    settings = get_settings()
    client = bigquery.Client(
        project=settings.monitor_project_id, credentials=credentials
    )
    selected = tuple(args.file or SQL_FILES)
    current_state_results = []
    atomic_publish_added = False
    for name in selected:
        if name in {"merge_incremental.sql", "check_data_quality.sql"}:
            if atomic_publish_added:
                continue
            atomic_publish_added = True
            label = "atomic_publish_contract"
            sql = render_publish_sql(settings)
            parameters = _publish_parameters(settings)
        else:
            label = name
            sql = render_sql(name, settings)
            parameters = _parameters(name, settings)
        current_state_results.append(
            _dry_run(
                client,
                settings,
                label=label,
                sql=sql,
                parameters=parameters,
            )
        )
    if args.file:
        failed = any(item["status"] != "passed" for item in current_state_results)
        print(
            json.dumps(
                {"dryRun": True, "currentStateResults": current_state_results},
                ensure_ascii=False,
            )
        )
        return 1 if failed else 0

    sequence_sql, sequence_parameters = _cutover_sequence(settings)
    sequence_result = _dry_run(
        client,
        settings,
        label="planned_cutover_sequence",
        sql=sequence_sql,
        parameters=sequence_parameters,
    )
    print(
        json.dumps(
            {
                "dryRun": True,
                "plannedSequence": sequence_result,
                "currentStateResults": current_state_results,
            },
            ensure_ascii=False,
        )
    )
    return 0 if sequence_result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
