-- Required parameters: @date_start DATE, @date_end DATE.
DELETE FROM `${PROJECT_ID}.${DATASET_ID}.user_daily`
WHERE activity_date BETWEEN @date_start AND @date_end;

INSERT INTO `${PROJECT_ID}.${DATASET_ID}.user_daily` (
  activity_date, roster_id, area_key, area, role, department, active,
  last_active_at, question_count, materialized_at
)
WITH dates AS (
  SELECT day AS activity_date FROM UNNEST(GENERATE_DATE_ARRAY(@date_start, @date_end)) day
), current_scope AS (
  SELECT * FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
  WHERE is_active = TRUE
), question_facts AS (
  SELECT q.question_date, q.question_ts, q.roster_id, q.event_id
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` q
  WHERE q.question_date BETWEEN @date_start AND @date_end
    AND q.valid_question = TRUE
)
SELECT
  dates.activity_date, scope.roster_id, scope.area_key, scope.area, scope.role, scope.department,
  COUNT(qa.event_id) > 0 AS active,
  MAX(qa.question_ts) AS last_active_at,
  COUNT(qa.event_id) AS question_count,
  CURRENT_TIMESTAMP() AS materialized_at
FROM dates
CROSS JOIN current_scope scope
LEFT JOIN question_facts qa
  ON qa.question_date = dates.activity_date AND qa.roster_id = scope.roster_id
GROUP BY dates.activity_date, scope.roster_id, scope.area_key, scope.area, scope.role, scope.department;
