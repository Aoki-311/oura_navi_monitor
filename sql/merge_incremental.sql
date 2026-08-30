-- Required parameters: @window_start TIMESTAMP, @window_end TIMESTAMP.
-- The job owns transaction boundaries and advances pipeline_state only after
-- this script and check_data_quality.sql both succeed.

-- Freeze one source snapshot for this run. Effective event time owns fact
-- partitions; source_ts owns only incremental ingestion. The two clocks must
-- never be interchanged, because delayed log delivery can legitimately write
-- an older fact partition.
CREATE TEMP TABLE _run_scope_by_user AS
SELECT
  user_id,
  ARRAY_AGG(roster_id ORDER BY updated_at DESC, roster_id LIMIT 1)[OFFSET(0)] AS roster_id,
  COUNT(DISTINCT roster_id) AS roster_match_count
FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
WHERE snapshot_run_id = @run_id
  AND NULLIF(user_id, '') IS NOT NULL
  AND user_map_scope_enabled = TRUE
GROUP BY user_id;

CREATE TEMP TABLE _run_monitor_source AS
WITH hashed AS (
  SELECT
    event.*,
    COALESCE(event.event_ts, event.source_ts) AS event_effective_ts,
    SAFE_CAST(JSON_VALUE(event.payload_json, '$.answer_ts') AS TIMESTAMP)
      AS persistence_answer_ts,
    scope.roster_id,
    COALESCE(scope.roster_match_count, 0) AS roster_match_count,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      event.insert_id,
      event.source_ts,
      event.event_id,
      event.event_family,
      event.monitor_contract_version,
      event.event_ts,
      event.cloud_trace,
      event.cloud_span_id,
      event.request_id,
      event.conversation_id,
      event.turn_id,
      event.message_id,
      event.user_id,
      event.payload_json
    )))) AS source_event_hash,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      event.event_id,
      event.event_family,
      event.monitor_contract_version,
      event.event_ts,
      event.cloud_trace,
      event.cloud_span_id,
      event.trace_id,
      event.request_id,
      event.conversation_id,
      event.turn_id,
      event.message_id,
      event.user_id,
      event.mode,
      event.device_class,
      event.endpoint_class,
      event.revision_name,
      event.git_sha,
      event.build_id,
      event.payload_json
    )))) AS event_content_hash
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` event
  LEFT JOIN _run_scope_by_user scope
    ON event.user_id = scope.user_id
  WHERE event.source_ts >= @window_start
    AND event.source_ts < @window_end
), ranked AS (
  SELECT
    hashed.*,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
      ORDER BY source_ts DESC, insert_id DESC
    ) AS delivery_row_number,
    MIN(event_content_hash) OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
    ) AS minimum_content_hash,
    MAX(event_content_hash) OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
    ) AS maximum_content_hash
  FROM hashed
)
SELECT * FROM ranked;

-- Every excluded source row receives a closed issue code. The permanent ledger
-- stores only source hashes and operational metadata, never raw ids or payloads.
CREATE TEMP TABLE _run_event_issues AS
SELECT source_event_hash, event_family, source_ts, event_effective_ts AS event_ts,
  'source_event_missing_event_id' AS issue_code, 'row_quarantined' AS disposition
FROM _run_monitor_source WHERE NULLIF(event_id, '') IS NULL
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_missing_user_id', 'row_quarantined'
FROM _run_monitor_source WHERE NULLIF(user_id, '') IS NULL
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'unsupported_event_family', 'row_quarantined'
FROM _run_monitor_source
WHERE event_family NOT IN (
  'question_received', 'answer_completed', 'message_persisted', 'answer_action'
)
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_without_roster', 'row_quarantined'
FROM _run_monitor_source
WHERE NULLIF(user_id, '') IS NOT NULL AND roster_match_count = 0
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_ambiguous_roster', 'row_quarantined'
FROM _run_monitor_source WHERE roster_match_count > 1
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_missing_correlation', 'row_quarantined'
FROM _run_monitor_source
WHERE event_family IN ('question_received', 'answer_completed', 'message_persisted')
  AND (
    NULLIF(request_id, '') IS NULL
    OR NULLIF(conversation_id, '') IS NULL
    OR NULLIF(turn_id, '') IS NULL
  )
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_timestamp_before_analytics_start', 'row_quarantined'
FROM _run_monitor_source WHERE event_effective_ts < @analytics_start
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'source_event_timestamp_in_future', 'row_quarantined'
FROM _run_monitor_source
WHERE event_effective_ts > TIMESTAMP_ADD(
  source_ts, INTERVAL @event_future_tolerance_minutes MINUTE
)
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'invalid_persistence_answer_ts', 'row_quarantined'
FROM _run_monitor_source
WHERE event_family = 'message_persisted'
  AND (
    persistence_answer_ts IS NULL
    OR persistence_answer_ts < @analytics_start
    OR persistence_answer_ts > TIMESTAMP_ADD(
      source_ts, INTERVAL @event_future_tolerance_minutes MINUTE
    )
  )
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'conflicting_duplicate_event_id', 'row_quarantined'
FROM _run_monitor_source
WHERE minimum_content_hash != maximum_content_hash
UNION ALL
SELECT source_event_hash, event_family, source_ts, event_effective_ts,
  'duplicate_delivery_deduplicated', 'deduplicated'
FROM _run_monitor_source
WHERE minimum_content_hash = maximum_content_hash
  AND delivery_row_number > 1;

CREATE TEMP TABLE _run_admissible_monitor_events AS
SELECT source.*
FROM _run_monitor_source source
WHERE source.delivery_row_number = 1
  AND NOT EXISTS (
    SELECT 1
    FROM _run_event_issues issue
    WHERE issue.source_event_hash = source.source_event_hash
      AND issue.disposition = 'row_quarantined'
  );

CREATE TEMP TABLE _run_affected_request_ids AS
SELECT DISTINCT request_id
FROM _run_admissible_monitor_events
WHERE event_family IN ('question_received', 'answer_completed', 'message_persisted')
  AND NULLIF(request_id, '') IS NOT NULL;

SET event_partition_start = COALESCE((
  SELECT MIN(DATE(event_effective_ts, '${MONITOR_TIMEZONE}'))
  FROM _run_admissible_monitor_events
  WHERE event_family IN ('question_received', 'answer_completed', 'answer_action')
), DATE(@window_start, '${MONITOR_TIMEZONE}'));
SET event_partition_end = COALESCE((
  SELECT MAX(DATE(event_effective_ts, '${MONITOR_TIMEZONE}'))
  FROM _run_admissible_monitor_events
  WHERE event_family IN ('question_received', 'answer_completed', 'answer_action')
), DATE(@window_end, '${MONITOR_TIMEZONE}'));
SET persistence_partition_start = COALESCE((
  SELECT MIN(DATE(persistence_answer_ts, '${MONITOR_TIMEZONE}'))
  FROM _run_admissible_monitor_events
  WHERE event_family = 'message_persisted'
), event_partition_start);
SET persistence_partition_end = COALESCE((
  SELECT MAX(DATE(persistence_answer_ts, '${MONITOR_TIMEZONE}'))
  FROM _run_admissible_monitor_events
  WHERE event_family = 'message_persisted'
), event_partition_end);
SET affected_answer_partition_start = LEAST(
  event_partition_start, persistence_partition_start
);
SET affected_answer_partition_end = GREATEST(
  event_partition_end, persistence_partition_end
);

-- A previous non-atomic or legacy writer may already have produced more than
-- one fact row for the same event_id. BigQuery MERGE cannot repair that state:
-- it fails when one source row matches multiple target rows. Repair only keys
-- replayed by this frozen source snapshot, within their effective partitions,
-- then let the canonical MERGEs below recreate exactly one row. Unrelated
-- historical rows and distinct event_ids are never touched.
CREATE TEMP TABLE _run_duplicate_fact_keys AS
SELECT 'question_events' AS fact_table, target.event_id
FROM `${PROJECT_ID}.${DATASET_ID}.question_events` target
JOIN (
  SELECT DISTINCT event_id
  FROM _run_admissible_monitor_events
  WHERE event_family = 'question_received'
) source USING (event_id)
WHERE target.question_date BETWEEN event_partition_start AND event_partition_end
GROUP BY target.event_id
HAVING COUNT(*) > 1
UNION ALL
SELECT 'answer_events' AS fact_table, target.event_id
FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` target
JOIN (
  SELECT DISTINCT event_id
  FROM _run_admissible_monitor_events
  WHERE event_family = 'answer_completed'
) source USING (event_id)
WHERE target.answer_date BETWEEN event_partition_start AND event_partition_end
GROUP BY target.event_id
HAVING COUNT(*) > 1;

