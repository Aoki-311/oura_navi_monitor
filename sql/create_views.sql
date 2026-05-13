CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_requests` AS
SELECT
  timestamp AS ts,
  SAFE_CAST(httpRequest.status AS INT64) AS status,
  CAST(httpRequest.requestMethod AS STRING) AS method,
  CAST(httpRequest.requestUrl AS STRING) AS request_url,
  SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
  LOWER(CAST(httpRequest.userAgent AS STRING)) AS user_agent,
  CASE
    WHEN REGEXP_CONTAINS(LOWER(CAST(httpRequest.userAgent AS STRING)), r'(iphone|android|mobile|ipad)') THEN 'mobile'
    WHEN CAST(httpRequest.userAgent AS STRING) IS NULL OR CAST(httpRequest.userAgent AS STRING) = '' THEN 'unknown'
    ELSE 'desktop'
  END AS device_class,
  resource.labels.service_name AS service_name
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_requests`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__';

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_query_suggest_results` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"mode=([^ ]+)") AS mode,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stable=([^ ]+)") AS stable,
  CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64) AS latency_ms,
  CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS INT64) AS suggestion_count
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_result ");

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_query_suggest_degraded` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"reason=([^ ]+)") AS reason,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"fallback=([^ ]+)") AS fallback_source,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_refine_degraded ");

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_sync_telemetry` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"event=([^ ]+)") AS event,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"detail=(\{.*\})$") AS detail_json
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^chat_sync_telemetry ");

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_ask_audit_events` AS
WITH src AS (
  SELECT
    timestamp AS event_ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^ask_audit_json=(.*)$")) AS payload,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^ask_audit_json=(.*)$") AS raw_payload_json
  FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = '__SERVICE_NAME__'
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^ask_audit_json=")
)
SELECT
  event_ts,
  DATE(event_ts, 'Asia/Tokyo') AS event_date,
  'ask_audit_json' AS event_family,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.ask_audit_schema_version'), ''), 'unknown') AS schema_version,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.ask_audit_schema_version'), ''), 'unknown') AS ask_audit_schema_version,
  NULLIF(JSON_VALUE(payload, '$.analytics_projection_version'), '') AS analytics_projection_version,
  NULLIF(JSON_VALUE(payload, '$.trace_id'), '') AS trace_id,
  NULLIF(JSON_VALUE(payload, '$.request_id'), '') AS request_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), '')) AS conversation_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.session_id'), ''), NULLIF(JSON_VALUE(payload, '$.conversation_id'), '')) AS session_id,
  NULLIF(JSON_VALUE(payload, '$.turn_id'), '') AS turn_id,
  NULLIF(JSON_VALUE(payload, '$.parent_turn_id'), '') AS parent_turn_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), NULLIF(JSON_VALUE(payload, '$.assistant_message_id'), '')) AS message_id,
  NULLIF(JSON_VALUE(payload, '$.assistant_message_id'), '') AS assistant_message_id,
  NULLIF(JSON_VALUE(payload, '$.user_id'), '') AS user_id,
  NULLIF(JSON_VALUE(payload, '$.user_id_hash'), '') AS user_id_hash,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
  NULLIF(JSON_VALUE(payload, '$.query_hash'), '') AS query_hash,
  NULLIF(JSON_VALUE(payload, '$.query_lang'), '') AS query_lang,
  SAFE_CAST(JSON_VALUE(payload, '$.query_length') AS INT64) AS query_length,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.intent_family'), ''), 'unknown')) AS intent_family,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.primary_task_intent'), ''), 'unknown')) AS primary_task_intent,
  JSON_VALUE_ARRAY(payload, '$.planner_intents') AS planner_intents,
  JSON_VALUE_ARRAY(payload, '$.structured_tasks') AS structured_tasks,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.route_path'), ''), 'unknown')) AS route_path,
  JSON_VALUE_ARRAY(payload, '$.channel_reason_codes') AS channel_reason_codes,
  JSON_QUERY(payload, '$.channel_plan') AS channel_plan_json,
  JSON_QUERY(payload, '$.final_channel_mix') AS final_channel_mix_json,
  SAFE_CAST(JSON_VALUE(payload, '$.structured_hit_count') AS INT64) AS structured_hit_count,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.structured_led') AS BOOL), FALSE) AS structured_led,
  SAFE_CAST(JSON_VALUE(payload, '$.evidence_doc_count') AS INT64) AS evidence_doc_count,
  SAFE_CAST(JSON_VALUE(payload, '$.evidence_structured_count') AS INT64) AS evidence_structured_count,
  SAFE_CAST(JSON_VALUE(payload, '$.citation_count') AS INT64) AS citation_count,
  JSON_QUERY(payload, '$.claim_evidence_summary') AS claim_evidence_summary_json,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.structured_answer_present') AS BOOL), FALSE) AS structured_answer_present,
  NULLIF(JSON_VALUE(payload, '$.structured_answer_profile'), '') AS structured_answer_profile,
  NULLIF(JSON_VALUE(payload, '$.structured_answer_version'), '') AS structured_answer_version,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.canonical_markdown_used_for_grounding') AS BOOL), FALSE) AS canonical_markdown_used_for_grounding,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.canonical_markdown_changed_post_grounding') AS BOOL), FALSE) AS canonical_markdown_changed_post_grounding,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.canonical_post_grounding_change_allowed') AS BOOL), FALSE) AS canonical_post_grounding_change_allowed,
  JSON_VALUE_ARRAY(payload, '$.post_grounding_mutation_reason_codes') AS post_grounding_mutation_reason_codes,
  NULLIF(JSON_VALUE(payload, '$.citation_binding_version'), '') AS citation_binding_version,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.claim_alignment_mode'), ''), 'unknown')) AS claim_alignment_mode,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.claim_alignment_fallback') AS BOOL), FALSE) AS claim_alignment_fallback,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.citation_mapping_source'), ''), 'unknown')) AS citation_mapping_source,
  JSON_QUERY(payload, '$.markdown_integrity') AS markdown_integrity_json,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.answerability_level'), ''), NULLIF(JSON_VALUE(payload, '$.governance.answerability_level'), ''), 'unknown')) AS answerability_level,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.usability_level'), ''), NULLIF(JSON_VALUE(payload, '$.governance.usability_level'), ''), 'unknown')) AS usability_level,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.delivery_readiness'), ''), NULLIF(JSON_VALUE(payload, '$.governance.delivery_readiness'), ''), 'unknown')) AS delivery_readiness,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.evidence_sufficiency'), ''), NULLIF(JSON_VALUE(payload, '$.governance.evidence_sufficiency'), ''), 'unknown')) AS evidence_sufficiency,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.survivable_telemetry.verification_verdict'), ''), NULLIF(JSON_VALUE(payload, '$.verification_verdict'), ''), 'unknown')) AS verification_verdict,
  SAFE_CAST(COALESCE(JSON_VALUE(payload, '$.coverage_score'), JSON_VALUE(payload, '$.survivable_telemetry.coverage_score')) AS FLOAT64) AS coverage_score,
  SAFE_CAST(COALESCE(JSON_VALUE(payload, '$.alignment_score'), JSON_VALUE(payload, '$.survivable_telemetry.alignment_score')) AS FLOAT64) AS alignment_score,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.primary_reason_code'), ''), NULLIF(JSON_VALUE(payload, '$.governance.primary_reason_code'), ''), 'unknown')) AS primary_reason_code,
  JSON_VALUE_ARRAY(payload, '$.secondary_reason_codes') AS secondary_reason_codes,
  NULLIF(JSON_VALUE(payload, '$.error_code'), '') AS error_code,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '')) AS conversation_turn_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), NULLIF(JSON_VALUE(payload, '$.assistant_message_id'), ''), '')) AS conversation_message_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.request_id'), ''), '')) AS trace_request_key,
  raw_payload_json
