from __future__ import annotations

from typing import Any, Dict, List

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from app.settings import Settings
from app.time_window import MetricsTimeWindow


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

    def _window_params(self, window: MetricsTimeWindow) -> List[bigquery.ScalarQueryParameter]:
        return [
            bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
            bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
            bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
            bigquery.ScalarQueryParameter("tz", "STRING", window.timezone),
        ]

    def get_overview(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        sql = f"""
WITH req AS (
  SELECT
    timestamp AS ts,
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
    COALESCE(REGEXP_EXTRACT(CAST(httpRequest.requestUrl AS STRING), r'https?://[^/]+(/[^? ]*)'), '/unknown') AS path
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
),
qs AS (
  SELECT
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
    CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64) AS latency_ms,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS suggestion_count
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_result ")
),
restore AS (
  SELECT
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"event=([^ ]+)") AS event
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^chat_sync_telemetry ")
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"event=restore_")
)
SELECT
  (SELECT COUNT(*) FROM req) AS request_count,
  (SELECT COUNT(*) FROM req WHERE status >= 500) AS error_5xx_count,
  (SELECT SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) FROM req) AS error_5xx_rate,
  (SELECT APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] FROM req WHERE latency_ms IS NOT NULL) AS request_p95_latency_ms,
  (SELECT AVG(latency_ms) FROM req WHERE latency_ms IS NOT NULL AND status < 500 AND path IN ('/v2/ask', '/v2/ask/stream')) AS first_answer_avg_ms,
  (SELECT AVG(latency_ms) FROM req WHERE latency_ms IS NOT NULL AND status < 500 AND path IN ('/v2/ask/enhance_full', '/v2/ask/enhance_full/stream')) AS enhance_answer_avg_ms,
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
        rows = self._run_query(sql, self._window_params(window)[:3])
        return rows[0] if rows else {}

    def get_usage_timeseries(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        if window.is_day_bucket:
            sql = f"""
WITH devices AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
grid AS (
  SELECT bucket_day
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS bucket_day
),
req AS (
  SELECT
    DATE(timestamp, @tz) AS bucket_day,
    CASE
      WHEN REGEXP_CONTAINS(LOWER(CAST(httpRequest.userAgent AS STRING)), r'(iphone|android|mobile|ipad)') THEN 'mobile'
      WHEN CAST(httpRequest.userAgent AS STRING) IS NULL OR CAST(httpRequest.userAgent AS STRING) = '' THEN 'unknown'
      ELSE 'desktop'
    END AS device_class,
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
),
agg AS (
  SELECT
    bucket_day,
    device_class,
    COUNT(*) AS request_count,
    COUNTIF(status >= 500) AS error_5xx_count,
    SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) AS error_5xx_rate,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency_ms
  FROM req
  GROUP BY bucket_day, device_class
)
SELECT
  FORMAT_DATE('%Y-%m-%d', g.bucket_day) AS bucket_key,
  FORMAT_DATE('%m-%d', g.bucket_day) AS bucket_label,
  d.device_class,
  COALESCE(a.request_count, 0) AS request_count,
  COALESCE(a.error_5xx_count, 0) AS error_5xx_count,
  COALESCE(a.error_5xx_rate, 0.0) AS error_5xx_rate,
  COALESCE(a.p95_latency_ms, 0.0) AS p95_latency_ms
FROM grid g
CROSS JOIN devices d
LEFT JOIN agg a ON a.bucket_day = g.bucket_day AND a.device_class = d.device_class
ORDER BY g.bucket_day ASC, d.device_class ASC
"""
            return self._run_query(sql, self._window_params(window))

        label_format = "%H:%M" if window.duration_seconds <= 24 * 60 * 60 else "%m-%d %H:%M"
        sql = f"""