DELETE FROM `${PROJECT_ID}.${DATASET_ID}.question_events` target
WHERE target.question_date BETWEEN event_partition_start AND event_partition_end
  AND target.event_id IN (
    SELECT event_id
    FROM _run_duplicate_fact_keys
    WHERE fact_table = 'question_events'
  );

DELETE FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` target
WHERE target.answer_date BETWEEN event_partition_start AND event_partition_end
  AND target.event_id IN (
    SELECT event_id
    FROM _run_duplicate_fact_keys
    WHERE fact_table = 'answer_events'
  );

UPDATE `${PROJECT_ID}.${DATASET_ID}.pipeline_event_issues` target
SET resolution_status = 'resolved',
    resolved_at = CURRENT_TIMESTAMP(),
    last_run_id = @run_id,
    last_observed_at = CURRENT_TIMESTAMP()
WHERE target.resolution_status = 'open'
  AND target.source_event_hash IN (
    SELECT DISTINCT source_event_hash FROM _run_monitor_source
  )
  AND NOT EXISTS (
    SELECT 1
    FROM _run_event_issues issue
    WHERE issue.source_event_hash = target.source_event_hash
      AND issue.issue_code = target.issue_code
      AND issue.disposition = 'row_quarantined'
  );

MERGE `${PROJECT_ID}.${DATASET_ID}.pipeline_event_issues` target
USING (
  SELECT DISTINCT
    source_event_hash,
    issue_code,
    disposition,
    CASE
      WHEN event_family IN (
        'question_received', 'answer_completed', 'message_persisted', 'answer_action'
      ) THEN event_family
      ELSE 'unsupported'
    END AS event_family,
    source_ts,
    event_ts
  FROM _run_event_issues
) source
ON target.source_event_hash = source.source_event_hash
  AND target.issue_code = source.issue_code
WHEN MATCHED THEN UPDATE SET
  disposition = source.disposition,
  event_family = source.event_family,
  source_ts = source.source_ts,
  event_ts = source.event_ts,
  last_run_id = @run_id,
  last_observed_at = CURRENT_TIMESTAMP(),
  observation_count = IF(
    target.last_run_id = @run_id,
    target.observation_count,
    target.observation_count + 1
  ),
  resolution_status = IF(source.disposition = 'row_quarantined', 'open', 'handled'),
  resolved_at = IF(source.disposition = 'row_quarantined', NULL, CURRENT_TIMESTAMP())
WHEN NOT MATCHED THEN INSERT (
  source_event_hash, issue_code, disposition, event_family, source_ts, event_ts,
  first_run_id, last_run_id, first_observed_at, last_observed_at,
  observation_count, resolution_status, resolved_at
) VALUES (
  source.source_event_hash, source.issue_code, source.disposition,
  source.event_family, source.source_ts, source.event_ts, @run_id, @run_id,
  CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 1,
  IF(source.disposition = 'row_quarantined', 'open', 'handled'),
  IF(source.disposition = 'row_quarantined', NULL, CURRENT_TIMESTAMP())
);

DELETE FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest`
WHERE DATE(observed_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()
  AND run_id = @run_id;

INSERT INTO `${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest` (
  run_id, source_event_hash, event_key_hash, event_family, source_ts,
  event_ts, disposition, observed_at
)
SELECT
  @run_id,
  source.source_event_hash,
  IF(
    NULLIF(source.event_id, '') IS NULL,
    NULL,
    TO_HEX(SHA256(source.event_id))
  ),
  IF(
    source.event_family IN (
      'question_received', 'answer_completed', 'message_persisted',
      'answer_action'
    ),
    source.event_family,
    'unsupported'
  ),
  source.source_ts,
  source.event_effective_ts,
  CASE
    WHEN EXISTS (
      SELECT 1
      FROM _run_event_issues issue
      WHERE issue.source_event_hash = source.source_event_hash
        AND issue.disposition = 'row_quarantined'
    ) THEN 'row_quarantined'
    WHEN source.delivery_row_number > 1 THEN 'deduplicated'
    ELSE 'canonical'
  END,
  CURRENT_TIMESTAMP()
FROM _run_monitor_source source;

MERGE `${PROJECT_ID}.${DATASET_ID}.http_request_events` target
USING (
  SELECT * EXCEPT(request_path, row_number)
  FROM (
    SELECT
      COALESCE(NULLIF(insert_id, ''), TO_HEX(SHA256(CONCAT(CAST(source_ts AS STRING), '|', method, '|', request_url)))) AS event_id,
      source_ts AS request_ts,
      DATE(source_ts, '${MONITOR_TIMEZONE}') AS request_date,
      endpoint_class,
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
    FROM `${PROJECT_ID}.${DATASET_ID}.http_request_source`
    WHERE source_ts >= @window_start AND source_ts < @window_end
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
      event.roster_id,
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
      CAST(NULL AS STRING) AS analytics_contract_version,
      CAST([] AS ARRAY<STRING>) AS classification_reason_codes,
      CAST(NULL AS BOOL) AS is_multi_intent,
      CAST([] AS ARRAY<STRING>) AS analytics_tasks,
      CAST(NULL AS STRING) AS primary_product_key,
      CAST(NULL AS STRING) AS primary_product_name,
      CAST([] AS ARRAY<STRING>) AS product_keys,
      CAST([] AS ARRAY<STRING>) AS product_names,
      CAST(NULL AS INT64) AS product_candidate_count,
      CAST(NULL AS INT64) AS product_resolved_count,
      CAST(NULL AS STRING) AS product_resolution_status,
      CAST([] AS ARRAY<STRING>) AS product_resolution_reason_codes,
      revision_name AS producer_revision,
      git_sha AS producer_git_sha,
      'canonical_event' AS record_origin,
      'question_event' AS measurement_profile,
      source_ts AS source_event_ts,
      CURRENT_TIMESTAMP() AS materialized_at,
      ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC) AS row_number
    FROM _run_admissible_monitor_events event
    WHERE event_family = 'question_received'
  )
  WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.question_date BETWEEN event_partition_start AND event_partition_end
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
  analytics_contract_version, classification_reason_codes,
  is_multi_intent, analytics_tasks, primary_product_key,
  primary_product_name, product_keys, product_names,
  product_candidate_count, product_resolved_count, product_resolution_status,
  product_resolution_reason_codes, producer_revision,
  producer_git_sha, record_origin, measurement_profile, source_event_ts,
  materialized_at
) VALUES (
  source.event_id, source.question_ts, source.question_date, source.user_id,
  source.roster_id, source.request_id, source.trace_id,
  source.conversation_id, source.turn_id, source.message_id, source.mode,
  source.device_class, source.endpoint_class, source.valid_question,
  source.attachment_count, source.primary_question_category,
  source.question_categories, source.classification_status,
  source.analytics_contract_version, source.classification_reason_codes,
  source.is_multi_intent, source.analytics_tasks, source.primary_product_key,
  source.primary_product_name, source.product_keys, source.product_names,
  source.product_candidate_count, source.product_resolved_count,
  source.product_resolution_status, source.product_resolution_reason_codes,
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
      event.roster_id,
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
      JSON_VALUE(payload_json, '$.analytics_contract_version') AS analytics_contract_version,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.classification_reason_codes'), [])) item) AS classification_reason_codes,
      SAFE_CAST(JSON_VALUE(payload_json, '$.is_multi_intent') AS BOOL) AS is_multi_intent,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.analytics_tasks'), [])) item) AS analytics_tasks,
      JSON_VALUE(payload_json, '$.primary_product_key') AS primary_product_key,
      JSON_VALUE(payload_json, '$.primary_product_name') AS primary_product_name,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.product_keys'), [])) item) AS product_keys,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.product_names'), [])) item) AS product_names,
      SAFE_CAST(JSON_VALUE(payload_json, '$.product_candidate_count') AS INT64) AS product_candidate_count,
      SAFE_CAST(JSON_VALUE(payload_json, '$.product_resolved_count') AS INT64) AS product_resolved_count,
      JSON_VALUE(payload_json, '$.product_resolution_status') AS product_resolution_status,
      ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(payload_json, '$.product_resolution_reason_codes'), [])) item) AS product_resolution_reason_codes,
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
    FROM _run_admissible_monitor_events event
    WHERE event_family = 'answer_completed'
  )
  WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.answer_date BETWEEN event_partition_start AND event_partition_end