FROM src
WHERE payload IS NOT NULL;

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_followup_resolution_events` AS
WITH src AS (
  SELECT
    timestamp AS event_ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_resolution_json=(.*)$")) AS payload,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_resolution_json=(.*)$") AS raw_payload_json
  FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = '__SERVICE_NAME__'
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^followup_resolution_json=")
)
SELECT
  event_ts,
  DATE(event_ts, 'Asia/Tokyo') AS event_date,
  'followup_resolution_json' AS event_family,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.followup_resolution_schema_version'), ''), 'unknown') AS schema_version,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.followup_resolution_schema_version'), ''), 'unknown') AS followup_resolution_schema_version,
  NULLIF(JSON_VALUE(payload, '$.trace_id'), '') AS trace_id,
  NULLIF(JSON_VALUE(payload, '$.request_id'), '') AS request_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), '')) AS conversation_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.session_id'), ''), NULLIF(JSON_VALUE(payload, '$.conversation_id'), '')) AS session_id,
  NULLIF(JSON_VALUE(payload, '$.turn_id'), '') AS turn_id,
  NULLIF(JSON_VALUE(payload, '$.parent_turn_id'), '') AS parent_turn_id,
  NULLIF(JSON_VALUE(payload, '$.message_id'), '') AS message_id,
  NULLIF(JSON_VALUE(payload, '$.anchor_message_id'), '') AS anchor_message_id,
  NULLIF(JSON_VALUE(payload, '$.anchor_turn_id'), '') AS anchor_turn_id,
  NULLIF(JSON_VALUE(payload, '$.user_id'), '') AS user_id,
  NULLIF(JSON_VALUE(payload, '$.user_id_hash'), '') AS user_id_hash,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.decision_normalized'), ''), NULLIF(JSON_VALUE(payload, '$.decision'), ''), 'unknown')) AS decision_normalized,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.decision'), ''), 'unknown')) AS decision,
  JSON_VALUE_ARRAY(payload, '$.reason_codes') AS reason_codes,
  NULLIF(JSON_VALUE(payload, '$.entity_lock_key'), '') AS entity_lock_key,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.entity_lock_inherited') AS BOOL), FALSE) AS entity_lock_inherited,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.entity_lock_level'), ''), 'unknown')) AS entity_lock_level,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.temporal_context_inherited') AS BOOL), FALSE) AS temporal_context_inherited,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.answer_structure_profile_inherited') AS BOOL), FALSE) AS answer_structure_profile_inherited,
  JSON_VALUE_ARRAY(payload, '$.inherited_fields') AS inherited_fields,
  JSON_VALUE_ARRAY(payload, '$.dropped_fields') AS dropped_fields,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.facet_cleaned') AS BOOL), FALSE) AS facet_cleaned,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.facet_forbidden') AS BOOL), FALSE) AS facet_forbidden,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.conversation_expand_enabled') AS BOOL), FALSE) AS conversation_expand_enabled,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.followup_offtopic') AS BOOL), FALSE) AS followup_offtopic,
  JSON_QUERY(payload, '$.structured_answer_summary') AS structured_answer_summary_json,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '')) AS conversation_turn_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '')) AS conversation_message_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.request_id'), ''), '')) AS trace_request_key,
  raw_payload_json
FROM src
WHERE payload IS NOT NULL;

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_followup_open_result_events` AS
WITH src AS (
  SELECT
    timestamp AS event_ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_open_result_json=(.*)$")) AS payload,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^followup_open_result_json=(.*)$") AS raw_payload_json
  FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = '__SERVICE_NAME__'
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^followup_open_result_json=")
)
SELECT
  event_ts,
  DATE(event_ts, 'Asia/Tokyo') AS event_date,
  'followup_open_result_json' AS event_family,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.followup_open_result_schema_version'), ''), 'unknown') AS schema_version,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.followup_open_result_schema_version'), ''), 'unknown') AS followup_open_result_schema_version,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.event'), ''), 'unknown')) AS event,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.success') AS BOOL), FALSE) AS success,
  NULLIF(JSON_VALUE(payload, '$.trace_id'), '') AS trace_id,
  NULLIF(JSON_VALUE(payload, '$.request_id'), '') AS request_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), '')) AS conversation_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.session_id'), ''), NULLIF(JSON_VALUE(payload, '$.conversation_id'), '')) AS session_id,
  NULLIF(JSON_VALUE(payload, '$.turn_id'), '') AS turn_id,
  NULLIF(JSON_VALUE(payload, '$.parent_turn_id'), '') AS parent_turn_id,
  NULLIF(JSON_VALUE(payload, '$.message_id'), '') AS message_id,
  NULLIF(JSON_VALUE(payload, '$.anchor_turn_id'), '') AS anchor_turn_id,
  NULLIF(JSON_VALUE(payload, '$.user_id'), '') AS user_id,
  NULLIF(JSON_VALUE(payload, '$.user_id_hash'), '') AS user_id_hash,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.state_action'), ''), 'unknown')) AS state_action,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.decision'), ''), 'unknown')) AS decision,
  NULLIF(JSON_VALUE(payload, '$.error_code'), '') AS error_code,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '')) AS conversation_turn_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '')) AS conversation_message_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.request_id'), ''), '')) AS trace_request_key,
  raw_payload_json
