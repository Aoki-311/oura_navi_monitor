from __future__ import annotations

import json
from typing import Any, Dict, List

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from app.services.google_auth import get_gcloud_cli_credentials_if_enabled
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class BigQueryMetricsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        credentials = get_gcloud_cli_credentials_if_enabled(settings)
        self._client = bigquery.Client(project=settings.monitor_project_id, credentials=credentials)
        self._project = settings.monitor_project_id
        self._dataset = settings.monitor_bq_dataset
        self._table_exists_cache: Dict[str, bool] = {}

    def _requests_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_requests`"

    def _stdout_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_stdout`"

    def _stderr_table(self) -> str:
        return f"`{self._project}.{self._dataset}.run_googleapis_com_stderr`"

    def _view(self, name: str) -> str:
        return f"`{self._project}.{self._dataset}.{name}`"

    def _table(self, name: str) -> str:
        return f"`{self._project}.{self._dataset}.{name}`"

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
        if table_name in self._table_exists_cache:
            return self._table_exists_cache[table_name]
        table_id = f"{self._project}.{self._dataset}.{table_name}"
        try:
            self._client.get_table(table_id)
            self._table_exists_cache[table_name] = True
            return True
        except NotFound:
            self._table_exists_cache[table_name] = False
            return False

    def _window_params(self, window: MetricsTimeWindow) -> List[bigquery.ScalarQueryParameter]:
        return [
            bigquery.ScalarQueryParameter("service_name", "STRING", self._settings.monitor_source_service),
            bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
            bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
            bigquery.ScalarQueryParameter("tz", "STRING", window.timezone),
        ]

    def _get_system_dashboard_metrics_from_snapshot(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        supported_presets = {
            "today",
            "last_6h",
            "last_12h",
            "last_3d",
            "last_7d",
            "last_14d",
            "last_30d",
            "last_60d",
            "all",
        }
        preset = str(window.preset or "").strip().lower()
        if window.source != "preset" or preset not in supported_presets:
            return {}
        if not self._table_exists("monitor_dashboard_snapshots"):
            return {}

        table_id = f"{self._project}.{self._dataset}.monitor_dashboard_snapshots"
        try:
            table = self._client.get_table(table_id)
            rows = self._client.list_rows(table, max_results=50)
        except NotFound:
            self._table_exists_cache["monitor_dashboard_snapshots"] = False
            return {}

        for row in rows:
            data = dict(row.items())
            if str(data.get("preset") or "") != preset:
                continue
            if str(data.get("timezone") or "") != window.timezone:
                continue
            raw = data.get("payload_json")
            if not raw:
                continue
            try:
                payload = json.loads(str(raw))
            except Exception:
                return {}
            if not isinstance(payload.get("questionCategory"), dict):
                # Older snapshots do not include the question category payload.
                # Fall back to aggregate tables instead of serving stale UI data.
                continue
            if payload.get("snapshotContract") != "monitor_exclusions_v2":
                # Older snapshots may include excluded service/test users.
                # Fall back until the snapshot table is refreshed with the current contract.
                continue
            answer_quality = payload.get("answerQuality") or {}
            if "usability" not in answer_quality or "evidenceSufficiency" not in answer_quality:
                continue
            kpis = payload.get("kpis") or {}
            if "coverageAttentionRate" not in kpis:
                # Keep the low-coverage KPI and auxiliary coverage caution in sync.
                continue
            return payload
        return {}

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
  (SELECT COUNT(*) FROM req WHERE REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS core_request_count,
  (SELECT COUNT(*) FROM req WHERE NOT REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS system_request_count,
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

    def _get_system_dashboard_metrics_from_tables(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        params = self._window_params(window)
        sql = f"""
WITH hours AS (
  SELECT hour FROM UNNEST(GENERATE_ARRAY(0, 23)) AS hour
),
device_labels AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
mode_labels AS (
  SELECT 'internal' AS mode UNION ALL
  SELECT 'websearch'
),
dates AS (
  SELECT event_date
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS event_date
),
hourly_window AS (
  SELECT *
  FROM {self._table("monitor_system_hourly")}
  WHERE bucket_ts >= TIMESTAMP_TRUNC(@start_ts, HOUR)
    AND bucket_ts < @end_ts
),
aggregate_contract AS (
  SELECT
    COUNT(*) AS row_count,
    COUNTIF(aggregate_contract_version = 'monitor_exclusions_v2') AS current_contract_row_count
  FROM hourly_window
),
request_summary AS (
  SELECT
    SUM(request_count) AS request_count,
    SUM(error_count) AS error_count,
    SAFE_DIVIDE(SUM(error_count), SUM(request_count)) AS error_rate,
    -- Conservative roll-up: hourly P95 cannot be exactly reconstructed from aggregate rows.
    MAX(p95_latency_ms) AS p95_latency_ms
  FROM hourly_window
),
request_by_hour AS (
  SELECT
    FORMAT('%02d:00', h.hour) AS hour_label,
    COALESCE(SUM(w.request_count), 0) AS request_count
  FROM hours h
  LEFT JOIN hourly_window w ON w.bucket_hour_jst = h.hour
  GROUP BY h.hour
),
device_distribution AS (
  SELECT 'desktop' AS device_class, COALESCE(SUM(desktop_request_count), 0) AS request_count FROM hourly_window
  UNION ALL
  SELECT 'mobile', COALESCE(SUM(mobile_request_count), 0) FROM hourly_window
  UNION ALL
  SELECT 'unknown', COALESCE(SUM(unknown_request_count), 0) FROM hourly_window
),
mode_distribution AS (
  SELECT 'internal' AS mode, COALESCE(SUM(internal_mode_count), 0) AS request_count FROM hourly_window
  UNION ALL
  SELECT 'websearch', COALESCE(SUM(websearch_mode_count), 0) FROM hourly_window
),
active_users AS (
  SELECT COALESCE(HLL_COUNT.MERGE(active_user_hll), 0) AS active_user_count
  FROM hourly_window
  WHERE active_user_hll IS NOT NULL
),
daily_window AS (
  SELECT *
  FROM {self._table("monitor_user_daily")}
  WHERE date_jst >= DATE(@start_ts, @tz)
    AND date_jst <= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
),
daily_14d AS (
  SELECT *
  FROM {self._table("monitor_user_daily")}
  WHERE date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz)
    AND date_jst <= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
),
usage_trend_agg AS (
  SELECT
    date_jst AS event_date,
    COUNT(DISTINCT IF(active_flag AND user_id != 'unknown', user_id, NULL)) AS active_user_count,
    SUM(message_count) AS message_count
  FROM daily_window
  GROUP BY event_date
),
usage_trend AS (
  SELECT
    FORMAT_DATE('%Y-%m-%d', d.event_date) AS date_label,
    COALESCE(a.active_user_count, 0) AS active_user_count,
    COALESCE(a.message_count, 0) AS message_count
  FROM dates d
  LEFT JOIN usage_trend_agg a USING(event_date)
),
activity_by_user AS (
  SELECT
    COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown') AS user_key,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY), @tz), message_count, 0)) AS core_count_3d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), message_count, 0)) AS core_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz), message_count, 0)) AS core_count_14d
  FROM daily_14d
  GROUP BY user_key
),
activity_segments AS (
  SELECT
    CASE
      WHEN core_count_3d >= 3 THEN '高アクティブ'
      WHEN core_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN core_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS label,
    COUNT(*) AS count
  FROM activity_by_user
  WHERE user_key != 'unknown'
  GROUP BY label
),
activity_labels AS (
  SELECT '高アクティブ' AS label UNION ALL
  SELECT '中アクティブ' UNION ALL
  SELECT '低アクティブ' UNION ALL
  SELECT '休眠ユーザー'
),
answer_summary AS (
  SELECT
    SUM(answer_count) AS answer_count,
    SUM(answer_success_count) AS answer_success_count,
    SAFE_DIVIDE(SUM(answer_success_count), SUM(answer_count)) AS answer_success_rate,
    SUM(low_coverage_count) AS low_coverage_count,
    SAFE_DIVIDE(SUM(low_coverage_count), SUM(answer_count)) AS low_coverage_rate,
    SUM(coverage_attention_count) AS coverage_attention_count,
    SAFE_DIVIDE(SUM(coverage_attention_count), SUM(answer_count)) AS coverage_attention_rate,
    SAFE_DIVIDE(SUM(coverage_score_sum), SUM(coverage_score_count)) AS average_coverage_score,
    SAFE_DIVIDE(SUM(alignment_score_sum), SUM(alignment_score_count)) AS average_alignment_score,
    SUM(structured_led_count) AS structured_led_count,
    SAFE_DIVIDE(SUM(structured_led_count), SUM(answer_count)) AS structured_led_rate,
    SUM(citation_binding_issue_count) AS citation_binding_issue_count,
    SAFE_DIVIDE(SUM(citation_binding_issue_count), SUM(answer_count)) AS citation_binding_issue_rate,
    SUM(answer_metric_official_count) AS answer_metric_official_count,
    SUM(answer_metric_proxy_count) AS answer_metric_proxy_count
  FROM hourly_window
),
answer_distribution AS (
  SELECT metric, label, SUM(count) AS count
  FROM (
    SELECT 'answerability' AS metric, item.label AS label, item.count AS count
    FROM hourly_window, UNNEST(IFNULL(answerability_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT 'usability', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(usability_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT 'deliveryReadiness', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(delivery_readiness_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT 'evidenceSufficiency', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(evidence_sufficiency_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT 'verificationVerdict', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(verification_verdict_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
  )
  GROUP BY metric, label
),
question_category_distribution AS (
  SELECT label, SUM(count) AS count
  FROM (
    SELECT COALESCE(NULLIF(question_category, ''), 'topic_ideation') AS label, COUNT(*) AS count
    FROM {self._table("monitor_answer_events")}
    WHERE event_ts >= @start_ts
      AND event_ts < @end_ts
    GROUP BY label
  )
  GROUP BY label
),
followup_summary AS (
  SELECT
    SUM(followup_recognized_count) AS recognized_count,
    SUM(followup_success_count) AS success_count,
    SUM(explicit_correction_count) AS explicit_correction_count,
    SUM(clarification_required_count) AS clarification_required_count,
    SUM(followup_offtopic_count) AS followup_offtopic_count
  FROM hourly_window
),
activity_total AS (
  SELECT SUM(COALESCE(count, 0)) AS total_count FROM activity_segments
),
device_total AS (
  SELECT SUM(request_count) AS total_count FROM device_distribution
),
mode_total AS (
  SELECT SUM(request_count) AS total_count FROM mode_distribution
)
SELECT TO_JSON_STRING(STRUCT(
  (
    SELECT IF(row_count = 0 OR current_contract_row_count = row_count, 'monitor_exclusions_v2', 'legacy')
    FROM aggregate_contract
  ) AS snapshotContract,
  STRUCT(
    (SELECT active_user_count FROM active_users) AS activeUserCount,
    (SELECT answer_success_rate FROM answer_summary) AS answerSuccessRate,
    (SELECT low_coverage_rate FROM answer_summary) AS lowCoverageRate,
    (SELECT coverage_attention_rate FROM answer_summary) AS coverageAttentionRate,
    (SELECT error_rate FROM request_summary) AS errorRate,
    (SELECT p95_latency_ms FROM request_summary) AS p95LatencyMs
  ) AS kpis,
  STRUCT(
    (
      SELECT CASE
        WHEN COALESCE(answer_count, 0) = 0 THEN 'unknown'
        WHEN COALESCE(answer_metric_official_count, 0) > 0
         AND COALESCE(answer_metric_proxy_count, 0) > 0 THEN 'mixed'
        WHEN COALESCE(answer_metric_official_count, 0) > 0 THEN 'official'
        ELSE 'proxy'
      END
      FROM answer_summary
    ) AS answerSuccessRate
  ) AS metricStatus,
  (
    SELECT ARRAY_AGG(STRUCT(
      date_label AS date,
      active_user_count AS activeUserCount,
      message_count AS messageCount
    ) ORDER BY date_label)
    FROM usage_trend
  ) AS usageTrend,
  STRUCT(
    (SELECT COALESCE(total_count, 0) FROM activity_total) AS totalUserCount,
    (
      SELECT ARRAY_AGG(STRUCT(
        l.label AS label,
        COALESCE(s.count, 0) AS count,
        SAFE_DIVIDE(COALESCE(s.count, 0), NULLIF((SELECT total_count FROM activity_total), 0)) AS rate
      ) ORDER BY CASE l.label
        WHEN '高アクティブ' THEN 1
        WHEN '中アクティブ' THEN 2
        WHEN '低アクティブ' THEN 3
        ELSE 4
      END)
      FROM activity_labels l
      LEFT JOIN activity_segments s USING(label)
    ) AS segments
  ) AS activityDistribution,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(hour_label AS hour, request_count AS requestCount) ORDER BY hour_label)
      FROM request_by_hour
    ) AS requestByHour,
    (
      SELECT ARRAY_AGG(STRUCT(
        CASE d.device_class
          WHEN 'desktop' THEN 'PC'
          WHEN 'mobile' THEN 'モバイル'
          ELSE '不明'
        END AS label,
        d.device_class AS value,
        COALESCE(dd.request_count, 0) AS count,
        SAFE_DIVIDE(COALESCE(dd.request_count, 0), NULLIF((SELECT total_count FROM device_total), 0)) AS rate
      ) ORDER BY COALESCE(dd.request_count, 0) DESC, d.device_class)
      FROM device_labels d
      LEFT JOIN device_distribution dd USING(device_class)
    ) AS deviceDistribution,
    (
      SELECT ARRAY_AGG(STRUCT(
        CASE m.mode
          WHEN 'internal' THEN '社内モード'
          WHEN 'websearch' THEN 'Web検索モード'
          ELSE 'その他'
        END AS label,
        m.mode AS value,
        COALESCE(md.request_count, 0) AS count,
        SAFE_DIVIDE(COALESCE(md.request_count, 0), NULLIF((SELECT total_count FROM mode_total), 0)) AS rate
      ) ORDER BY COALESCE(md.request_count, 0) DESC, m.mode)
      FROM mode_labels m
      LEFT JOIN mode_distribution md USING(mode)
    ) AS modeDistribution
  ) AS environmentMode,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'answerability'
    ) AS answerability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'usability'
    ) AS usability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'deliveryReadiness'
    ) AS deliveryReadiness,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'evidenceSufficiency'
    ) AS evidenceSufficiency,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'verificationVerdict'
    ) AS verificationVerdict
  ) AS answerQuality,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(
        label,
        label AS value,
        count,
        SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate
      ) ORDER BY count DESC, label)
      FROM question_category_distribution
    ) AS items
  ) AS questionCategory,
  STRUCT(
    (SELECT recognized_count FROM followup_summary) AS recognizedCount,
    (SELECT success_count FROM followup_summary) AS successCount,
    SAFE_DIVIDE((SELECT success_count FROM followup_summary), NULLIF((SELECT recognized_count FROM followup_summary), 0)) AS successRate,
    (SELECT explicit_correction_count FROM followup_summary) AS explicitCorrectionCount,
    (SELECT clarification_required_count FROM followup_summary) AS clarificationRequiredCount,
    (SELECT followup_offtopic_count FROM followup_summary) AS followupOfftopicCount
  ) AS followup
)) AS payload_json
"""
        rows = self._run_query(sql, params)
        if not rows:
            return {}
        payload = rows[0].get("payload_json")
        if not payload:
            return {}
        return json.loads(str(payload))

    def get_system_dashboard_metrics(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        snapshot = self._get_system_dashboard_metrics_from_snapshot(window=window)
        if snapshot:
            return snapshot

        if (
            self._table_exists("monitor_system_hourly")
            and self._table_exists("monitor_user_daily")
            and self._table_exists("monitor_answer_events")
        ):
            try:
                table_payload = self._get_system_dashboard_metrics_from_tables(window=window)
                if table_payload:
                    return table_payload
            except Exception:
                # A freshly deployed API can see old aggregate schemas before the refresh job runs.
                # Degrade to view-based SQL instead of failing the dashboard.
                pass

        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("coverage_threshold", "FLOAT64", 0.60),
        ]
        sql = f"""
WITH hours AS (
  SELECT hour FROM UNNEST(GENERATE_ARRAY(0, 23)) AS hour
),
device_labels AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
mode_labels AS (
  SELECT 'internal' AS mode UNION ALL
  SELECT 'websearch'
),
dates AS (
  SELECT event_date
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS event_date
),
req AS (
  SELECT
    event_ts AS ts,
    status,
    latency_ms,
    COALESCE(NULLIF(device_class, ''), 'unknown') AS device_class
  FROM {self._view("v_request_user_metric_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
),
request_summary AS (
  SELECT
    COUNT(*) AS request_count,
    COUNTIF(status >= 500) AS error_count,
    SAFE_DIVIDE(COUNTIF(status >= 500), COUNT(*)) AS error_rate,
    APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)] AS p95_latency_ms
  FROM req
),
request_by_hour AS (
  SELECT
    FORMAT('%02d:00', h.hour) AS hour_label,
    COALESCE(COUNT(r.ts), 0) AS request_count
  FROM hours h
  LEFT JOIN req r ON EXTRACT(HOUR FROM DATETIME(r.ts, @tz)) = h.hour
  GROUP BY h.hour
),
device_distribution AS (
  SELECT
    d.device_class,
    COALESCE(COUNT(r.ts), 0) AS request_count
  FROM device_labels d
  LEFT JOIN req r USING(device_class)
  GROUP BY d.device_class
),
request_user_window AS (
  SELECT
    event_ts,
    event_date,
    COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown') AS user_key,
    mode,
    is_core
  FROM {self._view("v_request_user_metric_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
),
request_user_14d AS (
  SELECT
    event_ts,
    COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown') AS user_key,
    is_core
  FROM {self._view("v_request_user_metric_events")}
  WHERE event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
),
active_users AS (
  SELECT COUNT(DISTINCT IF(user_key = 'unknown', NULL, user_key)) AS active_user_count
  FROM request_user_window
),
usage_trend_agg AS (
  SELECT
    event_date,
    COUNT(DISTINCT IF(user_key = 'unknown', NULL, user_key)) AS active_user_count,
    COUNTIF(is_core) AS message_count
  FROM request_user_window
  GROUP BY event_date
),
usage_trend AS (
  SELECT
    FORMAT_DATE('%Y-%m-%d', d.event_date) AS date_label,
    COALESCE(a.active_user_count, 0) AS active_user_count,
    COALESCE(a.message_count, 0) AS message_count
  FROM dates d
  LEFT JOIN usage_trend_agg a USING(event_date)
),
activity_by_user AS (
  SELECT
    user_key,
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY)) AS core_count_3d,
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY)) AS core_count_7d,
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)) AS core_count_14d
  FROM request_user_14d
  WHERE user_key != 'unknown'
  GROUP BY user_key
),
activity_segments AS (
  SELECT
    CASE
      WHEN core_count_3d >= 3 THEN '高アクティブ'
      WHEN core_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN core_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS label,
    COUNT(*) AS count
  FROM activity_by_user
  GROUP BY label
),
activity_labels AS (
  SELECT '高アクティブ' AS label UNION ALL
  SELECT '中アクティブ' UNION ALL
  SELECT '低アクティブ' UNION ALL
  SELECT '休眠ユーザー'
),
mode_distribution AS (
  SELECT
    m.mode,
    COALESCE(COUNTIF(u.is_core), 0) AS request_count
  FROM mode_labels m
  LEFT JOIN request_user_window u USING(mode)
  GROUP BY m.mode
),
answer_events AS (
  SELECT *
  FROM {self._view("v_ask_audit_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(user_id, '')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
    )
),
coverage_gap_keys AS (
  SELECT DISTINCT
    NULLIF(trace_request_key, '#') AS trace_request_key,
    NULLIF(conversation_turn_key, '#') AS conversation_turn_key,
    NULLIF(conversation_message_key, '#') AS conversation_message_key,
    gap_kind
  FROM {self._view("v_coverage_gap_workitems")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
),
answer_flags AS (
  SELECT
    a.*,
    EXISTS (
      SELECT 1
      FROM coverage_gap_keys g
      WHERE (
        g.trace_request_key IS NOT NULL
        AND g.trace_request_key = NULLIF(a.trace_request_key, '#')
      )
      OR (
        g.conversation_turn_key IS NOT NULL
        AND g.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
      )
      OR (
        g.conversation_message_key IS NOT NULL
        AND g.conversation_message_key = NULLIF(a.conversation_message_key, '#')
      )
    ) AS has_coverage_gap
  FROM answer_events a
),
answer_summary AS (
  SELECT
    COUNT(*) AS answer_count,
    COUNTIF(error_code IS NULL AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')) AS answer_success_count,
    SAFE_DIVIDE(
      COUNTIF(error_code IS NULL AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')),
      COUNT(*)
    ) AS answer_success_rate,
    COUNTIF(
      has_coverage_gap
      OR COALESCE(citation_count = 0, FALSE)
      OR evidence_sufficiency = 'insufficient'
    ) AS low_coverage_count,
    SAFE_DIVIDE(
      COUNTIF(
        has_coverage_gap
        OR COALESCE(citation_count = 0, FALSE)
        OR evidence_sufficiency = 'insufficient'
      ),
      COUNT(*)
    ) AS low_coverage_rate,
    COUNTIF(COALESCE(coverage_score < 0.50, FALSE)) AS coverage_attention_count,
    SAFE_DIVIDE(COUNTIF(COALESCE(coverage_score < 0.50, FALSE)), COUNT(*)) AS coverage_attention_rate,
    AVG(coverage_score) AS average_coverage_score,
    AVG(alignment_score) AS average_alignment_score,
    COUNTIF(structured_led) AS structured_led_count,
    SAFE_DIVIDE(COUNTIF(structured_led), COUNT(*)) AS structured_led_rate,
    COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy') AS citation_binding_issue_count,
    SAFE_DIVIDE(COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy'), COUNT(*)) AS citation_binding_issue_rate
  FROM answer_flags
),
answer_distribution AS (
  SELECT 'answerability' AS metric, answerability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'usability' AS metric, usability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'deliveryReadiness' AS metric, delivery_readiness AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'evidenceSufficiency' AS metric, evidence_sufficiency AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'verificationVerdict' AS metric, verification_verdict AS label, COUNT(*) AS count FROM answer_events GROUP BY label
),
question_category_distribution AS (
  SELECT COALESCE(NULLIF(question_category, ''), 'topic_ideation') AS label, COUNT(*) AS count
  FROM answer_events
  GROUP BY label
),
followup_open AS (
  SELECT *
  FROM {self._view("v_followup_open_result_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
    )
),
followup_resolution AS (
  SELECT *
  FROM {self._view("v_followup_resolution_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
    )
),
followup_summary AS (
  SELECT
    (SELECT COUNTIF(event = 'recognized') FROM followup_open) AS recognized_count,
    (SELECT COUNTIF(event = 'success') FROM followup_open) AS success_count,
    (SELECT COUNTIF(decision_normalized = 'explicit_correction') FROM followup_resolution) AS explicit_correction_count,
    (SELECT COUNTIF(decision_normalized = 'clarify_before_carry') FROM followup_resolution) AS clarification_required_count,
    (SELECT COUNTIF(followup_offtopic) FROM followup_resolution) AS followup_offtopic_count
),
activity_total AS (
  SELECT SUM(COALESCE(count, 0)) AS total_count FROM activity_segments
),
device_total AS (
  SELECT SUM(request_count) AS total_count FROM device_distribution
),
mode_total AS (
  SELECT SUM(request_count) AS total_count FROM mode_distribution
)
SELECT TO_JSON_STRING(STRUCT(
  STRUCT(
    (SELECT active_user_count FROM active_users) AS activeUserCount,
    (SELECT answer_success_rate FROM answer_summary) AS answerSuccessRate,
    (SELECT low_coverage_rate FROM answer_summary) AS lowCoverageRate,
    (SELECT coverage_attention_rate FROM answer_summary) AS coverageAttentionRate,
    (SELECT error_rate FROM request_summary) AS errorRate,
    (SELECT p95_latency_ms FROM request_summary) AS p95LatencyMs
  ) AS kpis,
  (
    SELECT ARRAY_AGG(STRUCT(
      date_label AS date,
      active_user_count AS activeUserCount,
      message_count AS messageCount
    ) ORDER BY date_label)
    FROM usage_trend
  ) AS usageTrend,
  STRUCT(
    (SELECT COALESCE(total_count, 0) FROM activity_total) AS totalUserCount,
    (
      SELECT ARRAY_AGG(STRUCT(
        l.label AS label,
        COALESCE(s.count, 0) AS count,
        SAFE_DIVIDE(COALESCE(s.count, 0), NULLIF((SELECT total_count FROM activity_total), 0)) AS rate
      ) ORDER BY CASE l.label
        WHEN '高アクティブ' THEN 1
        WHEN '中アクティブ' THEN 2
        WHEN '低アクティブ' THEN 3
        ELSE 4
      END)
      FROM activity_labels l
      LEFT JOIN activity_segments s USING(label)
    ) AS segments
  ) AS activityDistribution,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(hour_label AS hour, request_count AS requestCount) ORDER BY hour_label)
      FROM request_by_hour
    ) AS requestByHour,
    (
      SELECT ARRAY_AGG(STRUCT(
        CASE device_class
          WHEN 'desktop' THEN 'PC'
          WHEN 'mobile' THEN 'モバイル'
          ELSE '不明'
        END AS label,
        device_class AS value,
        request_count AS count,
        SAFE_DIVIDE(request_count, NULLIF((SELECT total_count FROM device_total), 0)) AS rate
      ) ORDER BY request_count DESC, device_class)
      FROM device_distribution
    ) AS deviceDistribution,
    (
      SELECT ARRAY_AGG(STRUCT(
        CASE mode
          WHEN 'internal' THEN '社内モード'
          WHEN 'websearch' THEN 'Web検索モード'
          ELSE 'その他'
        END AS label,
        mode AS value,
        request_count AS count,
        SAFE_DIVIDE(request_count, NULLIF((SELECT total_count FROM mode_total), 0)) AS rate
      ) ORDER BY request_count DESC, mode)
      FROM mode_distribution
    ) AS modeDistribution
  ) AS environmentMode,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'answerability'
    ) AS answerability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'usability'
    ) AS usability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'deliveryReadiness'
    ) AS deliveryReadiness,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'evidenceSufficiency'
    ) AS evidenceSufficiency,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'verificationVerdict'
    ) AS verificationVerdict
  ) AS answerQuality,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(
        label,
        label AS value,
        count,
        SAFE_DIVIDE(count, NULLIF((SELECT answer_count FROM answer_summary), 0)) AS rate
      ) ORDER BY count DESC, label)
      FROM question_category_distribution
    ) AS items
  ) AS questionCategory,
  STRUCT(
    (SELECT recognized_count FROM followup_summary) AS recognizedCount,
    (SELECT success_count FROM followup_summary) AS successCount,
    SAFE_DIVIDE((SELECT success_count FROM followup_summary), NULLIF((SELECT recognized_count FROM followup_summary), 0)) AS successRate,
    (SELECT explicit_correction_count FROM followup_summary) AS explicitCorrectionCount,
    (SELECT clarification_required_count FROM followup_summary) AS clarificationRequiredCount,
    (SELECT followup_offtopic_count FROM followup_summary) AS followupOfftopicCount
  ) AS followup
)) AS payload_json
"""
        rows = self._run_query(sql, params)
        if not rows:
            return {}
        payload = rows[0].get("payload_json")
        if not payload:
            return {}
        return json.loads(str(payload))

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
    COALESCE(REGEXP_EXTRACT(CAST(httpRequest.requestUrl AS STRING), r'https?://[^/]+(/[^? ]*)'), '/unknown') AS path,
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
    COUNTIF(REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS core_request_count,
    COUNTIF(NOT REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS system_request_count,
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
  COALESCE(a.core_request_count, 0) AS core_request_count,
  COALESCE(a.system_request_count, 0) AS system_request_count,
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
bounds AS (
  SELECT
    DATETIME_TRUNC(DATETIME(@start_ts, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(@start_ts, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_start_local,
    DATETIME_TRUNC(DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_end_local
),
grid AS (
  SELECT DATETIME_ADD(bucket_start_local, INTERVAL offset_minutes MINUTE) AS bucket_local
  FROM bounds,
  UNNEST(
    GENERATE_ARRAY(
      0,
      DATETIME_DIFF(bucket_end_local, bucket_start_local, MINUTE),
      @bucket_minutes
    )
  ) AS offset_minutes
),
req AS (
  SELECT
    DATETIME_TRUNC(DATETIME(timestamp, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(timestamp, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_local,
    COALESCE(REGEXP_EXTRACT(CAST(httpRequest.requestUrl AS STRING), r'https?://[^/]+(/[^? ]*)'), '/unknown') AS path,
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
    COUNTIF(REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS core_request_count,
    COUNTIF(NOT REGEXP_CONTAINS(path, r'^/v2/(ask|conversations)(/|$)')) AS system_request_count,
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
  COALESCE(a.core_request_count, 0) AS core_request_count,
  COALESCE(a.system_request_count, 0) AS system_request_count,
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
WITH bounds AS (
  SELECT
    DATETIME_TRUNC(DATETIME(@start_ts, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(@start_ts, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_start_local,
    DATETIME_TRUNC(DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_end_local
),
grid AS (
  SELECT DATETIME_ADD(bucket_start_local, INTERVAL offset_minutes MINUTE) AS bucket_local
  FROM bounds,
  UNNEST(
    GENERATE_ARRAY(
      0,
      DATETIME_DIFF(bucket_end_local, bucket_start_local, MINUTE),
      @bucket_minutes
    )
  ) AS offset_minutes
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

    def get_followup_open_aggregates(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        sql = f"""
WITH src AS (
  SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_open_result_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^followup_open_result_json=")
),
events AS (
  SELECT
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.event'), ''), 'unknown')) AS event_name,
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) AS user_email
  FROM src
  WHERE payload IS NOT NULL
)
SELECT
  event_name,
  user_id,
  user_email,
  COUNT(*) AS event_count
FROM events
GROUP BY event_name, user_id, user_email
"""
        rows = self._run_query(sql, self._window_params(window)[:3])
        recognized_total = 0
        success_total = 0
        by_user: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            user_id = str(row.get("user_id") or "").strip() or "unknown"
            user_email = str(row.get("user_email") or "").strip().lower()
            event_name = str(row.get("event_name") or "").strip().lower()
            count = int(row.get("event_count") or 0)
            key = f"{user_id}::{user_email}"
            slot = by_user.setdefault(
                key,
                {
                    "userId": user_id,
                    "userEmail": user_email,
                    "recognizedCount": 0,
                    "successCount": 0,
                    "successRate": None,
                },
            )
            if event_name == "recognized":
                recognized_total += count
                slot["recognizedCount"] += count
            elif event_name == "success":
                success_total += count
                slot["successCount"] += count
        users: List[Dict[str, Any]] = []
        for slot in by_user.values():
            recognized = int(slot.get("recognizedCount") or 0)
            success = int(slot.get("successCount") or 0)
            slot["successRate"] = (success / recognized) if recognized > 0 else None
            users.append(slot)
        users.sort(key=lambda item: int(item.get("recognizedCount") or 0), reverse=True)
        return {
            "recognizedCount": recognized_total,
            "successCount": success_total,
            "successRate": (success_total / recognized_total) if recognized_total > 0 else None,
            "users": users,
        }

    def get_request_user_aggregates(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        sql = f"""
WITH src AS (
  SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
),
events AS (
  SELECT
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) AS user_email,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.device_class'), ''), 'unknown')) AS device_class,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM src
  WHERE payload IS NOT NULL
)
SELECT
  user_id,
  user_email,
  COUNT(*) AS request_count,
  COUNTIF(is_core) AS core_request_count,
  COUNTIF(NOT is_core) AS system_request_count,
  COUNTIF(device_class = 'desktop') AS desktop_request_count,
  COUNTIF(device_class = 'mobile') AS mobile_request_count,
  COUNTIF(device_class = 'unknown') AS unknown_request_count
FROM events
GROUP BY user_id, user_email
ORDER BY request_count DESC
"""
        return self._run_query(sql, self._window_params(window)[:3])

    def get_request_user_usage_trend(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        sql = f"""
WITH events AS (
  SELECT
    DATE(timestamp, @tz) AS event_date,
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), NULLIF(JSON_VALUE(payload, '$.user_email'), ''), 'unknown') AS user_key,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT
      timestamp,
      SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= @start_ts
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
)
SELECT
  FORMAT_DATE('%Y-%m-%d', event_date) AS date,
  COUNT(DISTINCT user_key) AS activeUserCount,
  COUNTIF(is_core) AS messageCount
FROM events
GROUP BY event_date
ORDER BY event_date ASC
"""
        return self._run_query(sql, self._window_params(window))

    def get_request_user_activity_distribution(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        sql = f"""
WITH events AS (
  SELECT
    timestamp,
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), NULLIF(JSON_VALUE(payload, '$.user_email'), ''), 'unknown') AS user_key,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT
      timestamp,
      SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
),
by_user AS (
  SELECT
    user_key,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY)) AS core_count_3d,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY)) AS core_count_7d,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)) AS core_count_14d
  FROM events
  GROUP BY user_key
),
segments AS (
  SELECT
    CASE
      WHEN core_count_3d >= 3 THEN '高アクティブ'
      WHEN core_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN core_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS label,
    COUNT(*) AS count
  FROM by_user
  GROUP BY label
),
labels AS (
  SELECT '高アクティブ' AS label UNION ALL
  SELECT '中アクティブ' UNION ALL
  SELECT '低アクティブ' UNION ALL
  SELECT '休眠ユーザー'
)
SELECT
  labels.label,
  COALESCE(segments.count, 0) AS count
FROM labels
LEFT JOIN segments USING(label)
"""
        rows = self._run_query(sql, self._window_params(window)[:3])
        total = sum(int(row.get("count") or 0) for row in rows)
        return {
            "totalUserCount": total,
            "segments": [
                {
                    "label": str(row.get("label") or ""),
                    "count": int(row.get("count") or 0),
                    "rate": (int(row.get("count") or 0) / total) if total > 0 else None,
                }
                for row in rows
            ],
        }

    def get_request_user_mode_distribution(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        sql = f"""
WITH events AS (
  SELECT
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= @start_ts
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
)
SELECT
  mode,
  COUNTIF(is_core) AS count
FROM events
WHERE mode IN ('internal', 'websearch')
GROUP BY mode
ORDER BY count DESC
"""
        return self._run_query(sql, self._window_params(window)[:3])

    def get_request_user_monitoring_rows(
        self,
        *,
        window: MetricsTimeWindow,
        activity: str = "",
        q: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if self._table_exists("monitor_user_daily"):
            return self._get_request_user_monitoring_rows_from_table(
                window=window,
                activity=activity,
                q=q,
                limit=limit,
            )

        lookup = str(q or "").strip().lower()
        activity_filter = str(activity or "").strip().lower()
        size = max(1, min(int(limit or 100), 1000))
        sql = f"""
WITH events AS (
  SELECT
    timestamp,
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) AS user_email,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT
      timestamp,
      SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
    AND NOT (
      LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), NULLIF(JSON_VALUE(payload, '$.user_email'), ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(JSON_VALUE(payload, '$.user_id'), '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(JSON_VALUE(payload, '$.user_id_hash'), '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(JSON_VALUE(payload, '$.user_id'), '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(JSON_VALUE(payload, '$.user_email'), '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(JSON_VALUE(payload, '$.user_email'), '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(JSON_VALUE(payload, '$.user_id'), ''), ' ', COALESCE(JSON_VALUE(payload, '$.user_email'), ''))), r'lcs-agent')
    )
    AND (
      @lookup = ''
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) LIKE CONCAT('%', @lookup, '%')
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) LIKE CONCAT('%', @lookup, '%')
    )
),
by_user AS (
  SELECT
    user_id,
    user_email,
    MAX(timestamp) AS last_active_at,
    COUNT(DISTINCT IF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), DATE(timestamp, @tz), NULL)) AS active_days_7,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY)) AS message_count_7d,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY)) AS message_count_3d,
    COUNTIF(is_core AND timestamp >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)) AS message_count_14d
  FROM events
  GROUP BY user_id, user_email
),
classified AS (
  SELECT
    *,
    CASE
      WHEN message_count_3d >= 3 THEN '高アクティブ'
      WHEN message_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN message_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS activity_level,
    CASE
      WHEN message_count_3d >= 3 THEN 'high'
      WHEN message_count_7d BETWEEN 1 AND 2 THEN 'middle'
      WHEN message_count_14d >= 1 THEN 'low'
      ELSE 'dormant'
    END AS activity_key
  FROM by_user
)
SELECT
  user_id,
  user_email,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', last_active_at, @tz) AS last_active_at_jst,
  active_days_7,
  message_count_7d,
  activity_level,
  activity_key,
  message_count_3d,
  message_count_14d
FROM classified
WHERE @activity = '' OR activity_key = @activity OR LOWER(activity_level) = @activity
ORDER BY last_active_at DESC
LIMIT @limit
"""
        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("lookup", "STRING", lookup),
            bigquery.ScalarQueryParameter("activity", "STRING", activity_filter),
            bigquery.ScalarQueryParameter("limit", "INT64", size),
        ]
        rows = self._run_query(sql, params)
        return [
            {
                "userId": str(row.get("user_id") or ""),
                "userEmail": str(row.get("user_email") or ""),
                "userIdHash": "",
                "lastActiveAtJst": str(row.get("last_active_at_jst") or ""),
                "activeDays7": int(row.get("active_days_7") or 0),
                "messageCount7d": int(row.get("message_count_7d") or 0),
                "coverageRate": None,
                "badFeedbackRate": None,
                "activityLevel": str(row.get("activity_level") or ""),
                "activityKey": str(row.get("activity_key") or ""),
                "messageCount3d": int(row.get("message_count_3d") or 0),
                "messageCount14d": int(row.get("message_count_14d") or 0),
            }
            for row in rows
        ]

    def _get_request_user_monitoring_rows_from_table(
        self,
        *,
        window: MetricsTimeWindow,
        activity: str = "",
        q: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        lookup = str(q or "").strip().lower()
        activity_filter = str(activity or "").strip().lower()
        size = max(1, min(int(limit or 100), 1000))
        sql = f"""
WITH events AS (
  SELECT *
  FROM {self._table("monitor_user_daily")}
  WHERE date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz)
    AND date_jst <= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)
    AND NOT (
      LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
      OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
      OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
      OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
      OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
      OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
    )
    AND (
      @lookup = ''
      OR LOWER(COALESCE(user_id, '')) LIKE CONCAT('%', @lookup, '%')
      OR LOWER(COALESCE(user_email, '')) LIKE CONCAT('%', @lookup, '%')
      OR LOWER(COALESCE(user_id_hash, '')) LIKE CONCAT('%', @lookup, '%')
    )
),
by_user AS (
  SELECT
    user_id,
    ANY_VALUE(user_email HAVING MAX last_active_at) AS user_email,
    ANY_VALUE(user_id_hash HAVING MAX last_active_at) AS user_id_hash,
    MAX(last_active_at) AS last_active_at,
    COUNT(DISTINCT IF(message_count > 0 AND date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), date_jst, NULL)) AS active_days_7,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), message_count, 0)) AS message_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY), @tz), message_count, 0)) AS message_count_3d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz), message_count, 0)) AS message_count_14d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), answer_count, 0)) AS answer_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), low_coverage_count, 0)) AS low_coverage_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), bad_feedback_count, 0)) AS bad_feedback_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), feedback_count, 0)) AS feedback_count_7d
  FROM events
  GROUP BY user_id
),
classified AS (
  SELECT
    *,
    CASE
      WHEN message_count_3d >= 3 THEN '高アクティブ'
      WHEN message_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN message_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS activity_level,
    CASE
      WHEN message_count_3d >= 3 THEN 'high'
      WHEN message_count_7d BETWEEN 1 AND 2 THEN 'middle'
      WHEN message_count_14d >= 1 THEN 'low'
      ELSE 'dormant'
    END AS activity_key
  FROM by_user
)
SELECT
  user_id,
  user_email,
  user_id_hash,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', last_active_at, @tz) AS last_active_at_jst,
  active_days_7,
  message_count_7d,
  SAFE_DIVIDE(GREATEST(answer_count_7d - low_coverage_count_7d, 0), answer_count_7d) AS coverage_rate,
  SAFE_DIVIDE(bad_feedback_count_7d, feedback_count_7d) AS bad_feedback_rate,
  activity_level,
  activity_key,
  message_count_3d,
  message_count_14d
FROM classified
WHERE @activity = '' OR activity_key = @activity OR LOWER(activity_level) = @activity
ORDER BY last_active_at DESC
LIMIT @limit
"""
        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("lookup", "STRING", lookup),
            bigquery.ScalarQueryParameter("activity", "STRING", activity_filter),
            bigquery.ScalarQueryParameter("limit", "INT64", size),
        ]
        rows = self._run_query(sql, params)
        return [
            {
                "userId": str(row.get("user_id") or ""),
                "userEmail": str(row.get("user_email") or ""),
                "userIdHash": str(row.get("user_id_hash") or ""),
                "lastActiveAtJst": str(row.get("last_active_at_jst") or ""),
                "activeDays7": int(row.get("active_days_7") or 0),
                "messageCount7d": int(row.get("message_count_7d") or 0),
                "coverageRate": row.get("coverage_rate"),
                "badFeedbackRate": row.get("bad_feedback_rate"),
                "activityLevel": str(row.get("activity_level") or ""),
                "activityKey": str(row.get("activity_key") or ""),
                "messageCount3d": int(row.get("message_count_3d") or 0),
                "messageCount14d": int(row.get("message_count_14d") or 0),
            }
            for row in rows
        ]

    def get_user_detail_summary(self, *, window: MetricsTimeWindow, user_key: str, user_keys: List[str] | None = None) -> Dict[str, Any]:
        lookup_values = {
            str(value or "").strip().lower()
            for value in ([user_key] + list(user_keys or []))
            if str(value or "").strip()
        }
        lookup_list = sorted(lookup_values)
        if not lookup_list:
            return {}
        if self._table_exists("monitor_user_daily") and self._table_exists("monitor_answer_events"):
            return self._get_user_detail_summary_from_tables(window=window, user_key=user_key, user_keys=lookup_list)

        params = self._window_params(window) + [
            bigquery.ArrayQueryParameter("user_keys", "STRING", lookup_list),
            bigquery.ScalarQueryParameter("coverage_threshold", "FLOAT64", 0.60),
        ]
        sql = f"""
WITH dates AS (
  SELECT event_date
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS event_date
),
request_events AS (
  SELECT
    event_ts,
    event_date,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(user_email, ''), '')) AS user_email,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    mode,
    COALESCE(NULLIF(device_class, ''), 'unknown') AS device_class,
    is_core
  FROM {self._view("v_request_user_metric_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_email, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
request_events_14d AS (
  SELECT
    event_ts,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(user_email, ''), '')) AS user_email,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    is_core
  FROM {self._view("v_request_user_metric_events")}
  WHERE event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_email, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
request_summary AS (
  SELECT
    COUNTIF(is_core) AS message_count,
    MAX(event_ts) AS last_active_at
  FROM request_events
),
activity AS (
  SELECT
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY)) AS message_count_3d,
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY)) AS message_count_7d,
    COUNTIF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY)) AS message_count_14d,
    COUNT(DISTINCT IF(is_core AND event_ts >= TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), DATE(event_ts, @tz), NULL)) AS active_days_7,
    MAX(event_ts) AS last_active_at_14d
  FROM request_events_14d
),
mode_counts AS (
  SELECT mode, COUNTIF(is_core) AS count
  FROM request_events
  WHERE mode IN ('internal', 'websearch')
  GROUP BY mode
),
mode_labels AS (
  SELECT 'internal' AS mode UNION ALL
  SELECT 'websearch'
),
mode_total AS (
  SELECT SUM(count) AS total_count FROM mode_counts
),
device_counts AS (
  SELECT device_class, COUNTIF(is_core) AS count
  FROM request_events
  WHERE device_class IN ('desktop', 'mobile', 'unknown')
  GROUP BY device_class
),
device_labels AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
device_total AS (
  SELECT SUM(count) AS total_count FROM device_counts
),
request_trend AS (
  SELECT
    event_date,
    COUNTIF(is_core) AS message_count
  FROM request_events
  GROUP BY event_date
),
answer_events AS (
  SELECT *
  FROM {self._view("v_ask_audit_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
coverage_gap_keys AS (
  SELECT COUNT(*) AS coverage_gap_count
  FROM {self._view("v_coverage_gap_workitems")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
answer_flags AS (
  SELECT
    a.*,
    (
      error_code IS NULL
      AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')
    ) AS answer_success_flag,
    (
      COALESCE(citation_count = 0, FALSE)
      OR evidence_sufficiency = 'insufficient'
    ) AS low_coverage_flag
  FROM answer_events a
),
answer_summary AS (
  SELECT
    COUNT(*) AS answer_count,
    COUNTIF(answer_success_flag) AS answer_success_count,
    SAFE_DIVIDE(COUNTIF(answer_success_flag), COUNT(*)) AS answer_success_rate,
    LEAST(COUNT(*), COUNTIF(low_coverage_flag) + (SELECT coverage_gap_count FROM coverage_gap_keys)) AS low_coverage_count,
    SAFE_DIVIDE(
      LEAST(COUNT(*), COUNTIF(low_coverage_flag) + (SELECT coverage_gap_count FROM coverage_gap_keys)),
      COUNT(*)
    ) AS low_coverage_rate,
    COUNTIF(COALESCE(coverage_score < 0.50, FALSE)) AS coverage_attention_count,
    SAFE_DIVIDE(COUNTIF(COALESCE(coverage_score < 0.50, FALSE)), COUNT(*)) AS coverage_attention_rate
  FROM answer_flags
),
answer_trend AS (
  SELECT
    event_date,
    SAFE_DIVIDE(COUNTIF(answer_success_flag), COUNT(*)) AS answer_success_rate,
    SAFE_DIVIDE(COUNTIF(low_coverage_flag), COUNT(*)) AS low_coverage_rate
  FROM answer_flags
  GROUP BY event_date
),
answer_distribution AS (
  SELECT 'answerability' AS metric, answerability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'usability' AS metric, usability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'deliveryReadiness' AS metric, delivery_readiness AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'evidenceSufficiency' AS metric, evidence_sufficiency AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'verificationVerdict' AS metric, verification_verdict AS label, COUNT(*) AS count FROM answer_events GROUP BY label
),
question_category_distribution AS (
  SELECT COALESCE(NULLIF(question_category, ''), 'topic_ideation') AS label, COUNT(*) AS count
  FROM answer_events
  GROUP BY label
),
followup_open AS (
  SELECT *
  FROM {self._view("v_followup_open_result_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
followup_resolution AS (
  SELECT *
  FROM {self._view("v_followup_resolution_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(NULLIF(user_id, ''), '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(NULLIF(user_id_hash, ''), '')) IN UNNEST(@user_keys)
    )
),
followup_summary AS (
  SELECT
    (SELECT COUNTIF(event = 'recognized') FROM followup_open) AS recognized_count,
    (SELECT COUNTIF(event = 'success') FROM followup_open) AS success_count,
    (SELECT COUNTIF(decision_normalized = 'explicit_correction') FROM followup_resolution) AS explicit_correction_count,
    (SELECT COUNTIF(decision_normalized = 'clarify_before_carry') FROM followup_resolution) AS clarification_required_count,
    (SELECT COUNTIF(followup_offtopic) FROM followup_resolution) AS followup_offtopic_count
),
answer_total AS (
  SELECT COUNT(*) AS total_count FROM answer_events
)
SELECT TO_JSON_STRING(STRUCT(
  STRUCT(
    COALESCE((SELECT message_count FROM request_summary), 0) AS messageCount,
    (SELECT answer_success_rate FROM answer_summary) AS answerSuccessRate,
    (SELECT low_coverage_rate FROM answer_summary) AS lowCoverageRate,
    CAST(NULL AS FLOAT64) AS badFeedbackRate,
    (SELECT recognized_count FROM followup_summary) AS followupCount,
    (SELECT answer_count FROM answer_summary) AS answerCount,
    (SELECT answer_success_count FROM answer_summary) AS answerSuccessCount,
    (SELECT low_coverage_count FROM answer_summary) AS lowCoverageCount,
    (SELECT coverage_attention_rate FROM answer_summary) AS coverageAttentionRate,
    (SELECT coverage_attention_count FROM answer_summary) AS coverageAttentionCount,
    (SELECT active_days_7 FROM activity) AS activeDays7,
    (SELECT message_count_7d FROM activity) AS messageCount7d,
    (SELECT message_count_3d FROM activity) AS messageCount3d,
    (SELECT message_count_14d FROM activity) AS messageCount14d,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', COALESCE((SELECT last_active_at_14d FROM activity), (SELECT last_active_at FROM request_summary)), @tz) AS lastActiveAtJst
  ) AS summary,
  (
    SELECT ARRAY_AGG(STRUCT(
      FORMAT_DATE('%Y-%m-%d', d.event_date) AS date,
      COALESCE(r.message_count, 0) AS messageCount,
      a.answer_success_rate AS answerSuccessRate,
      a.low_coverage_rate AS lowCoverageRate
    ) ORDER BY d.event_date)
    FROM dates d
    LEFT JOIN request_trend r USING(event_date)
    LEFT JOIN answer_trend a USING(event_date)
  ) AS trend,
  (
    SELECT ARRAY_AGG(STRUCT(
      CASE m.mode
        WHEN 'internal' THEN '社内モード'
        WHEN 'websearch' THEN 'Web検索モード'
        ELSE 'その他'
      END AS label,
      m.mode AS value,
      COALESCE(c.count, 0) AS count,
      SAFE_DIVIDE(COALESCE(c.count, 0), NULLIF((SELECT total_count FROM mode_total), 0)) AS rate
    ) ORDER BY m.mode)
    FROM mode_labels m
    LEFT JOIN mode_counts c USING(mode)
  ) AS modeDistribution,
  (
    SELECT ARRAY_AGG(STRUCT(
      CASE d.device_class
        WHEN 'desktop' THEN 'PC'
        WHEN 'mobile' THEN 'モバイル'
        ELSE '不明'
      END AS label,
      d.device_class AS value,
      COALESCE(c.count, 0) AS count,
      SAFE_DIVIDE(COALESCE(c.count, 0), NULLIF((SELECT total_count FROM device_total), 0)) AS rate
    ) ORDER BY COALESCE(c.count, 0) DESC, d.device_class)
    FROM device_labels d
    LEFT JOIN device_counts c USING(device_class)
  ) AS deviceDistribution,
  (
    SELECT ARRAY_AGG(STRUCT(
      label,
      label AS value,
      count,
      SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate
    ) ORDER BY count DESC, label)
    FROM question_category_distribution
  ) AS questionCategoryDistribution,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'answerability'
    ) AS answerability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'usability'
    ) AS usability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'deliveryReadiness'
    ) AS deliveryReadiness,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'evidenceSufficiency'
    ) AS evidenceSufficiency,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'verificationVerdict'
    ) AS verificationVerdict
  ) AS answerQualityDistribution,
  STRUCT(
    STRUCT(
      (SELECT recognized_count FROM followup_summary) AS recognizedCount,
      (SELECT success_count FROM followup_summary) AS successCount,
      SAFE_DIVIDE((SELECT success_count FROM followup_summary), NULLIF((SELECT recognized_count FROM followup_summary), 0)) AS successRate,
      (SELECT explicit_correction_count FROM followup_summary) AS explicitCorrectionCount,
      (SELECT clarification_required_count FROM followup_summary) AS clarificationRequiredCount,
      (SELECT followup_offtopic_count FROM followup_summary) AS followupOfftopicCount
    ) AS summary,
    [
      STRUCT('追問認識' AS label, (SELECT recognized_count FROM followup_summary) AS count),
      STRUCT('追問成功' AS label, (SELECT success_count FROM followup_summary) AS count),
      STRUCT('明示的な訂正' AS label, (SELECT explicit_correction_count FROM followup_summary) AS count),
      STRUCT('確認が必要な追問' AS label, (SELECT clarification_required_count FROM followup_summary) AS count)
    ] AS funnel
  ) AS followup
)) AS payload_json
"""
        rows = self._run_query(sql, params)
        if not rows:
            return {}
        raw = rows[0].get("payload_json")
        if not raw:
            return {}
        payload = json.loads(str(raw))
        summary = payload.get("summary") or {}
        message_count_3d = int(summary.get("messageCount3d") or 0)
        message_count_7d = int(summary.get("messageCount7d") or 0)
        message_count_14d = int(summary.get("messageCount14d") or 0)
        if message_count_3d >= 3:
            activity_level = "高アクティブ"
            activity_key = "high"
        elif 1 <= message_count_7d <= 2:
            activity_level = "中アクティブ"
            activity_key = "middle"
        elif message_count_14d >= 1:
            activity_level = "低アクティブ"
            activity_key = "low"
        else:
            activity_level = "休眠ユーザー"
            activity_key = "dormant"
        summary["activityLevel"] = activity_level
        summary["activityKey"] = activity_key
        payload["summary"] = summary
        return payload

    def _get_user_detail_summary_from_tables(
        self,
        *,
        window: MetricsTimeWindow,
        user_key: str,
        user_keys: List[str] | None = None,
    ) -> Dict[str, Any]:
        lookup_values = {
            str(value or "").strip().lower()
            for value in ([user_key] + list(user_keys or []))
            if str(value or "").strip()
        }
        lookup_list = sorted(lookup_values)
        if not lookup_list:
            return {}
        params = self._window_params(window) + [
            bigquery.ArrayQueryParameter("user_keys", "STRING", lookup_list),
        ]
        sql = f"""
WITH dates AS (
  SELECT event_date AS date_jst
  FROM UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(@start_ts, @tz),
      DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz),
      INTERVAL 1 DAY
    )
  ) AS event_date
),
daily_window AS (
  SELECT *
  FROM {self._table("monitor_user_daily")}
  WHERE date_jst >= DATE(@start_ts, @tz)
    AND date_jst <= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)
    AND (
      LOWER(COALESCE(user_id, '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(user_email, '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(user_id_hash, '')) IN UNNEST(@user_keys)
    )
),
daily_14d AS (
  SELECT *
  FROM {self._table("monitor_user_daily")}
  WHERE date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz)
    AND date_jst <= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)
    AND (
      LOWER(COALESCE(user_id, '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(user_email, '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(user_id_hash, '')) IN UNNEST(@user_keys)
    )
),
summary AS (
  SELECT
    SUM(message_count) AS message_count,
    SUM(answer_count) AS answer_count,
    SUM(answer_success_count) AS answer_success_count,
    SUM(low_coverage_count) AS low_coverage_count,
    SUM(coverage_attention_count) AS coverage_attention_count,
    SUM(bad_feedback_count) AS bad_feedback_count,
    SUM(feedback_count) AS feedback_count,
    SUM(followup_recognized_count) AS followup_count,
    SUM(internal_message_count) AS internal_message_count,
    SUM(websearch_message_count) AS websearch_message_count,
    SUM(desktop_request_count) AS desktop_request_count,
    SUM(mobile_request_count) AS mobile_request_count,
    SUM(unknown_request_count) AS unknown_request_count,
    SUM(followup_recognized_count) AS followup_recognized_count,
    SUM(followup_success_count) AS followup_success_count,
    SUM(explicit_correction_count) AS explicit_correction_count,
    SUM(clarification_required_count) AS clarification_required_count,
    SUM(followup_offtopic_count) AS followup_offtopic_count,
    MAX(last_active_at) AS last_active_at
  FROM daily_window
),
activity AS (
  SELECT
    COUNT(DISTINCT IF(message_count > 0 AND date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), date_jst, NULL)) AS active_days_7,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 7 DAY), @tz), message_count, 0)) AS message_count_7d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 3 DAY), @tz), message_count, 0)) AS message_count_3d,
    SUM(IF(date_jst >= DATE(TIMESTAMP_SUB(@end_ts, INTERVAL 14 DAY), @tz), message_count, 0)) AS message_count_14d,
    MAX(last_active_at) AS last_active_at_14d
  FROM daily_14d
),
trend AS (
  SELECT
    d.date_jst,
    COALESCE(SUM(w.message_count), 0) AS message_count,
    SAFE_DIVIDE(SUM(w.answer_success_count), SUM(w.answer_count)) AS answer_success_rate,
    SAFE_DIVIDE(SUM(w.low_coverage_count), SUM(w.answer_count)) AS low_coverage_rate
  FROM dates d
  LEFT JOIN daily_window w USING(date_jst)
  GROUP BY d.date_jst
),
mode_rows AS (
  SELECT 'internal' AS mode, (SELECT internal_message_count FROM summary) AS count UNION ALL
  SELECT 'websearch', (SELECT websearch_message_count FROM summary)
),
mode_total AS (
  SELECT SUM(COALESCE(count, 0)) AS total_count FROM mode_rows
),
device_rows AS (
  SELECT 'desktop' AS device_class, (SELECT desktop_request_count FROM summary) AS count UNION ALL
  SELECT 'mobile', (SELECT mobile_request_count FROM summary) UNION ALL
  SELECT 'unknown', (SELECT unknown_request_count FROM summary)
),
device_total AS (
  SELECT SUM(COALESCE(count, 0)) AS total_count FROM device_rows
),
answer_events AS (
  SELECT *
  FROM {self._table("monitor_answer_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      LOWER(COALESCE(user_id, '')) IN UNNEST(@user_keys)
      OR LOWER(COALESCE(user_id_hash, '')) IN UNNEST(@user_keys)
    )
),
answer_distribution AS (
  SELECT 'answerability' AS metric, answerability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'usability' AS metric, usability_level AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'deliveryReadiness' AS metric, delivery_readiness AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'evidenceSufficiency' AS metric, evidence_sufficiency AS label, COUNT(*) AS count FROM answer_events GROUP BY label
  UNION ALL
  SELECT 'verificationVerdict' AS metric, verification_verdict AS label, COUNT(*) AS count FROM answer_events GROUP BY label
),
question_category_distribution AS (
  SELECT COALESCE(NULLIF(question_category, ''), 'topic_ideation') AS label, COUNT(*) AS count
  FROM answer_events
  GROUP BY label
),
answer_total AS (
  SELECT COUNT(*) AS total_count FROM answer_events
)
SELECT TO_JSON_STRING(STRUCT(
  STRUCT(
    COALESCE((SELECT message_count FROM summary), 0) AS messageCount,
    SAFE_DIVIDE((SELECT answer_success_count FROM summary), (SELECT answer_count FROM summary)) AS answerSuccessRate,
    SAFE_DIVIDE((SELECT low_coverage_count FROM summary), (SELECT answer_count FROM summary)) AS lowCoverageRate,
    SAFE_DIVIDE((SELECT bad_feedback_count FROM summary), (SELECT feedback_count FROM summary)) AS badFeedbackRate,
    COALESCE((SELECT followup_count FROM summary), 0) AS followupCount,
    COALESCE((SELECT answer_count FROM summary), 0) AS answerCount,
    COALESCE((SELECT answer_success_count FROM summary), 0) AS answerSuccessCount,
    COALESCE((SELECT low_coverage_count FROM summary), 0) AS lowCoverageCount,
    SAFE_DIVIDE((SELECT coverage_attention_count FROM summary), (SELECT answer_count FROM summary)) AS coverageAttentionRate,
    COALESCE((SELECT coverage_attention_count FROM summary), 0) AS coverageAttentionCount,
    COALESCE((SELECT active_days_7 FROM activity), 0) AS activeDays7,
    COALESCE((SELECT message_count_7d FROM activity), 0) AS messageCount7d,
    COALESCE((SELECT message_count_3d FROM activity), 0) AS messageCount3d,
    COALESCE((SELECT message_count_14d FROM activity), 0) AS messageCount14d,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', COALESCE((SELECT last_active_at_14d FROM activity), (SELECT last_active_at FROM summary)), @tz) AS lastActiveAtJst
  ) AS summary,
  (
    SELECT ARRAY_AGG(STRUCT(
      FORMAT_DATE('%Y-%m-%d', date_jst) AS date,
      message_count AS messageCount,
      answer_success_rate AS answerSuccessRate,
      low_coverage_rate AS lowCoverageRate
    ) ORDER BY date_jst)
    FROM trend
  ) AS trend,
  (
    SELECT ARRAY_AGG(STRUCT(
      CASE mode
        WHEN 'internal' THEN '社内モード'
        WHEN 'websearch' THEN 'Web検索モード'
        ELSE 'その他'
      END AS label,
      mode AS value,
      COALESCE(count, 0) AS count,
      SAFE_DIVIDE(COALESCE(count, 0), NULLIF((SELECT total_count FROM mode_total), 0)) AS rate
    ) ORDER BY mode)
    FROM mode_rows
  ) AS modeDistribution,
  (
    SELECT ARRAY_AGG(STRUCT(
      CASE device_class
        WHEN 'desktop' THEN 'PC'
        WHEN 'mobile' THEN 'モバイル'
        ELSE '不明'
      END AS label,
      device_class AS value,
      COALESCE(count, 0) AS count,
      SAFE_DIVIDE(COALESCE(count, 0), NULLIF((SELECT total_count FROM device_total), 0)) AS rate
    ) ORDER BY COALESCE(count, 0) DESC, device_class)
    FROM device_rows
  ) AS deviceDistribution,
  (
    SELECT ARRAY_AGG(STRUCT(
      label,
      label AS value,
      count,
      SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate
    ) ORDER BY count DESC, label)
    FROM question_category_distribution
  ) AS questionCategoryDistribution,
  STRUCT(
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'answerability'
    ) AS answerability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'usability'
    ) AS usability,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'deliveryReadiness'
    ) AS deliveryReadiness,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'evidenceSufficiency'
    ) AS evidenceSufficiency,
    (
      SELECT ARRAY_AGG(STRUCT(label, count, SAFE_DIVIDE(count, NULLIF((SELECT total_count FROM answer_total), 0)) AS rate) ORDER BY count DESC, label)
      FROM answer_distribution WHERE metric = 'verificationVerdict'
    ) AS verificationVerdict
  ) AS answerQualityDistribution,
  STRUCT(
    STRUCT(
      COALESCE((SELECT followup_recognized_count FROM summary), 0) AS recognizedCount,
      COALESCE((SELECT followup_success_count FROM summary), 0) AS successCount,
      SAFE_DIVIDE((SELECT followup_success_count FROM summary), (SELECT followup_recognized_count FROM summary)) AS successRate,
      COALESCE((SELECT explicit_correction_count FROM summary), 0) AS explicitCorrectionCount,
      COALESCE((SELECT clarification_required_count FROM summary), 0) AS clarificationRequiredCount,
      COALESCE((SELECT followup_offtopic_count FROM summary), 0) AS followupOfftopicCount
    ) AS summary,
    [
      STRUCT('追問認識' AS label, COALESCE((SELECT followup_recognized_count FROM summary), 0) AS count),
      STRUCT('追問成功' AS label, COALESCE((SELECT followup_success_count FROM summary), 0) AS count),
      STRUCT('明示的な訂正' AS label, COALESCE((SELECT explicit_correction_count FROM summary), 0) AS count),
      STRUCT('確認が必要な追問' AS label, COALESCE((SELECT clarification_required_count FROM summary), 0) AS count)
    ] AS funnel
  ) AS followup
)) AS payload_json
"""
        rows = self._run_query(sql, params)
        if not rows:
            return {}
        raw = rows[0].get("payload_json")
        if not raw:
            return {}
        payload = json.loads(str(raw))
        summary = payload.get("summary") or {}
        message_count_3d = int(summary.get("messageCount3d") or 0)
        message_count_7d = int(summary.get("messageCount7d") or 0)
        message_count_14d = int(summary.get("messageCount14d") or 0)
        if message_count_3d >= 3:
            activity_level = "高アクティブ"
            activity_key = "high"
        elif 1 <= message_count_7d <= 2:
            activity_level = "中アクティブ"
            activity_key = "middle"
        elif message_count_14d >= 1:
            activity_level = "低アクティブ"
            activity_key = "low"
        else:
            activity_level = "休眠ユーザー"
            activity_key = "dormant"
        summary["activityLevel"] = activity_level
        summary["activityKey"] = activity_key
        payload["summary"] = summary
        return payload

    def get_request_user_timeseries(self, *, window: MetricsTimeWindow, user_key: str) -> List[Dict[str, Any]]:
        lookup = str(user_key or "").strip()
        if not lookup:
            return []
        lookup_lower = lookup.lower()

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
events AS (
  SELECT
    DATE(timestamp, @tz) AS bucket_day,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.device_class'), ''), 'unknown')) AS device_class,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT
      timestamp,
      SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= @start_ts
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
    AND (
      LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) = @user_key
    )
),
agg AS (
  SELECT
    bucket_day,
    device_class,
    COUNT(*) AS request_count,
    COUNTIF(is_core) AS core_request_count
  FROM events
  GROUP BY bucket_day, device_class
)
SELECT
  FORMAT_DATE('%Y-%m-%d', g.bucket_day) AS bucket_key,
  FORMAT_DATE('%m-%d', g.bucket_day) AS bucket_label,
  d.device_class,
  COALESCE(a.request_count, 0) AS request_count,
  COALESCE(a.core_request_count, 0) AS core_request_count,
  GREATEST(COALESCE(a.request_count, 0) - COALESCE(a.core_request_count, 0), 0) AS system_request_count
FROM grid g
CROSS JOIN devices d
LEFT JOIN agg a ON a.bucket_day = g.bucket_day AND a.device_class = d.device_class
ORDER BY g.bucket_day ASC, d.device_class ASC
"""
            params = self._window_params(window) + [
                bigquery.ScalarQueryParameter("user_key", "STRING", lookup_lower),
            ]
            return self._run_query(sql, params)

        label_format = "%H:%M" if window.duration_seconds <= 24 * 60 * 60 else "%m-%d %H:%M"
        sql = f"""
WITH devices AS (
  SELECT 'desktop' AS device_class UNION ALL
  SELECT 'mobile' UNION ALL
  SELECT 'unknown'
),
bounds AS (
  SELECT
    DATETIME_TRUNC(DATETIME(@start_ts, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(@start_ts, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_start_local,
    DATETIME_TRUNC(DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(TIMESTAMP_SUB(@end_ts, INTERVAL 1 SECOND), @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_end_local
),
grid AS (
  SELECT DATETIME_ADD(bucket_start_local, INTERVAL offset_minutes MINUTE) AS bucket_local
  FROM bounds,
  UNNEST(
    GENERATE_ARRAY(
      0,
      DATETIME_DIFF(bucket_end_local, bucket_start_local, MINUTE),
      @bucket_minutes
    )
  ) AS offset_minutes
),
events AS (
  SELECT
    DATETIME_TRUNC(DATETIME(timestamp, @tz), HOUR)
    + INTERVAL (DIV(EXTRACT(MINUTE FROM DATETIME(timestamp, @tz)), @bucket_minutes) * @bucket_minutes) MINUTE AS bucket_local,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.device_class'), ''), 'unknown')) AS device_class,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core
  FROM (
    SELECT
      timestamp,
      SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload
    FROM {self._stdout_table()}
    WHERE resource.type = 'cloud_run_revision'
      AND resource.labels.service_name = @service_name
      AND timestamp >= @start_ts
      AND timestamp < @end_ts
      AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
  )
  WHERE payload IS NOT NULL
    AND (
      LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_email'), ''), '')) = @user_key
    )
),
agg AS (
  SELECT
    bucket_local,
    device_class,
    COUNT(*) AS request_count,
    COUNTIF(is_core) AS core_request_count
  FROM events
  GROUP BY bucket_local, device_class
)
SELECT
  FORMAT_DATETIME('%Y-%m-%d %H:%M', g.bucket_local) AS bucket_key,
  FORMAT_DATETIME(@label_format, g.bucket_local) AS bucket_label,
  d.device_class,
  COALESCE(a.request_count, 0) AS request_count,
  COALESCE(a.core_request_count, 0) AS core_request_count,
  GREATEST(COALESCE(a.request_count, 0) - COALESCE(a.core_request_count, 0), 0) AS system_request_count
FROM grid g
CROSS JOIN devices d
LEFT JOIN agg a ON a.bucket_local = g.bucket_local AND a.device_class = d.device_class
ORDER BY g.bucket_local ASC, d.device_class ASC
"""
        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("bucket_minutes", "INT64", int(window.bucket_minutes)),
            bigquery.ScalarQueryParameter("label_format", "STRING", label_format),
            bigquery.ScalarQueryParameter("user_key", "STRING", lookup_lower),
        ]
        return self._run_query(sql, params)

    def get_request_hour_distribution(self, *, window: MetricsTimeWindow) -> List[Dict[str, Any]]:
        sql = f"""
WITH hours AS (
  SELECT hour
  FROM UNNEST(GENERATE_ARRAY(0, 23)) AS hour
),
req AS (
  SELECT EXTRACT(HOUR FROM DATETIME(timestamp, @tz)) AS hour
  FROM {self._requests_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
)
SELECT
  FORMAT('%02d:00', h.hour) AS hour,
  COALESCE(COUNT(r.hour), 0) AS request_count
FROM hours h
LEFT JOIN req r ON r.hour = h.hour
GROUP BY h.hour
ORDER BY h.hour ASC
"""
        return self._run_query(sql, self._window_params(window))

    def get_answer_quality_metrics(
        self,
        *,
        window: MetricsTimeWindow,
        user_key: str = "",
    ) -> Dict[str, Any]:
        if self._table_exists("monitor_answer_events"):
            return self._get_answer_quality_metrics_from_table(window=window, user_key=user_key)

        lookup = str(user_key or "").strip().lower()
        params = self._window_params(window)[:3] + [
            bigquery.ScalarQueryParameter("user_key", "STRING", lookup),
            bigquery.ScalarQueryParameter("coverage_threshold", "FLOAT64", 0.60),
        ]
        base_cte = f"""
WITH src AS (
  SELECT
    timestamp AS ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^ask_audit_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^ask_audit_json=")
),
events AS (
  SELECT
    COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), NULLIF(JSON_VALUE(payload, '$.user_id_hash'), ''), 'unknown') AS user_key_value,
    COALESCE(NULLIF(JSON_VALUE(payload, '$.ask_audit_schema_version'), ''), 'unknown') AS schema_version,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.answerability_level'), ''), NULLIF(JSON_VALUE(payload, '$.governance.answerability_level'), ''), 'unknown')) AS answerability_level,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.usability_level'), ''), NULLIF(JSON_VALUE(payload, '$.governance.usability_level'), ''), 'unknown')) AS usability_level,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.delivery_readiness'), ''), NULLIF(JSON_VALUE(payload, '$.governance.delivery_readiness'), ''), 'unknown')) AS delivery_readiness,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.evidence_sufficiency'), ''), NULLIF(JSON_VALUE(payload, '$.governance.evidence_sufficiency'), ''), 'unknown')) AS evidence_sufficiency,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.survivable_telemetry.verification_verdict'), ''), NULLIF(JSON_VALUE(payload, '$.verification_verdict'), ''), 'unknown')) AS verification_verdict,
    SAFE_CAST(COALESCE(JSON_VALUE(payload, '$.coverage_score'), JSON_VALUE(payload, '$.survivable_telemetry.coverage_score')) AS FLOAT64) AS coverage_score,
    SAFE_CAST(COALESCE(JSON_VALUE(payload, '$.alignment_score'), JSON_VALUE(payload, '$.survivable_telemetry.alignment_score')) AS FLOAT64) AS alignment_score,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.structured_led') AS BOOL), FALSE) AS structured_led,
    SAFE_CAST(JSON_VALUE(payload, '$.citation_count') AS INT64) AS citation_count,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.claim_alignment_fallback') AS BOOL), FALSE) AS claim_alignment_fallback,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.citation_mapping_source'), ''), 'unknown')) AS citation_mapping_source,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.primary_reason_code'), ''), NULLIF(JSON_VALUE(payload, '$.governance.primary_reason_code'), ''), 'unknown')) AS primary_reason_code,
    NULLIF(JSON_VALUE(payload, '$.error_code'), '') AS error_code
  FROM src
  WHERE payload IS NOT NULL
    AND (
      @user_key = ''
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id_hash'), ''), '')) = @user_key
    )
)
"""
        summary_sql = f"""
{base_cte}
SELECT
  COUNT(*) AS answer_count,
  COUNTIF(
    error_code IS NULL
    AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')
  ) AS answer_success_count,
  SAFE_DIVIDE(
    COUNTIF(
      error_code IS NULL
      AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')
    ),
    COUNT(*)
  ) AS answer_success_rate,
  COUNTIF(
    COALESCE(citation_count = 0, FALSE)
    OR evidence_sufficiency = 'insufficient'
  ) AS low_coverage_count,
  SAFE_DIVIDE(
    COUNTIF(
      COALESCE(citation_count = 0, FALSE)
      OR evidence_sufficiency = 'insufficient'
    ),
    COUNT(*)
  ) AS low_coverage_rate,
  COUNTIF(COALESCE(coverage_score < 0.50, FALSE)) AS coverage_attention_count,
  SAFE_DIVIDE(COUNTIF(COALESCE(coverage_score < 0.50, FALSE)), COUNT(*)) AS coverage_attention_rate,
  AVG(coverage_score) AS average_coverage_score,
  AVG(alignment_score) AS average_alignment_score,
  COUNTIF(structured_led) AS structured_led_count,
  SAFE_DIVIDE(COUNTIF(structured_led), COUNT(*)) AS structured_led_rate,
  COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy') AS citation_binding_issue_count,
  SAFE_DIVIDE(COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy'), COUNT(*)) AS citation_binding_issue_rate
FROM events
"""
        distribution_sql = f"""
{base_cte}
SELECT 'answerability' AS metric, answerability_level AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'usability' AS metric, usability_level AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'deliveryReadiness' AS metric, delivery_readiness AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'evidenceSufficiency' AS metric, evidence_sufficiency AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'verificationVerdict' AS metric, verification_verdict AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'riskReasons' AS metric, primary_reason_code AS label, COUNT(*) AS count FROM events GROUP BY label
"""
        gap_sql = f"""
WITH src AS (
  SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^coverage_gap_workitem_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^coverage_gap_workitem_json=")
),
events AS (
  SELECT
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.gap_kind'), ''), 'unknown')) AS gap_kind
  FROM src
  WHERE payload IS NOT NULL
    AND (
      @user_key = ''
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id_hash'), ''), '')) = @user_key
    )
)
SELECT gap_kind, COUNT(*) AS count
FROM events
GROUP BY gap_kind
ORDER BY count DESC
LIMIT 30
"""
        summary_rows = self._run_query(summary_sql, params)
        distribution_rows = self._run_query(distribution_sql, params)
        gap_rows = self._run_query(gap_sql, params)
        summary = summary_rows[0] if summary_rows else {}
        answer_count = int(summary.get("answer_count") or 0)

        distributions: Dict[str, List[Dict[str, Any]]] = {
            "answerability": [],
            "usability": [],
            "deliveryReadiness": [],
            "evidenceSufficiency": [],
            "verificationVerdict": [],
        }
        risk_reasons: List[Dict[str, Any]] = []
        for row in distribution_rows:
            metric = str(row.get("metric") or "")
            label = str(row.get("label") or "unknown")
            count = int(row.get("count") or 0)
            item = {
                "label": label,
                "count": count,
                "rate": (count / answer_count) if answer_count > 0 else None,
            }
            if metric == "riskReasons":
                if label != "unknown":
                    risk_reasons.append(item)
            elif metric in distributions:
                distributions[metric].append(item)

        coverage_gap_count = sum(int(row.get("count") or 0) for row in gap_rows)
        return {
            "summary": {
                "answerCount": answer_count,
                "answerSuccessRate": summary.get("answer_success_rate"),
                "answerSuccessCount": int(summary.get("answer_success_count") or 0),
                "lowCoverageRate": summary.get("low_coverage_rate"),
                "lowCoverageCount": int(summary.get("low_coverage_count") or 0),
                "coverageAttentionRate": summary.get("coverage_attention_rate"),
                "coverageAttentionCount": int(summary.get("coverage_attention_count") or 0),
                "coverageGapWorkitemCount": coverage_gap_count,
                "averageCoverageScore": summary.get("average_coverage_score"),
                "averageAlignmentScore": summary.get("average_alignment_score"),
                "structuredLedCount": int(summary.get("structured_led_count") or 0),
                "structuredLedRate": summary.get("structured_led_rate"),
                "citationBindingIssueCount": int(summary.get("citation_binding_issue_count") or 0),
                "citationBindingIssueRate": summary.get("citation_binding_issue_rate"),
            },
            "distributions": distributions,
            "riskReasons": risk_reasons,
            "coverageGapKinds": [
                {
                    "label": str(row.get("gap_kind") or "unknown"),
                    "count": int(row.get("count") or 0),
                    "rate": (int(row.get("count") or 0) / coverage_gap_count) if coverage_gap_count > 0 else None,
                }
                for row in gap_rows
            ],
        }

    def _get_answer_quality_metrics_from_table(
        self,
        *,
        window: MetricsTimeWindow,
        user_key: str = "",
    ) -> Dict[str, Any]:
        lookup = str(user_key or "").strip().lower()
        params = self._window_params(window)[:3] + [
            bigquery.ScalarQueryParameter("user_key", "STRING", lookup),
        ]
        base_cte = f"""
WITH events AS (
  SELECT *
  FROM {self._table("monitor_answer_events")}
  WHERE event_ts >= @start_ts
    AND event_ts < @end_ts
    AND (
      @user_key = ''
      OR LOWER(COALESCE(user_id, '')) = @user_key
      OR LOWER(COALESCE(user_id_hash, '')) = @user_key
    )
)
"""
        summary_sql = f"""
{base_cte}
SELECT
  COUNT(*) AS answer_count,
  COUNTIF(answer_success_flag) AS answer_success_count,
  SAFE_DIVIDE(COUNTIF(answer_success_flag), COUNT(*)) AS answer_success_rate,
  COUNTIF(low_coverage_flag) AS low_coverage_count,
  SAFE_DIVIDE(COUNTIF(low_coverage_flag), COUNT(*)) AS low_coverage_rate,
  AVG(coverage_score) AS average_coverage_score,
  AVG(alignment_score) AS average_alignment_score,
  COUNTIF(structured_led) AS structured_led_count,
  SAFE_DIVIDE(COUNTIF(structured_led), COUNT(*)) AS structured_led_rate,
  COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy') AS citation_binding_issue_count,
  SAFE_DIVIDE(COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy'), COUNT(*)) AS citation_binding_issue_rate
FROM events
"""
        distribution_sql = f"""
{base_cte}
SELECT 'answerability' AS metric, answerability_level AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'usability' AS metric, usability_level AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'deliveryReadiness' AS metric, delivery_readiness AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'evidenceSufficiency' AS metric, evidence_sufficiency AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'verificationVerdict' AS metric, verification_verdict AS label, COUNT(*) AS count FROM events GROUP BY label
UNION ALL
SELECT 'riskReasons' AS metric, primary_reason_code AS label, COUNT(*) AS count FROM events GROUP BY label
"""
        gap_sql = f"""
SELECT gap_kind, COUNT(*) AS count
FROM {self._view("v_coverage_gap_workitems")}
WHERE event_ts >= @start_ts
  AND event_ts < @end_ts
  AND (
    @user_key = ''
    OR LOWER(COALESCE(user_id, '')) = @user_key
    OR LOWER(COALESCE(user_id_hash, '')) = @user_key
  )
GROUP BY gap_kind
ORDER BY count DESC
LIMIT 30
"""
        summary_rows = self._run_query(summary_sql, params)
        distribution_rows = self._run_query(distribution_sql, params)
        gap_rows = self._run_query(gap_sql, params)
        summary = summary_rows[0] if summary_rows else {}
        answer_count = int(summary.get("answer_count") or 0)

        distributions: Dict[str, List[Dict[str, Any]]] = {
            "answerability": [],
            "usability": [],
            "deliveryReadiness": [],
            "evidenceSufficiency": [],
            "verificationVerdict": [],
        }
        risk_reasons: List[Dict[str, Any]] = []
        for row in distribution_rows:
            metric = str(row.get("metric") or "")
            label = str(row.get("label") or "unknown")
            count = int(row.get("count") or 0)
            item = {
                "label": label,
                "count": count,
                "rate": (count / answer_count) if answer_count > 0 else None,
            }
            if metric == "riskReasons":
                if label != "unknown":
                    risk_reasons.append(item)
            elif metric in distributions:
                distributions[metric].append(item)

        coverage_gap_count = sum(int(row.get("count") or 0) for row in gap_rows)
        return {
            "summary": {
                "answerCount": answer_count,
                "answerSuccessRate": summary.get("answer_success_rate"),
                "answerSuccessCount": int(summary.get("answer_success_count") or 0),
                "lowCoverageRate": summary.get("low_coverage_rate"),
                "lowCoverageCount": int(summary.get("low_coverage_count") or 0),
                "coverageGapWorkitemCount": coverage_gap_count,
                "averageCoverageScore": summary.get("average_coverage_score"),
                "averageAlignmentScore": summary.get("average_alignment_score"),
                "structuredLedCount": int(summary.get("structured_led_count") or 0),
                "structuredLedRate": summary.get("structured_led_rate"),
                "citationBindingIssueCount": int(summary.get("citation_binding_issue_count") or 0),
                "citationBindingIssueRate": summary.get("citation_binding_issue_rate"),
            },
            "distributions": distributions,
            "riskReasons": risk_reasons,
            "coverageGapKinds": [
                {
                    "label": str(row.get("gap_kind") or "unknown"),
                    "count": int(row.get("count") or 0),
                    "rate": (int(row.get("count") or 0) / coverage_gap_count) if coverage_gap_count > 0 else None,
                }
                for row in gap_rows
            ],
        }

    def get_followup_metrics(
        self,
        *,
        window: MetricsTimeWindow,
        user_key: str = "",
    ) -> Dict[str, Any]:
        lookup = str(user_key or "").strip().lower()
        params = self._window_params(window)[:3] + [
            bigquery.ScalarQueryParameter("user_key", "STRING", lookup),
        ]
        sql = f"""
WITH open_src AS (
  SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_open_result_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^followup_open_result_json=")
),
open_events AS (
  SELECT
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.event'), ''), 'unknown')) AS event_name,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.state_action'), ''), 'unknown')) AS state_action,
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.error_code'), ''), '')) AS error_code
  FROM open_src
  WHERE payload IS NOT NULL
    AND (
      @user_key = ''
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id_hash'), ''), '')) = @user_key
    )
),
resolution_src AS (
  SELECT SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_resolution_json=(.*)$")) AS payload
  FROM {self._stdout_table()}
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = @service_name
    AND timestamp >= @start_ts
    AND timestamp < @end_ts
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^followup_resolution_json=")
),
resolution_events AS (
  SELECT
    LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.decision_normalized'), ''), NULLIF(JSON_VALUE(payload, '$.decision'), ''), 'unknown')) AS decision,
    COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.followup_offtopic') AS BOOL), FALSE) AS followup_offtopic,
    JSON_VALUE_ARRAY(payload, '$.reason_codes') AS reason_codes
  FROM resolution_src
  WHERE payload IS NOT NULL
    AND (
      @user_key = ''
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id'), ''), '')) = @user_key
      OR LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.user_id_hash'), ''), '')) = @user_key
    )
),
reason_rows AS (
  SELECT LOWER(COALESCE(NULLIF(reason, ''), 'unknown')) AS reason
  FROM resolution_events, UNNEST(COALESCE(reason_codes, ['unknown'])) AS reason
)
SELECT 'open_event' AS kind, event_name AS value, COUNT(*) AS count FROM open_events GROUP BY value
UNION ALL
SELECT 'decision' AS kind, decision AS value, COUNT(*) AS count FROM resolution_events GROUP BY value
UNION ALL
SELECT 'reason' AS kind, reason AS value, COUNT(*) AS count FROM reason_rows GROUP BY value
UNION ALL
SELECT 'state_action' AS kind, state_action AS value, COUNT(*) AS count FROM open_events GROUP BY value
UNION ALL
SELECT 'offtopic' AS kind, CAST(followup_offtopic AS STRING) AS value, COUNT(*) AS count FROM resolution_events GROUP BY value
"""
        rows = self._run_query(sql, params)
        by_kind: Dict[str, Dict[str, int]] = {}
        for row in rows:
            kind = str(row.get("kind") or "")
            value = str(row.get("value") or "unknown")
            count = int(row.get("count") or 0)
            by_kind.setdefault(kind, {})[value] = count

        open_events = by_kind.get("open_event", {})
        decisions = by_kind.get("decision", {})
        recognized = int(open_events.get("recognized", 0))
        success = int(open_events.get("success", 0))
        explicit_correction = int(decisions.get("explicit_correction", 0))
        clarification = int(decisions.get("clarify_before_carry", 0))
        offtopic = int(by_kind.get("offtopic", {}).get("true", 0))
        reason_total = sum(by_kind.get("reason", {}).values())
        state_total = sum(by_kind.get("state_action", {}).values())
        return {
            "summary": {
                "recognizedCount": recognized,
                "successCount": success,
                "successRate": (success / recognized) if recognized > 0 else None,
                "explicitCorrectionCount": explicit_correction,
                "clarificationRequiredCount": clarification,
                "followupOfftopicCount": offtopic,
            },
            "funnel": [
                {"label": "追問認識", "count": recognized},
                {"label": "追問成功", "count": success},
                {"label": "明示的な訂正", "count": explicit_correction},
                {"label": "確認が必要な追問", "count": clarification},
            ],
            "reasonBreakdown": [
                {
                    "label": "理由シグナル",
                    "value": value,
                    "count": count,
                    "rate": (count / reason_total) if reason_total > 0 else None,
                }
                for value, count in sorted(by_kind.get("reason", {}).items(), key=lambda item: item[1], reverse=True)
                if value != "unknown"
            ],
            "stateActionBreakdown": [
                {
                    "label": value,
                    "count": count,
                    "rate": (count / state_total) if state_total > 0 else None,
                }
                for value, count in sorted(by_kind.get("state_action", {}).items(), key=lambda item: item[1], reverse=True)
                if value != "unknown"
            ],
        }

    def get_schema_health_metrics(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        sql = f"""
WITH families AS (
  SELECT 'ask_audit_json' AS event_family, r"^ask_audit_json=(.*)$" AS pattern UNION ALL
  SELECT 'followup_resolution_json', r"^followup_resolution_json=(.*)$" UNION ALL
  SELECT 'followup_open_result_json', r"^followup_open_result_json=(.*)$" UNION ALL
  SELECT 'coverage_gap_workitem_json', r"^coverage_gap_workitem_json=(.*)$" UNION ALL
  SELECT 'answer_action_json', r"^answer_action_json=(.*)$"
),
src AS (
  SELECT
    f.event_family,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(s.textPayload AS STRING), f.pattern)) AS payload
  FROM {self._stdout_table()} s
  JOIN families f ON REGEXP_CONTAINS(CAST(s.textPayload AS STRING), REGEXP_REPLACE(f.pattern, r"\(\.\*\)\$", ""))
  WHERE s.resource.type = 'cloud_run_revision'
    AND s.resource.labels.service_name = @service_name
    AND s.timestamp >= @start_ts
    AND s.timestamp < @end_ts
),
events AS (
  SELECT
    event_family,
    COALESCE(
      NULLIF(JSON_VALUE(payload, '$.ask_audit_schema_version'), ''),
      NULLIF(JSON_VALUE(payload, '$.followup_resolution_schema_version'), ''),
      NULLIF(JSON_VALUE(payload, '$.followup_open_result_schema_version'), ''),
      NULLIF(JSON_VALUE(payload, '$.coverage_gap_workitem_schema_version'), ''),
      'unknown'
    ) AS schema_version,
    payload
  FROM src
)
SELECT
  event_family,
  schema_version,
  COUNT(*) AS event_count,
  COUNTIF(payload IS NULL) AS schema_mismatch_count,
  COUNTIF(
    COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), '') = ''
    OR COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), '') = ''
    OR COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '') = ''
  ) AS required_field_missing_count,
  COUNTIF(COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '') != '') AS joined_message_id_count,
  COUNTIF(COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '') = '') AS missing_message_id_count
FROM events
GROUP BY event_family, schema_version
ORDER BY event_family, schema_version
"""
        rows = self._run_query(sql, self._window_params(window)[:3])
        answer_rows = sum(int(row.get("event_count") or 0) for row in rows if row.get("event_family") == "ask_audit_json")
        joined_message = sum(
            int(row.get("joined_message_id_count") or 0)
            for row in rows
            if row.get("event_family") == "ask_audit_json"
        )
        followup_unjoined = sum(
            int(row.get("required_field_missing_count") or 0)
            for row in rows
            if row.get("event_family") in {"followup_resolution_json", "followup_open_result_json"}
        )
        coverage_rows = sum(
            int(row.get("event_count") or 0)
            for row in rows
            if row.get("event_family") == "coverage_gap_workitem_json"
        )
        coverage_joined = sum(
            int(row.get("joined_message_id_count") or 0)
            for row in rows
            if row.get("event_family") == "coverage_gap_workitem_json"
        )
        return {
            "events": [
                {
                    "eventFamily": str(row.get("event_family") or "unknown"),
                    "schemaVersion": str(row.get("schema_version") or "unknown"),
                    "eventCount": int(row.get("event_count") or 0),
                    "requiredFieldMissingCount": int(row.get("required_field_missing_count") or 0),
                    "schemaMismatchCount": int(row.get("schema_mismatch_count") or 0),
                    "missingMessageIdCount": int(row.get("missing_message_id_count") or 0),
                }
                for row in rows
            ],
            "joinHealth": {
                "answerRowCount": answer_rows,
                "joinedMessageCount": joined_message,
                "joinRate": (joined_message / answer_rows) if answer_rows > 0 else None,
                "followupUnjoinedCount": followup_unjoined,
                "coverageGapJoinRate": (coverage_joined / coverage_rows) if coverage_rows > 0 else None,
            },
            "dataDelay": {
                "p95Sec": None,
            },
        }

    def search_trace_payloads(
        self,
        *,
        window: MetricsTimeWindow,
        conversation_id: str = "",
        trace_id: str = "",
        turn_id: str = "",
        user_id: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        size = max(1, min(int(limit or 50), 500))
        sql = f"""
SELECT
  event_ts,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', event_ts, @tz) AS event_ts_jst,
  event_family,
  schema_version,
  trace_id,
  request_id,
  conversation_id,
  session_id,
  turn_id,
  parent_turn_id,
  message_id,
  user_id,
  user_id_hash,
  mode,
  intent_family,
  question_category,
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `{self._project}.{self._dataset}.v_monitor_event_message_join_keys`
WHERE event_ts >= @start_ts
  AND event_ts < @end_ts
  AND (@conversation_id = '' OR conversation_id = @conversation_id)
  AND (@trace_id = '' OR trace_id = @trace_id)
  AND (@turn_id = '' OR turn_id = @turn_id)
  AND (@user_id = '' OR user_id = @user_id OR user_id_hash = @user_id)
ORDER BY event_ts DESC
LIMIT @limit
"""
        params = self._window_params(window) + [
            bigquery.ScalarQueryParameter("conversation_id", "STRING", str(conversation_id or "").strip()),
            bigquery.ScalarQueryParameter("trace_id", "STRING", str(trace_id or "").strip()),
            bigquery.ScalarQueryParameter("turn_id", "STRING", str(turn_id or "").strip()),
            bigquery.ScalarQueryParameter("user_id", "STRING", str(user_id or "").strip()),
            bigquery.ScalarQueryParameter("limit", "INT64", size),
        ]
        rows = self._run_query(sql, params)
        return [
            {
                "eventTs": row.get("event_ts").isoformat() if hasattr(row.get("event_ts"), "isoformat") else row.get("event_ts"),
                "eventTsJst": str(row.get("event_ts_jst") or ""),
                "eventFamily": str(row.get("event_family") or ""),
                "schemaVersion": str(row.get("schema_version") or ""),
                "traceId": str(row.get("trace_id") or ""),
                "requestId": str(row.get("request_id") or ""),
                "conversationId": str(row.get("conversation_id") or ""),
                "sessionId": str(row.get("session_id") or ""),
                "turnId": str(row.get("turn_id") or ""),
                "parentTurnId": str(row.get("parent_turn_id") or ""),
                "messageId": str(row.get("message_id") or ""),
                "userId": str(row.get("user_id") or ""),
                "userIdHash": str(row.get("user_id_hash") or ""),
                "mode": str(row.get("mode") or ""),
                "intentFamily": str(row.get("intent_family") or ""),
                "questionCategory": str(row.get("question_category") or ""),
                "conversationTurnKey": str(row.get("conversation_turn_key") or ""),
                "conversationMessageKey": str(row.get("conversation_message_key") or ""),
                "traceRequestKey": str(row.get("trace_request_key") or ""),
            }
            for row in rows
        ]
