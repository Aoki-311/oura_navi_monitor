CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.dashboard_overview` AS
SELECT
  q.event_id AS question_event_id,
  q.question_ts,
  q.question_date,
  q.roster_id,
  scope.area_key,
  scope.area,
  scope.role,
  scope.department,
  q.valid_question,
  q.mode,
  q.device_class,
  q.primary_question_category,
  q.question_categories,
  q.classification_status,
  q.is_multi_intent,
  q.analytics_tasks,
  q.primary_product_key,
  q.primary_product_name,
  q.product_keys,
  q.product_names,
  q.product_candidate_count,
  q.product_resolved_count,
  a.measurement_available,
  a.complete_delivery,
  a.primary_failure_reason,
  a.total_latency_ms
FROM `${PROJECT_ID}.${DATASET_ID}.question_events` q
LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.answer_events` a
 ON q.request_id = a.request_id
 AND a.answer_date BETWEEN DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL ${FACT_RETENTION_DAYS} DAY)
                       AND CURRENT_DATE('${MONITOR_TIMEZONE}')
 AND a.answer_date BETWEEN DATE_SUB(q.question_date, INTERVAL 1 DAY) AND DATE_ADD(q.question_date, INTERVAL 1 DAY)
LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
  ON q.roster_id = scope.roster_id
 AND q.question_ts >= scope.valid_from
 AND (scope.valid_to IS NULL OR q.question_ts < scope.valid_to);

CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list` AS
WITH current_scope AS (
  SELECT * FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
  WHERE valid_to IS NULL AND is_active = TRUE AND user_map_scope_enabled = TRUE
), recent AS (
  SELECT roster_id,
    COUNTIF(active AND activity_date >= DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL 6 DAY)) AS active_days_7,
    SUM(IF(activity_date >= DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL 6 DAY), question_count, 0)) AS question_count_7
  FROM `${PROJECT_ID}.${DATASET_ID}.user_daily`
  WHERE activity_date BETWEEN DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL 13 DAY) AND CURRENT_DATE('${MONITOR_TIMEZONE}')
  GROUP BY roster_id
), last_seen AS (
  SELECT roster_id, MAX(last_active_at) AS last_active_at
  FROM `${PROJECT_ID}.${DATASET_ID}.user_daily`
  WHERE activity_date BETWEEN DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL ${AGGREGATE_RETENTION_DAYS} DAY)
    AND CURRENT_DATE('${MONITOR_TIMEZONE}')
  GROUP BY roster_id
)
SELECT scope.roster_id, scope.area_key, scope.area, scope.role, scope.department,
  last_seen.last_active_at,
  COALESCE(recent.active_days_7, 0) AS active_days_7,
  COALESCE(recent.question_count_7, 0) AS question_count_7
FROM current_scope scope
LEFT JOIN recent USING (roster_id)
LEFT JOIN last_seen USING (roster_id);

CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.dashboard_user_detail` AS
SELECT
  q.question_ts, q.question_date, q.roster_id, q.conversation_id, q.turn_id,
  q.mode, q.device_class, q.primary_question_category,
  q.question_categories, q.analytics_tasks, q.primary_product_key, q.primary_product_name,
  q.product_keys, q.product_names, q.product_candidate_count, q.product_resolved_count,
  a.measurement_available, a.complete_delivery,
  a.primary_failure_reason, a.total_latency_ms
FROM `${PROJECT_ID}.${DATASET_ID}.question_events` q
LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.answer_events` a
 ON q.request_id = a.request_id
 AND a.answer_date BETWEEN DATE_SUB(CURRENT_DATE('${MONITOR_TIMEZONE}'), INTERVAL ${FACT_RETENTION_DAYS} DAY)
                       AND CURRENT_DATE('${MONITOR_TIMEZONE}')
 AND a.answer_date BETWEEN DATE_SUB(q.question_date, INTERVAL 1 DAY) AND DATE_ADD(q.question_date, INTERVAL 1 DAY)
WHERE q.valid_question = TRUE;