FROM src
WHERE payload IS NOT NULL;

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_coverage_gap_workitems` AS
WITH src AS (
  SELECT
    timestamp AS event_ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^coverage_gap_workitem_json=(.*)$")) AS payload,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^coverage_gap_workitem_json=(.*)$") AS raw_payload_json
  FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = '__SERVICE_NAME__'
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^coverage_gap_workitem_json=")
)
SELECT
  event_ts,
  DATE(event_ts, 'Asia/Tokyo') AS event_date,
  'coverage_gap_workitem_json' AS event_family,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.coverage_gap_workitem_schema_version'), ''), 'unknown') AS schema_version,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.coverage_gap_workitem_schema_version'), ''), 'unknown') AS coverage_gap_workitem_schema_version,
  NULLIF(JSON_VALUE(payload, '$.trace_id'), '') AS trace_id,
  NULLIF(JSON_VALUE(payload, '$.request_id'), '') AS request_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), '')) AS conversation_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.session_id'), ''), NULLIF(JSON_VALUE(payload, '$.conversation_id'), '')) AS session_id,
  NULLIF(JSON_VALUE(payload, '$.turn_id'), '') AS turn_id,
  NULLIF(JSON_VALUE(payload, '$.parent_turn_id'), '') AS parent_turn_id,
  NULLIF(JSON_VALUE(payload, '$.message_id'), '') AS message_id,
  NULLIF(JSON_VALUE(payload, '$.user_id'), '') AS user_id,
  NULLIF(JSON_VALUE(payload, '$.user_id_hash'), '') AS user_id_hash,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
  NULLIF(JSON_VALUE(payload, '$.query_hash'), '') AS query_hash,
  NULLIF(JSON_VALUE(payload, '$.query_lang'), '') AS query_lang,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.gap_kind'), ''), 'unknown')) AS gap_kind,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.intent_family'), ''), 'unknown')) AS intent_family,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.primary_task_intent'), ''), 'unknown')) AS primary_task_intent,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.primary_reason_code'), ''), 'unknown')) AS primary_reason_code,
  JSON_VALUE_ARRAY(payload, '$.secondary_reason_codes') AS secondary_reason_codes,
  JSON_VALUE_ARRAY(payload, '$.open_issues') AS open_issues,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.evidence_sufficiency'), ''), 'unknown')) AS evidence_sufficiency,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.clarification_required') AS BOOL), FALSE) AS clarification_required,
  JSON_VALUE_ARRAY(payload, '$.structured_tasks') AS structured_tasks,
  SAFE_CAST(JSON_VALUE(payload, '$.structured_hit_count') AS INT64) AS structured_hit_count,
  SAFE_CAST(JSON_VALUE(payload, '$.evidence_doc_count') AS INT64) AS evidence_doc_count,
  SAFE_CAST(JSON_VALUE(payload, '$.evidence_structured_count') AS INT64) AS evidence_structured_count,
  SAFE_CAST(JSON_VALUE(payload, '$.citation_count') AS INT64) AS citation_count,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.route_path'), ''), 'unknown')) AS route_path,
  JSON_QUERY(payload, '$.final_channel_mix') AS final_channel_mix_json,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.owner_bucket'), ''), 'unknown')) AS owner_bucket,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.artifact_type'), ''), 'unknown')) AS artifact_type,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.status'), ''), 'open')) AS status,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.priority'), ''), 'unknown')) AS priority,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '')) AS conversation_turn_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '')) AS conversation_message_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.request_id'), ''), '')) AS trace_request_key,
  raw_payload_json
FROM src
WHERE payload IS NOT NULL;

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_request_user_metric_events` AS
WITH src AS (
  SELECT
    timestamp AS event_ts,
    SAFE.PARSE_JSON(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$")) AS payload,
    REGEXP_EXTRACT(CAST(textPayload AS STRING), r"^request_user_metric_json=(.*)$") AS raw_payload_json
  FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
  WHERE resource.type = 'cloud_run_revision'
    AND resource.labels.service_name = '__SERVICE_NAME__'
    AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^request_user_metric_json=")
)
SELECT
  event_ts,
  DATE(event_ts, 'Asia/Tokyo') AS event_date,
  'request_user_metric_json' AS event_family,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.schema_version'), ''), 'request_user_metric.v1') AS schema_version,
  NULLIF(JSON_VALUE(payload, '$.trace_id'), '') AS trace_id,
  NULLIF(JSON_VALUE(payload, '$.request_id'), '') AS request_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), '')) AS conversation_id,
  COALESCE(NULLIF(JSON_VALUE(payload, '$.session_id'), ''), NULLIF(JSON_VALUE(payload, '$.conversation_id'), '')) AS session_id,
  NULLIF(JSON_VALUE(payload, '$.turn_id'), '') AS turn_id,
  NULLIF(JSON_VALUE(payload, '$.message_id'), '') AS message_id,
  NULLIF(JSON_VALUE(payload, '$.user_id'), '') AS user_id,
  NULLIF(JSON_VALUE(payload, '$.user_id_hash'), '') AS user_id_hash,
  LOWER(NULLIF(JSON_VALUE(payload, '$.user_email'), '')) AS user_email,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.mode'), ''), 'unknown')) AS mode,
  LOWER(COALESCE(NULLIF(JSON_VALUE(payload, '$.device_class'), ''), 'unknown')) AS device_class,
  COALESCE(SAFE_CAST(JSON_VALUE(payload, '$.is_core') AS BOOL), FALSE) AS is_core,
  NULLIF(JSON_VALUE(payload, '$.method'), '') AS method,
  NULLIF(JSON_VALUE(payload, '$.path'), '') AS path,
  SAFE_CAST(JSON_VALUE(payload, '$.status') AS INT64) AS status,
  SAFE_CAST(JSON_VALUE(payload, '$.latency_ms') AS FLOAT64) AS latency_ms,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.turn_id'), ''), '')) AS conversation_turn_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.conversation_id'), ''), NULLIF(JSON_VALUE(payload, '$.session_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.message_id'), ''), '')) AS conversation_message_key,
  CONCAT(COALESCE(NULLIF(JSON_VALUE(payload, '$.trace_id'), ''), ''), '#', COALESCE(NULLIF(JSON_VALUE(payload, '$.request_id'), ''), '')) AS trace_request_key,
  raw_payload_json
FROM src
WHERE payload IS NOT NULL;

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_monitor_event_message_join_keys` AS
SELECT
  event_ts,
  event_date,
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
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `__PROJECT_ID__.__DATASET_ID__.v_ask_audit_events`
UNION ALL
SELECT
  event_ts,
  event_date,
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
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_resolution_events`
UNION ALL
SELECT
  event_ts,
  event_date,
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
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_open_result_events`
UNION ALL
SELECT
  event_ts,
  event_date,
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
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `__PROJECT_ID__.__DATASET_ID__.v_coverage_gap_workitems`
UNION ALL
SELECT
  event_ts,
  event_date,
  event_family,
  schema_version,
  trace_id,
  request_id,
  conversation_id,
  session_id,
  turn_id,
  CAST(NULL AS STRING) AS parent_turn_id,
  message_id,
  user_id,
  user_id_hash,
  mode,
  conversation_turn_key,
  conversation_message_key,
  trace_request_key
FROM `__PROJECT_ID__.__DATASET_ID__.v_request_user_metric_events`;