WHEN MATCHED THEN UPDATE SET
  answer_ts = source.answer_ts, answer_date = source.answer_date, user_id = source.user_id,
  roster_id = source.roster_id, request_id = source.request_id, trace_id = source.trace_id,
  conversation_id = source.conversation_id, turn_id = source.turn_id, message_id = source.message_id,
  mode = source.mode, device_class = source.device_class, terminal = source.terminal,
  runtime_status = source.runtime_status, failure_stage = source.failure_stage, failure_code = source.failure_code,
  primary_question_category = source.primary_question_category, question_categories = source.question_categories,
  classification_status = source.classification_status,
  analytics_contract_version = source.analytics_contract_version,
  classification_reason_codes = source.classification_reason_codes,
  is_multi_intent = source.is_multi_intent,
  analytics_tasks = source.analytics_tasks, primary_product_key = source.primary_product_key,
  primary_product_name = source.primary_product_name, product_keys = source.product_keys,
  product_names = source.product_names, product_candidate_count = source.product_candidate_count,
  product_resolved_count = source.product_resolved_count,
  product_resolution_status = source.product_resolution_status,
  product_resolution_reason_codes = source.product_resolution_reason_codes,
  demand_total = source.demand_total,
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
  primary_question_category, question_categories, classification_status,
  analytics_contract_version, classification_reason_codes, is_multi_intent, analytics_tasks,
  primary_product_key, primary_product_name, product_keys, product_names, product_candidate_count,
  product_resolved_count, product_resolution_status, product_resolution_reason_codes,
  demand_total, delivered_demand_count,
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
  source.analytics_contract_version, source.classification_reason_codes,
  source.is_multi_intent, source.analytics_tasks, source.primary_product_key, source.primary_product_name,
  source.product_keys, source.product_names, source.product_candidate_count, source.product_resolved_count,
  source.product_resolution_status, source.product_resolution_reason_codes,
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
  analytics_contract_version = answer.analytics_contract_version,
  classification_reason_codes = answer.classification_reason_codes,
  is_multi_intent = answer.is_multi_intent,
  analytics_tasks = answer.analytics_tasks,
  primary_product_key = answer.primary_product_key,
  primary_product_name = answer.primary_product_name,
  product_keys = answer.product_keys,
  product_names = answer.product_names,
  product_candidate_count = answer.product_candidate_count,
  product_resolved_count = answer.product_resolved_count,
  product_resolution_status = answer.product_resolution_status,
  product_resolution_reason_codes = answer.product_resolution_reason_codes,
  producer_revision = answer.revision_name,
  producer_git_sha = answer.git_sha,
  materialized_at = CURRENT_TIMESTAMP()
FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
WHERE question.question_date BETWEEN event_partition_start AND event_partition_end
  AND answer.answer_date BETWEEN event_partition_start AND event_partition_end
  AND question.request_id = answer.request_id;

-- Persist outcome is a separate owner and may arrive after answer_completed.
UPDATE `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
SET
  -- Persistence is a monotonic fact. Once any authoritative event for this
  -- exact answer identity confirms TRUE, a delayed/replayed FALSE event must
  -- never turn the published answer back into a failure.
  message_persisted = CASE
    WHEN answer.message_persisted IS TRUE OR persistence.persisted IS TRUE THEN TRUE
    WHEN persistence.persisted IS FALSE THEN FALSE
    ELSE answer.message_persisted
  END,
  assistant_error_present = CASE
    WHEN answer.message_persisted IS TRUE AND persistence.persisted IS NOT TRUE
      THEN answer.assistant_error_present
    ELSE persistence.assistant_error_present
  END,
  persistence_error_code = CASE
    WHEN answer.message_persisted IS TRUE AND persistence.persisted IS NOT TRUE
      THEN answer.persistence_error_code
    ELSE persistence.error_code
  END,
  materialized_at = CURRENT_TIMESTAMP()
FROM (
  SELECT * EXCEPT(source_ts, insert_id)
  FROM (
    SELECT request_id, conversation_id, message_id,
      SAFE_CAST(JSON_VALUE(payload_json, '$.answer_ts') AS TIMESTAMP) AS answer_ts,
      SAFE_CAST(JSON_VALUE(payload_json, '$.persisted') AS BOOL) AS persisted,
      SAFE_CAST(JSON_VALUE(payload_json, '$.assistant_error_present') AS BOOL) AS assistant_error_present,
      JSON_VALUE(payload_json, '$.error_code') AS error_code,
      source_ts,
      insert_id
    FROM _run_admissible_monitor_events
    WHERE event_family = 'message_persisted'
  ) persistence_candidates
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY request_id, conversation_id, message_id
    ORDER BY IF(persisted IS TRUE, 1, 0) DESC, source_ts DESC, insert_id DESC
  ) = 1
) persistence
WHERE persistence.answer_ts IS NOT NULL
  AND answer.answer_date BETWEEN persistence_partition_start AND persistence_partition_end
  AND answer.answer_date = DATE(persistence.answer_ts, '${MONITOR_TIMEZONE}')
  AND answer.request_id = persistence.request_id
  AND answer.conversation_id = persistence.conversation_id
  AND COALESCE(answer.message_id, '') = COALESCE(persistence.message_id, '');

-- One official complete-delivery owner and one failure priority.
UPDATE `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
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
WHERE answer.answer_date BETWEEN affected_answer_partition_start AND affected_answer_partition_end;

