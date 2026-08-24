-- Required parameters: @window_start TIMESTAMP, @window_end TIMESTAMP.
WITH source_questions AS (
  SELECT
    event_id,
    user_id,
    COALESCE(event_ts, source_ts) AS event_ts
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND event_family = 'question_received'
), accepted_ask_requests AS (
  SELECT insert_id
  FROM `${PROJECT_ID}.${DATASET_ID}.http_request_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND method = 'POST'
    AND status BETWEEN 200 AND 399
    AND REGEXP_CONTAINS(
      REGEXP_EXTRACT(request_url, r'^https?://[^/]+([^?]*)'),
      r'^/v[0-9]+/ask(/stream)?/?$'
    )
), source_answers AS (
  SELECT event_id
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND event_family = 'answer_completed'
), questions AS (
  SELECT * FROM `${PROJECT_ID}.${DATASET_ID}.question_events`
  WHERE question_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
    AND question_ts >= @window_start AND question_ts < @window_end
), answers AS (
  SELECT * FROM `${PROJECT_ID}.${DATASET_ID}.answer_events`
  WHERE answer_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
    AND answer_ts >= @window_start AND answer_ts < @window_end
), question_links AS (
  SELECT DISTINCT request_id
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events`
  WHERE question_date BETWEEN DATE_SUB(DATE(@window_start, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
    AND DATE_ADD(DATE(@window_end, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
), checks AS (
  SELECT 'duplicate_question_event_id' AS check_name, COUNT(*) - COUNT(DISTINCT event_id) AS failure_count FROM questions
  UNION ALL
  SELECT 'duplicate_answer_event_id', COUNT(*) - COUNT(DISTINCT event_id) FROM answers
  UNION ALL
  SELECT 'duplicate_source_question_event_id', COUNT(*) - COUNT(DISTINCT event_id) FROM source_questions
  UNION ALL
  SELECT 'duplicate_source_answer_event_id', COUNT(*) - COUNT(DISTINCT event_id) FROM source_answers
  UNION ALL
  SELECT 'accepted_http_without_question_event', GREATEST(
    (SELECT COUNT(*) FROM accepted_ask_requests)
      - (SELECT COUNT(DISTINCT event_id) FROM source_questions),
    0
  )
  UNION ALL
  SELECT 'unknown_question_category', COUNTIF(primary_question_category IS NULL OR primary_question_category NOT IN (
    'product_information','price_product_code','comparison_fit_selection','usage_procedure',
    'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
    'other_general','unclassified'
  )) FROM answers
  UNION ALL
  SELECT 'unknown_secondary_question_category', COUNT(*)
  FROM answers, UNNEST(IFNULL(question_categories, [])) category
  WHERE category NOT IN (
    'product_information','price_product_code','comparison_fit_selection','usage_procedure',
    'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
    'other_general','unclassified'
  )
  UNION ALL
  SELECT 'unknown_analytics_task', COUNT(*)
  FROM answers, UNNEST(IFNULL(analytics_tasks, [])) task
  WHERE task NOT IN (
    'fact_lookup','explanation','comparison_selection','procedure_guidance',
    'troubleshooting','content_creation','source_retrieval','market_research',
    'other','unclassified'
  )
  UNION ALL
  SELECT 'missing_analytics_axes', COUNTIF(
    IFNULL(ARRAY_LENGTH(question_categories), 0) = 0
    OR IFNULL(ARRAY_LENGTH(analytics_tasks), 0) = 0
  ) FROM answers
  UNION ALL
  SELECT 'invalid_classification_producer', COUNTIF(classification_status = 'producer_invalid') FROM answers
  UNION ALL
  SELECT 'invalid_product_resolution_counts', COUNTIF(
    product_candidate_count IS NULL OR product_resolved_count IS NULL
    OR product_candidate_count < 0 OR product_resolved_count < 0
    OR product_resolved_count > product_candidate_count
  ) FROM answers
  UNION ALL
  SELECT 'unknown_classification_status', COUNTIF(classification_status IS NULL OR classification_status NOT IN (
    'classified','unclassified','producer_invalid'
  )) FROM answers
  UNION ALL
  SELECT 'source_question_without_roster', COUNTIF(scope.user_id IS NULL)
  FROM source_questions source
  LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
    ON source.user_id = scope.user_id
  UNION ALL
  SELECT 'question_without_terminal', COUNTIF(a.event_id IS NULL)
  FROM questions q LEFT JOIN answers a USING (request_id)
  UNION ALL
  SELECT 'answer_without_question', COUNTIF(q.request_id IS NULL)
  FROM answers a LEFT JOIN question_links q USING (request_id)
  UNION ALL
  SELECT 'terminal_without_persistence_measurement', COUNTIF(terminal IS NOT NULL AND message_persisted IS NULL) FROM answers
)
SELECT
  check_name,
  failure_count,
  IF(check_name = 'terminal_without_persistence_measurement', 'coverage', 'critical') AS severity,
  failure_count = 0 AS passed
FROM checks
ORDER BY check_name;
