-- Required parameters: @window_start TIMESTAMP, @window_end TIMESTAMP.
-- The job owns transaction boundaries and advances pipeline_state only after
-- this script and check_data_quality.sql both succeed.

MERGE `${PROJECT_ID}.${DATASET_ID}.http_request_events` target
USING (
  SELECT * EXCEPT(request_path, row_number)
  FROM (
    SELECT
      COALESCE(NULLIF(insert_id, ''), TO_HEX(SHA256(CONCAT(CAST(source_ts AS STRING), '|', method, '|', request_url)))) AS event_id,
      source_ts AS request_ts,
      DATE(source_ts, '${MONITOR_TIMEZONE}') AS request_date,
      CASE
        WHEN REGEXP_CONTAINS(request_path, r'^/v[0-9]+/ask/stream/?$') THEN 'ask_stream'
        WHEN REGEXP_CONTAINS(request_path, r'^/v[0-9]+/ask/?$') THEN 'ask'
        WHEN REGEXP_CONTAINS(request_path, r'^/v[0-9]+/conversations/[^/]+/messages/[^/]+/?$') THEN 'message_write'
        WHEN REGEXP_CONTAINS(request_path, r'^/v[0-9]+/conversations') THEN 'conversation'
        ELSE 'other'
      END AS endpoint_class,
      method,
      status,
      CAST(ROUND(COALESCE(
        SAFE_CAST(REGEXP_EXTRACT(latency_text, r'^([0-9.]+)s$') AS FLOAT64),
        SAFE_CAST(latency_text AS FLOAT64)
      ) * 1000) AS INT64) AS latency_ms,
      revision_name,
      source_ts AS source_event_ts,
      CURRENT_TIMESTAMP() AS materialized_at,
      request_path,
      ROW_NUMBER() OVER (
        PARTITION BY COALESCE(NULLIF(insert_id, ''), TO_HEX(SHA256(CONCAT(CAST(source_ts AS STRING), '|', method, '|', request_url))))
        ORDER BY source_ts DESC
      ) AS row_number
    FROM (
      SELECT *, REGEXP_EXTRACT(request_url, r'^https?://[^/]+([^?]*)') AS request_path
      FROM `${PROJECT_ID}.${DATASET_ID}.http_request_source`
      WHERE source_ts >= @window_start AND source_ts < @window_end
    )
  )
  WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.request_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
WHEN MATCHED THEN UPDATE SET request_ts = source.request_ts, request_date = source.request_date,
  endpoint_class = source.endpoint_class, method = source.method, status = source.status,
  latency_ms = source.latency_ms, revision_name = source.revision_name,
  source_event_ts = source.source_event_ts, materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, request_ts, request_date, endpoint_class, method, status,
  latency_ms, revision_name, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.request_ts, source.request_date,
  source.endpoint_class, source.method, source.status, source.latency_ms,
  source.revision_name, source.source_event_ts, source.materialized_at
);

