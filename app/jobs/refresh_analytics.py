from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.cloud import bigquery

from app.jobs.project_firestore import (
    CITATION_SCHEMA,
    CONVERSATION_SCHEMA,
    USER_SCOPE_SCHEMA,
    FirestoreProjector,
    struct_array_parameter,
)
from app.settings import Settings, get_settings

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def render_sql(name: str, settings: Settings) -> str:
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    values = {
        "PROJECT_ID": settings.monitor_project_id,
        "DATASET_ID": settings.monitor_bq_dataset,
        "BQ_LOCATION": settings.monitor_bq_location,
        "MONITOR_TIMEZONE": settings.monitor_timezone,
        "FACT_RETENTION_DAYS": str(settings.monitor_fact_retention_days),
        "AGGREGATE_RETENTION_DAYS": str(settings.monitor_aggregate_retention_days),
    }
    for key, value in values.items():
        text = text.replace("${" + key + "}", str(value))
    if "${" in text:
        raise ValueError(f"unresolved SQL placeholder in {name}")
    return text


def render_publish_sql(settings: Settings) -> str:
    """One atomic publisher; component SQL files remain the sole field owners."""

    projection_sql = render_sql("merge_firestore_projection.sql", settings)
    fact_sql = render_sql("merge_incremental.sql", settings)
    daily_sql = render_sql("refresh_daily.sql", settings)
    quality_sql = render_sql("check_data_quality.sql", settings).rstrip().removesuffix(";")
    return f"""
BEGIN TRANSACTION;
{projection_sql}
{fact_sql}
{daily_sql}
ASSERT (
  SELECT COUNTIF(severity = 'critical' AND passed IS NOT TRUE) = 0
  FROM ({quality_sql})
) AS 'canonical monitor data quality gate failed';
DELETE FROM `{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_state`
WHERE source = 'published';
INSERT INTO `{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_state`
  (source, data_through, published_run_id, status, updated_at)
VALUES ('published', @window_end, @run_id, 'succeeded', CURRENT_TIMESTAMP());
UPDATE `{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_runs`
SET status = 'succeeded', finished_at = CURRENT_TIMESTAMP()
WHERE run_id = @run_id AND DATE(started_at) = CURRENT_DATE();
COMMIT TRANSACTION;
{quality_sql};
""".strip()


def _parse_start(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("MONITOR_ANALYTICS_START_AT is required before the first refresh")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class AnalyticsRefreshJob:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        projector: FirestoreProjector | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._projector = projector or FirestoreProjector(settings)
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"

    def window(self, *, now: datetime | None = None) -> tuple[datetime, datetime]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        window_end = current - timedelta(minutes=self._settings.monitor_refresh_delay_minutes)
        config = bigquery.QueryJobConfig(maximum_bytes_billed=self._settings.monitor_query_maximum_bytes)
        rows = list(self._client.query(
            f"SELECT MAX(data_through) AS data_through FROM `{self._dataset}.pipeline_state` WHERE status = 'succeeded'",
            job_config=config, location=self._settings.monitor_bq_location,
        ).result())
        watermark = rows[0].get("data_through") if rows else None
        base = watermark if isinstance(watermark, datetime) else _parse_start(self._settings.monitor_analytics_start_at)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        window_start = max(
            _parse_start(self._settings.monitor_analytics_start_at),
            base.astimezone(timezone.utc) - timedelta(minutes=self._settings.monitor_refresh_overlap_minutes),
        )
        if window_end <= window_start:
            raise ValueError("refresh window is empty")
        return window_start, window_end

    def _query(self, sql: str, parameters: list[Any]) -> Any:
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=self._settings.monitor_query_maximum_bytes,
            use_query_cache=False,
        )
        return self._client.query(sql, job_config=config, location=self._settings.monitor_bq_location).result()

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        window_start, window_end = self.window(now=now)
        run_id = f"refresh_{uuid4().hex}"
        common = [
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
        ]
        self._query(
            f"""INSERT INTO `{self._dataset}.pipeline_runs`
            (run_id, started_at, window_start, window_end, source, status)
            VALUES (@run_id, CURRENT_TIMESTAMP(), @window_start, @window_end, 'published', 'running')""",
            [bigquery.ScalarQueryParameter("run_id", "STRING", run_id), *common],
        )
        try:
            matched_users = self._projector.resolve_chat_identities()
            scope_rows = self._projector.user_scope_rows()
            if not scope_rows:
                raise RuntimeError("canonical user scope is empty")
            conversation_rows, citation_rows = self._projector.changed_conversation_rows(
                window_start=window_start, window_end=window_end
            )
            fallback_start = window_start.date()
            fallback_end = window_end.date()
            conversation_dates = [row["updated_date"] for row in conversation_rows]
            citation_dates = [row["answer_date"] for row in citation_rows]
            publish_parameters = [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("date_start", "DATE", fallback_start),
                bigquery.ScalarQueryParameter("date_end", "DATE", fallback_end),
                bigquery.ScalarQueryParameter(
                    "conversation_partition_start",
                    "DATE",
                    min(conversation_dates, default=fallback_start),
                ),
                bigquery.ScalarQueryParameter(
                    "conversation_partition_end",
                    "DATE",
                    max(conversation_dates, default=fallback_end),
                ),
                bigquery.ScalarQueryParameter(
                    "citation_partition_start",
                    "DATE",
                    min(citation_dates, default=fallback_start),
                ),
                bigquery.ScalarQueryParameter(
                    "citation_partition_end",
                    "DATE",
                    max(citation_dates, default=fallback_end),
                ),
                struct_array_parameter("user_scope_rows", USER_SCOPE_SCHEMA, scope_rows),
                struct_array_parameter("conversation_rows", CONVERSATION_SCHEMA, conversation_rows),
                struct_array_parameter("citation_rows", CITATION_SCHEMA, citation_rows),
                *common,
            ]
            checks = [
                dict(row.items())
                for row in self._query(
                    render_publish_sql(self._settings),
                    publish_parameters,
                )
            ]
            return {
                "runId": run_id, "status": "succeeded", "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(), "matchedUsers": matched_users, "scopeRows": len(scope_rows),
                "conversationRows": len(conversation_rows), "citationRows": len(citation_rows),
                "dataQualityChecks": checks,
            }
        except Exception as exc:
            self._query(
                f"""UPDATE `{self._dataset}.pipeline_runs`
                SET status = 'failed', finished_at = CURRENT_TIMESTAMP(), error_code = @error_code
                WHERE run_id = @run_id AND DATE(started_at) = CURRENT_DATE()""",
                [
                    bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                    bigquery.ScalarQueryParameter("error_code", "STRING", type(exc).__name__),
                ],
            )
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the single OurA Navi Monitor incremental pipeline")
    parser.add_argument("--apply", action="store_true", help="execute cloud mutations")
    args = parser.parse_args()
    settings = get_settings()
    if not args.apply:
        print(json.dumps({"mode": "plan", "project": settings.monitor_project_id, "dataset": settings.monitor_bq_dataset}, sort_keys=True))
        return 0
    try:
        result = AnalyticsRefreshJob(settings).run()
    except Exception as exc:
        print(json.dumps({
            "monitor_pipeline_event": True,
            "status": "failed",
            "error_code": type(exc).__name__,
            "event_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }, sort_keys=True), flush=True)
        raise
    print(json.dumps({"monitor_pipeline_event": True, **result}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
