from __future__ import annotations

from typing import Any, Dict, List

from google.cloud import bigquery
from google.api_core.exceptions import NotFound

from app.settings import Settings


class BigQueryMetricsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = bigquery.Client(project=settings.monitor_project_id)
        self._project = settings.monitor_project_id
        self._dataset = settings.monitor_bq_dataset

    def _requests_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_requests`"

    def _stdout_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_stdout`"

    def _stderr_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_stderr`"

    def _run_query(self, sql: str, params: List[bigquery.ScalarQueryParameter]) -> List[Dict[str, Any]]:
        try:
            job = self._client.query(
                sql,
                job_config=bigquery.QueryJobConfig(query_parameters=params),
                location=self._settings.monitor_bq_location,
            )
            rows = job.result()
        except NotFound:
            # Sink/bootstrap race is common on first deploy; degrade to empty instead of hard 500.
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append({key: row.get(key) for key in row.keys()})
        return out

    def _table_exists(self, table_name: str) -> bool:
        table_id = f"{self._project}.{self._dataset}.{table_name}"
        try:
            self._client.get_table(table_id)
            return True
        except NotFound:
            return False

    def get_overview(self, *, days: int) -> Dict[str, Any]:
        sql = f"""
WITH req AS (
  SELECT
    timestamp AS ts,
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
),
qs AS (
  SELECT
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
    CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64) AS latency_ms,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS suggestion_count
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_result ")
),
restore AS (
  SELECT
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"event=([^ ]+)") AS event
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^chat_sync_telemetry ")
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"event=restore_")
)
SELECT
  (SELECT COUNT(*) FROM req) AS request_count,
  (SELECT COUNT(*) FROM req WHERE status >= 500) AS error_5xx_count,
  (SELECT SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) FROM req) AS error_5xx_rate,
  (SELECT APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] FROM req WHERE latency_ms IS NOT NULL) AS request_p95_latency_ms,
  (SELECT COUNT(*) FROM qs) AS qs_total,
  (SELECT COUNT(*) FROM qs WHERE stage = 'stable') AS qs_stable_count,
  (SELECT COUNT(*) FROM qs WHERE stage = 'degraded') AS qs_degraded_count,
  (SELECT SAFE_DIVIDE(COUNTIF(stage = 'stable'), COUNT(*)) FROM qs) AS qs_stable_rate,
  (SELECT AVG(latency_ms) FROM qs WHERE latency_ms IS NOT NULL) AS qs_avg_latency_ms,
  (SELECT AVG(SAFE_CAST(suggestion_count AS INT64)) FROM qs WHERE suggestion_count IS NOT NULL) AS qs_avg_suggestion_count,
  (SELECT COUNT(*) FROM restore) AS restore_total,
  (SELECT COUNT(*) FROM restore WHERE event IN ('restore_success', 'restore_empty')) AS restore_success_count,
  (SELECT SAFE_DIVIDE(COUNTIF(event IN ('restore_success', 'restore_empty')), COUNT(*)) FROM restore) AS restore_success_rate
"""
        rows = self._run_query(
            sql,
            [
                bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
                bigquery.ScalarQueryParameter("days", "INT64", max(1, int(days))),
            ],
        )
        return rows[0] if rows else {}

    def get_usage_timeseries(self, *, days: int) -> List[Dict[str, Any]]:
        sql = f"""
WITH req AS (
  SELECT
    DATE(timestamp, @tz) AS day,
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
    LOWER(CAST(httpRequest.userAgent AS STRING)) AS ua
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
)
SELECT
  day,
  CASE
    WHEN REGEXP_CONTAINS(ua, r'(iphone|android|mobile|ipad)') THEN 'mobile'
    WHEN ua IS NULL OR ua = '' THEN 'unknown'
    ELSE 'desktop'
  END AS device_class,
  COUNT(*) AS request_count,
  COUNTIF(status >= 500) AS error_5xx_count,
  SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) AS error_5xx_rate,
  APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency_ms
FROM req
GROUP BY day, device_class
ORDER BY day ASC, device_class ASC
"""
        return self._run_query(
            sql,
            [
                bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
                bigquery.ScalarQueryParameter("days", "INT64", max(1, int(days))),
                bigquery.ScalarQueryParameter("tz", "STRING", self._settings.monitor_timezone),
            ],
        )

    def get_error_report(self, *, days: int) -> Dict[str, Any]:
        trend_sql = f"""
SELECT
  DATE(timestamp, @tz) AS day,
  COUNT(*) AS error_5xx_count
FROM {self._requests_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
  AND SAFE_CAST(httpRequest.status AS INT64) >= 500
GROUP BY day
ORDER BY day ASC
"""
        top_endpoint_sql = f"""
SELECT
  COALESCE(REGEXP_EXTRACT(CAST(httpRequest.requestUrl AS STRING), r'https?://[^/]+(/[^? ]*)'), '/unknown') AS endpoint,
  COUNT(*) AS error_5xx_count
FROM {self._requests_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
  AND SAFE_CAST(httpRequest.status AS INT64) >= 500
GROUP BY endpoint
ORDER BY error_5xx_count DESC
LIMIT 30
"""
        log_sources: List[str] = []
        if self._table_exists("run_googleapis_com_stderr"):
            log_sources.append(
                f"""
  SELECT CAST(textPayload AS STRING) AS line
  FROM {self._stderr_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
"""
            )
        if self._table_exists("run_googleapis_com_stdout"):
            log_sources.append(
                f"""
  SELECT CAST(textPayload AS STRING) AS line
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
"""
            )
        top_error_sql = f"""
WITH logs AS (
{("  SELECT '' AS line WHERE FALSE" if not log_sources else "  UNION ALL".join(log_sources))}
)
SELECT
  COALESCE(
    REGEXP_EXTRACT(line, r'([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failed|Timeout|Conflict))'),
    REGEXP_EXTRACT(line, r'([a-z_]+(?:_failed|_error|_timeout|_conflict))'),
    'unknown'
  ) AS error_type,
  COUNT(*) AS count
FROM logs
WHERE REGEXP_CONTAINS(LOWER(line), r'(error|exception|failed|timeout|traceback|conflict)')
GROUP BY error_type
ORDER BY count DESC
LIMIT 30
"""

        params = [
            bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
            bigquery.ScalarQueryParameter("days", "INT64", max(1, int(days))),
            bigquery.ScalarQueryParameter("tz", "STRING", self._settings.monitor_timezone),
        ]
        return {
            "trend": self._run_query(trend_sql, params),
            "topEndpoints": self._run_query(top_endpoint_sql, params[:2]),
            "topErrors": self._run_query(top_error_sql, params[:2]),
        }

    def get_device_report(self, *, days: int) -> List[Dict[str, Any]]:
        sql = f"""
WITH req AS (
  SELECT
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
    LOWER(CAST(httpRequest.userAgent AS STRING)) AS ua
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
)
SELECT
  CASE
    WHEN REGEXP_CONTAINS(ua, r'(iphone|android|mobile|ipad)') THEN 'mobile'
    WHEN ua IS NULL OR ua = '' THEN 'unknown'
    ELSE 'desktop'
  END AS device_class,
  COUNT(*) AS request_count,
  COUNTIF(status >= 500) AS error_5xx_count,
  SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) AS error_5xx_rate,
  APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency_ms
FROM req
GROUP BY device_class
ORDER BY request_count DESC
"""
        return self._run_query(
            sql,
            [
                bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
                bigquery.ScalarQueryParameter("days", "INT64", max(1, int(days))),
            ],
        )

    def get_query_suggest_report(self, *, days: int) -> Dict[str, Any]:
        stage_sql = f"""
SELECT
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
  COUNT(*) AS count,
  AVG(CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64)) AS avg_latency_ms,
  AVG(CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS INT64)) AS avg_suggestion_count
FROM {self._stdout_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_result ")
GROUP BY stage
ORDER BY count DESC
"""
        fallback_sql = f"""
SELECT
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"fallback=([^ ]+)") AS fallback_source,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"reason=([^ ]+)") AS reason,
  COUNT(*) AS count
FROM {self._stdout_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_refine_degraded ")
GROUP BY fallback_source, reason
ORDER BY count DESC
"""

        params = [
            bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
            bigquery.ScalarQueryParameter("days", "INT64", max(1, int(days))),
        ]
        return {
            "stages": self._run_query(stage_sql, params),
            "fallbackSources": self._run_query(fallback_sql, params),
        }