MERGE `${PROJECT_ID}.${DATASET_ID}.question_events` target
USING (
  SELECT * EXCEPT(row_number)
  FROM (
    SELECT
      event_id,
      COALESCE(event_ts, source_ts) AS question_ts,
      DATE(COALESCE(event_ts, source_ts), '${MONITOR_TIMEZONE}') AS question_date,
      event.user_id,
      scope.roster_id,
      request_id,
      trace_id,
      conversation_id,
      turn_id,
      message_id,
      mode,
      device_class,
      endpoint_class,
      COALESCE(SAFE_CAST(JSON_VALUE(payload_json, '$.valid_question') AS BOOL), FALSE) AS valid_question,
      SAFE_CAST(JSON_VALUE(payload_json, '$.attachment_count') AS INT64) AS attachment_count,
      CAST(NULL AS STRING) AS primary_question_category,
      CAST([] AS ARRAY<STRING>) AS question_categories,
      CAST(NULL AS STRING) AS classification_status,
      CAST(NULL AS BOOL) AS is_multi_intent,
      CAST([] AS ARRAY<STRING>) AS analytics_tasks,
      CAST(NULL AS STRING) AS primary_product_key,
      CAST(NULL AS STRING) AS primary_product_name,
      CAST([] AS ARRAY<STRING>) AS product_keys,
      CAST([] AS ARRAY<STRING>) AS product_names,
      CAST(NULL AS INT64) AS product_candidate_count,
      CAST(NULL AS INT64) AS product_resolved_count,
      revision_name AS producer_revision,
      git_sha AS producer_git_sha,
      'canonical_event' AS record_origin,
      'question_event' AS measurement_profile,
      source_ts AS source_event_ts,
      CURRENT_TIMESTAMP() AS materialized_at,
      ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC) AS row_number
    FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` event
    JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
     ON event.user_id = scope.user_id
     AND scope.user_map_scope_enabled = TRUE
    WHERE source_ts >= @window_start
      AND source_ts < @window_end
      AND event_family = 'question_received'
  )
  WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.question_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
WHEN MATCHED THEN UPDATE SET
  question_ts = source.question_ts,
  question_date = source.question_date,
  user_id = source.user_id,
  roster_id = source.roster_id,
  request_id = source.request_id,
  trace_id = source.trace_id,
  conversation_id = source.conversation_id,
  turn_id = source.turn_id,
  message_id = source.message_id,
  mode = source.mode,
  device_class = source.device_class,
  endpoint_class = source.endpoint_class,
  valid_question = source.valid_question,
  attachment_count = source.attachment_count,
  producer_revision = source.producer_revision,
  producer_git_sha = source.producer_git_sha,
  record_origin = source.record_origin,
  measurement_profile = source.measurement_profile,
  source_event_ts = source.source_event_ts,
  materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, question_ts, question_date, user_id, roster_id, request_id,
  trace_id, conversation_id, turn_id, message_id, mode, device_class,
  endpoint_class, valid_question, attachment_count,
  primary_question_category, question_categories, classification_status,
  is_multi_intent, analytics_tasks, primary_product_key,
  primary_product_name, product_keys, product_names,
  product_candidate_count, product_resolved_count, producer_revision,
  producer_git_sha, record_origin, measurement_profile, source_event_ts,
  materialized_at
) VALUES (
  source.event_id, source.question_ts, source.question_date, source.user_id,
  source.roster_id, source.request_id, source.trace_id,
  source.conversation_id, source.turn_id, source.message_id, source.mode,
  source.device_class, source.endpoint_class, source.valid_question,
  source.attachment_count, source.primary_question_category,
  source.question_categories, source.classification_status,
  source.is_multi_intent, source.analytics_tasks, source.primary_product_key,
  source.primary_product_name, source.product_keys, source.product_names,
  source.product_candidate_count, source.product_resolved_count,
  source.producer_revision, source.producer_git_sha, source.record_origin,
  source.measurement_profile, source.source_event_ts, source.materialized_at
);

MERGE `${PROJECT_ID}.${DATASET_ID}.answer_events` target
USING (
  SELECT * EXCEPT(row_number)
  FROM (
    SELECT
      event_id,
      COALESCE(event_ts, source_ts) AS answer_ts,
      DATE(COALESCE(event_ts, source_ts), '${MONITOR_TIMEZONE}') AS answer_date,
      event.user_id,
      scope.roster_id,
      request_id,
      trace_id,
      conversation_id,
      turn_id,
      message_id,
      mode,
      device_class,
      JSON_VALUE(payload_json, '$.terminal') AS terminal,
      JSON_VALUE(payload_json, '$.runtime_status') AS runtime_status,
      JSON_VALUE(payload_json, '$.failure_stage') AS failure_stage,
      JSON_VALUE(payload_json, '$.failure_code') AS failure_code,
      JSON_VALUE(payload_json, '$.primary_question_category') AS primary_question_category,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.question_categories'), [])) item) AS question_categories,
      JSON_VALUE(payload_json, '$.classification_status') AS classification_status,
      SAFE_CAST(JSON_VALUE(payload_json, '$.is_multi_intent') AS BOOL) AS is_multi_intent,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.analytics_tasks'), [])) item) AS analytics_tasks,
      JSON_VALUE(payload_json, '$.primary_product_key') AS primary_product_key,
      JSON_VALUE(payload_json, '$.primary_product_name') AS primary_product_name,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.product_keys'), [])) item) AS product_keys,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.product_names'), [])) item) AS product_names,
      SAFE_CAST(JSON_VALUE(payload_json, '$.product_candidate_count') AS INT64) AS product_candidate_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.product_resolved_count') AS INT64) AS product_resolved_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.demand_total') AS INT64) AS demand_total,
      SAFE_CAST(JSON_VALUE(payload_json, '$.delivered_demand_count') AS INT64) AS delivered_demand_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.partial_demand_count') AS INT64) AS partial_demand_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.omitted_demand_count') AS INT64) AS omitted_demand_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.system_fault_count') AS INT64) AS system_fault_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.citation_count') AS INT64) AS citation_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.supported_claim_count') AS INT64) AS supported_claim_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.unsupported_claim_count') AS INT64) AS unsupported_claim_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.total_latency_ms') AS INT64) AS total_latency_ms,
      SAFE.PARSE_JSON(JSON_QUERY(payload_json, '$.stage_latency_ms')) AS stage_latency_ms,
      JSON_VALUE(payload_json, '$.writer_error_code') AS writer_error_code,
      SAFE_CAST(JSON_VALUE(payload_json, '$.retry_count') AS INT64) AS retry_count,
      CAST(NULL AS BOOL) AS message_persisted,
      CAST(NULL AS BOOL) AS assistant_error_present,
      CAST(NULL AS STRING) AS persistence_error_code,
      FALSE AS measurement_available,
      FALSE AS complete_delivery,
      'measurement_missing' AS primary_failure_reason,
      revision_name,
      git_sha,
      build_id,
      'canonical_event' AS record_origin,
      'complete_delivery_full' AS measurement_profile,
      source_ts AS source_event_ts,
      CURRENT_TIMESTAMP() AS materialized_at,
      payload_json,
      ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC) AS row_number
    FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` event
    JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
     ON event.user_id = scope.user_id
     AND scope.user_map_scope_enabled = TRUE
    WHERE source_ts >= @window_start
      AND source_ts < @window_end
      AND event_family = 'answer_completed'
  )
  WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.answer_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
