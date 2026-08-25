-- One partition-bounded semantic owner for both the overview and user detail.
-- The answer CTE guarantees at most one answer row per request, so a retry or
-- duplicate terminal event can never multiply a user's question count.
CREATE OR REPLACE TABLE FUNCTION `${PROJECT_ID}.${DATASET_ID}.dashboard_events`(
  p_start_date DATE,
  p_end_date DATE
) AS (
  WITH canonical_answers AS (
    SELECT *
    FROM `${PROJECT_ID}.${DATASET_ID}.answer_events`
    WHERE answer_date BETWEEN DATE_SUB(p_start_date, INTERVAL 1 DAY)
      AND DATE_ADD(p_end_date, INTERVAL 1 DAY)
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY COALESCE(NULLIF(request_id, ''), event_id)
      ORDER BY answer_ts DESC, source_event_ts DESC, event_id DESC
    ) = 1
  )
  SELECT
    q.event_id AS question_event_id,
    q.question_ts,
    q.question_date,
    q.roster_id,
    q.request_id,
    q.conversation_id,
    q.turn_id,
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
    q.record_origin,
    q.measurement_profile AS question_measurement_profile,
    answer.measurement_available,
    answer.complete_delivery,
    answer.primary_failure_reason,
    answer.total_latency_ms,
    answer.measurement_profile AS answer_measurement_profile
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` q
  LEFT JOIN canonical_answers answer
    ON q.request_id IS NOT NULL
    AND q.request_id != ''
    AND q.request_id = answer.request_id
    AND answer.answer_date BETWEEN DATE_SUB(q.question_date, INTERVAL 1 DAY)
      AND DATE_ADD(q.question_date, INTERVAL 1 DAY)
  LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
    ON q.roster_id = scope.roster_id
  WHERE q.question_date BETWEEN p_start_date AND p_end_date
);

-- The current 80-person user list reads the same question fact owner directly.
-- No user_daily copy is maintained, so a stale aggregate cannot disagree with
-- the overview or hide historical last-use timestamps.
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list`;
DROP TABLE FUNCTION IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list`;
CREATE OR REPLACE TABLE FUNCTION `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list`(
  p_history_start DATE,
  p_today DATE
) AS (
  WITH current_scope AS (
    SELECT *
    FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
    WHERE is_active = TRUE AND user_map_scope_enabled = TRUE
  ), question_facts AS (
    SELECT roster_id, question_date, question_ts
    FROM `${PROJECT_ID}.${DATASET_ID}.question_events`
    WHERE question_date BETWEEN p_history_start AND p_today
      AND valid_question = TRUE
  ), metrics AS (
    SELECT
      roster_id,
      MAX(question_ts) AS last_active_at,
      COUNT(DISTINCT IF(question_date >= DATE_SUB(p_today, INTERVAL 6 DAY), question_date, NULL)) AS active_days_7,
      COUNTIF(question_date >= DATE_SUB(p_today, INTERVAL 6 DAY)) AS user_message_count_7
    FROM question_facts
    GROUP BY roster_id
  )
  SELECT
    scope.roster_id,
    scope.area_key,
    scope.area,
    scope.role,
    scope.department,
    metrics.last_active_at,
    COALESCE(metrics.active_days_7, 0) AS active_days_7,
    COALESCE(metrics.user_message_count_7, 0) AS user_message_count_7
  FROM current_scope scope
  LEFT JOIN metrics USING (roster_id)
);

-- Explicitly close the two former unbounded mega-view owners.
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_overview`;
DROP VIEW IF EXISTS `${PROJECT_ID}.${DATASET_ID}.dashboard_user_detail`;
DROP TABLE IF EXISTS `${PROJECT_ID}.${DATASET_ID}.user_daily`;
