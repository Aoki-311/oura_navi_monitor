CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_answer_events`
PARTITION BY event_date
CLUSTER BY user_id, conversation_id, trace_id AS
WITH gap_keys AS (
  SELECT DISTINCT
    NULLIF(trace_request_key, '#') AS trace_request_key,
    NULLIF(conversation_turn_key, '#') AS conversation_turn_key,
    NULLIF(conversation_message_key, '#') AS conversation_message_key
  FROM `__PROJECT_ID__.__DATASET_ID__.v_coverage_gap_workitems`
),
answer_actions AS (
  SELECT
    NULLIF(trace_request_key, '#') AS trace_request_key,
    NULLIF(conversation_turn_key, '#') AS conversation_turn_key,
    NULLIF(conversation_message_key, '#') AS conversation_message_key,
    LOGICAL_OR(event = 'feedback_submitted' AND feedback = 'bad') AS has_bad_feedback,
    LOGICAL_OR(event = 'regenerate_requested') AS has_regenerate_request,
    LOGICAL_OR(event = 'enhance_requested') AS has_enhance_request,
    LOGICAL_OR(event = 'correction_requested') AS has_correction_request
  FROM `__PROJECT_ID__.__DATASET_ID__.v_answer_action_events`
  GROUP BY trace_request_key, conversation_turn_key, conversation_message_key
),
answer_success_cutover AS (
  SELECT
    -- lcs-rag-app was cut over to the answer-action capable revision at this time.
    TIMESTAMP('__ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS__') AS official_cutover_ts
),
answer_base AS (
  SELECT
    a.*,
    (
      gt.trace_request_key IS NOT NULL
      OR gturn.conversation_turn_key IS NOT NULL
      OR gmsg.conversation_message_key IS NOT NULL
    ) AS has_coverage_gap,
    EXISTS (
      SELECT 1
      FROM answer_actions act
      WHERE act.has_bad_feedback
        AND (
          act.trace_request_key = NULLIF(a.trace_request_key, '#')
          OR act.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
          OR act.conversation_message_key = NULLIF(a.conversation_message_key, '#')
        )
    ) AS has_bad_feedback,
    EXISTS (
      SELECT 1
      FROM answer_actions act
      WHERE act.has_regenerate_request
        AND (
          act.trace_request_key = NULLIF(a.trace_request_key, '#')
          OR act.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
          OR act.conversation_message_key = NULLIF(a.conversation_message_key, '#')
        )
    ) AS has_regenerate_request,
    EXISTS (
      SELECT 1
      FROM answer_actions act
      WHERE act.has_enhance_request
        AND (
          act.trace_request_key = NULLIF(a.trace_request_key, '#')
          OR act.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
          OR act.conversation_message_key = NULLIF(a.conversation_message_key, '#')
        )
    ) AS has_enhance_request,
    EXISTS (
      SELECT 1
      FROM answer_actions act
      WHERE act.has_correction_request
        AND (
          act.trace_request_key = NULLIF(a.trace_request_key, '#')
          OR act.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
          OR act.conversation_message_key = NULLIF(a.conversation_message_key, '#')
        )
    ) AS has_correction_request
  FROM `__PROJECT_ID__.__DATASET_ID__.v_ask_audit_events` a
  LEFT JOIN gap_keys gt
    ON gt.trace_request_key = NULLIF(a.trace_request_key, '#')
  LEFT JOIN gap_keys gturn
    ON gturn.conversation_turn_key = NULLIF(a.conversation_turn_key, '#')
  LEFT JOIN gap_keys gmsg
    ON gmsg.conversation_message_key = NULLIF(a.conversation_message_key, '#')
  WHERE NOT (
    LOWER(COALESCE(a.user_id, '')) = 'unknown'
    OR LOWER(COALESCE(a.user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(a.user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(a.user_id, '')) = '2401145@tc.terumo.co.jp'
    OR REGEXP_CONTAINS(LOWER(COALESCE(a.user_id, '')), r'lcs-agent')
  )
)
SELECT
  event_ts,
  event_date,
  event_family,
  schema_version,
  ask_audit_schema_version,
  analytics_projection_version,
  trace_id,
  request_id,
  conversation_id,
  session_id,
  turn_id,
  parent_turn_id,
  message_id,
  assistant_message_id,
  user_id,
  user_id_hash,
  mode,
  query_hash,
  query_lang,
  query_length,
  intent_family,
  primary_task_intent,
  planner_intents,
  structured_tasks,
  route_path,
  channel_reason_codes,
  channel_plan_json,
  final_channel_mix_json,
  structured_hit_count,
  structured_led,
  evidence_doc_count,
  evidence_structured_count,
  citation_count,
  claim_evidence_summary_json,
  structured_answer_present,
  structured_answer_profile,
  structured_answer_version,
  canonical_markdown_used_for_grounding,
  canonical_markdown_changed_post_grounding,
  canonical_post_grounding_change_allowed,
  post_grounding_mutation_reason_codes,
  citation_binding_version,
  claim_alignment_mode,
  claim_alignment_fallback,
  citation_mapping_source,
  markdown_integrity_json,
  answerability_level,
  usability_level,
  delivery_readiness,
  evidence_sufficiency,
  verification_verdict,
  coverage_score,
  alignment_score,
  primary_reason_code,
  secondary_reason_codes,
  error_code,
  error_code IS NOT NULL AS has_error,
  has_bad_feedback,
  has_regenerate_request,
  has_enhance_request,
  has_correction_request,
  has_coverage_gap,
  (
    has_coverage_gap
    OR COALESCE(citation_count, 0) = 0
    OR evidence_sufficiency = 'insufficient'
    OR COALESCE(coverage_score, 1.0) < 0.60
  ) AS low_coverage_flag,
  CASE
    WHEN event_ts >= (SELECT official_cutover_ts FROM answer_success_cutover) THEN
      error_code IS NULL
      AND NOT has_bad_feedback
      AND NOT has_regenerate_request
      AND NOT has_enhance_request
      AND NOT has_correction_request
    ELSE
      error_code IS NULL
      AND answerability_level NOT IN ('not_answerable', 'clarification_blocked')
      AND NOT has_bad_feedback
      AND NOT has_regenerate_request
      AND NOT has_enhance_request
      AND NOT has_correction_request
  END AS answer_success_flag,
  ARRAY_CONCAT(
    IF(error_code IS NOT NULL, ['error'], []),
    IF(has_bad_feedback, ['bad_feedback'], []),
    IF(has_regenerate_request, ['regenerate_requested'], []),
    IF(has_enhance_request, ['enhance_requested'], []),
    IF(has_correction_request, ['correction_requested'], []),
    IF(
      event_ts < (SELECT official_cutover_ts FROM answer_success_cutover)
      AND answerability_level IN ('not_answerable', 'clarification_blocked'),
      ['proxy_answerability_failure'],
      []
    )
  ) AS answer_success_reason_codes,
  CASE
    WHEN event_ts >= (SELECT official_cutover_ts FROM answer_success_cutover) THEN 'official'
    ELSE 'proxy'
  END AS answer_success_metric_status,
  conversation_turn_key,
  conversation_message_key,
  trace_request_key,
  raw_payload_json,
  CURRENT_TIMESTAMP() AS materialized_at
FROM answer_base;

CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_user_daily`
PARTITION BY date_jst
CLUSTER BY user_id, user_email AS
WITH request_daily AS (
  SELECT
    event_date AS date_jst,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    LOWER(COALESCE(NULLIF(user_email, ''), '')) AS user_email,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    COUNT(*) AS request_count,
    COUNTIF(is_core) AS message_count,
    COUNTIF(is_core AND mode = 'internal') AS internal_message_count,
    COUNTIF(is_core AND mode = 'websearch') AS websearch_message_count,
    COUNTIF(device_class = 'desktop') AS desktop_request_count,
    COUNTIF(device_class = 'mobile') AS mobile_request_count,
    COUNTIF(device_class = 'unknown') AS unknown_request_count,
    MAX(event_ts) AS last_active_at
  FROM `__PROJECT_ID__.__DATASET_ID__.v_request_user_metric_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
    OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
  )
  GROUP BY date_jst, user_id, user_email, user_id_hash
),
answer_daily AS (
  SELECT
    event_date AS date_jst,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    COUNT(*) AS answer_count,
    COUNTIF(answer_success_flag) AS answer_success_count,
    COUNTIF(low_coverage_flag) AS low_coverage_count,
    COUNTIF(has_error) AS answer_error_count,
    COUNTIF(has_bad_feedback) AS bad_feedback_count,
    COUNTIF(has_bad_feedback OR has_regenerate_request OR has_enhance_request OR has_correction_request) AS feedback_count,
    COUNTIF(structured_led) AS structured_led_count,
    SUM(COALESCE(citation_count, 0)) AS citation_count_sum
  FROM `__PROJECT_ID__.__DATASET_ID__.monitor_answer_events`
  GROUP BY date_jst, user_id, user_id_hash
),
followup_open_daily AS (
  SELECT
    event_date AS date_jst,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    COUNTIF(event = 'recognized') AS followup_recognized_count,
    COUNTIF(event = 'success') AS followup_success_count
  FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_open_result_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
  )
  GROUP BY date_jst, user_id, user_id_hash
),
followup_resolution_daily AS (
  SELECT
    event_date AS date_jst,
    COALESCE(NULLIF(user_id, ''), 'unknown') AS user_id,
    COALESCE(NULLIF(user_id_hash, ''), '') AS user_id_hash,
    COUNTIF(decision_normalized = 'explicit_correction') AS explicit_correction_count,
    COUNTIF(decision_normalized = 'clarify_before_carry') AS clarification_required_count,
    COUNTIF(followup_offtopic) AS followup_offtopic_count
  FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_resolution_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
  )
  GROUP BY date_jst, user_id, user_id_hash
),
keys AS (
  SELECT date_jst, user_id, user_id_hash FROM request_daily
  UNION DISTINCT
  SELECT date_jst, user_id, user_id_hash FROM answer_daily
  UNION DISTINCT
  SELECT date_jst, user_id, user_id_hash FROM followup_open_daily
  UNION DISTINCT
  SELECT date_jst, user_id, user_id_hash FROM followup_resolution_daily
)
SELECT
  k.date_jst,
  k.user_id,
  COALESCE(r.user_email, '') AS user_email,
  COALESCE(NULLIF(k.user_id_hash, ''), r.user_id_hash, a.user_id_hash, fo.user_id_hash, fr.user_id_hash, '') AS user_id_hash,
  COALESCE(r.request_count, 0) AS request_count,
  COALESCE(r.message_count, 0) AS message_count,
  COALESCE(r.message_count, 0) > 0 AS active_flag,
  COALESCE(r.internal_message_count, 0) AS internal_message_count,
  COALESCE(r.websearch_message_count, 0) AS websearch_message_count,
  COALESCE(r.desktop_request_count, 0) AS desktop_request_count,
  COALESCE(r.mobile_request_count, 0) AS mobile_request_count,
  COALESCE(r.unknown_request_count, 0) AS unknown_request_count,
  COALESCE(a.answer_count, 0) AS answer_count,
  COALESCE(a.answer_success_count, 0) AS answer_success_count,
  COALESCE(a.low_coverage_count, 0) AS low_coverage_count,
  COALESCE(a.answer_error_count, 0) AS answer_error_count,
  COALESCE(a.structured_led_count, 0) AS structured_led_count,
  COALESCE(a.citation_count_sum, 0) AS citation_count_sum,
  COALESCE(a.bad_feedback_count, 0) AS bad_feedback_count,
  COALESCE(a.feedback_count, 0) AS feedback_count,
  COALESCE(fo.followup_recognized_count, 0) AS followup_recognized_count,
  COALESCE(fo.followup_success_count, 0) AS followup_success_count,
  COALESCE(fr.explicit_correction_count, 0) AS explicit_correction_count,
  COALESCE(fr.clarification_required_count, 0) AS clarification_required_count,
  COALESCE(fr.followup_offtopic_count, 0) AS followup_offtopic_count,
  r.last_active_at,
  CURRENT_TIMESTAMP() AS materialized_at
FROM keys k
LEFT JOIN request_daily r
  ON r.date_jst = k.date_jst
 AND r.user_id = k.user_id
 AND COALESCE(r.user_id_hash, '') = COALESCE(k.user_id_hash, '')
LEFT JOIN answer_daily a
  ON a.date_jst = k.date_jst
 AND a.user_id = k.user_id
 AND COALESCE(a.user_id_hash, '') = COALESCE(k.user_id_hash, '')
LEFT JOIN followup_open_daily fo
  ON fo.date_jst = k.date_jst
 AND fo.user_id = k.user_id
 AND COALESCE(fo.user_id_hash, '') = COALESCE(k.user_id_hash, '')
LEFT JOIN followup_resolution_daily fr
  ON fr.date_jst = k.date_jst
 AND fr.user_id = k.user_id
 AND COALESCE(fr.user_id_hash, '') = COALESCE(k.user_id_hash, '');

CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly`
PARTITION BY bucket_date_jst
CLUSTER BY bucket_hour_jst AS
WITH request_hourly AS (
  SELECT
    TIMESTAMP_TRUNC(event_ts, HOUR) AS bucket_ts,
    COUNT(*) AS request_count,
    COUNTIF(status >= 500) AS error_count,
    COUNTIF(device_class = 'desktop') AS desktop_request_count,
    COUNTIF(device_class = 'mobile') AS mobile_request_count,
    COUNTIF(device_class = 'unknown') AS unknown_request_count,
    COUNTIF(latency_ms IS NOT NULL) AS latency_count,
    SUM(COALESCE(latency_ms, 0.0)) AS latency_sum_ms,
    APPROX_QUANTILES(latency_ms, 100 IGNORE NULLS)[OFFSET(95)] AS p95_latency_ms
  FROM `__PROJECT_ID__.__DATASET_ID__.v_request_user_metric_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
    OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
  )
  GROUP BY bucket_ts
),
request_user_hourly AS (
  SELECT
    TIMESTAMP_TRUNC(event_ts, HOUR) AS bucket_ts,
    COUNTIF(is_core) AS message_count,
    COUNTIF(is_core AND mode = 'internal') AS internal_mode_count,
    COUNTIF(is_core AND mode = 'websearch') AS websearch_mode_count,
    COUNT(DISTINCT IF(user_key = 'unknown', NULL, user_key)) AS active_user_count,
    HLL_COUNT.INIT(IF(user_key = 'unknown', NULL, user_key)) AS active_user_hll
  FROM (
    SELECT
      event_ts,
      mode,
      is_core,
      user_id,
      user_email,
      user_id_hash,
      COALESCE(NULLIF(user_id, ''), NULLIF(user_email, ''), NULLIF(user_id_hash, ''), 'unknown') AS user_key
    FROM `__PROJECT_ID__.__DATASET_ID__.v_request_user_metric_events`
  )
  WHERE NOT (
    LOWER(COALESCE(user_key, 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = '2401145@tc.terumo.co.jp'
    OR LOWER(COALESCE(user_email, '')) = 'lcs-agent@lcs-developer-483404.iam.gserviceaccount.com'
    OR REGEXP_CONTAINS(LOWER(CONCAT(COALESCE(user_id, ''), ' ', COALESCE(user_email, ''))), r'lcs-agent')
  )
  GROUP BY bucket_ts
),
followup_open_hourly AS (
  SELECT
    TIMESTAMP_TRUNC(event_ts, HOUR) AS bucket_ts,
    COUNTIF(event = 'recognized') AS followup_recognized_count,
    COUNTIF(event = 'success') AS followup_success_count
  FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_open_result_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
  )
  GROUP BY bucket_ts
),
followup_resolution_hourly AS (
  SELECT
    TIMESTAMP_TRUNC(event_ts, HOUR) AS bucket_ts,
    COUNTIF(decision_normalized = 'explicit_correction') AS explicit_correction_count,
    COUNTIF(decision_normalized = 'clarify_before_carry') AS clarification_required_count,
    COUNTIF(followup_offtopic) AS followup_offtopic_count
  FROM `__PROJECT_ID__.__DATASET_ID__.v_followup_resolution_events`
  WHERE NOT (
    LOWER(COALESCE(NULLIF(user_id, ''), NULLIF(user_id_hash, ''), 'unknown')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) = 'unknown'
    OR LOWER(COALESCE(user_id, '')) IN ('109382080128482733156', '102048678887357191337', '2401145')
    OR LOWER(COALESCE(user_id_hash, '')) IN ('109382080128482733156', '102048678887357191337')
    OR LOWER(COALESCE(user_id, '')) = '2401145@tc.terumo.co.jp'
    OR REGEXP_CONTAINS(LOWER(COALESCE(user_id, '')), r'lcs-agent')
  )
  GROUP BY bucket_ts
),
answer_hour_base AS (
  SELECT
    TIMESTAMP_TRUNC(event_ts, HOUR) AS bucket_ts,
    answer_success_flag,
    low_coverage_flag,
    coverage_score,
    alignment_score,
    structured_led,
    claim_alignment_fallback,
    citation_mapping_source,
    COALESCE(NULLIF(answer_success_metric_status, ''), 'proxy') AS answer_success_metric_status,
    COALESCE(NULLIF(answerability_level, ''), 'unknown') AS answerability_level,
    COALESCE(NULLIF(usability_level, ''), 'unknown') AS usability_level,
    COALESCE(NULLIF(delivery_readiness, ''), 'unknown') AS delivery_readiness,
    COALESCE(NULLIF(evidence_sufficiency, ''), 'unknown') AS evidence_sufficiency,
    COALESCE(NULLIF(verification_verdict, ''), 'unknown') AS verification_verdict,
    COALESCE(NULLIF(intent_family, ''), 'unknown') AS intent_family
  FROM `__PROJECT_ID__.__DATASET_ID__.monitor_answer_events`
),
answer_hourly AS (
  SELECT
    bucket_ts,
    COUNT(*) AS answer_count,
    COUNTIF(answer_success_flag) AS answer_success_count,
    COUNTIF(low_coverage_flag) AS low_coverage_count,
    SUM(coverage_score) AS coverage_score_sum,
    COUNTIF(coverage_score IS NOT NULL) AS coverage_score_count,
    SUM(alignment_score) AS alignment_score_sum,
    COUNTIF(alignment_score IS NOT NULL) AS alignment_score_count,
    COUNTIF(structured_led) AS structured_led_count,
    COUNTIF(claim_alignment_fallback OR citation_mapping_source = 'legacy') AS citation_binding_issue_count,
    COUNTIF(answer_success_metric_status = 'official') AS answer_metric_official_count,
    COUNTIF(answer_success_metric_status = 'proxy') AS answer_metric_proxy_count
  FROM answer_hour_base
  GROUP BY bucket_ts
),
answerability_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS answerability_distribution
  FROM (
    SELECT bucket_ts, answerability_level AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
usability_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS usability_distribution
  FROM (
    SELECT bucket_ts, usability_level AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
delivery_readiness_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS delivery_readiness_distribution
  FROM (
    SELECT bucket_ts, delivery_readiness AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
evidence_sufficiency_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS evidence_sufficiency_distribution
  FROM (
    SELECT bucket_ts, evidence_sufficiency AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
verification_verdict_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS verification_verdict_distribution
  FROM (
    SELECT bucket_ts, verification_verdict AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
question_category_distribution_hourly AS (
  SELECT bucket_ts, ARRAY_AGG(STRUCT(label AS label, count AS count) ORDER BY count DESC, label) AS question_category_distribution
  FROM (
    SELECT bucket_ts, intent_family AS label, COUNT(*) AS count
    FROM answer_hour_base
    GROUP BY bucket_ts, label
  )
  GROUP BY bucket_ts
),
keys AS (
  SELECT bucket_ts FROM request_hourly
  UNION DISTINCT
  SELECT bucket_ts FROM request_user_hourly
  UNION DISTINCT
  SELECT bucket_ts FROM followup_open_hourly
  UNION DISTINCT
  SELECT bucket_ts FROM followup_resolution_hourly
  UNION DISTINCT
  SELECT bucket_ts FROM answer_hourly
)
SELECT
  k.bucket_ts,
  'monitor_exclusions_v2' AS aggregate_contract_version,
  DATE(k.bucket_ts, 'Asia/Tokyo') AS bucket_date_jst,
  EXTRACT(HOUR FROM DATETIME(k.bucket_ts, 'Asia/Tokyo')) AS bucket_hour_jst,
  COALESCE(r.request_count, 0) AS request_count,
  COALESCE(r.error_count, 0) AS error_count,
  SAFE_DIVIDE(COALESCE(r.error_count, 0), NULLIF(COALESCE(r.request_count, 0), 0)) AS error_rate,
  COALESCE(r.desktop_request_count, 0) AS desktop_request_count,
  COALESCE(r.mobile_request_count, 0) AS mobile_request_count,
  COALESCE(r.unknown_request_count, 0) AS unknown_request_count,
  COALESCE(r.latency_count, 0) AS latency_count,
  COALESCE(r.latency_sum_ms, 0.0) AS latency_sum_ms,
  r.p95_latency_ms,
  COALESCE(u.message_count, 0) AS message_count,
  COALESCE(u.internal_mode_count, 0) AS internal_mode_count,
  COALESCE(u.websearch_mode_count, 0) AS websearch_mode_count,
  COALESCE(u.active_user_count, 0) AS active_user_count_hourly,
  u.active_user_hll,
  COALESCE(fo.followup_recognized_count, 0) AS followup_recognized_count,
  COALESCE(fo.followup_success_count, 0) AS followup_success_count,
  COALESCE(fr.explicit_correction_count, 0) AS explicit_correction_count,
  COALESCE(fr.clarification_required_count, 0) AS clarification_required_count,
  COALESCE(fr.followup_offtopic_count, 0) AS followup_offtopic_count,
  COALESCE(a.answer_count, 0) AS answer_count,
  COALESCE(a.answer_success_count, 0) AS answer_success_count,
  COALESCE(a.low_coverage_count, 0) AS low_coverage_count,
  COALESCE(a.coverage_score_sum, 0.0) AS coverage_score_sum,
  COALESCE(a.coverage_score_count, 0) AS coverage_score_count,
  COALESCE(a.alignment_score_sum, 0.0) AS alignment_score_sum,
  COALESCE(a.alignment_score_count, 0) AS alignment_score_count,
  COALESCE(a.structured_led_count, 0) AS structured_led_count,
  COALESCE(a.citation_binding_issue_count, 0) AS citation_binding_issue_count,
  COALESCE(a.answer_metric_official_count, 0) AS answer_metric_official_count,
  COALESCE(a.answer_metric_proxy_count, 0) AS answer_metric_proxy_count,
  ad.answerability_distribution,
  ud.usability_distribution,
  drd.delivery_readiness_distribution,
  esd.evidence_sufficiency_distribution,
  vvd.verification_verdict_distribution,
  qcd.question_category_distribution,
  CURRENT_TIMESTAMP() AS materialized_at
FROM keys k
LEFT JOIN request_hourly r USING(bucket_ts)
LEFT JOIN request_user_hourly u USING(bucket_ts)
LEFT JOIN followup_open_hourly fo USING(bucket_ts)
LEFT JOIN followup_resolution_hourly fr USING(bucket_ts)
LEFT JOIN answer_hourly a USING(bucket_ts)
LEFT JOIN answerability_distribution_hourly ad USING(bucket_ts)
LEFT JOIN usability_distribution_hourly ud USING(bucket_ts)
LEFT JOIN delivery_readiness_distribution_hourly drd USING(bucket_ts)
LEFT JOIN evidence_sufficiency_distribution_hourly esd USING(bucket_ts)
LEFT JOIN verification_verdict_distribution_hourly vvd USING(bucket_ts)
LEFT JOIN question_category_distribution_hourly qcd USING(bucket_ts);

CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_dashboard_snapshots`
CLUSTER BY preset AS
WITH clock AS (
  SELECT
    CURRENT_TIMESTAMP() AS now_ts,
    'Asia/Tokyo' AS timezone,
    CURRENT_DATETIME('Asia/Tokyo') AS now_jst,
    DATETIME_TRUNC(CURRENT_DATETIME('Asia/Tokyo'), DAY) AS today_start_jst
),
preset_windows AS (
  SELECT 'today' AS preset, timezone,
    TIMESTAMP(today_start_jst, timezone) AS source_start_ts,
    now_ts AS source_end_ts
  FROM clock
  UNION ALL
  SELECT 'last_6h', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 6 HOUR), now_ts FROM clock
  UNION ALL
  SELECT 'last_12h', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 12 HOUR), now_ts FROM clock
  UNION ALL
  SELECT 'last_3d', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 3 DAY), now_ts FROM clock
  UNION ALL
  SELECT 'last_7d', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 7 DAY), now_ts FROM clock
  UNION ALL
  SELECT 'last_14d', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 14 DAY), now_ts FROM clock
  UNION ALL
  SELECT 'last_30d', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 30 DAY), now_ts FROM clock
  UNION ALL
  SELECT 'last_60d', timezone, TIMESTAMP_SUB(now_ts, INTERVAL 60 DAY), now_ts FROM clock
  UNION ALL
  SELECT 'all', timezone, TIMESTAMP_SUB(now_ts, INTERVAL __MONITOR_RETENTION_DAYS__ DAY), now_ts FROM clock
),
hours AS (
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
preset_dates AS (
  SELECT
    p.preset,
    p.timezone,
    event_date
  FROM preset_windows p,
  UNNEST(
    GENERATE_DATE_ARRAY(
      DATE(p.source_start_ts, p.timezone),
      DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 1 SECOND), p.timezone),
      INTERVAL 1 DAY
    )
  ) AS event_date
),
hourly_window AS (
  SELECT
    p.preset,
    h.*
  FROM preset_windows p
  LEFT JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly` h
    ON h.bucket_ts >= TIMESTAMP_TRUNC(p.source_start_ts, HOUR)
   AND h.bucket_ts < p.source_end_ts
),
request_summary AS (
  SELECT
    preset,
    SUM(request_count) AS request_count,
    SUM(error_count) AS error_count,
    SAFE_DIVIDE(SUM(error_count), SUM(request_count)) AS error_rate,
    MAX(p95_latency_ms) AS p95_latency_ms
  FROM hourly_window
  GROUP BY preset
),
request_by_hour AS (
  SELECT
    p.preset,
    FORMAT('%02d:00', h.hour) AS hour_label,
    COALESCE(SUM(w.request_count), 0) AS request_count
  FROM preset_windows p
  CROSS JOIN hours h
  LEFT JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly` w
    ON w.bucket_ts >= TIMESTAMP_TRUNC(p.source_start_ts, HOUR)
   AND w.bucket_ts < p.source_end_ts
   AND w.bucket_hour_jst = h.hour
  GROUP BY p.preset, h.hour
),
device_distribution AS (
  SELECT
    p.preset,
    d.device_class,
    CASE d.device_class
      WHEN 'desktop' THEN COALESCE(SUM(w.desktop_request_count), 0)
      WHEN 'mobile' THEN COALESCE(SUM(w.mobile_request_count), 0)
      ELSE COALESCE(SUM(w.unknown_request_count), 0)
    END AS request_count
  FROM preset_windows p
  CROSS JOIN device_labels d
  LEFT JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly` w
    ON w.bucket_ts >= TIMESTAMP_TRUNC(p.source_start_ts, HOUR)
   AND w.bucket_ts < p.source_end_ts
  GROUP BY p.preset, d.device_class
),
mode_distribution AS (
  SELECT
    p.preset,
    m.mode,
    CASE m.mode
      WHEN 'internal' THEN COALESCE(SUM(w.internal_mode_count), 0)
      WHEN 'websearch' THEN COALESCE(SUM(w.websearch_mode_count), 0)
      ELSE 0
    END AS request_count
  FROM preset_windows p
  CROSS JOIN mode_labels m
  LEFT JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly` w
    ON w.bucket_ts >= TIMESTAMP_TRUNC(p.source_start_ts, HOUR)
   AND w.bucket_ts < p.source_end_ts
  GROUP BY p.preset, m.mode
),
active_users AS (
  SELECT
    preset,
    COALESCE(HLL_COUNT.MERGE(active_user_hll), 0) AS active_user_count
  FROM hourly_window
  WHERE active_user_hll IS NOT NULL
  GROUP BY preset
),
usage_trend_agg AS (
  SELECT
    p.preset,
    d.date_jst AS event_date,
    COUNT(DISTINCT IF(d.active_flag AND d.user_id != 'unknown', d.user_id, NULL)) AS active_user_count,
    SUM(d.message_count) AS message_count
  FROM preset_windows p
  JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_user_daily` d
    ON d.date_jst >= DATE(p.source_start_ts, p.timezone)
   AND d.date_jst <= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 1 SECOND), p.timezone)
  GROUP BY p.preset, event_date
),
usage_trend AS (
  SELECT
    d.preset,
    FORMAT_DATE('%Y-%m-%d', d.event_date) AS date_label,
    COALESCE(a.active_user_count, 0) AS active_user_count,
    COALESCE(a.message_count, 0) AS message_count
  FROM preset_dates d
  LEFT JOIN usage_trend_agg a
    ON a.preset = d.preset
   AND a.event_date = d.event_date
),
activity_by_user AS (
  SELECT
    p.preset,
    COALESCE(NULLIF(d.user_id, ''), NULLIF(d.user_email, ''), NULLIF(d.user_id_hash, ''), 'unknown') AS user_key,
    SUM(IF(d.date_jst >= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 3 DAY), p.timezone), d.message_count, 0)) AS core_count_3d,
    SUM(IF(d.date_jst >= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 7 DAY), p.timezone), d.message_count, 0)) AS core_count_7d,
    SUM(IF(d.date_jst >= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 14 DAY), p.timezone), d.message_count, 0)) AS core_count_14d
  FROM preset_windows p
  JOIN `__PROJECT_ID__.__DATASET_ID__.monitor_user_daily` d
    ON d.date_jst >= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 14 DAY), p.timezone)
   AND d.date_jst <= DATE(TIMESTAMP_SUB(p.source_end_ts, INTERVAL 1 SECOND), p.timezone)
  GROUP BY p.preset, user_key
),
activity_segments AS (
  SELECT
    preset,
    CASE
      WHEN core_count_3d >= 3 THEN '高アクティブ'
      WHEN core_count_7d BETWEEN 1 AND 2 THEN '中アクティブ'
      WHEN core_count_14d >= 1 THEN '低アクティブ'
      ELSE '休眠ユーザー'
    END AS label,
    COUNT(*) AS count
  FROM activity_by_user
  WHERE user_key != 'unknown'
  GROUP BY preset, label
),
activity_labels AS (
  SELECT '高アクティブ' AS label UNION ALL
  SELECT '中アクティブ' UNION ALL
  SELECT '低アクティブ' UNION ALL
  SELECT '休眠ユーザー'
),
answer_summary AS (
  SELECT
    preset,
    SUM(answer_count) AS answer_count,
    SUM(answer_success_count) AS answer_success_count,
    SAFE_DIVIDE(SUM(answer_success_count), SUM(answer_count)) AS answer_success_rate,
    SUM(low_coverage_count) AS low_coverage_count,
    SAFE_DIVIDE(SUM(low_coverage_count), SUM(answer_count)) AS low_coverage_rate,
    SAFE_DIVIDE(SUM(coverage_score_sum), SUM(coverage_score_count)) AS average_coverage_score,
    SAFE_DIVIDE(SUM(alignment_score_sum), SUM(alignment_score_count)) AS average_alignment_score,
    SUM(structured_led_count) AS structured_led_count,
    SAFE_DIVIDE(SUM(structured_led_count), SUM(answer_count)) AS structured_led_rate,
    SUM(citation_binding_issue_count) AS citation_binding_issue_count,
    SAFE_DIVIDE(SUM(citation_binding_issue_count), SUM(answer_count)) AS citation_binding_issue_rate,
    SUM(answer_metric_official_count) AS answer_metric_official_count,
    SUM(answer_metric_proxy_count) AS answer_metric_proxy_count
  FROM hourly_window
  GROUP BY preset
),
answer_distribution AS (
  SELECT preset, metric, label, SUM(count) AS count
  FROM (
    SELECT preset, 'answerability' AS metric, item.label AS label, item.count AS count
    FROM hourly_window, UNNEST(IFNULL(answerability_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT preset, 'usability', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(usability_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT preset, 'deliveryReadiness', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(delivery_readiness_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT preset, 'evidenceSufficiency', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(evidence_sufficiency_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
    UNION ALL
    SELECT preset, 'verificationVerdict', item.label, item.count
    FROM hourly_window, UNNEST(IFNULL(verification_verdict_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
  )
  GROUP BY preset, metric, label
),
question_category_rollup AS (
  SELECT preset, label, SUM(count) AS count
  FROM (
    SELECT preset, item.label AS label, item.count AS count
    FROM hourly_window, UNNEST(IFNULL(question_category_distribution, ARRAY<STRUCT<label STRING, count INT64>>[])) AS item
  )
  GROUP BY preset, label
),
followup_summary AS (
  SELECT
    preset,
    SUM(followup_recognized_count) AS recognized_count,
    SUM(followup_success_count) AS success_count,
    SUM(explicit_correction_count) AS explicit_correction_count,
    SUM(clarification_required_count) AS clarification_required_count,
    SUM(followup_offtopic_count) AS followup_offtopic_count
  FROM hourly_window
  GROUP BY preset
),
activity_total AS (
  SELECT preset, SUM(COALESCE(count, 0)) AS total_count
  FROM activity_segments
  GROUP BY preset
),
device_total AS (
  SELECT preset, SUM(request_count) AS total_count
  FROM device_distribution
  GROUP BY preset
),
mode_total AS (
  SELECT preset, SUM(request_count) AS total_count
  FROM mode_distribution
  GROUP BY preset
),
usage_trend_payload AS (
  SELECT
    preset,
    ARRAY_AGG(STRUCT(
      date_label AS date,
      active_user_count AS activeUserCount,
      message_count AS messageCount
    ) ORDER BY date_label) AS items
  FROM usage_trend
  GROUP BY preset
),
activity_distribution_payload AS (
  SELECT
    p.preset,
    COALESCE(t.total_count, 0) AS total_user_count,
    ARRAY_AGG(STRUCT(
      l.label AS label,
      COALESCE(s.count, 0) AS count,
      SAFE_DIVIDE(COALESCE(s.count, 0), NULLIF(t.total_count, 0)) AS rate
    ) ORDER BY CASE l.label
      WHEN '高アクティブ' THEN 1
      WHEN '中アクティブ' THEN 2
      WHEN '低アクティブ' THEN 3
      ELSE 4
    END) AS segments
  FROM preset_windows p
  CROSS JOIN activity_labels l
  LEFT JOIN activity_segments s
    ON s.preset = p.preset
   AND s.label = l.label
  LEFT JOIN activity_total t
    ON t.preset = p.preset
  GROUP BY p.preset, t.total_count
),
request_by_hour_payload AS (
  SELECT
    preset,
    ARRAY_AGG(STRUCT(hour_label AS hour, request_count AS requestCount) ORDER BY hour_label) AS items
  FROM request_by_hour
  GROUP BY preset
),
device_distribution_payload AS (
  SELECT
    d.preset,
    ARRAY_AGG(STRUCT(
      CASE d.device_class
        WHEN 'desktop' THEN 'PC'
        WHEN 'mobile' THEN 'モバイル'
        ELSE '不明'
      END AS label,
      d.device_class AS value,
      d.request_count AS count,
      SAFE_DIVIDE(d.request_count, NULLIF(t.total_count, 0)) AS rate
    ) ORDER BY d.request_count DESC, d.device_class) AS items
  FROM device_distribution d
  LEFT JOIN device_total t USING(preset)
  GROUP BY d.preset
),
mode_distribution_payload AS (
  SELECT
    m.preset,
    ARRAY_AGG(STRUCT(
      CASE m.mode
        WHEN 'internal' THEN '社内モード'
        WHEN 'websearch' THEN 'Web検索モード'
        ELSE 'その他'
      END AS label,
      m.mode AS value,
      m.request_count AS count,
      SAFE_DIVIDE(m.request_count, NULLIF(t.total_count, 0)) AS rate
    ) ORDER BY m.request_count DESC, m.mode) AS items
  FROM mode_distribution m
  LEFT JOIN mode_total t USING(preset)
  GROUP BY m.preset
),
answer_metric_payload AS (
  SELECT
    d.preset,
    d.metric,
    ARRAY_AGG(STRUCT(
      d.label AS label,
      d.count AS count,
      SAFE_DIVIDE(d.count, NULLIF(a.answer_count, 0)) AS rate
    ) ORDER BY d.count DESC, d.label) AS items
  FROM answer_distribution d
  LEFT JOIN answer_summary a USING(preset)
  GROUP BY d.preset, d.metric
),
question_category_payload AS (
  SELECT
    q.preset,
    ARRAY_AGG(STRUCT(
      q.label AS label,
      q.label AS value,
      q.count AS count,
      SAFE_DIVIDE(q.count, NULLIF(a.answer_count, 0)) AS rate
    ) ORDER BY q.count DESC, q.label) AS items
  FROM question_category_rollup q
  LEFT JOIN answer_summary a USING(preset)
  GROUP BY q.preset
)
SELECT
  p.preset,
  p.timezone,
  p.source_start_ts,
  p.source_end_ts,
  TO_JSON_STRING(STRUCT(
    'monitor_exclusions_v2' AS snapshotContract,
    STRUCT(
      COALESCE(au.active_user_count, 0) AS activeUserCount,
      ans.answer_success_rate AS answerSuccessRate,
      ans.low_coverage_rate AS lowCoverageRate,
      req.error_rate AS errorRate,
      req.p95_latency_ms AS p95LatencyMs
    ) AS kpis,
    STRUCT(
      CASE
        WHEN COALESCE(ans.answer_count, 0) = 0 THEN 'unknown'
        WHEN COALESCE(ans.answer_metric_official_count, 0) > 0
         AND COALESCE(ans.answer_metric_proxy_count, 0) > 0 THEN 'mixed'
        WHEN COALESCE(ans.answer_metric_official_count, 0) > 0 THEN 'official'
        ELSE 'proxy'
      END AS answerSuccessRate
    ) AS metricStatus,
    ut.items AS usageTrend,
    STRUCT(
      COALESCE(adp.total_user_count, 0) AS totalUserCount,
      adp.segments AS segments
    ) AS activityDistribution,
    STRUCT(
      rhp.items AS requestByHour,
      ddp.items AS deviceDistribution,
      mdp.items AS modeDistribution
    ) AS environmentMode,
    STRUCT(
      aq_answerability.items AS answerability,
      aq_usability.items AS usability,
      aq_delivery.items AS deliveryReadiness,
      aq_evidence.items AS evidenceSufficiency,
      aq_verification.items AS verificationVerdict
    ) AS answerQuality,
    STRUCT(
      qcp.items AS items
    ) AS questionCategory,
    STRUCT(
      fs.recognized_count AS recognizedCount,
      fs.success_count AS successCount,
      SAFE_DIVIDE(fs.success_count, NULLIF(fs.recognized_count, 0)) AS successRate,
      fs.explicit_correction_count AS explicitCorrectionCount,
      fs.clarification_required_count AS clarificationRequiredCount,
      fs.followup_offtopic_count AS followupOfftopicCount
    ) AS followup
  )) AS payload_json,
  CURRENT_TIMESTAMP() AS materialized_at
FROM preset_windows p
LEFT JOIN active_users au USING(preset)
LEFT JOIN answer_summary ans USING(preset)
LEFT JOIN request_summary req USING(preset)
LEFT JOIN usage_trend_payload ut USING(preset)
LEFT JOIN activity_distribution_payload adp USING(preset)
LEFT JOIN request_by_hour_payload rhp USING(preset)
LEFT JOIN device_distribution_payload ddp USING(preset)
LEFT JOIN mode_distribution_payload mdp USING(preset)
LEFT JOIN answer_metric_payload aq_answerability
  ON aq_answerability.preset = p.preset
 AND aq_answerability.metric = 'answerability'
LEFT JOIN answer_metric_payload aq_usability
  ON aq_usability.preset = p.preset
 AND aq_usability.metric = 'usability'
LEFT JOIN answer_metric_payload aq_delivery
  ON aq_delivery.preset = p.preset
 AND aq_delivery.metric = 'deliveryReadiness'
LEFT JOIN answer_metric_payload aq_evidence
  ON aq_evidence.preset = p.preset
 AND aq_evidence.metric = 'evidenceSufficiency'
LEFT JOIN answer_metric_payload aq_verification
  ON aq_verification.preset = p.preset
 AND aq_verification.metric = 'verificationVerdict'
LEFT JOIN question_category_payload qcp
  ON qcp.preset = p.preset
LEFT JOIN followup_summary fs
  ON fs.preset = p.preset;