WITH devices AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
grid AS (
  SELECT bucket_local
  FROM UNNEST(
    GENERATE_DATETIME_ARRAY(
      DATETIME_TRUNC(DATETIME(@start_ts, @tz), HOUR)
      + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(@start_ts, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE,
      DATETIME_TRUNC(DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz), HOUR)
      + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)), @bucket_minutes) * @bucket_minutes) MINUTE,
      INTERVAL @bucket_minutes MINUTE
    )
  ) AS bucket_local
),
req AS (
  SELECT
    DATETIME_TRUNC(DATETIME(timestamp, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(timestamp, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_local,
    CASE
      WHEN REGEXP_CONTAINS(LOWER(CAST(httpRequest.userAgent AS STRING)), r'(iphone|android|mobile|ipad)') THEN 'mobile'
      WHEN CAST(httpRequest.userAgent AS STRING) IS NULL OR CAST(httpRequest.userAgent AS STRING) = '' THEN 'unknown'
      ELSE 'desktop'
    END AS device_class,
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
),
agg AS (
  SELECT
    bucket_local,
    device_class,
    COUNT(*) AS request_count,
    COUNTIF(status >= 500) AS error_5xx_count,
    SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) AS error_5xx_rate,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency_ms
  FROM req
  GROUP BY bucket_local, device_class
)
SELECT
  FORMAT_DATETIME('%Y-%m-%d %H:%M', g.bucket_local) AS bucket_key,
  FORMAT_DATETIME(@label_format, g.bucket_local) AS bucket_label,
  d.device_class,
  COALESCE(a.request_count, 0) AS request_count,
  COALESCE(a.error_5xx_count, 0) AS error_5xx_count,
  COALESCE(a.error_5xx_rate, 0.0) AS error_5xx_rate,
  COALESCE(a.p95_latency_ms, 0.0) AS p95_latency_ms
FROM grid g
CROSS JOIN devices d
LEFT JOIN agg a ON a.bucket_local = g.bucket_local AND a.device_class = d.device_class
ORDER BY g.bucket_local ASC, d.device_class ASC
"""
        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("bucket_minutes", "INT64", int(window.bucket_minutes)),
            bigquery.ScalarQueryParameter("label_format", "STRING", label_format),
        ]
        return self._run_query(sql, params)

    def get_error_report(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        if window.is_day_bucket:
            trend_sql = f"""
WITH grid AS (
  SELECT bucket_day
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS bucket_day
),
agg AS (
  SELECT
    DATE(timestamp, @tz) AS bucket_day,
    COUNT(*) AS error_5xx_count
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND SAFE_CAST(httpRequest.status AS INT64) >= 500
  GROUP BY bucket_day
)
SELECT
  FORMAT_DATE('%Y-%m-%d', g.bucket_day) AS bucket_key,
  FORMAT_DATE('%m-%d', g.bucket_day) AS bucket_label,
  COALESCE(a.error_5xx_count, 0) AS error_5xx_count
FROM grid g
LEFT JOIN agg a ON a.bucket_day = g.bucket_day
ORDER BY g.bucket_day ASC
"""
            trend_params = self._window_params(window)
        else:
            label_format = "%H:%M" if window.duration_seconds <= 24 * 60 * 60 else "%m-%d %H:%M"
            trend_sql = f"""
WITH grid AS (
  SELECT bucket_local
  FROM UNNEST(
    GENERATE_DATETIME_ARRAY(
      DATETIME_TRUNC(DATETIME(@start_ts, @tz), HOUR)
      + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(@start_ts, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE,
      DATETIME_TRUNC(DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz), HOUR)
      + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)), @bucket_minutes) * @bucket_minutes) MINUTE,
      INTERVAL @bucket_minutes MINUTE
    )
  ) AS bucket_local
),
agg AS (
  SELECT
    DATETIME_TRUNC(DATETIME(timestamp, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(timestamp, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_local,
    COUNT(*) AS error_5xx_count
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND SAFE_CAST(httpRequest.status AS INT64) >= 500
  GROUP BY bucket_local
)
SELECT
  FORMAT_DATETIME('%Y-%m-%d %H:%M', g.bucket_local) AS bucket_key,
  FORMAT_DATETIME(@label_format, g.bucket_local) AS bucket_label,
  COALESCE(a.error_5xx_count, 0) AS error_5xx_count
FROM grid g
LEFT JOIN agg a ON a.bucket_local = g.bucket_local
ORDER BY g.bucket_local ASC
"""
            trend_params = self._window_params(window) + [
                bigquery.ScalarQueryParameter("bucket_minutes", "INT64", int(window.bucket_minutes)),
                bigquery.ScalarQueryParameter("label_format", "STRING", label_format),
            ]
        top_endpoint_sql = f"""
SELECT
  COALESCE(REGEXP_EXTRACT(CAST(httpRequest.requestUrl AS STRING), r'https?://[^/]+(/[^? ]*)'), '/unknown') AS endpoint,
  COUNT(*) AS error_5xx_count
FROM {self._requests_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= @start_ts
  AND timestamp < @end_ts
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
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
"""
            )
        if self._table_exists("run_googleapis_com_stdout"):
            log_sources.append(
                f"""
  SELECT CAST(textPayload AS STRING) AS line
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
"""
            )
        log_union = "  SELECT '' AS line WHERE FALSE" if not log_sources else "  UNION ALL".join(log_sources)
        top_error_sql = f"""
WITH logs AS (
{log_union}
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

        params = self._window_params(window)
        return {
            "trend": self._run_query(trend_sql, trend_params),
            "topEndpoints": self._run_query(top_endpoint_sql, params[:3]),
            "topErrors": self._run_query(top_error_sql, params[:3]),
        }

    def get_device_report(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        sql = f"""
WITH req AS (
  SELECT
    SAFE_CAST(httpRequest.status AS INT64) AS status,
    SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
    LOWER(CAST(httpRequest.userAgent AS STRING)) AS ua
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
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
        return self._run_query(sql, self._window_params(window)[:3])

    def get_query_suggest_report(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        stage_sql = f"""
SELECT
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
  COUNT(*) AS count,
  AVG(CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64)) AS avg_latency_ms,
  AVG(CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS INT64)) AS avg_suggestion_count
FROM {self._stdout_table()}
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = @service_name
  AND timestamp >= @start_ts
  AND timestamp < @end_ts
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
  AND timestamp >= @start_ts
  AND timestamp < @end_ts
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_refine_degraded ")
GROUP BY fallback_source, reason
ORDER BY count DESC
"""

        params = self._window_params(window)[:3]
        return {
            "stages": self._run_query(stage_sql, params),
            "fallbackSources": self._run_query(fallback_sql, params),
        }