WHEN MATCHED THEN UPDATE SET
  answer_ts = source.answer_ts, answer_date = source.answer_date, user_id = source.user_id,
  roster_id = source.roster_id, request_id = source.request_id, trace_id = source.trace_id,
  conversation_id = source.conversation_id, turn_id = source.turn_id, message_id = source.message_id,
  mode = source.mode, device_class = source.device_class, terminal = source.terminal,
  runtime_status = source.runtime_status, failure_stage = source.failure_stage, failure_code = source.failure_code,
  primary_question_category = source.primary_question_category, question_categories = source.question_categories,
  classification_status = source.classification_status, is_multi_intent = source.is_multi_intent,
  analytics_tasks = source.analytics_tasks, primary_product_key = source.primary_product_key,
  primary_product_name = source.primary_product_name, product_keys = source.product_keys,
  product_names = source.product_names, product_candidate_count = source.product_candidate_count,
  product_resolved_count = source.product_resolved_count, demand_total = source.demand_total,
  delivered_demand_count = source.delivered_demand_count, partial_demand_count = source.partial_demand_count,
  omitted_demand_count = source.omitted_demand_count, system_fault_count = source.system_fault_count,
  citation_count = source.citation_count, supported_claim_count = source.supported_claim_count,
  unsupported_claim_count = source.unsupported_claim_count, total_latency_ms = source.total_latency_ms,
  stage_latency_ms = source.stage_latency_ms, writer_error_code = source.writer_error_code,
  retry_count = source.retry_count, revision_name = source.revision_name, git_sha = source.git_sha,
  build_id = source.build_id, record_origin = source.record_origin,
  measurement_profile = source.measurement_profile,
  source_event_ts = source.source_event_ts, materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, answer_ts, answer_date, user_id, roster_id, request_id, trace_id, conversation_id, turn_id,
  message_id, mode, device_class, terminal, runtime_status, failure_stage, failure_code,
  primary_question_category, question_categories, classification_status, is_multi_intent, analytics_tasks,
  primary_product_key, primary_product_name, product_keys, product_names, product_candidate_count,
  product_resolved_count, demand_total, delivered_demand_count,
  partial_demand_count, omitted_demand_count, system_fault_count, citation_count, supported_claim_count,
  unsupported_claim_count, total_latency_ms, stage_latency_ms, writer_error_code, retry_count,
  message_persisted, assistant_error_present, persistence_error_code, measurement_available,
  complete_delivery, primary_failure_reason, revision_name, git_sha, build_id,
  record_origin, measurement_profile,
  source_event_ts, materialized_at
) VALUES (
  source.event_id, source.answer_ts, source.answer_date, source.user_id, source.roster_id, source.request_id,
  source.trace_id, source.conversation_id, source.turn_id, source.message_id, source.mode, source.device_class,
  source.terminal, source.runtime_status, source.failure_stage, source.failure_code,
  source.primary_question_category, source.question_categories, source.classification_status,
  source.is_multi_intent, source.analytics_tasks, source.primary_product_key, source.primary_product_name,
  source.product_keys, source.product_names, source.product_candidate_count, source.product_resolved_count,
  source.demand_total, source.delivered_demand_count,
  source.partial_demand_count, source.omitted_demand_count, source.system_fault_count, source.citation_count,
  source.supported_claim_count, source.unsupported_claim_count, source.total_latency_ms,
  source.stage_latency_ms, source.writer_error_code, source.retry_count, source.message_persisted,
  source.assistant_error_present, source.persistence_error_code, source.measurement_available,
  source.complete_delivery, source.primary_failure_reason, source.revision_name, source.git_sha,
  source.build_id, source.record_origin, source.measurement_profile,
  source.source_event_ts, source.materialized_at
);