MERGE `${PROJECT_ID}.${DATASET_ID}.answer_action_events` target
USING (
  SELECT * EXCEPT(row_number)
  FROM (
    SELECT event_id, COALESCE(event_ts, source_ts) AS action_ts,
      DATE(COALESCE(event_ts, source_ts), '${MONITOR_TIMEZONE}') AS action_date,
      event.user_id, event.roster_id, request_id, conversation_id, turn_id, message_id,
      JSON_VALUE(payload_json, '$.target_message_id') AS target_message_id,
      JSON_VALUE(payload_json, '$.action') AS action,
      JSON_VALUE(payload_json, '$.feedback') AS feedback,
      mode, JSON_VALUE(payload_json, '$.request_mode') AS request_mode,
      JSON_VALUE(payload_json, '$.client_origin') AS client_origin,
      source_ts AS source_event_ts, CURRENT_TIMESTAMP() AS materialized_at,
      ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC) AS row_number
    FROM _run_admissible_monitor_events event
    WHERE event_family = 'answer_action'
  ) WHERE row_number = 1
) source
ON target.event_id = source.event_id
AND target.action_date BETWEEN event_partition_start AND event_partition_end
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
    event.user_id, event.roster_id,
    JSON_VALUE(demand, '$.demand_id') AS demand_id,
    demand_order,
    JSON_VALUE(demand, '$.question_category') AS question_category,
    JSON_VALUE(demand, '$.analytics_task') AS analytics_task,
    JSON_VALUE(demand, '$.analytics_contract_version') AS analytics_contract_version,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.classification_reason_codes'), [])) item) AS classification_reason_codes,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.product_keys'), [])) item) AS product_keys,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.product_names'), [])) item) AS product_names,
    JSON_VALUE(demand, '$.product_resolution_status') AS product_resolution_status,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.product_resolution_reason_codes'), [])) item) AS product_resolution_reason_codes,
    JSON_VALUE(demand, '$.requirement') AS requirement,
    JSON_VALUE(demand, '$.delivery_state') AS delivery_state,
    JSON_VALUE(demand, '$.evidence_state') AS evidence_state,
    JSON_VALUE(demand, '$.system_fault') AS system_fault,
    ARRAY(SELECT JSON_VALUE(item) FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(demand, '$.reason_codes'), [])) item) AS reason_codes,
    event.source_ts AS source_event_ts, CURRENT_TIMESTAMP() AS materialized_at
  FROM _run_admissible_monitor_events event
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(event.payload_json, '$.demands'), [])) demand WITH OFFSET demand_order
  WHERE event.event_family = 'answer_completed'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY event.event_id, demand_order ORDER BY event.source_ts DESC, event.insert_id DESC) = 1
) source
ON target.event_id = source.event_id
AND target.question_date BETWEEN event_partition_start AND event_partition_end
WHEN MATCHED THEN UPDATE SET question_event_id = source.question_event_id, question_ts = source.question_ts,
  question_date = source.question_date, user_id = source.user_id, roster_id = source.roster_id,
  demand_id = source.demand_id, demand_order = source.demand_order, question_category = source.question_category,
  analytics_task = source.analytics_task, analytics_contract_version = source.analytics_contract_version,
  classification_reason_codes = source.classification_reason_codes,
  product_keys = source.product_keys, product_names = source.product_names,
  product_resolution_status = source.product_resolution_status,
  product_resolution_reason_codes = source.product_resolution_reason_codes,
  requirement = source.requirement, delivery_state = source.delivery_state, evidence_state = source.evidence_state,
  system_fault = source.system_fault, reason_codes = source.reason_codes,
  source_event_ts = source.source_event_ts, materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT (
  event_id, question_event_id, question_ts, question_date, user_id,
  roster_id, demand_id, demand_order, question_category, analytics_task,
  analytics_contract_version, classification_reason_codes,
  product_keys, product_names, product_resolution_status,
  product_resolution_reason_codes, requirement, delivery_state, evidence_state,
  system_fault, reason_codes, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.question_event_id, source.question_ts,
  source.question_date, source.user_id, source.roster_id, source.demand_id,
  source.demand_order, source.question_category, source.analytics_task,
  source.analytics_contract_version, source.classification_reason_codes,
  source.product_keys, source.product_names, source.product_resolution_status,
  source.product_resolution_reason_codes, source.requirement,
  source.delivery_state, source.evidence_state, source.system_fault,
  source.reason_codes, source.source_event_ts, source.materialized_at
);
