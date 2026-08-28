from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

from app.settings import Settings


class PipelineRepository:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        self._table = f"`{dataset}.pipeline_state`"
        self._runs_table = f"`{dataset}.pipeline_runs`"
        self._quality_table = f"`{dataset}.pipeline_quality_events`"
        self._issue_table = f"`{dataset}.pipeline_event_issues`"

    def publication_snapshot(self) -> dict[str, Any]:
        """Return one coherent published watermark and its run-level quality."""

        config = bigquery.QueryJobConfig(
            maximum_bytes_billed=max(1, int(self._settings.monitor_query_maximum_bytes)),
            use_query_cache=True,
        )
        rows = list(
            self._client.query(
                f"""
                WITH published AS (
                  SELECT data_through, published_run_id, updated_at
                  FROM {self._table}
                  WHERE source = 'published' AND status = 'succeeded'
                  ORDER BY updated_at DESC
                  LIMIT 1
                ), latest_run AS (
                  SELECT run_id, status, error_code, started_at, finished_at
                  FROM {self._runs_table}
                  WHERE DATE(started_at) BETWEEN
                    DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()
                    AND source = 'published'
                  ORDER BY started_at DESC
                  LIMIT 1
                )
                SELECT
                  published.data_through,
                  published.published_run_id,
                  published.updated_at,
                  latest_run.run_id AS latest_run_id,
                  latest_run.status AS latest_run_status,
                  latest_run.error_code AS latest_run_error_code,
                  latest_run.finished_at AS latest_run_finished_at,
                  COALESCE((
                    SELECT COUNT(DISTINCT issue.source_event_hash)
                    FROM {self._issue_table} issue
                    WHERE issue.last_run_id = published.published_run_id
                      AND issue.disposition = 'row_quarantined'
                      AND issue.resolution_status = 'open'
                  ), 0) AS quarantined_event_count,
                  COALESCE((
                    SELECT COUNT(DISTINCT issue.source_event_hash)
                    FROM {self._issue_table} issue
                    WHERE issue.last_run_id = published.published_run_id
                      AND issue.issue_code = 'duplicate_delivery_deduplicated'
                  ), 0) AS deduplicated_delivery_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = published.published_run_id
                      AND quality.disposition = 'repaired'
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(published.updated_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(published.updated_at), INTERVAL 1 DAY)
                  ), 0) AS repaired_duplicate_fact_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = published.published_run_id
                      AND quality.disposition = 'axis_unmeasured'
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(published.updated_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(published.updated_at), INTERVAL 1 DAY)
                  ), 0) AS axis_unmeasured_finding_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = latest_run.run_id
                      AND quality.disposition = 'batch_blocking'
                      AND quality.passed IS NOT TRUE
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(latest_run.started_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(latest_run.started_at), INTERVAL 1 DAY)
                  ), 0) AS batch_blocking_failure_count
                FROM (SELECT 1) anchor
                LEFT JOIN published ON TRUE
                LEFT JOIN latest_run ON TRUE
                """,
                job_config=config,
                location=self._settings.monitor_bq_location,
            ).result()
        )
        if not rows:
            return {}
        row = rows[0]
        values = dict(row.items()) if hasattr(row, "items") else dict(row)
        for field in ("data_through", "latest_run_finished_at"):
            value = values.get(field)
            if isinstance(value, datetime) and value.tzinfo is None:
                values[field] = value.replace(tzinfo=timezone.utc)
        return values

    def data_through(self) -> datetime | None:
        value = self.publication_snapshot().get("data_through")
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None
