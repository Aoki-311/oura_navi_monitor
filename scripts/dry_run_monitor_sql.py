#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery

from app.jobs.project_firestore import (
    CITATION_SCHEMA,
    CONVERSATION_SCHEMA,
    USER_SCOPE_SCHEMA,
    struct_array_parameter,
)
from app.jobs.rebuild_history import ANSWER_SCHEMA, QUESTION_SCHEMA
from app.jobs.refresh_analytics import render_sql
from app.settings import get_settings


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


def _credential_guard() -> None:
    approved = str(
        os.environ.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE") or ""
    ).strip()
    if not approved or not Path(approved).is_file():
        raise SystemExit("approved CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE is required")
    configured = str(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if configured and Path(configured).resolve() != Path(approved).resolve():
        raise SystemExit(
            "GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential"
        )
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = approved


def _parameters(name: str) -> list[Any]:
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc)
    today = date.today()
    if name in {"merge_incremental.sql", "check_data_quality.sql"}:
        return [
            bigquery.ScalarQueryParameter(
                "window_start", "TIMESTAMP", window_start
            ),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
        ]
    if name == "merge_firestore_projection.sql":
        return [
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
        "merge_firestore_projection.sql",
        "merge_incremental.sql",
        "check_data_quality.sql",
        "create_api_views.sql",
    )
    today = date.today()
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=1)
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
        bigquery.ScalarQueryParameter(
            "window_start", "TIMESTAMP", window_start
        ),
        bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
    ]
    return "\n".join(render_sql(name, settings) for name in names), parameters


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
    args = parser.parse_args()
    _credential_guard()
    settings = get_settings()
    client = bigquery.Client(project=settings.monitor_project_id)
    selected = tuple(args.file or SQL_FILES)
    current_state_results = []
    for name in selected:
        current_state_results.append(
            _dry_run(
                client,
                settings,
                label=name,
                sql=render_sql(name, settings),
                parameters=_parameters(name),
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