-- Enrich the question once from the sole RequestSpec producer carried by its answer.
UPDATE `${PROJECT_ID}.${DATASET_ID}.question_events` question
SET
  primary_question_category = answer.primary_question_category,
  question_categories = answer.question_categories,
  classification_status = answer.classification_status,
  is_multi_intent = answer.is_multi_intent,
  analytics_tasks = answer.analytics_tasks,
  primary_product_key = answer.primary_product_key,
  primary_product_name = answer.primary_product_name,
  product_keys = answer.product_keys,
  product_names = answer.product_names,
  product_candidate_count = answer.product_candidate_count,
  product_resolved_count = answer.product_resolved_count,
  producer_revision = answer.revision_name,
  producer_git_sha = answer.git_sha,
  materialized_at = CURRENT_TIMESTAMP()
FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
WHERE question.question_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
  AND answer.answer_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
  AND question.request_id = answer.request_id;

-- Persist outcome is a separate owner and may arrive after answer_completed.
UPDATE `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
SET
  message_persisted = persistence.persisted,
  assistant_error_present = persistence.assistant_error_present,
  persistence_error_code = persistence.error_code,
  materialized_at = CURRENT_TIMESTAMP()
FROM (
  SELECT request_id, conversation_id, message_id,
    SAFE_CAST(JSON_VALUE(payload_json, '$.answer_ts') AS TIMESTAMP) AS answer_ts,
    SAFE_CAST(JSON_VALUE(payload_json, '$.persisted') AS BOOL) AS persisted,
    SAFE_CAST(JSON_VALUE(payload_json, '$.assistant_error_present') AS BOOL) AS assistant_error_present,
    JSON_VALUE(payload_json, '$.error_code') AS error_code
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND event_family = 'message_persisted'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY request_id, conversation_id ORDER BY source_ts DESC, insert_id DESC) = 1
) persistence
WHERE persistence.answer_ts IS NOT NULL
  AND answer.answer_date BETWEEN
    DATE_SUB(DATE(@window_start, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
    AND DATE_ADD(DATE(@window_end, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
  AND answer.answer_date BETWEEN
    DATE_SUB(DATE(persistence.answer_ts, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
    AND DATE_ADD(DATE(persistence.answer_ts, '${MONITOR_TIMEZONE}'), INTERVAL 1 DAY)
  AND answer.request_id = persistence.request_id
  AND answer.conversation_id = persistence.conversation_id;

-- One official complete-delivery owner and one failure priority.
UPDATE `${PROJECT_ID}.${DATASET_ID}.answer_events`
SET
  measurement_available = CASE
    WHEN terminal IN ('error', 'cancelled') OR runtime_status IN ('failed', 'cancelled') THEN TRUE
    WHEN terminal IS NOT NULL AND terminal != 'final' THEN TRUE
    WHEN message_persisted = FALSE OR assistant_error_present = TRUE THEN TRUE
    WHEN COALESCE(writer_error_code, '') != '' THEN TRUE
    WHEN system_fault_count > 0 OR omitted_demand_count > 0 OR partial_demand_count > 0 THEN TRUE
    WHEN terminal IS NULL OR runtime_status IS NULL OR demand_total IS NULL OR demand_total = 0
      OR partial_demand_count IS NULL OR omitted_demand_count IS NULL OR system_fault_count IS NULL
      OR message_persisted IS NULL OR assistant_error_present IS NULL THEN FALSE
    ELSE TRUE END,
  complete_delivery = terminal = 'final' AND runtime_status = 'completed' AND demand_total > 0
    AND partial_demand_count = 0 AND omitted_demand_count = 0 AND system_fault_count = 0
    AND message_persisted = TRUE AND assistant_error_present = FALSE AND COALESCE(writer_error_code, '') = '',
  primary_failure_reason = CASE
    WHEN terminal IN ('error', 'cancelled') OR runtime_status IN ('failed', 'cancelled') THEN 'stream_failed'
    WHEN terminal IS NOT NULL AND terminal != 'final' THEN 'not_final'
    WHEN message_persisted = FALSE THEN 'not_persisted'
    WHEN assistant_error_present = TRUE THEN 'assistant_error'
    WHEN COALESCE(writer_error_code, '') != '' THEN 'writer_error'
    WHEN system_fault_count > 0 THEN 'system_fault'
    WHEN omitted_demand_count > 0 THEN 'demand_omitted'
    WHEN partial_demand_count > 0 THEN 'demand_partial'
    WHEN terminal IS NULL OR runtime_status IS NULL OR demand_total IS NULL OR demand_total = 0
      OR partial_demand_count IS NULL OR omitted_demand_count IS NULL OR system_fault_count IS NULL
      OR message_persisted IS NULL OR assistant_error_present IS NULL THEN 'measurement_missing'
    ELSE NULL END,
  materialized_at = CURRENT_TIMESTAMP()
WHERE answer_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}');

MERGE `${PROJECT_ID}.${DATASET_ID}.answer_action_events` target
USING (
  SELECT * EXCEPT(row_number)
  FROM (
    SELECT event_id, COALESCE(event_ts, source_ts) AS action_ts,
      DATE(COALESCE(event_ts, source_ts), '${MONITOR_TIMEZONE}') AS action_date,
      event.user_id, scope.roster_id, request_id, conversation_id, turn_id, message_id,
      JSON_VALUE(payload_json, '$.target_message_id') AS target_message_id,
      JSON_VALUE(payload_json, '$.action') AS action,
      JSON_VALUE(payload_json, '$.feedback') AS feedback,
      mode, JSON_VALUE(payload_json, '$.request_mode') AS request_mode,
      JSON_VALUE(payload_json, '$.client_origin') AS client_origin,
      source_ts AS source_event_ts, CURRENT_TIMESTAMP() AS materialized_at,
      ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC) AS row_number
    FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` event
    JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
     ON event.user_id = scope.user_id
     AND scope.user_map_scope_enabled = TRUE
    WHERE source_ts >= @window_start AND source_ts < @window_end
      AND event_family = 'answer_action'
  ) WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.action_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
WHEN MATCHED THEN UPDATE SET action_ts = source.action_ts, action_date = source.action_date,
  user_id = source.user_id, roster_id = source.roster_id, request_id = source.request_id,
  conversation_id = source.conversation_id, turn_id = source.turn_id, message_id = source.message_id,
  target_message_id = source.target_message_id, action = source.action, feedback = source.feedback,
  mode = source.mode, request_mode = source.request_mode, client_origin = source.client_origin,
  source_event_ts = source.source_event_ts, materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, action_ts, action_date, user_id, roster_id, request_id,
  conversation_id, turn_id, message_id, target_message_id, action, feedback,
  mode, request_mode, client_origin, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.action_ts, source.action_date, source.user_id,
  source.roster_id, source.request_id, source.conversation_id, source.turn_id,
  source.message_id, source.target_message_id, source.action, source.feedback,
  source.mode, source.request_mode, source.client_origin,
  source.source_event_ts, source.materialized_at
);

MERGE `${PROJECT_ID}.${DATASET_ID}.demand_events` target
USING (
  SELECT
    CONCAT(event.event_id, ':demand:', CAST(demand_order AS STRING)) AS event_id,
    CONCAT('question:', event.request_id) AS question_event_id,
    COALESCE(event.event_ts, event.source_ts) AS question_ts,
    DATE(COALESCE(event.event_ts, event.source_ts), '${MONITOR_TIMEZONE}') AS question_date,
    event.user_id, scope.roster_id,
    JSON_VALUE(demand, '$.demand_id') AS demand_id,
    demand_order,
    JSON_VALUE(demand, '$.question_category') AS question_category,
    JSON_VALUE(demand, '$.analytics_task') AS analytics_task,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.product_keys'), [])) item) AS product_keys,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.product_names'), [])) item) AS product_names,
    JSON_VALUE(demand, '$.requirement') AS requirement,
    JSON_VALUE(demand, '$.delivery_state') AS delivery_state,
    JSON_VALUE(demand, '$.evidence_state') AS evidence_state,
    JSON_VALUE(demand, '$.system_fault') AS system_fault,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.reason_codes'), [])) item) AS reason_codes,
    event.source_ts AS source_event_ts, CURRENT_TIMESTAMP() AS materialized_at
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` event
  JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
   ON event.user_id = scope.user_id
   AND scope.user_map_scope_enabled = TRUE
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(event.payload_json, '$.demands'), [])) demand WITH OFFSET demand_order
  WHERE event.source_ts >= @window_start AND event.source_ts < @window_end
    AND event.event_family = 'answer_completed'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event.event_id, demand_order ORDER BY event.source_ts DESC, event.insert_id DESC) = 1
) source
ON target.event_id = source.event_id
AND target.question_date BETWEEN DATE(@window_start, '${MONITOR_TIMEZONE}') AND DATE(@window_end, '${MONITOR_TIMEZONE}')
WHEN MATCHED THEN UPDATE SET question_event_id = source.question_event_id, question_ts = source.question_ts,
  question_date = source.question_date, user_id = source.user_id, roster_id = source.roster_id,
  demand_id = source.demand_id, demand_order = source.demand_order, question_category = source.question_category,
  analytics_task = source.analytics_task, product_keys = source.product_keys, product_names = source.product_names,
  requirement = source.requirement, delivery_state = source.delivery_state, evidence_state = source.evidence_state,
  system_fault = source.system_fault, reason_codes = source.reason_codes,
  source_event_ts = source.source_event_ts, materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, question_event_id, question_ts, question_date, user_id,
  roster_id, demand_id, demand_order, question_category, analytics_task,
  product_keys, product_names, requirement, delivery_state, evidence_state,
  system_fault, reason_codes, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.question_event_id, source.question_ts,
  source.question_date, source.user_id, source.roster_id, source.demand_id,
  source.demand_order, source.question_category, source.analytics_task,
  source.product_keys, source.product_names, source.requirement,
  source.delivery_state, source.evidence_state, source.system_fault,
  source.reason_codes, source.source_event_ts, source.materialized_at
);
